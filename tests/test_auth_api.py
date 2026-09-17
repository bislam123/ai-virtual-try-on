"""Milestone 6 auth + save-to-account tests.

Unlike test_tryon_api.py, these need a real database — auth relies on real
uniqueness constraints and persisted rows, which aren't worth faking with a
mock session. They auto-skip (not fail) if Postgres isn't reachable, so the
rest of the suite stays runnable without it — see docs/DEVELOPMENT.md for
how to stand up the local Postgres these tests use.

Run with: ai\\.venv\\Scripts\\python.exe -m pytest tests/test_auth_api.py
"""

import io
import threading
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
from backend.app.db import JobRecord, Plan, User, engine, get_session
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


def make_test_app(
    tmp_path,
    quota_service=None,
    auth_login_ip_rate_limit=1000,
    auth_login_account_rate_limit=1000,
    auth_signup_rate_limit=1000,
):
    app = FastAPI()
    configure_exception_handlers(app)
    app.include_router(tryon_module.router)
    app.include_router(auth_module.router)
    app.include_router(usage_module.router)
    storage = LocalStorageService(str(tmp_path))
    app.state.tryon_service = TryOnService(provider=FakeProvider(), storage=storage, job_store=DbJobStore())
    app.state.storage = storage  # used directly by auth_module's account-deletion endpoint
    app.state.rate_limiter = RateLimiter(max_requests=1000, window_seconds=3600)
    # Permissive by default (matches the pattern in test_extraction_api.py's
    # make_test_app(rate_limit=1000)): existing tests call signup()/login()
    # a handful of times per test and must never accidentally trip these.
    # Tests that specifically verify auth rate limiting pass a small value
    # for the relevant parameter instead.
    app.state.auth_login_ip_rate_limiter = RateLimiter(max_requests=auth_login_ip_rate_limit, window_seconds=900)
    app.state.auth_login_account_rate_limiter = RateLimiter(
        max_requests=auth_login_account_rate_limit, window_seconds=900
    )
    app.state.auth_signup_rate_limiter = RateLimiter(max_requests=auth_signup_rate_limit, window_seconds=3600)
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


# --- Login/signup rate limiting ---------------------------------------------


def login(client, email, password):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def test_login_allows_requests_up_to_the_limit(tmp_path, test_email):
    """Legitimate use (a couple of mistyped-password retries) must not be
    blocked -- confirms the limit is a ceiling, not a trip-wire on normal
    traffic."""
    client = TestClient(make_test_app(tmp_path, auth_login_ip_rate_limit=3, auth_login_account_rate_limit=3))
    signup(client, test_email, password="right-password")

    for _ in range(3):
        resp = login(client, test_email, "wrong-password")
        assert resp.status_code == 401  # allowed through to the real auth check, just fails on password


def test_login_rate_limited_by_account_across_many_attempts_same_email(tmp_path, test_email):
    """Brute-forcing one account's password, all from the same IP -- the
    account-scoped limit must trip regardless of the (generous) IP limit."""
    client = TestClient(make_test_app(tmp_path, auth_login_ip_rate_limit=1000, auth_login_account_rate_limit=2))
    signup(client, test_email, password="right-password")

    for _ in range(2):
        assert login(client, test_email, "wrong-password").status_code == 401

    limited = login(client, test_email, "wrong-password")
    assert limited.status_code == 429
    assert "Retry-After" in limited.headers


def test_login_rate_limited_by_ip_across_different_target_emails(tmp_path):
    """Credential-stuffing many different accounts from one source -- each
    target email gets its own fresh account-level budget, so only the
    per-IP limit can catch this pattern."""
    client = TestClient(make_test_app(tmp_path, auth_login_ip_rate_limit=2, auth_login_account_rate_limit=1000))

    for i in range(2):
        resp = login(client, f"target-{i}@example.com", "whatever")
        assert resp.status_code == 401  # allowed through, just an unknown email

    limited = login(client, "yet-another-target@example.com", "whatever")
    assert limited.status_code == 429
    assert "Retry-After" in limited.headers


