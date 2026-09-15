"""Milestone 6 auth + save-to-account tests.

Unlike test_tryon_api.py, these need a real database — auth relies on real
uniqueness constraints and persisted rows, which aren't worth faking with a
mock session. They auto-skip (not fail) if Postgres isn't reachable, so the
rest of the suite stays runnable without it — see docs/DEVELOPMENT.md for
how to stand up the local Postgres these tests use.

Run with: ai\\.venv\\Scripts\\python.exe -m pytest tests/test_auth_api.py
"""

import io
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from backend.app.api import auth as auth_module
from backend.app.api import tryon as tryon_module
from backend.app.api import usage as usage_module
from backend.app.core.errors import configure_exception_handlers
from backend.app.db import Plan, User, engine, get_session
from backend.app.providers.base import TryOnRequest, TryOnResult, VirtualTryOnProvider
from backend.app.services.db_job_store import DbJobStore
from backend.app.services.quota_service import QuotaService
from backend.app.services.rate_limiter import RateLimiter
from backend.app.services.storage import LocalStorageService
from backend.app.services.tryon_service import TryOnService
from tests.conftest import FakeQuotaService


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


class FakeProvider(VirtualTryOnProvider):
    def generate(self, request: TryOnRequest) -> TryOnResult:
        return TryOnResult(image=Image.new("RGB", (64, 64), color="red"))


def make_test_app(tmp_path, quota_service=None):
    app = FastAPI()
    configure_exception_handlers(app)
    app.include_router(tryon_module.router)
    app.include_router(auth_module.router)
    app.include_router(usage_module.router)
    app.state.tryon_service = TryOnService(
        provider=FakeProvider(),
        storage=LocalStorageService(str(tmp_path)),
        job_store=DbJobStore(),
    )
    app.state.rate_limiter = RateLimiter(max_requests=1000, window_seconds=3600)
    # Permissive by default: anonymous usage is tracked by client IP, which
    # every TestClient request in this file shares — a real QuotaService
    # here by default would make repeated test runs flaky as that shared
    # identity's daily count climbs (see conftest.py's FakeQuotaService
    # docstring). Tests that specifically verify quota enforcement pass
    # quota_service=QuotaService() explicitly and use a fresh per-test user.
    app.state.quota_service = quota_service or FakeQuotaService()
    return app


def _png_bytes(color="white"):
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), color=color).save(buf, format="PNG")
    return buf.getvalue()


def _submit(client, headers=None):
    files = {
        "person_image": ("person.png", _png_bytes("blue"), "image/png"),
        "garment_image": ("garment.png", _png_bytes("green"), "image/png"),
    }
    return client.post("/api/try-on", files=files, data={"category": "tops"}, headers=headers or {})


@pytest.fixture
def client(tmp_path):
    with TestClient(make_test_app(tmp_path)) as c:
        yield c


@pytest.fixture
def test_email():
    """A unique email per test, cleaned up (and its jobs, via FK cascade
    ordering below) from the shared local dev database afterward."""
    email = f"test-{uuid.uuid4().hex}@example.com"
    yield email
    with get_session() as session:
        user = session.query(User).filter(User.email == email).first()
        if user is not None:
            session.delete(user)


def signup(client, email, password="correct-horse-battery"):
    return client.post("/api/auth/signup", json={"email": email, "password": password})


def test_signup_then_me(client, test_email):
    resp = signup(client, test_email)
    assert resp.status_code == 201, resp.text
    token = resp.json()["access_token"]

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == test_email
    assert me.json()["plan"] == "free"


def test_me_requires_auth(client):
    assert client.get("/api/auth/me").status_code == 401


def test_duplicate_signup_rejected(client, test_email):
    assert signup(client, test_email).status_code == 201
    dup = signup(client, test_email)
    assert dup.status_code == 409
    assert "already exists" in dup.json()["detail"]


