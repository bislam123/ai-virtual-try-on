"""JWT / token-versioning tests (session-hardening milestone, 2026-09-17).

See backend/app/db/models.py's User.auth_version, backend/app/auth/security.py's
AccessTokenClaims/create_access_token/decode_access_token, and
backend/app/auth/dependencies.py's get_current_user_optional for the design:
every access token embeds the auth_version it was issued under, and a
request is authenticated only if that still matches the live User row's
current value. Password reset (tests/test_password_reset.py) and account
deletion (tests/test_auth_api.py) are what actually trigger revocation in
practice; this file covers the token-claims/validation mechanics themselves
-- pure unit tests where no database is needed, and against the real live
endpoint (auto-skipped if Postgres isn't reachable, same convention as
test_auth_api.py) where a real User row is.

Run with: ai\\.venv\\Scripts\\python.exe -m pytest tests/test_token_versioning.py
"""

import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from backend.app.api import auth as auth_module
from backend.app.auth.security import create_access_token, decode_access_token
from backend.app.config import settings
from backend.app.core.errors import configure_exception_handlers
from backend.app.db import User, engine, get_session
from backend.app.services.rate_limiter import RateLimiter


# --- Pure unit tests: no database, no live app -------------------------------
# security.py's decode/create round-trip and its fail-closed policies are
# properties of the JWT payload itself, independent of any User row.


def test_create_access_token_embeds_user_id_and_auth_version():
    token = create_access_token(user_id=42, auth_version=7)

    claims = decode_access_token(token)

    assert claims is not None
    assert claims.user_id == 42
    assert claims.auth_version == 7


def test_decode_access_token_rejects_malformed_token():
    assert decode_access_token("this-is-not-a-jwt-at-all") is None


def test_decode_access_token_rejects_wrong_signature():
    # A structurally valid JWT, correct claims, just signed with a
    # different secret -- must never be trusted.
    payload = {"sub": "1", "exp": datetime.now(timezone.utc) + timedelta(minutes=5), "ver": 1}
    forged = jwt.encode(payload, "a-completely-different-secret", algorithm=settings.jwt_algorithm)

    assert decode_access_token(forged) is None


def test_decode_access_token_rejects_expired_token():
    payload = {
        "sub": "1",
        "exp": datetime.now(timezone.utc) - timedelta(minutes=1),  # already expired
        "ver": 1,
    }
    expired = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)

    assert decode_access_token(expired) is None


def test_decode_access_token_rejects_token_without_version_claim():
    """The fail-closed backward-compatibility policy: a token signed with
    the real secret, otherwise well-formed and unexpired, but missing the
    "ver" claim entirely (what every token looked like before this
    feature existed) is rejected outright -- not treated as "no version to
    check" or auto-trusted. See security.py's decode_access_token
    docstring for the full reasoning."""
    payload = {"sub": "1", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}  # no "ver"
    legacy_shaped = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)

    assert decode_access_token(legacy_shaped) is None


def test_access_token_payload_contains_no_sensitive_data():
    """Only an id and a small integer version -- never a password, email,
    or anything else worth protecting if a token is ever logged or
    intercepted (see security.py's module docstring)."""
    token = create_access_token(user_id=1, auth_version=1)

    # Decoded without signature verification here on purpose -- this test
    # inspects exactly what's inside the payload an attacker (or a stray
    # log line) would see, not whether it's valid.
    raw_payload = jwt.decode(token, options={"verify_signature": False})

    assert set(raw_payload.keys()) == {"sub", "exp", "ver"}


# --- Live endpoint tests: need a real User row and a real database ----------


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


def make_test_app():
    app = FastAPI()
    configure_exception_handlers(app)
    app.include_router(auth_module.router)
    app.state.auth_login_ip_rate_limiter = RateLimiter(max_requests=1000, window_seconds=900)
    app.state.auth_login_account_rate_limiter = RateLimiter(max_requests=1000, window_seconds=900)
    app.state.auth_signup_rate_limiter = RateLimiter(max_requests=1000, window_seconds=3600)
    return app