def test_login_rate_limit_response_does_not_reveal_account_existence(tmp_path, test_email):
    """The core enumeration-safety property: once account-rate-limited,
    the response must be indistinguishable whether the targeted email is
    a real, registered account or a made-up one -- same status, same
    message shape, same header. Retry-After's exact numeric value is
    intentionally compared with a small tolerance rather than exact
    equality: it's derived from real elapsed time between the two calls
    (see RateLimiter.check's retry_after computation), so exact equality
    would be a real-time-dependent assertion and genuinely flaky under
    load, not a meaningful enumeration signal -- see this test's own
    history for a real instance of that flakiness, caught by running the
    full suite, not just this file in isolation."""
    client = TestClient(make_test_app(tmp_path, auth_login_ip_rate_limit=1000, auth_login_account_rate_limit=1))
    signup(client, test_email, password="right-password")

    login(client, test_email, "wrong-password")  # consumes the real account's budget
    real_limited = login(client, test_email, "wrong-password")

    fake_email = f"never-registered-{uuid.uuid4().hex}@example.com"
    login(client, fake_email, "whatever")  # consumes the fake account's own, separate budget
    fake_limited = login(client, fake_email, "whatever")

    assert real_limited.status_code == fake_limited.status_code == 429

    import re

    real_detail = real_limited.json()["detail"]
    fake_detail = fake_limited.json()["detail"]
    # Same message with the retry-seconds number normalized out.
    assert re.sub(r"\d+", "N", real_detail) == re.sub(r"\d+", "N", fake_detail)

    real_retry_after = int(real_limited.headers["Retry-After"])
    fake_retry_after = int(fake_limited.headers["Retry-After"])
    assert abs(real_retry_after - fake_retry_after) <= 2  # tolerance for real elapsed time between the two calls


def test_signup_allows_requests_up_to_the_limit(tmp_path):
    client = TestClient(make_test_app(tmp_path, auth_signup_rate_limit=2))
    emails = [f"signup-limit-test-{uuid.uuid4().hex}@example.com" for _ in range(2)]
    try:
        for email in emails:
            assert signup(client, email).status_code == 201
    finally:
        with get_session() as session:
            session.query(User).filter(User.email.in_(emails)).delete(synchronize_session=False)


def test_signup_rate_limited_after_max_requests(tmp_path):
    client = TestClient(make_test_app(tmp_path, auth_signup_rate_limit=2))
    emails = [f"signup-limit-test-{uuid.uuid4().hex}@example.com" for _ in range(3)]
    try:
        for email in emails[:2]:
            assert signup(client, email).status_code == 201

        limited = signup(client, emails[2])
        assert limited.status_code == 429
        assert "Retry-After" in limited.headers
    finally:
        with get_session() as session:
            session.query(User).filter(User.email.in_(emails)).delete(synchronize_session=False)


def test_auth_rate_limiting_does_not_affect_unrelated_endpoints(tmp_path, test_email):
    """Exhausting the login rate limit must not leak into try-on, /me, or
    any other route sharing the same app/process -- each limiter instance
    is keyed and scoped to its own endpoint only."""
    client = TestClient(make_test_app(tmp_path, auth_login_ip_rate_limit=1, auth_login_account_rate_limit=1))
    token = signup(client, test_email, password="right-password").json()["access_token"]

    login(client, test_email, "wrong-password")
    assert login(client, test_email, "wrong-password").status_code == 429  # login now rate-limited

    # Unrelated routes, same client/IP, same process: unaffected.
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert _submit(client, headers={"Authorization": f"Bearer {token}"}).status_code == 202


def test_existing_login_behavior_unchanged_with_generous_limits(client, test_email):
    """Sanity check against the default (generous) test-app limits every
    other test in this file already relies on -- proves the new rate
    limiting is additive, not a behavior change, for ordinary traffic."""
    signup(client, test_email, password="right-password")
    ok = login(client, test_email, "right-password")
    assert ok.status_code == 200
    assert ok.json()["access_token"]


# --- Account deletion -----------------------------------------------------


def test_delete_account_requires_auth(client):
    resp = client.request("DELETE", "/api/auth/me", json={"password": "whatever"})
    assert resp.status_code == 401


def test_delete_account_rejects_wrong_password(client, test_email):
    token = signup(client, test_email, password="right-password").json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.request("DELETE", "/api/auth/me", json={"password": "wrong-password"}, headers=headers)
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Incorrect email or password."

    # Account must still exist and be usable — a rejected deletion attempt is a no-op.
    still_there = client.post("/api/auth/login", json={"email": test_email, "password": "right-password"})
    assert still_there.status_code == 200


