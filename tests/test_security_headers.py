"""Tests for backend/app/core/security_headers.py's SecurityHeadersMiddleware.

Builds a minimal test app with the same middleware stack (and ordering) as
the real main.py -- RequestBodySizeLimitMiddleware, SecurityHeadersMiddleware,
then CORSMiddleware added last so it ends up outermost -- rather than
importing the real `app` object, which would trigger main.py's real
lifespan() (loads the actual AI model). Same convention as
test_cors_config.py.

Most routes here need no database or real rate limiters at all (/health,
/fake-image, a 404, and /api/auth/me with no credentials all short-circuit
before touching either) -- the one exception (a real 403 from a wrong
delete-account password) needs a real signed-up user, so that section is
marked with the same real-Postgres/auto-skip convention as test_auth_api.py.
"""

import uuid

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from backend.app.api import auth as auth_module
from backend.app.core.body_size_limit import RequestBodySizeLimitMiddleware
from backend.app.core.errors import CatchUnhandledExceptionsMiddleware, configure_exception_handlers
from backend.app.core.security_headers import SecurityHeadersMiddleware
from backend.app.db import User, engine, get_session
from backend.app.services.rate_limiter import RateLimiter
from backend.app.services.storage import LocalStorageService

ALLOWED_ORIGIN = "http://localhost:5173"

# The full, expected set on an ordinary API response -- checked together so
# a future accidental removal of any one header is caught immediately.
EXPECTED_HEADERS = {
    "x-content-type-options": "nosniff",
    "referrer-policy": "strict-origin-when-cross-origin",
    "x-frame-options": "DENY",
    "content-security-policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
}


def _make_app(tmp_path, max_request_body_bytes: int = 25 * 1024 * 1024) -> FastAPI:
    app = FastAPI()
    # Same order as main.py: CatchUnhandledExceptionsMiddleware innermost, CORS outermost.
    app.add_middleware(CatchUnhandledExceptionsMiddleware)
    app.add_middleware(RequestBodySizeLimitMiddleware, max_bytes=max_request_body_bytes)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[ALLOWED_ORIGIN],
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
    )
    configure_exception_handlers(app)
    app.include_router(auth_module.router)
    app.state.storage = LocalStorageService(str(tmp_path))  # used by DELETE /api/auth/me
    app.state.auth_login_ip_rate_limiter = RateLimiter(max_requests=1000, window_seconds=900)
    app.state.auth_login_account_rate_limiter = RateLimiter(max_requests=1000, window_seconds=900)
    app.state.auth_signup_rate_limiter = RateLimiter(max_requests=1000, window_seconds=3600)
    app.state.auth_forgot_password_ip_rate_limiter = RateLimiter(max_requests=1000, window_seconds=3600)
    app.state.auth_forgot_password_email_rate_limiter = RateLimiter(max_requests=1000, window_seconds=3600)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/fake-image")
    async def fake_image():
        return Response(content=b"fake-png-bytes", media_type="image/png")

    @app.get("/boom")
    async def boom():
        # A genuinely unhandled exception -- not a UserFacingError, not an
        # HTTPException, not a validation error -- the case the catch-all
        # handler in core/errors.py exists for.
        raise ValueError("simulated unhandled bug")

    return app


@pytest.fixture
def client(tmp_path):
    return TestClient(_make_app(tmp_path))


# --- Successful responses -----------------------------------------------------


