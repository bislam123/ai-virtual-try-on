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
from backend.app.config import settings
from backend.app.core.errors import CatchUnhandledExceptionsMiddleware, configure_exception_handlers
from backend.app.db import AdminAuditLog, JobRecord, Plan, User, engine, get_session
from backend.app.providers.base import TryOnRequest, TryOnResult, VirtualTryOnProvider
from backend.app.services import admin_service as admin_service_module
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
    # Innermost middleware, same as main.py -- needed so a genuinely
    # unhandled exception (see test_audit_log_write_failure_surfaces_...
    # below) gets the same safe generic-500 handling production requests
    # do, instead of propagating raw through TestClient.
    app.add_middleware(CatchUnhandledExceptionsMiddleware)
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
def test_plan():
    """A throwaway plan row, created and torn down directly via the
    database -- exactly the out-of-band mechanism real plans are managed
    through (see docs/DEVELOPMENT.md's Milestone 11). Deliberately never
    the real seeded 'free'/'premium' plans: test_quota_service.py asserts
    exact numeric values against 'free' (limit_per_day == 5), and mutating
    it here -- even temporarily -- would make those tests flaky depending
    on run order against the same shared local dev database."""
    name = f"test-plan-{uuid.uuid4().hex[:16]}"
    with get_session() as session:
        session.add(Plan(name=name, max_generations_per_day=5, max_generations_per_month=None, max_num_timesteps=30))
    yield name
    with get_session() as session:
        plan = session.get(Plan, name)
        if plan is not None:
            session.delete(plan)


@pytest.fixture
def normal_user(client, cleanup_users):
    """A real signed-up, never-promoted user. Returns (token, email, user_id)."""
    email = _unique_email("normal")
    cleanup_users.append(email)
    token = signup(client, email)
    user_id = client.get("/api/auth/me", headers=_auth_headers(token)).json()["id"]
    return token, email, user_id


ADMIN_GET_ENDPOINTS = [
    "/api/admin/users",
    "/api/admin/plans",
    "/api/admin/jobs",
    "/api/admin/dashboard",
    "/api/admin/audit-log",
]


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
    assert resp.json() == {"users": [], "total": 0, "limit": 50, "offset": 0}


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


def test_list_users_echoes_requested_limit_and_offset(client, admin):
    admin_token, _, _ = admin
    resp = client.get("/api/admin/users", params={"limit": 3, "offset": 2}, headers=_auth_headers(admin_token))
    assert resp.status_code == 200
    assert resp.json()["limit"] == 3
    assert resp.json()["offset"] == 2


def test_list_users_rejects_out_of_range_limit_and_offset(client, admin):
    admin_token, _, _ = admin
    assert client.get("/api/admin/users", params={"limit": 0}, headers=_auth_headers(admin_token)).status_code == 422
    assert (
        client.get("/api/admin/users", params={"limit": 500}, headers=_auth_headers(admin_token)).status_code == 422
    )
    assert (
        client.get("/api/admin/users", params={"offset": -1}, headers=_auth_headers(admin_token)).status_code == 422
    )


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


def _plan_update_body(**overrides):
    body = {"max_generations_per_day": 10, "max_generations_per_month": None, "max_num_timesteps": 20}
    body.update(overrides)
    return body


def test_update_plan_authorization_matrix(client, admin, normal_user, test_plan):
    admin_token, _, _ = admin
    other_token, _, _ = normal_user
    body = _plan_update_body()

    assert client.post(f"/api/admin/plans/{test_plan}", json=body).status_code == 401
    assert (
        client.post(f"/api/admin/plans/{test_plan}", json=body, headers=_auth_headers(other_token)).status_code
        == 403
    )
    resp = client.post(f"/api/admin/plans/{test_plan}", json=body, headers=_auth_headers(admin_token))
    assert resp.status_code == 200


def test_update_plan_changes_are_persisted(client, admin, test_plan):
    admin_token, _, _ = admin
    body = _plan_update_body(max_generations_per_day=42, max_generations_per_month=999, max_num_timesteps=15)

    resp = client.post(f"/api/admin/plans/{test_plan}", json=body, headers=_auth_headers(admin_token))
    assert resp.status_code == 200
    assert resp.json() == {
        "name": test_plan,
        "max_generations_per_day": 42,
        "max_generations_per_month": 999,
        "max_num_timesteps": 15,
    }

    listed = client.get("/api/admin/plans", headers=_auth_headers(admin_token)).json()
    updated = next(p for p in listed if p["name"] == test_plan)
    assert updated["max_generations_per_day"] == 42


def test_update_plan_allows_null_for_unlimited(client, admin, test_plan):
    admin_token, _, _ = admin
    body = _plan_update_body(max_generations_per_day=None, max_generations_per_month=None)

    resp = client.post(f"/api/admin/plans/{test_plan}", json=body, headers=_auth_headers(admin_token))
    assert resp.status_code == 200
    assert resp.json()["max_generations_per_day"] is None