def test_login_success_and_wrong_password(client, test_email):
    signup(client, test_email, password="right-password")

    ok = client.post("/api/auth/login", json={"email": test_email, "password": "right-password"})
    assert ok.status_code == 200
    assert ok.json()["access_token"]

    bad = client.post("/api/auth/login", json={"email": test_email, "password": "wrong-password"})
    assert bad.status_code == 401
    assert bad.json()["detail"] == "Incorrect email or password."  # same message as unknown-email case


def test_login_unknown_email_same_message_as_wrong_password(client):
    resp = client.post("/api/auth/login", json={"email": "nobody-here@example.com", "password": "whatever"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Incorrect email or password."


def test_authenticated_job_can_be_saved_by_its_owner(client, test_email):
    token = signup(client, test_email).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    job_id = _submit(client, headers=headers).json()["job_id"]
    status = client.get(f"/api/try-on/{job_id}").json()
    assert status["status"] == "completed"  # fake provider is instant
    assert status["saved"] is False

    save_resp = client.post(f"/api/try-on/{job_id}/save", headers=headers)
    assert save_resp.status_code == 200, save_resp.text
    assert save_resp.json()["saved"] is True

    assert client.get(f"/api/try-on/{job_id}").json()["saved"] is True


def test_save_requires_auth(client):
    job_id = _submit(client).json()["job_id"]  # anonymous submission
    assert client.post(f"/api/try-on/{job_id}/save").status_code == 401


def test_cannot_save_someone_elses_or_anonymous_job(client, test_email):
    # Anonymous job: nobody, not even a signed-in user, can retroactively claim it.
    anon_job_id = _submit(client).json()["job_id"]
    token = signup(client, test_email).json()["access_token"]
    resp = client.post(f"/api/try-on/{anon_job_id}/save", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
    assert "your own" in resp.json()["detail"]


def test_save_nonexistent_job_returns_404(client, test_email):
    token = signup(client, test_email).json()["access_token"]
    resp = client.post("/api/try-on/does-not-exist/save", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 404


# --- Milestone 11: usage quotas, against the real QuotaService --------------
#
# These use a fresh per-test user (via test_email), never the shared `client`
# fixture's default FakeQuotaService — a brand-new user_id has no prior job
# history, so this is naturally isolated from other tests/runs without
# needing any special cleanup, unlike anonymous (IP-based) quota tracking
# would be. See conftest.py's FakeQuotaService docstring for the reasoning.


def test_free_plan_daily_quota_enforced(tmp_path, test_email):
    with get_session() as session:
        free_plan = session.get(Plan, "free")
        daily_limit = free_plan.max_generations_per_day
    assert daily_limit, "this test needs the free plan to have a real numeric daily cap to test against"

    client = TestClient(make_test_app(tmp_path, quota_service=QuotaService()))
    token = signup(client, test_email).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    for i in range(daily_limit):
        resp = _submit(client, headers=headers)
        assert resp.status_code == 202, f"submission {i + 1}/{daily_limit} should be allowed: {resp.text}"

    over_limit = _submit(client, headers=headers)
    assert over_limit.status_code == 429
    assert "generations for today" in over_limit.json()["detail"]


def test_usage_endpoint_reflects_consumption(tmp_path, test_email):
    client = TestClient(make_test_app(tmp_path, quota_service=QuotaService()))
    token = signup(client, test_email).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    before = client.get("/api/usage/me", headers=headers).json()
    assert before["plan"] == "free"
    assert before["used_today"] == 0

    _submit(client, headers=headers)

    after = client.get("/api/usage/me", headers=headers).json()
    assert after["used_today"] == before["used_today"] + 1
    if before["remaining_today"] is not None:
        assert after["remaining_today"] == before["remaining_today"] - 1


def test_usage_endpoint_works_anonymously(tmp_path):
    client = TestClient(make_test_app(tmp_path, quota_service=QuotaService()))
    resp = client.get("/api/usage/me")
    assert resp.status_code == 200
    assert resp.json()["plan"] == "free"
