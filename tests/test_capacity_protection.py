"""Tests for backend/app/services/capacity_service.py and its wiring into
POST /api/try-on (backend/app/api/tryon.py's _check_capacity).

CapacityService always queries the real JobRecord table directly (same
design as QuotaService) regardless of which JobStore the app itself uses --
so, like test_auth_api.py's real-QuotaService tests, these need the real
database and use DbJobStore, not the fast InMemoryJobStore test apps
elsewhere. Auto-skips if Postgres isn't reachable, same convention as every
other real-DB test file in this suite.
"""

import threading
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from backend.app.api import tryon as tryon_module
from backend.app.core.errors import configure_exception_handlers
from backend.app.db import JobRecord, engine, get_session
from backend.app.services.capacity_service import CapacityService
from backend.app.services.db_job_store import DbJobStore
from backend.app.services.rate_limiter import RateLimiter
from backend.app.services.storage import LocalStorageService
from backend.app.services.tryon_service import TryOnService
from tests.conftest import FakeQuotaService
from tests.test_tryon_api import FakeProvider, _png_bytes


def _db_reachable() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False


pytestmark = pytest.mark.skipif(
    not _db_reachable(),
    reason="Postgres isn't reachable at settings.database_url — see docs/DEVELOPMENT.md",
)


def make_test_app(tmp_path, monkeypatch, max_active_tryon_jobs=10):
    from backend.app import config as config_module

    app = FastAPI()
    configure_exception_handlers(app)
    app.include_router(tryon_module.router)
    app.state.tryon_service = TryOnService(
        provider=FakeProvider(), storage=LocalStorageService(str(tmp_path)), job_store=DbJobStore()
    )
    app.state.rate_limiter = RateLimiter(max_requests=1000, window_seconds=3600)
    app.state.quota_service = FakeQuotaService()
    app.state.capacity_service = CapacityService()
    # settings.max_active_tryon_jobs is read directly inside _check_capacity
    # (not injected via app.state) -- monkeypatched (auto-reverted after
    # the test, unlike a raw attribute assignment) rather than passed
    # through app.state, same convention as test_tryon_api.py's own
    # test_rejects_oversized_upload.
    monkeypatch.setattr(config_module.settings, "max_active_tryon_jobs", max_active_tryon_jobs)
    return app


@pytest.fixture
def job_tag():
    """A unique marker (stashed in client_ip) so filler jobs this file
    inserts directly can never collide with another test's or a previous
    run's, and are cleaned up unconditionally afterward."""
    tag = f"capacity-test-{uuid.uuid4().hex}"
    yield tag
    with get_session() as session:
        session.query(JobRecord).filter(JobRecord.client_ip == tag).delete()


def _make_active_job(tag: str, status: str = "processing") -> JobRecord:
    return JobRecord(
        id=uuid.uuid4().hex,
        user_id=None,
        client_ip=tag,
        status=status,
        category="tops",
        num_timesteps=4,
        guidance_scale=1.5,
        seed=42,
    )


def _submit(client, **overrides):
    files = {
        "person_image": ("person.png", _png_bytes("blue"), "image/png"),
        "garment_image": ("garment.png", _png_bytes("green"), "image/png"),
    }
    files.update(overrides.pop("files", {}))
    headers = overrides.pop("headers", None)
    data = {"category": "tops"}
    data.update(overrides)
    return client.post("/api/try-on", files=files, data=data, headers=headers)


def _total_job_count() -> int:
    with get_session() as session:
        return session.query(JobRecord).count()


# --- CapacityService, direct (no HTTP) -----------------------------------------


def test_get_status_counts_only_pending_and_processing_jobs(job_tag):
    with get_session() as session:
        session.add_all(
            [
                _make_active_job(job_tag, status="pending"),
                _make_active_job(job_tag, status="processing"),
                _make_active_job(job_tag, status="completed"),
                _make_active_job(job_tag, status="failed"),
                _make_active_job(job_tag, status="cancelled"),
            ]
        )

    # This counts ALL pending/processing jobs system-wide, not just this
    # test's own -- so assert the *minimum* it must include (this test's 2
    # active fillers), not an exact total, which the shared dev database
    # can't promise.
    status = CapacityService().get_status(max_active_jobs=10_000_000)
    assert status.active_jobs >= 2


def test_is_at_capacity_boundary_is_inclusive():
    from backend.app.services.capacity_service import CapacityStatus

    assert CapacityStatus(active_jobs=5, max_active_jobs=5).is_at_capacity is True
    assert CapacityStatus(active_jobs=4, max_active_jobs=5).is_at_capacity is False
    assert CapacityStatus(active_jobs=6, max_active_jobs=5).is_at_capacity is True


# --- Wired into POST /api/try-on ------------------------------------------------