def test_update_plan_404_for_nonexistent_plan(client, admin):
    admin_token, _, _ = admin
    resp = client.post(
        f"/api/admin/plans/no-such-plan-{uuid.uuid4().hex}", json=_plan_update_body(), headers=_auth_headers(admin_token)
    )
    assert resp.status_code == 404


def test_update_plan_rejects_negative_generation_limits(client, admin, test_plan):
    admin_token, _, _ = admin
    resp = client.post(
        f"/api/admin/plans/{test_plan}",
        json=_plan_update_body(max_generations_per_day=-1),
        headers=_auth_headers(admin_token),
    )
    assert resp.status_code == 422


def test_update_plan_rejects_missing_required_field(client, admin, test_plan):
    admin_token, _, _ = admin
    incomplete = {"max_generations_per_day": 5, "max_generations_per_month": None}  # max_num_timesteps omitted
    resp = client.post(
        f"/api/admin/plans/{test_plan}", json=incomplete, headers=_auth_headers(admin_token)
    )
    assert resp.status_code == 422


def test_update_plan_rejects_num_timesteps_outside_configured_range(client, admin, test_plan):
    admin_token, _, _ = admin
    too_high = client.post(
        f"/api/admin/plans/{test_plan}",
        json=_plan_update_body(max_num_timesteps=settings.max_num_timesteps + 1),
        headers=_auth_headers(admin_token),
    )
    assert too_high.status_code == 422

    too_low = client.post(
        f"/api/admin/plans/{test_plan}",
        json=_plan_update_body(max_num_timesteps=settings.min_num_timesteps - 1),
        headers=_auth_headers(admin_token),
    )
    assert too_low.status_code == 422


def test_update_plan_allows_null_num_timesteps_for_unlimited(client, admin, test_plan):
    admin_token, _, _ = admin
    resp = client.post(
        f"/api/admin/plans/{test_plan}",
        json=_plan_update_body(max_num_timesteps=None),
        headers=_auth_headers(admin_token),
    )
    assert resp.status_code == 200
    assert resp.json()["max_num_timesteps"] is None


def test_non_admin_cannot_change_plan_limits_side_effect_free(client, normal_user, test_plan):
    non_admin_token, _, _ = normal_user
    resp = client.post(
        f"/api/admin/plans/{test_plan}", json=_plan_update_body(), headers=_auth_headers(non_admin_token)
    )
    assert resp.status_code == 403
    with get_session() as session:
        plan = session.get(Plan, test_plan)
        assert plan.max_generations_per_day == 5  # unchanged from the fixture's seeded value


# --- Admin audit log --------------------------------------------------------------


def test_disable_and_enable_each_write_an_audit_entry(client, admin, normal_user):
    admin_token, _, admin_id = admin
    _, _, target_id = normal_user

    client.post(f"/api/admin/users/{target_id}/disable", headers=_auth_headers(admin_token))
    client.post(f"/api/admin/users/{target_id}/enable", headers=_auth_headers(admin_token))

    resp = client.get(
        "/api/admin/audit-log", params={"target_type": "user", "target_id": target_id}, headers=_auth_headers(admin_token)
    )
    assert resp.status_code == 200
    entries = resp.json()["entries"]
    actions = [e["action"] for e in entries]
    assert "user_disabled" in actions
    assert "user_enabled" in actions
    for entry in entries:
        assert entry["admin_user_id"] == admin_id
        assert entry["target_type"] == "user"
        assert entry["target_id"] == str(target_id)
        assert "created_at" in entry and entry["created_at"]