@pytest.fixture
def client():
    with TestClient(make_test_app()) as c:
        yield c


@pytest.fixture
def test_email():
    email = f"test-{uuid.uuid4().hex}@example.com"
    yield email
    with get_session() as session:
        user = session.query(User).filter(User.email == email).first()
        if user is not None:
            session.delete(user)


def signup(client, email, password="correct-horse-battery"):
    return client.post("/api/auth/signup", json={"email": email, "password": password})


def test_valid_current_token_authenticates(client, test_email):
    token = signup(client, test_email).json()["access_token"]

    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert resp.json()["email"] == test_email


def test_newly_issued_token_contains_the_current_auth_version(client, test_email):
    token = signup(client, test_email).json()["access_token"]

    claims = decode_access_token(token)

    assert claims is not None
    with get_session() as session:
        user = session.query(User).filter(User.email == test_email).first()
        assert claims.auth_version == user.auth_version == 1  # fresh account, never revoked


def test_expired_token_rejected_by_live_endpoint(client, test_email):
    signup(client, test_email)
    with get_session() as session:
        user_id = session.query(User).filter(User.email == test_email).first().id

    expired = jwt.encode(
        {"sub": str(user_id), "exp": datetime.now(timezone.utc) - timedelta(minutes=1), "ver": 1},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )

    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert resp.status_code == 401


def test_malformed_token_rejected_by_live_endpoint(client):
    resp = client.get("/api/auth/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 401


def test_token_without_version_claim_rejected_by_live_endpoint(client, test_email):
    """End-to-end proof of the fail-closed policy against a real user row,
    not just the pure decode function above: a well-formed, unexpired,
    correctly-signed token for a real account is still rejected if it has
    no "ver" claim at all."""
    signup(client, test_email)
    with get_session() as session:
        user_id = session.query(User).filter(User.email == test_email).first().id

    legacy_shaped = jwt.encode(
        {"sub": str(user_id), "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )

    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {legacy_shaped}"})
    assert resp.status_code == 401


def test_token_with_stale_version_rejected(client, test_email):
    """Simulates what a password reset actually does (bump auth_version)
    without going through that whole flow -- test_password_reset.py covers
    the real end-to-end reset-triggers-revocation path; this isolates just
    the version-mismatch check itself."""
    old_token = signup(client, test_email).json()["access_token"]

    with get_session() as session:
        user = session.query(User).filter(User.email == test_email).first()
        user.auth_version += 1  # simulate a revocation event

    stale = client.get("/api/auth/me", headers={"Authorization": f"Bearer {old_token}"})
    assert stale.status_code == 401

    # A freshly issued token (as if the user logged back in) carries the
    # new version and works normally.
    new_token = client.post(
        "/api/auth/login", json={"email": test_email, "password": "correct-horse-battery"}
    ).json()["access_token"]
    fresh = client.get("/api/auth/me", headers={"Authorization": f"Bearer {new_token}"})
    assert fresh.status_code == 200


def test_no_bearer_token_at_all_still_gets_the_original_please_sign_in_message(client):
    """This feature must not change *why* a credential-less request to an
    auth-required endpoint is rejected: still get_current_user_required's
    original "please sign in" 401, not something new-looking that would
    suggest a revoked session rather than simply never having one."""
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Please sign in to use this feature."


def test_me_response_never_exposes_auth_version_or_the_token_itself(client, test_email):
    token = signup(client, test_email).json()["access_token"]

    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert "auth_version" not in resp.json()
    assert token not in resp.text


def test_401_response_never_contains_a_token_value(client, test_email):
    token = signup(client, test_email).json()["access_token"]
    with get_session() as session:
        session.query(User).filter(User.email == test_email).first().auth_version += 1

    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert token not in resp.text
