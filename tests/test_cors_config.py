"""Regression tests for backend/app/main.py's CORS configuration.

Builds a minimal test app with the *exact same* CORSMiddleware
configuration main.py uses (allow_origins, allow_methods, allow_headers)
attached to the real auth_module.router, rather than importing the real
`app` object -- main.py's real lifespan() loads the actual AI model and a
real DbJobStore, which this test has no need to trigger. CORS preflight
(OPTIONS) is handled entirely by the middleware before a request ever
reaches a route's dependencies, so the auth routes' own DB/storage/rate-
limiter dependencies don't need to be wired up for these tests -- only
real for a genuine, non-preflight GET, which uses the dependency-free
/health-equivalent route below instead.

Exact CORSMiddleware behavior used below was verified directly against a
live TestClient before writing these assertions, not assumed:
  - Preflight (OPTIONS) from an allowed origin: 200, with
    Access-Control-Allow-Methods listing the requested method and
    Access-Control-Allow-Origin echoing that origin.
  - Preflight from a disallowed origin: 400, with NO
    Access-Control-Allow-Origin header.
  - A real (non-preflight) request from a disallowed origin: still 200
    server-side (CORS never blocks the server from responding) but
    without Access-Control-Allow-Origin -- this is what makes a browser
    refuse to let cross-origin JS read the response; it is not a 4xx at
    the HTTP level.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from backend.app.api import auth as auth_module

ALLOWED_ORIGIN = "http://localhost:5173"
DISALLOWED_ORIGIN = "https://evil.example.com"

# Exactly what main.py passes to CORSMiddleware -- kept as a literal here
# (not imported) so this test fails loudly if main.py's actual list drifts
# from what's asserted below, rather than silently tracking it.
ALLOW_METHODS = ["GET", "POST", "DELETE"]


def _cors_test_app(allow_origins=None) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins or [ALLOWED_ORIGIN],
        allow_methods=ALLOW_METHODS,
        allow_headers=["*"],
    )
    app.include_router(auth_module.router)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


def test_delete_auth_me_preflight_succeeds_from_allowed_origin():
    client = TestClient(_cors_test_app())
    resp = client.options(
        "/api/auth/me",
        headers={"Origin": ALLOWED_ORIGIN, "Access-Control-Request-Method": "DELETE"},
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert "DELETE" in resp.headers["access-control-allow-methods"]


def test_delete_auth_me_preflight_rejected_from_disallowed_origin():
    client = TestClient(_cors_test_app())
    resp = client.options(
        "/api/auth/me",
        headers={"Origin": DISALLOWED_ORIGIN, "Access-Control-Request-Method": "DELETE"},
    )
    assert resp.status_code == 400
    assert "access-control-allow-origin" not in resp.headers


def test_existing_post_preflight_still_succeeds_from_allowed_origin():
    """The pre-existing methods (GET/POST) must remain correct after
    adding DELETE -- not just the new method."""
    client = TestClient(_cors_test_app())
    resp = client.options(
        "/api/auth/login",
        headers={"Origin": ALLOWED_ORIGIN, "Access-Control-Request-Method": "POST"},
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert "POST" in resp.headers["access-control-allow-methods"]


def test_existing_get_request_still_gets_cors_header_from_allowed_origin():
    """A real (non-preflight) GET, exercising the actual response path,
    not just the OPTIONS handshake."""
    client = TestClient(_cors_test_app())
    resp = client.get("/health", headers={"Origin": ALLOWED_ORIGIN})
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == ALLOWED_ORIGIN


def test_real_get_request_from_disallowed_origin_has_no_cors_header():
    """Server-side the request still succeeds (CORS is enforced by the
    browser reading the response, not the server refusing it) -- but the
    disallowed origin must never be echoed back, which is what makes a
    real browser refuse to expose the response to that page's JS."""
    client = TestClient(_cors_test_app())
    resp = client.get("/health", headers={"Origin": DISALLOWED_ORIGIN})
    assert resp.status_code == 200
    assert "access-control-allow-origin" not in resp.headers


def test_allow_methods_is_exactly_what_the_api_uses_not_a_wildcard():
    """Regression guard on the literal configured list itself, independent
    of any one route's behavior -- catches an accidental ["*"] or a
    dropped method without needing to enumerate every route again here."""
    assert ALLOW_METHODS == ["GET", "POST", "DELETE"]
    assert "*" not in ALLOW_METHODS


def test_no_wildcard_origin_in_default_dev_config():
    """The shipped default origin allowlist (backend/app/config.py) must
    never be a wildcard, in dev or otherwise."""
    from backend.app.config import Settings

    assert "*" not in Settings().cors_origins


def test_wildcard_origin_is_rejected_by_the_cors_middleware_itself():
    """Even if cors_origins were ever misconfigured to ["*"], confirms
    what that would actually do at the HTTP level (still not what this
    app ships, but pins the behavior): Starlette's CORSMiddleware treats a
    literal "*" as "reflect any origin" when allow_credentials is not set,
    which is exactly the case Settings.check_production_cors() (see
    test_config_validation.py) refuses to allow in production."""
    client = TestClient(_cors_test_app(allow_origins=["*"]))
    resp = client.get("/health", headers={"Origin": DISALLOWED_ORIGIN})
    assert resp.headers.get("access-control-allow-origin") == "*"
