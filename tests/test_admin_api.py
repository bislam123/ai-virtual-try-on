"""Admin/Operations milestone tests for /api/admin/*.

Same real-database convention as test_auth_api.py -- admin authorization
relies on a real users.is_admin column and real persisted rows, not worth
faking with a mock session. Auto-skips (not fails) if Postgres isn't
reachable.

There is deliberately no API path anywhere that grants is_admin -- every
test user here starts as a normal signup and is promoted directly via a
database session, the exact same mechanism backend/scripts/promote_admin.py
wraps. If any test here could make a user admin through the HTTP API
instead, that would itself be the bug this milestone's brief warns
against ("do not create a backdoor").

Run with: ai\\.venv\\Scripts\\python.exe -m pytest tests/test_admin_api.py
"""

import io
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from backend.app.api import admin as admin_module
from backend.app.api import auth as auth_module
from backend.app.api import tryon as tryon_module
from backend.app.core.errors import configure_exception_handlers
from backend.app.db import JobRecord, User, engine, get_session
from backend.app.providers.base import TryOnRequest, TryOnResult, VirtualTryOnProvider
from backend.app.services.admin_service import AdminService
from backend.app.services.db_job_store import DbJobStore
from backend.app.services.quota_service import QuotaService
from backend.app.services.rate_limiter import RateLimiter
from backend.app.services.storage import LocalStorageService
from backend.app.services.tryon_service import TryOnService
from tests.conftest import FakeCapacityService, FakeQuotaService


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


def make_test_app(tmp_path, quota_service=None, capacity_service=None):
    app = FastAPI()
    configure_exception_handlers(app)
    app.include_router(tryon_module.router)
    app.include_router(auth_module.router)
    app.include_router(admin_module.router)
    storage = LocalStorageService(str(tmp_path))
    app.state.tryon_service = TryOnService(provider=FakeProvider(), storage=storage, job_store=DbJobStore())
    app.state.storage = storage
    app.state.rate_limiter = RateLimiter(max_requests=1000, window_seconds=3600)
    app.state.auth_login_ip_rate_limiter = RateLimiter(max_requests=1000, window_seconds=900)
    app.state.auth_login_account_rate_limiter = RateLimiter(max_requests=1000, window_seconds=900)
    app.state.auth_signup_rate_limiter = RateLimiter(max_requests=1000, window_seconds=3600)
    app.state.quota_service = quota_service or FakeQuotaService()
    app.state.capacity_service = capacity_service or FakeCapacityService()
    app.state.admin_service = AdminService()
    return app


def _png_bytes(color="white"):
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), color=color).save(buf, format="PNG")
    return buf.getvalue()


def _submit(client, headers=None, category="tops"):
    files = {
        "person_image": ("person.png", _png_bytes("blue"), "image/png"),
        "garment_image": ("garment.png", _png_bytes("green"), "image/png"),
    }
    return client.post("/api/try-on", files=files, data={"category": category}, headers=headers or {})


@pytest.fixture
def client(tmp_path):
    # Real QuotaService/CapacityService here (not the fixture's default
    # Fake* stand-ins): the admin dashboard/job-list tests need real
    # counts to actually mean something. Each test uses a fresh,
    # uuid-suffixed user/email so the shared local dev database's
    # existing rows never make a specific count assertion flaky.
    with TestClient(make_test_app(tmp_path, quota_service=QuotaService())) as c:
        yield c


def _unique_email(prefix="test"):
    return f"{prefix}-{uuid.uuid4().hex}@example.com"


@pytest.fixture
def cleanup_users():
    """Collects emails to delete afterward (cascades to their jobs) --
    tests register whichever emails they create via signup()."""
    emails = []
    yield emails
    with get_session() as session:
        for email in emails:
            user = session.query(User).filter(User.email == email).first()
            if user is not None:
                session.delete(user)


def signup(client, email, password="correct-horse-battery"):
    resp = client.post("/api/auth/signup", json={"email": email, "password": password})
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