def test_delete_account_removes_account_jobs_and_result_files(client, tmp_path, test_email):
    token = signup(client, test_email, password="right-password").json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    job_id = _submit(client, headers=headers).json()["job_id"]
    # This job belongs to a signed-in user, so viewing its status requires
    # that same owner's token (see the ownership tests below) -- unlike
    # before this task, when any caller who knew the job_id could poll it.
    assert client.get(f"/api/try-on/{job_id}", headers=headers).json()["status"] == "completed"  # fake provider is instant

    storage = LocalStorageService(str(tmp_path))
    assert storage.get_result_path(job_id) is not None  # result exists before deletion

    resp = client.request("DELETE", "/api/auth/me", json={"password": "right-password"}, headers=headers)
    assert resp.status_code == 204

    # Result image actually removed from storage, not just the DB row.
    assert storage.get_result_path(job_id) is None

    # Job row gone too (cascade) -- the job is no longer reachable at all.
    assert client.get(f"/api/try-on/{job_id}").status_code == 404

    # Account itself is gone: can no longer log in, and the old token is now meaningless.
    login_resp = client.post("/api/auth/login", json={"email": test_email, "password": "right-password"})
    assert login_resp.status_code == 401
    assert client.get("/api/auth/me", headers=headers).status_code == 401

    # Re-signing up with the same email must succeed -- proves the row is
    # truly gone, not just marked deleted (the email column is unique).
    resignup = signup(client, test_email, password="a-new-password")
    assert resignup.status_code == 201


def test_authenticated_job_can_be_saved_by_its_owner(client, test_email):
    token = signup(client, test_email).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    job_id = _submit(client, headers=headers).json()["job_id"]
    # Owned job -- polling its status requires the owner's own token, same
    # reasoning as test_delete_account_removes_account_jobs_and_result_files above.
    status = client.get(f"/api/try-on/{job_id}", headers=headers).json()
    assert status["status"] == "completed"  # fake provider is instant
    assert status["saved"] is False

    save_resp = client.post(f"/api/try-on/{job_id}/save", headers=headers)
    assert save_resp.status_code == 200, save_resp.text
    assert save_resp.json()["saved"] is True

    assert client.get(f"/api/try-on/{job_id}", headers=headers).json()["saved"] is True


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


# --- Job status/result endpoints: per-owner protection -----------------------
#
# Before this, GET /api/try-on/{job_id} and .../result relied solely on the
# job_id being an unguessable uuid4 -- anyone who learned an id (browser
# history, a referrer header, a shared link, a server log) could view that
# job's status and result image, even for a job created by a signed-in user.
# These target TryOnService.get_job_for_viewer, the ownership gate both
# endpoints now share -- see its docstring for the anonymous-vs-owned design
# split these tests exercise.


@pytest.fixture
def other_test_email():
    """A second unique email per test, for ownership tests that need two
    distinct signed-in users -- same shape as test_email above."""
    email = f"test-{uuid.uuid4().hex}@example.com"
    yield email
    with get_session() as session:
        user = session.query(User).filter(User.email == email).first()
        if user is not None:
            session.delete(user)