def test_normal_api_response_has_all_security_headers(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    for name, value in EXPECTED_HEADERS.items():
        assert resp.headers[name] == value
    assert "permissions-policy" in resp.headers  # exact value checked separately below


def test_permissions_policy_restricts_the_expected_capabilities(client):
    resp = client.get("/health")
    policy = resp.headers["permissions-policy"]
    for capability in ["camera=()", "microphone=()", "geolocation=()", "payment=()"]:
        assert capability in policy


def test_image_response_has_security_headers_and_correct_content_type(client):
    resp = client.get("/fake-image")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content == b"fake-png-bytes"
    for name, value in EXPECTED_HEADERS.items():
        assert resp.headers[name] == value


def test_no_hsts_header_is_sent(client):
    """Deliberate, not an oversight -- see security_headers.py's module
    docstring: HSTS is only safe once HTTPS is guaranteed, which this
    dev-only setup (no reverse proxy, no TLS termination) cannot promise."""
    resp = client.get("/health")
    assert "strict-transport-security" not in resp.headers


# --- Error responses ------------------------------------------------------------


def test_404_response_has_security_headers(client):
    resp = client.get("/this-route-does-not-exist")
    assert resp.status_code == 404
    for name, value in EXPECTED_HEADERS.items():
        assert resp.headers[name] == value


def test_401_response_has_security_headers(client):
    """No Authorization header at all -- get_current_user_required's
    "please sign in" 401, reachable with zero app.state/database setup."""
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401
    for name, value in EXPECTED_HEADERS.items():
        assert resp.headers[name] == value


def test_validation_error_response_has_security_headers(client):
    """A FastAPI/pydantic 422 -- a different error path than
    UserFacingError/HTTPException, still expected to carry the headers."""
    resp = client.post("/api/auth/signup", json={"email": "not-an-email", "password": "whatever123"})
    assert resp.status_code == 422
    for name, value in EXPECTED_HEADERS.items():
        assert resp.headers[name] == value


def test_unhandled_exception_response_still_has_security_headers(client):
    """A genuinely unhandled exception (a real bug, not a UserFacingError)
    must not skip SecurityHeadersMiddleware -- see core/errors.py's
    CatchUnhandledExceptionsMiddleware docstring for why this needed its
    own middleware, and never leaks the exception's own message.
    CatchUnhandledExceptionsMiddleware fully handles it within the app, so
    (unlike a truly unhandled exception) it never reaches the ASGI server
    -- the default client fixture's TestClient is fine here."""
    resp = client.get("/boom")

    assert resp.status_code == 500
    assert resp.json() == {"detail": "An unexpected error occurred."}
    assert "simulated unhandled bug" not in resp.text
    for name, value in EXPECTED_HEADERS.items():
        assert resp.headers[name] == value


def test_429_rate_limited_response_has_security_headers(tmp_path):
    app = _make_app(tmp_path)
    app.state.auth_login_ip_rate_limiter = RateLimiter(max_requests=1, window_seconds=900)
    client = TestClient(app)

    client.post("/api/auth/login", json={"email": "someone@example.com", "password": "whatever"})  # consumes the budget
    resp = client.post("/api/auth/login", json={"email": "someone@example.com", "password": "whatever"})

    assert resp.status_code == 429
    for name, value in EXPECTED_HEADERS.items():
        assert resp.headers[name] == value


# --- /docs is exempt from CSP but keeps the other headers -----------------------


def test_docs_page_has_no_csp_but_keeps_other_security_headers(client):
    resp = client.get("/docs")
    assert resp.status_code == 200
    assert "content-security-policy" not in resp.headers
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"


# --- CORS still works alongside the new headers ----------------------------------


def test_cors_header_and_security_headers_both_present_on_the_same_response(client):
    resp = client.get("/health", headers={"Origin": ALLOWED_ORIGIN})
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    for name, value in EXPECTED_HEADERS.items():
        assert resp.headers[name] == value


def test_cors_header_present_on_an_error_response_too(client):
    """The core ordering proof: CORSMiddleware must be outermost so it can
    still decorate a response an inner middleware (or a route's own error
    handling) produced -- confirmed here against a real 401, not just a
    2xx."""
    resp = client.get("/api/auth/me", headers={"Origin": ALLOWED_ORIGIN})
    assert resp.status_code == 401
    assert resp.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert resp.headers["x-content-type-options"] == "nosniff"


def test_cors_still_rejects_disallowed_origins_unaffected_by_new_middleware(client):
    resp = client.get("/health", headers={"Origin": "https://evil.example.com"})
    assert resp.status_code == 200  # server still responds; CORS is enforced by the browser
    assert "access-control-allow-origin" not in resp.headers
    assert resp.headers["x-content-type-options"] == "nosniff"  # security headers unaffected either way


# --- 403 (needs a real signed-up user) -------------------------------------------


def _db_reachable() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False


@pytest.mark.skipif(not _db_reachable(), reason="Postgres isn't reachable at settings.database_url — see docs/DEVELOPMENT.md")
def test_403_response_has_security_headers(client):
    email = f"test-{uuid.uuid4().hex}@example.com"
    try:
        token = client.post("/api/auth/signup", json={"email": email, "password": "right-password"}).json()[
            "access_token"
        ]

        resp = client.request(
            "DELETE", "/api/auth/me", json={"password": "wrong-password"}, headers={"Authorization": f"Bearer {token}"}
        )

        assert resp.status_code == 403
        for name, value in EXPECTED_HEADERS.items():
            assert resp.headers[name] == value
    finally:
        with get_session() as session:
            user = session.query(User).filter(User.email == email).first()
            if user is not None:
                session.delete(user)