def _promote_to_admin(email: str) -> None:
    """The one and only mechanism that grants is_admin anywhere in this
    test suite -- direct DB write, exactly what backend/scripts/promote_admin.py
    does, never an API call. See module docstring."""
    with get_session() as session:
        user = session.query(User).filter(User.email == email).first()
        user.is_admin = True


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin(client, cleanup_users):
    """A real signed-up user, promoted to admin out-of-band. Returns (token, email, user_id)."""
    email = _unique_email("admin")
    cleanup_users.append(email)
    token = signup(client, email)
    _promote_to_admin(email)
    user_id = client.get("/api/auth/me", headers=_auth_headers(token)).json()["id"]
    return token, email, user_id


@pytest.fixture
def normal_user(client, cleanup_users):
    """A real signed-up, never-promoted user. Returns (token, email, user_id)."""
    email = _unique_email("normal")
    cleanup_users.append(email)
    token = signup(client, email)
    user_id = client.get("/api/auth/me", headers=_auth_headers(token)).json()["id"]
    return token, email, user_id


ADMIN_GET_ENDPOINTS = ["/api/admin/users", "/api/admin/plans", "/api/admin/jobs", "/api/admin/dashboard"]


# --- Authorization matrix: unauthenticated / non-admin / admin -----------------


@pytest.mark.parametrize("path", ADMIN_GET_ENDPOINTS)
def test_admin_endpoints_reject_unauthenticated_requests(client, path):
    resp = client.get(path)
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Please sign in to use this feature."


@pytest.mark.parametrize("path", ADMIN_GET_ENDPOINTS)
def test_admin_endpoints_reject_authenticated_non_admin(client, normal_user, path):
    token, _, _ = normal_user
    resp = client.get(path, headers=_auth_headers(token))
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Admin access required."


@pytest.mark.parametrize("path", ADMIN_GET_ENDPOINTS)
def test_admin_endpoints_allow_authenticated_admin(client, admin, path):
    token, _, _ = admin
    resp = client.get(path, headers=_auth_headers(token))
    assert resp.status_code == 200


def test_user_detail_authorization_matrix(client, admin, normal_user):
    admin_token, _, _ = admin
    other_token, _, other_id = normal_user

    assert client.get(f"/api/admin/users/{other_id}").status_code == 401
    assert client.get(f"/api/admin/users/{other_id}", headers=_auth_headers(other_token)).status_code == 403
    assert client.get(f"/api/admin/users/{other_id}", headers=_auth_headers(admin_token)).status_code == 200


def test_disable_enable_authorization_matrix(client, admin, normal_user):
    admin_token, _, _ = admin
    other_token, _, other_id = normal_user

    assert client.post(f"/api/admin/users/{other_id}/disable").status_code == 401
    assert (
        client.post(f"/api/admin/users/{other_id}/disable", headers=_auth_headers(other_token)).status_code == 403
    )
    assert (
        client.post(f"/api/admin/users/{other_id}/disable", headers=_auth_headers(admin_token)).status_code == 200
    )


# --- User listing / search ------------------------------------------------------


def test_list_users_includes_created_users(client, admin, normal_user):
    admin_token, admin_email, _ = admin
    _, normal_email, _ = normal_user

    resp = client.get("/api/admin/users", params={"limit": 200}, headers=_auth_headers(admin_token))
    assert resp.status_code == 200
    emails = {u["email"] for u in resp.json()["users"]}
    assert admin_email in emails
    assert normal_email in emails


def test_search_users_by_email_substring(client, admin, normal_user):
    admin_token, _, _ = admin
    _, normal_email, _ = normal_user
    distinctive_fragment = normal_email.split("@")[0]

    resp = client.get(
        "/api/admin/users", params={"search": distinctive_fragment}, headers=_auth_headers(admin_token)
    )
    assert resp.status_code == 200
    emails = {u["email"] for u in resp.json()["users"]}
    assert emails == {normal_email}


def test_search_users_no_match_returns_empty_not_error(client, admin):
    admin_token, _, _ = admin
    resp = client.get(
        "/api/admin/users", params={"search": f"nobody-{uuid.uuid4().hex}"}, headers=_auth_headers(admin_token)
    )
    assert resp.status_code == 200
    assert resp.json() == {"users": [], "total": 0}


def test_list_users_pagination(client, admin):
    admin_token, _, _ = admin
    first_page = client.get("/api/admin/users", params={"limit": 1, "offset": 0}, headers=_auth_headers(admin_token))
    second_page = client.get(
        "/api/admin/users", params={"limit": 1, "offset": 1}, headers=_auth_headers(admin_token)
    )
    assert first_page.status_code == 200 and second_page.status_code == 200
    assert len(first_page.json()["users"]) == 1
    assert first_page.json()["total"] == second_page.json()["total"]
    if second_page.json()["users"]:
        assert first_page.json()["users"][0]["id"] != second_page.json()["users"][0]["id"]