def test_owner_can_view_their_own_job_status(client, test_email):
    token = signup(client, test_email).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    job_id = _submit(client, headers=headers).json()["job_id"]

    resp = client.get(f"/api/try-on/{job_id}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"  # fake provider is instant


def test_owner_can_view_their_own_result(client, test_email):
    token = signup(client, test_email).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    job_id = _submit(client, headers=headers).json()["job_id"]

    resp = client.get(f"/api/try-on/{job_id}/result", headers=headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"


def test_unauthenticated_caller_cannot_view_a_signed_in_users_job_status_or_result(client, test_email):
    token = signup(client, test_email).json()["access_token"]
    job_id = _submit(client, headers={"Authorization": f"Bearer {token}"}).json()["job_id"]

    status_resp = client.get(f"/api/try-on/{job_id}")  # no Authorization header at all
    assert status_resp.status_code == 403
    assert "your own" in status_resp.json()["detail"]

    result_resp = client.get(f"/api/try-on/{job_id}/result")
    assert result_resp.status_code == 403


def test_different_signed_in_user_cannot_view_someone_elses_job_status_or_result(
    client, test_email, other_test_email
):
    owner_token = signup(client, test_email).json()["access_token"]
    job_id = _submit(client, headers={"Authorization": f"Bearer {owner_token}"}).json()["job_id"]

    other_token = signup(client, other_test_email).json()["access_token"]
    other_headers = {"Authorization": f"Bearer {other_token}"}

    status_resp = client.get(f"/api/try-on/{job_id}", headers=other_headers)
    assert status_resp.status_code == 403
    assert "your own" in status_resp.json()["detail"]

    result_resp = client.get(f"/api/try-on/{job_id}/result", headers=other_headers)
    assert result_resp.status_code == 403


def test_unauthorized_response_never_reveals_whose_job_it_is(client, test_email, other_test_email):
    """The 403 body must be generic enough that it can't be used to
    fingerprint the real owner -- identical message regardless of who's
    asking, and never containing the owner's email."""
    owner_token = signup(client, test_email).json()["access_token"]
    job_id = _submit(client, headers={"Authorization": f"Bearer {owner_token}"}).json()["job_id"]

    other_token = signup(client, other_test_email).json()["access_token"]
    from_other_user = client.get(f"/api/try-on/{job_id}", headers={"Authorization": f"Bearer {other_token}"})
    from_anonymous = client.get(f"/api/try-on/{job_id}")

    assert from_other_user.status_code == from_anonymous.status_code == 403
    assert from_other_user.json()["detail"] == from_anonymous.json()["detail"]
    assert test_email not in from_other_user.text
    assert test_email not in from_anonymous.text


def test_nonexistent_job_returns_404_not_403_even_when_authenticated(client, test_email):
    """404-before-403 (see get_job_for_viewer): a made-up id must behave
    identically to test_tryon_api.py's existing (anonymous)
    test_unknown_job_returns_404, not start returning 403 just because the
    caller happens to be signed in."""
    token = signup(client, test_email).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    assert client.get("/api/try-on/does-not-exist", headers=headers).status_code == 404
    assert client.get("/api/try-on/does-not-exist/result", headers=headers).status_code == 404


def test_anonymous_job_remains_viewable_by_anyone_holding_the_id(client, test_email):
    """No regression for the core no-account flow: a job created with no
    token at all is unchanged by this task -- still viewable by its
    anonymous creator, a different signed-in user, or nobody at all, since
    there is no owner to restrict access to."""
    anon_job_id = _submit(client).json()["job_id"]  # no Authorization header

    assert client.get(f"/api/try-on/{anon_job_id}").status_code == 200  # anonymous viewer

    token = signup(client, test_email).json()["access_token"]
    signed_in_resp = client.get(f"/api/try-on/{anon_job_id}", headers={"Authorization": f"Bearer {token}"})
    assert signed_in_resp.status_code == 200


# --- Idempotency protection for POST /api/try-on ----------------------------
#
# tests/test_tryon_api.py covers the fast, InMemoryJobStore-backed cases
# (same key/same request, conflict, concurrency, invalid/failed requests
# not poisoning the key). These cover what specifically needs a real,
# signed-in user or the real database-level unique constraint
# (jobs.ix_jobs_idempotency_active) instead of the in-memory lock.


def test_different_signed_in_users_can_independently_reuse_the_same_idempotency_key(
    client, test_email, other_test_email
):
    """No global idempotency-key namespace: the scope is "user:<id>" (see
    api/tryon.py's client_key), so two different signed-in users using the
    literal same key string must not interfere with each other."""
    token_a = signup(client, test_email).json()["access_token"]
    token_b = signup(client, other_test_email).json()["access_token"]
    headers = {"Idempotency-Key": "shared-literal-key"}

    resp_a = _submit(client, headers={**headers, "Authorization": f"Bearer {token_a}"})
    resp_b = _submit(client, headers={**headers, "Authorization": f"Bearer {token_b}"})

    assert resp_a.status_code == 202, resp_a.text
    assert resp_b.status_code == 202, resp_b.text
    assert resp_a.json()["job_id"] != resp_b.json()["job_id"]


def test_idempotent_retry_does_not_double_charge_quota(tmp_path, test_email):
    client = TestClient(make_test_app(tmp_path, quota_service=QuotaService()))
    token = signup(client, test_email).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "quota-retry-key"}

    first = _submit(client, headers=headers)
    assert first.status_code == 202, first.text

    before = client.get("/api/usage/me", headers=headers).json()
    assert before["used_today"] == 1

    retry = _submit(client, headers=headers)
    assert retry.status_code == 202, retry.text
    assert retry.json()["job_id"] == first.json()["job_id"]

    after = client.get("/api/usage/me", headers=headers).json()
    assert after["used_today"] == 1  # the retry did not create (or charge for) a second job


def test_concurrent_duplicate_requests_against_real_database_create_only_one_job(client, test_email):
    """Same race as test_tryon_api.py's InMemoryJobStore version, but
    against the real DbJobStore -- proving jobs.ix_jobs_idempotency_active
    (the partial unique index added by this task's migration) genuinely
    enforces the guarantee at the database level, not just the in-process
    lock the fast test relies on."""
    token = signup(client, test_email).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "db-concurrent-key"}
    barrier = threading.Barrier(2)
    results = [None, None]

    def submit(i):
        barrier.wait()
        results[i] = _submit(client, headers=headers)

    threads = [threading.Thread(target=submit, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results[0].status_code == 202, results[0].text
    assert results[1].status_code == 202, results[1].text
    assert results[0].json()["job_id"] == results[1].json()["job_id"]

    with get_session() as session:
        count = session.query(JobRecord).filter(JobRecord.idempotency_key == "db-concurrent-key").count()
    assert count == 1


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