def test_plan_update_writes_an_audit_entry_with_before_and_after(client, admin, test_plan):
    admin_token, _, admin_id = admin
    client.post(
        f"/api/admin/plans/{test_plan}",
        json=_plan_update_body(max_generations_per_day=77),
        headers=_auth_headers(admin_token),
    )

    resp = client.get(
        "/api/admin/audit-log", params={"target_type": "plan", "target_id": test_plan}, headers=_auth_headers(admin_token)
    )
    entries = resp.json()["entries"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["action"] == "plan_updated"
    assert entry["admin_user_id"] == admin_id
    assert entry["details"]["before"]["max_generations_per_day"] == 5
    assert entry["details"]["after"]["max_generations_per_day"] == 77


def test_audit_log_entries_ordered_newest_first(client, admin, test_plan):
    admin_token, _, _ = admin
    client.post(f"/api/admin/plans/{test_plan}", json=_plan_update_body(max_generations_per_day=1), headers=_auth_headers(admin_token))
    client.post(f"/api/admin/plans/{test_plan}", json=_plan_update_body(max_generations_per_day=2), headers=_auth_headers(admin_token))

    resp = client.get(
        "/api/admin/audit-log", params={"target_type": "plan", "target_id": test_plan}, headers=_auth_headers(admin_token)
    )
    entries = resp.json()["entries"]
    assert entries[0]["details"]["after"]["max_generations_per_day"] == 2
    assert entries[1]["details"]["after"]["max_generations_per_day"] == 1


def test_audit_log_pagination_metadata(client, admin):
    admin_token, _, _ = admin
    resp = client.get("/api/admin/audit-log", params={"limit": 3, "offset": 1}, headers=_auth_headers(admin_token))
    assert resp.status_code == 200
    assert resp.json()["limit"] == 3
    assert resp.json()["offset"] == 1


def test_non_admin_cannot_read_audit_log(client, normal_user):
    non_admin_token, _, _ = normal_user
    resp = client.get("/api/admin/audit-log", headers=_auth_headers(non_admin_token))
    assert resp.status_code == 403


def test_audit_log_write_failure_surfaces_generic_error_and_rolls_back_the_action(
    client, admin, normal_user, monkeypatch
):
    """If writing the audit row itself fails, the whole get_session()
    transaction (mutation + audit write) rolls back together -- the
    account must NOT end up disabled with no audit trail of it, and the
    error the client sees must be the same safe generic message any other
    unhandled exception gets (CatchUnhandledExceptionsMiddleware), never a
    raw exception detail."""
    admin_token, _, _ = admin
    _, _, target_id = normal_user

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated audit-log write failure with internal detail: /etc/secret-path")

    monkeypatch.setattr(admin_service_module, "record_admin_audit_log", _boom)

    resp = client.post(f"/api/admin/users/{target_id}/disable", headers=_auth_headers(admin_token))

    assert resp.status_code == 500
    assert resp.json() == {"detail": "An unexpected error occurred."}
    assert "secret-path" not in resp.text
    assert "RuntimeError" not in resp.text

    with get_session() as session:
        target = session.get(User, target_id)
        assert target.is_active is True  # the disable itself was rolled back too


def test_audit_entry_survives_deletion_of_the_acting_admin_account(client, admin, normal_user):
    """ON DELETE SET NULL (db/models.py's AdminAuditLog, migration
    e1f5538acd61): deleting the admin who performed an action must not
    delete the historical record that it happened -- only the FK linking
    it to that now-gone account."""
    admin_token, _, admin_id = admin
    _, _, target_id = normal_user

    client.post(f"/api/admin/users/{target_id}/disable", headers=_auth_headers(admin_token))

    del_resp = client.request(
        "DELETE", "/api/auth/me", json={"password": "correct-horse-battery"}, headers=_auth_headers(admin_token)
    )
    assert del_resp.status_code == 204

    with get_session() as session:
        entries = (
            session.query(AdminAuditLog)
            .filter(AdminAuditLog.target_type == "user", AdminAuditLog.target_id == str(target_id))
            .all()
        )
        assert len(entries) >= 1
        assert all(e.admin_user_id is None for e in entries)


def test_no_sensitive_fields_in_audit_log_or_plan_update_responses(client, admin, normal_user, test_plan):
    admin_token, admin_email, target_id = admin
    _, _, other_id = normal_user
    client.post(f"/api/admin/users/{other_id}/disable", headers=_auth_headers(admin_token))
    client.post(f"/api/admin/users/{other_id}/enable", headers=_auth_headers(admin_token))
    client.post(f"/api/admin/plans/{test_plan}", json=_plan_update_body(), headers=_auth_headers(admin_token))

    responses = [
        client.get("/api/admin/audit-log", params={"limit": 200}, headers=_auth_headers(admin_token)),
        client.get("/api/admin/plans", headers=_auth_headers(admin_token)),
    ]
    forbidden_substrings = ["password_hash", "password", "auth_version", "token_hash", "Bearer ", admin_token]
    for resp in responses:
        assert resp.status_code == 200
        for forbidden in forbidden_substrings:
            assert forbidden not in resp.text, f"{forbidden!r} leaked in {resp.request.url}"


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


def test_job_list_pagination(client, admin, normal_user):
    admin_token, _, _ = admin
    normal_token, _, _ = normal_user
    _submit(client, headers=_auth_headers(normal_token))
    _submit(client, headers=_auth_headers(normal_token))

    first_page = client.get("/api/admin/jobs", params={"limit": 1, "offset": 0}, headers=_auth_headers(admin_token))
    second_page = client.get("/api/admin/jobs", params={"limit": 1, "offset": 1}, headers=_auth_headers(admin_token))
    assert first_page.status_code == 200 and second_page.status_code == 200
    assert len(first_page.json()["jobs"]) == 1
    assert first_page.json()["total"] == second_page.json()["total"]
    assert first_page.json()["jobs"][0]["job_id"] != second_page.json()["jobs"][0]["job_id"]


def test_list_jobs_echoes_requested_limit_and_offset(client, admin):
    admin_token, _, _ = admin
    resp = client.get("/api/admin/jobs", params={"limit": 5, "offset": 1}, headers=_auth_headers(admin_token))
    assert resp.status_code == 200
    assert resp.json()["limit"] == 5
    assert resp.json()["offset"] == 1


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