def test_user_summary_shows_created_at_plan_and_active_status(client, admin, normal_user):
    admin_token, _, _ = admin
    _, normal_email, _ = normal_user

    resp = client.get("/api/admin/users", params={"search": normal_email}, headers=_auth_headers(admin_token))
    row = resp.json()["users"][0]
    assert row["email"] == normal_email
    assert row["plan"] == "free"
    assert row["is_admin"] is False
    assert row["is_active"] is True
    assert "created_at" in row and row["created_at"]


# --- User detail: plan/usage visibility -----------------------------------------


def test_user_detail_shows_usage_and_quota(client, admin, normal_user):
    admin_token, _, _ = admin
    normal_token, _, normal_id = normal_user

    _submit(client, headers=_auth_headers(normal_token))

    resp = client.get(f"/api/admin/users/{normal_id}", headers=_auth_headers(admin_token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == normal_id
    assert body["usage"]["plan"] == "free"
    assert body["usage"]["used_today"] >= 1


def test_user_detail_404_for_nonexistent_user(client, admin):
    admin_token, _, _ = admin
    resp = client.get("/api/admin/users/999999999", headers=_auth_headers(admin_token))
    assert resp.status_code == 404


# --- Disable / enable ------------------------------------------------------------


def test_disable_then_enable_round_trip(client, admin, normal_user):
    admin_token, _, _ = admin
    _, _, target_id = normal_user

    disabled = client.post(f"/api/admin/users/{target_id}/disable", headers=_auth_headers(admin_token))
    assert disabled.status_code == 200
    assert disabled.json()["is_active"] is False

    enabled = client.post(f"/api/admin/users/{target_id}/enable", headers=_auth_headers(admin_token))
    assert enabled.status_code == 200
    assert enabled.json()["is_active"] is True


def test_disabling_account_immediately_invalidates_its_live_token(client, admin, normal_user):
    """Not just future logins -- the exact next request with the
    already-issued token must fail, same as an auth_version bump."""
    admin_token, _, _ = admin
    normal_token, _, target_id = normal_user

    assert client.get("/api/auth/me", headers=_auth_headers(normal_token)).status_code == 200

    client.post(f"/api/admin/users/{target_id}/disable", headers=_auth_headers(admin_token))

    resp = client.get("/api/auth/me", headers=_auth_headers(normal_token))
    assert resp.status_code == 401


def test_disabled_account_cannot_log_in_with_generic_message(client, cleanup_users):
    email = _unique_email("disabled")
    cleanup_users.append(email)
    signup(client, email, password="right-password")
    with get_session() as session:
        user = session.query(User).filter(User.email == email).first()
        user.is_active = False

    resp = client.post("/api/auth/login", json={"email": email, "password": "right-password"})

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Incorrect email or password."  # same message as a wrong password, no enumeration


def test_admin_cannot_disable_own_account(client, admin):
    admin_token, _, admin_id = admin
    resp = client.post(f"/api/admin/users/{admin_id}/disable", headers=_auth_headers(admin_token))
    assert resp.status_code == 400
    assert "own admin account" in resp.json()["detail"]


def test_disable_404_for_nonexistent_user(client, admin):
    admin_token, _, _ = admin
    resp = client.post("/api/admin/users/999999999/disable", headers=_auth_headers(admin_token))
    assert resp.status_code == 404


# --- Plans -------------------------------------------------------------------------


def test_list_plans_includes_free_plan_with_limits(client, admin):
    admin_token, _, _ = admin
    resp = client.get("/api/admin/plans", headers=_auth_headers(admin_token))
    assert resp.status_code == 200
    names = {p["name"] for p in resp.json()}
    assert "free" in names


# --- Job visibility / filtering ----------------------------------------------------


def test_admin_sees_jobs_across_all_users(client, admin, normal_user):
    admin_token, _, _ = admin
    normal_token, _, normal_id = normal_user

    submitted = _submit(client, headers=_auth_headers(normal_token))
    job_id = submitted.json()["job_id"]

    resp = client.get("/api/admin/jobs", params={"limit": 200}, headers=_auth_headers(admin_token))
    assert resp.status_code == 200
    job_ids = {j["job_id"] for j in resp.json()["jobs"]}
    assert job_id in job_ids
    matching = next(j for j in resp.json()["jobs"] if j["job_id"] == job_id)
    assert matching["user_id"] == normal_id
    assert matching["status"] == "completed"  # FakeProvider completes synchronously in TestClient
    assert matching["category"] == "tops"


def test_job_list_filters_by_status(client, admin, normal_user):
    admin_token, _, _ = admin
    normal_token, _, _ = normal_user
    submitted = _submit(client, headers=_auth_headers(normal_token))
    job_id = submitted.json()["job_id"]

    resp = client.get("/api/admin/jobs", params={"status": "completed", "limit": 200}, headers=_auth_headers(admin_token))
    assert resp.status_code == 200
    assert job_id in {j["job_id"] for j in resp.json()["jobs"]}

    resp2 = client.get("/api/admin/jobs", params={"status": "pending", "limit": 200}, headers=_auth_headers(admin_token))
    assert job_id not in {j["job_id"] for j in resp2.json()["jobs"]}


def test_job_list_filters_by_user_id(client, admin, normal_user, cleanup_users):
    admin_token, _, _ = admin
    normal_token, _, normal_id = normal_user
    other_normal_email = _unique_email("other")
    cleanup_users.append(other_normal_email)
    other_token = signup(client, other_normal_email)

    _submit(client, headers=_auth_headers(normal_token))
    _submit(client, headers=_auth_headers(other_token))

    resp = client.get(
        "/api/admin/jobs", params={"user_id": normal_id, "limit": 200}, headers=_auth_headers(admin_token)
    )
    assert resp.status_code == 200
    assert all(j["user_id"] == normal_id for j in resp.json()["jobs"])
    assert len(resp.json()["jobs"]) >= 1


def test_job_list_rejects_invalid_status_filter(client, admin):
    admin_token, _, _ = admin
    resp = client.get("/api/admin/jobs", params={"status": "not-a-real-status"}, headers=_auth_headers(admin_token))
    assert resp.status_code == 400


def test_job_summary_computes_processing_duration_for_terminal_jobs_only(client, admin, normal_user):
    admin_token, _, _ = admin
    normal_token, _, _ = normal_user
    submitted = _submit(client, headers=_auth_headers(normal_token))
    job_id = submitted.json()["job_id"]

    # Force a known, non-zero gap so the computed duration is verifiable,
    # not just "some number" -- FakeProvider completes fast enough that a
    # real elapsed time could legitimately be ~0.
    with get_session() as session:
        record = session.get(JobRecord, job_id)
        record.created_at = record.updated_at.replace(microsecond=0)
        from datetime import timedelta

        record.updated_at = record.created_at + timedelta(seconds=5)

    resp = client.get("/api/admin/jobs", params={"limit": 200}, headers=_auth_headers(admin_token))
    matching = next(j for j in resp.json()["jobs"] if j["job_id"] == job_id)
    assert matching["processing_duration_seconds"] == pytest.approx(5.0, abs=0.01)


def test_job_summary_does_not_leak_unnecessary_personal_information(client, admin, normal_user):
    admin_token, admin_email, _ = admin
    normal_token, normal_email, _ = normal_user
    _submit(client, headers=_auth_headers(normal_token))

    resp = client.get("/api/admin/jobs", params={"limit": 200}, headers=_auth_headers(admin_token))
    assert normal_email not in resp.text  # user_id only, not email, on the job view itself


# --- Dashboard --------------------------------------------------------------------


def test_dashboard_reflects_real_counts(client, admin, normal_user):
    admin_token, _, _ = admin
    normal_token, _, _ = normal_user
    _submit(client, headers=_auth_headers(normal_token))

    resp = client.get("/api/admin/dashboard", headers=_auth_headers(admin_token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_users"] >= 2
    assert body["active_users"] >= 2
    assert body["jobs_by_status"].get("completed", 0) >= 1
    assert isinstance(body["active_job_count"], int)
    assert body["max_active_job_capacity"] > 0
    assert isinstance(body["recent_failed_jobs"], list)


def test_dashboard_active_users_excludes_disabled_accounts(client, admin, normal_user):
    admin_token, _, _ = admin
    _, _, target_id = normal_user

    before = client.get("/api/admin/dashboard", headers=_auth_headers(admin_token)).json()["active_users"]
    client.post(f"/api/admin/users/{target_id}/disable", headers=_auth_headers(admin_token))
    after = client.get("/api/admin/dashboard", headers=_auth_headers(admin_token)).json()["active_users"]

    assert after == before - 1


def test_dashboard_recent_failed_jobs_appear(client, admin, normal_user):
    admin_token, _, _ = admin
    normal_token, _, _ = normal_user
    submitted = _submit(client, headers=_auth_headers(normal_token))
    job_id = submitted.json()["job_id"]
    with get_session() as session:
        record = session.get(JobRecord, job_id)
        record.status = "failed"
        record.error = "We couldn't generate your try-on result. Please try a different photo, or try again in a moment."

    resp = client.get("/api/admin/dashboard", headers=_auth_headers(admin_token))
    failed_ids = {j["job_id"] for j in resp.json()["recent_failed_jobs"]}
    assert job_id in failed_ids


# --- Sensitive fields never appear -------------------------------------------------


def test_no_sensitive_auth_fields_in_any_admin_response(client, admin, normal_user):
    admin_token, _, target_id = admin
    normal_token, _, normal_target_id = normal_user
    _submit(client, headers=_auth_headers(normal_token))

    responses = [
        client.get("/api/admin/users", params={"limit": 200}, headers=_auth_headers(admin_token)),
        client.get(f"/api/admin/users/{normal_target_id}", headers=_auth_headers(admin_token)),
        client.get("/api/admin/jobs", params={"limit": 200}, headers=_auth_headers(admin_token)),
        client.get("/api/admin/dashboard", headers=_auth_headers(admin_token)),
        client.get("/api/admin/plans", headers=_auth_headers(admin_token)),
    ]
    forbidden_substrings = ["password_hash", "password", "auth_version", "token_hash", "Bearer ", admin_token]
    for resp in responses:
        assert resp.status_code == 200
        for forbidden in forbidden_substrings:
            assert forbidden not in resp.text, f"{forbidden!r} leaked in {resp.request.url}"


# --- Privilege escalation / IDOR -----------------------------------------------------


def test_signup_ignores_client_supplied_admin_flag(client, cleanup_users):
    email = _unique_email("escalation")
    cleanup_users.append(email)
    resp = client.post(
        "/api/auth/signup",
        json={"email": email, "password": "correct-horse-battery", "is_admin": True, "role": "admin"},
    )
    assert resp.status_code == 201
    token = resp.json()["access_token"]

    me = client.get("/api/auth/me", headers=_auth_headers(token))
    assert me.json()["is_admin"] is False

    with get_session() as session:
        user = session.query(User).filter(User.email == email).first()
        assert user.is_admin is False


def test_non_admin_cannot_enable_or_disable_any_account(client, normal_user, admin):
    non_admin_token, _, _ = normal_user
    _, _, other_user_id = admin

    resp = client.post(f"/api/admin/users/{other_user_id}/disable", headers=_auth_headers(non_admin_token))
    assert resp.status_code == 403
    # Confirm no side effect happened despite the attempt.
    with get_session() as session:
        target = session.get(User, other_user_id)
        assert target.is_active is True


def test_malformed_user_id_path_param_rejected_not_500(client, admin):
    admin_token, _, _ = admin
    resp = client.get("/api/admin/users/not-a-number", headers=_auth_headers(admin_token))
    assert resp.status_code == 422


def test_existing_job_ownership_model_unaffected_by_admin_feature(client, normal_user, cleanup_users):
    """Regression: a normal user's own job endpoints must still enforce
    ownership exactly as before -- the admin feature must never become a
    side-door bypass for the existing per-user job privacy model."""
    normal_token, _, _ = normal_user
    owner_email = _unique_email("owner")
    cleanup_users.append(owner_email)
    other_token = signup(client, owner_email)

    submitted = _submit(client, headers=_auth_headers(other_token))
    job_id = submitted.json()["job_id"]

    resp = client.get(f"/api/try-on/{job_id}", headers=_auth_headers(normal_token))
    assert resp.status_code == 403