def test_submission_succeeds_when_comfortably_under_capacity(tmp_path, monkeypatch):
    app = make_test_app(tmp_path, monkeypatch, max_active_tryon_jobs=10_000_000)  # effectively unlimited
    client = TestClient(app)

    resp = _submit(client)

    assert resp.status_code == 202, resp.text


def test_submission_rejected_with_safe_message_when_at_capacity(tmp_path, monkeypatch, job_tag):
    with get_session() as session:
        session.add_all([_make_active_job(job_tag), _make_active_job(job_tag)])

    app = make_test_app(tmp_path, monkeypatch, max_active_tryon_jobs=2)
    client = TestClient(app)

    resp = _submit(client)

    assert resp.status_code == 429
    assert "capacity" in resp.json()["detail"].lower()
    assert "Traceback" not in resp.text


def test_capacity_rejected_request_creates_no_job(tmp_path, monkeypatch, job_tag):
    with get_session() as session:
        session.add_all([_make_active_job(job_tag), _make_active_job(job_tag)])

    app = make_test_app(tmp_path, monkeypatch, max_active_tryon_jobs=2)
    client = TestClient(app)

    before = _total_job_count()
    resp = _submit(client)
    after = _total_job_count()

    assert resp.status_code == 429
    assert after == before  # no row was inserted for the rejected request


def test_capacity_check_does_not_poison_the_idempotency_key(tmp_path, monkeypatch, job_tag):
    # Unique per run, not a fixed literal: a fixed key's real (non-filler)
    # job would use TestClient's shared default client_ip ("testclient",
    # not this test's own job_tag), so it would never be cleaned up by the
    # job_tag fixture below and could poison a *later* run of this exact
    # test -- exactly what happened once during development, tracked down
    # via a real leftover row in the shared dev database, not hypothetical.
    idempotency_key = f"capacity-then-retry-{job_tag}"

    with get_session() as session:
        session.add_all([_make_active_job(job_tag), _make_active_job(job_tag)])

    app = make_test_app(tmp_path, monkeypatch, max_active_tryon_jobs=2)
    client = TestClient(app)
    headers = {"Idempotency-Key": idempotency_key}

    try:
        rejected = _submit(client, headers=headers)
        assert rejected.status_code == 429

        # Free up capacity, then retry the *same* key -- must succeed as a
        # fresh submission, not a 409 conflict (which would mean the
        # rejected attempt had wrongly reserved the key).
        with get_session() as session:
            session.query(JobRecord).filter(JobRecord.client_ip == job_tag).delete()

        retried = _submit(client, headers=headers)
        assert retried.status_code == 202, retried.text
    finally:
        with get_session() as session:
            session.query(JobRecord).filter(JobRecord.idempotency_key == idempotency_key).delete()


def test_idempotent_retry_of_an_existing_job_succeeds_even_at_capacity(tmp_path, monkeypatch, job_tag):
    """find_idempotent_job's early return (an existing match needs no new
    capacity) must not be blocked by a *subsequent* capacity check that
    only matters for genuinely new job creation."""
    from backend.app import config as config_module

    # Unique per run -- see test_capacity_check_does_not_poison_the_idempotency_key's
    # comment for why a fixed literal key here previously left a real,
    # never-cleaned-up row (client_ip="testclient", not this test's own
    # job_tag) that poisoned a later run of this same test.
    idempotency_key = f"reuse-under-capacity-pressure-{job_tag}"

    app = make_test_app(tmp_path, monkeypatch, max_active_tryon_jobs=10_000_000)
    client = TestClient(app)
    headers = {"Idempotency-Key": idempotency_key}

    try:
        first = _submit(client, headers=headers)
        assert first.status_code == 202, first.text

        # Now fill capacity to its configured max.
        monkeypatch.setattr(config_module.settings, "max_active_tryon_jobs", 0)

        retried = _submit(client, headers=headers)

        assert retried.status_code == 202, retried.text
        assert retried.json()["job_id"] == first.json()["job_id"]
    finally:
        with get_session() as session:
            session.query(JobRecord).filter(JobRecord.idempotency_key == idempotency_key).delete()


def test_all_concurrent_submissions_rejected_when_already_at_capacity(tmp_path, monkeypatch, job_tag):
    """Deterministic, not a boundary-race test: capacity is already fully
    occupied by filler jobs *before* any of these concurrent requests
    arrive, so every one of them must see "at or over capacity" and be
    rejected -- none should be able to slip through under concurrency."""
    with get_session() as session:
        session.add_all([_make_active_job(job_tag), _make_active_job(job_tag)])

    app = make_test_app(tmp_path, monkeypatch, max_active_tryon_jobs=2)
    client = TestClient(app)

    barrier = threading.Barrier(5)
    results = [None] * 5

    def submit(i):
        barrier.wait()
        results[i] = _submit(client)

    threads = [threading.Thread(target=submit, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert all(r is not None and r.status_code == 429 for r in results)
