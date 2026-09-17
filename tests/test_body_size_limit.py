"""Tests for backend/app/core/body_size_limit.py's RequestBodySizeLimitMiddleware.

Two layers of tests:
1. Through a real TestClient/HTTP request (the Content-Length fast path --
   what virtually every real client, including a browser, actually
   triggers) against test apps mirroring tryon_module/extraction_module's
   own make_test_app conventions from test_tryon_api.py/test_extraction_api.py.
2. Direct, low-level ASGI unit tests driving the middleware's __call__ by
   hand with a synthetic receive() (no Content-Length header, multiple
   small chunks) -- the only way to exercise and prove the streaming
   byte-counter path, since TestClient/httpx always sends a real
   Content-Length for an in-memory body.
"""

import io

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from backend.app.api import extraction as extraction_module
from backend.app.api import tryon as tryon_module
from backend.app.core.body_size_limit import TOO_LARGE_DETAIL, RequestBodySizeLimitMiddleware
from backend.app.core.errors import configure_exception_handlers
from backend.app.providers.base import TryOnRequest, TryOnResult, VirtualTryOnProvider
from backend.app.services.job_store import InMemoryJobStore
from backend.app.services.rate_limiter import RateLimiter
from backend.app.services.storage import LocalStorageService
from backend.app.services.tryon_service import TryOnService
from tests.conftest import FakeCapacityService, FakeQuotaService


class CapturingProvider(VirtualTryOnProvider):
    """Records whether/how many times generate() was actually invoked --
    the direct proof that an oversized request never reaches the
    expensive part of the pipeline, not just that it returns 413."""

    def __init__(self):
        self.calls = 0

    def generate(self, request: TryOnRequest) -> TryOnResult:
        self.calls += 1
        return TryOnResult(image=Image.new("RGB", (64, 64), color="red"))


class SpyQuotaService(FakeQuotaService):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def get_status(self, *, user_id, client_ip, plan_name):
        self.calls += 1
        return super().get_status(user_id=user_id, client_ip=client_ip, plan_name=plan_name)


def make_tryon_test_app(tmp_path, max_request_body_bytes, provider=None, quota_service=None):
    app = FastAPI()
    app.add_middleware(RequestBodySizeLimitMiddleware, max_bytes=max_request_body_bytes)
    configure_exception_handlers(app)
    app.include_router(tryon_module.router)
    job_store = InMemoryJobStore()
    app.state.tryon_service = TryOnService(
        provider=provider or CapturingProvider(),
        storage=LocalStorageService(str(tmp_path)),
        job_store=job_store,
    )
    app.state.rate_limiter = RateLimiter(max_requests=1000, window_seconds=3600)
    app.state.quota_service = quota_service or SpyQuotaService()
    app.state.capacity_service = FakeCapacityService()
    return app, job_store


def make_extraction_test_app(max_request_body_bytes):
    app = FastAPI()
    app.add_middleware(RequestBodySizeLimitMiddleware, max_bytes=max_request_body_bytes)
    configure_exception_handlers(app)
    app.include_router(extraction_module.router)
    app.state.extraction_rate_limiter = RateLimiter(max_requests=1000, window_seconds=600)
    return app


def _png_bytes(size=(32, 32), color="white"):
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="PNG")
    return buf.getvalue()


def _submit(client, person=None, garment=None):
    files = {
        "person_image": ("person.png", person or _png_bytes(color="blue"), "image/png"),
        "garment_image": ("garment.png", garment or _png_bytes(color="green"), "image/png"),
    }
    return client.post("/api/try-on", files=files, data={"category": "tops"})


# --- Content-Length fast path, through a real request ----------------------------


def test_request_under_the_limit_reaches_normal_processing(tmp_path):
    app, job_store = make_tryon_test_app(tmp_path, max_request_body_bytes=10 * 1024 * 1024)
    client = TestClient(app)

    resp = _submit(client)

    assert resp.status_code == 202, resp.text
    assert len(job_store._jobs) == 1
    assert app.state.tryon_service.provider.calls == 1


def test_request_over_the_limit_returns_413_with_safe_generic_message(tmp_path):
    app, _ = make_tryon_test_app(tmp_path, max_request_body_bytes=400)  # tiny -- any real photo exceeds this
    client = TestClient(app)

    resp = _submit(client)

    assert resp.status_code == 413
    assert resp.json() == {"detail": TOO_LARGE_DETAIL}
    # No stack trace, no filesystem path, no internal exception detail.
    assert "Traceback" not in resp.text
    assert "backend" not in resp.text
    assert ".py" not in resp.text


def test_oversized_request_does_not_reach_expensive_processing(tmp_path):
    app, job_store = make_tryon_test_app(tmp_path, max_request_body_bytes=400)
    client = TestClient(app)

    resp = _submit(client)

    assert resp.status_code == 413
    assert app.state.tryon_service.provider.calls == 0  # generate() never invoked


def test_oversized_request_does_not_create_a_job(tmp_path):
    app, job_store = make_tryon_test_app(tmp_path, max_request_body_bytes=400)
    client = TestClient(app)

    resp = _submit(client)

    assert resp.status_code == 413
    assert job_store._jobs == {}


def test_oversized_request_does_not_consume_quota(tmp_path):
    spy_quota = SpyQuotaService()
    app, _ = make_tryon_test_app(tmp_path, max_request_body_bytes=400, quota_service=spy_quota)
    client = TestClient(app)

    resp = _submit(client)

    assert resp.status_code == 413
    assert spy_quota.calls == 0  # never even checked, let alone consumed


def test_oversized_request_does_not_leave_temporary_files(tmp_path):
    app, _ = make_tryon_test_app(tmp_path, max_request_body_bytes=400)
    client = TestClient(app)

    resp = _submit(client)

    assert resp.status_code == 413
    # LocalStorageService.__init__ eagerly creates tmp/ and results/
    # themselves, so the storage root isn't empty -- but no job_id
    # subdirectory should exist inside either, since no job was ever
    # created to write one under.
    assert list((tmp_path / "tmp").iterdir()) == []
    assert list((tmp_path / "results").iterdir()) == []


def test_oversized_request_rejected_on_extraction_endpoint_too():
    """Not just POST /api/try-on -- the middleware is application-wide, so
    a client can't bypass it by hitting a different upload route."""
    app = make_extraction_test_app(max_request_body_bytes=400)
    client = TestClient(app)

    resp = client.post(
        "/api/extract-product-image", files={"image": ("image.png", _png_bytes((800, 800)), "image/png")}
    )

    assert resp.status_code == 413
    assert resp.json() == {"detail": TOO_LARGE_DETAIL}


def test_oversized_json_body_rejected_too(tmp_path):
    """Not just multipart -- an oversized *JSON* request body (any POST
    endpoint accepting a pydantic body, e.g. auth) is caught by the same
    middleware, proving this isn't an upload-specific special case."""
    app = FastAPI()
    app.add_middleware(RequestBodySizeLimitMiddleware, max_bytes=200)
    configure_exception_handlers(app)
    from backend.app.api import auth as auth_module

    app.include_router(auth_module.router)
    app.state.auth_login_ip_rate_limiter = RateLimiter(max_requests=1000, window_seconds=900)
    app.state.auth_login_account_rate_limiter = RateLimiter(max_requests=1000, window_seconds=900)
    client = TestClient(app)

    oversized_email = "a" * 500 + "@example.com"
    resp = client.post("/api/auth/login", json={"email": oversized_email, "password": "whatever"})

    assert resp.status_code == 413
    assert resp.json() == {"detail": TOO_LARGE_DETAIL}


# --- Boundary: exactly at the limit vs. one byte over -----------------------------


def _login_app(max_bytes):
    from backend.app.api import auth as auth_module

    app = FastAPI()
    app.add_middleware(RequestBodySizeLimitMiddleware, max_bytes=max_bytes)
    configure_exception_handlers(app)
    app.include_router(auth_module.router)
    app.state.auth_login_ip_rate_limiter = RateLimiter(max_requests=1000, window_seconds=900)
    app.state.auth_login_account_rate_limiter = RateLimiter(max_requests=1000, window_seconds=900)
    return app


def _login_request(client, body: bytes):
    # Explicit `content=` bytes, not `json=` -- gives this test exact,
    # predictable control over Content-Length rather than guessing how
    # httpx would serialize a dict, which is what the boundary assertions
    # below depend on being precise.
    return client.post("/api/auth/login", content=body, headers={"Content-Type": "application/json"})


def test_request_exactly_at_the_limit_is_not_rejected_for_size():
    body = b'{"email": "person@example.com", "password": "whatever"}'
    client = TestClient(_login_app(max_bytes=len(body)))

    resp = _login_request(client, body)

    # Rejected (if at all) for being wrong credentials, never for size --
    # proves the boundary is inclusive (declared == max_bytes passes).
    assert resp.status_code != 413


def test_request_one_byte_over_the_limit_is_rejected():
    body = b'{"email": "person@example.com", "password": "whatever"}'
    client = TestClient(_login_app(max_bytes=len(body) - 1))

    resp = _login_request(client, body)

    assert resp.status_code == 413


# --- Malformed input around the middleware itself ----------------------------------


def test_malformed_content_length_header_does_not_crash_the_middleware():
    """A garbage (non-numeric) Content-Length must never crash this
    middleware with a 500 -- it's simply not usable for the fast-path
    check, so the request falls through to normal handling (still subject
    to the streaming counter as the real enforcement)."""
    app = _login_app(max_bytes=10 * 1024 * 1024)
    client = TestClient(app)

    resp = client.post(
        "/api/auth/login",
        content=b'{"email": "person@example.com", "password": "whatever"}',
        headers={"Content-Type": "application/json", "Content-Length": "not-a-number"},
    )

    assert resp.status_code != 500
    assert resp.status_code != 413


def test_non_multipart_garbage_under_the_limit_still_gets_normal_validation(tmp_path):
    """Below the size limit, this middleware must be fully transparent --
    a malformed try-on submission still gets the EXISTING, specific
    validation error, not a 413 or an unexpected server error."""
    app, _ = make_tryon_test_app(tmp_path, max_request_body_bytes=10 * 1024 * 1024)
    client = TestClient(app)

    resp = _submit(client, person=b"not a real image")

    assert resp.status_code == 400
    assert "valid image" in resp.json()["detail"]


# --- Low-level ASGI unit tests: streaming path, no Content-Length -----------------
#
# TestClient/httpx always sends a real Content-Length for an in-memory
# body, so the tests above can only ever exercise the fast-path check.
# These drive RequestBodySizeLimitMiddleware.__call__ directly with a
# hand-written receive() (no content-length header, multiple small
# chunks) -- the only way to prove the streaming byte-counter itself:
# that it counts incrementally, and that it never forwards the chunk that
# would cross the limit to whatever's downstream.

import json

import anyio
from starlette.requests import ClientDisconnect


class _RecordingApp:
    """A spy inner ASGI app standing in for the router: records every
    message its own receive() call returns, and sends back a trivial 200
    once the body is exhausted -- lets a test assert exactly what (if
    anything) reached "downstream" of the middleware.

    Raises ClientDisconnect on an `http.disconnect` message rather than
    just returning quietly -- matching real Starlette request-body
    consumption (see starlette.requests.Request.stream()), which is
    exactly the behavior RequestBodySizeLimitMiddleware's own except
    ClientDisconnect clause is written to catch. A spy that merely
    returned on disconnect (an earlier draft of this test double) would
    prove nothing about that catch clause at all.
    """

    def __init__(self):
        self.received_messages = []

    async def __call__(self, scope, receive, send):
        while True:
            message = await receive()
            self.received_messages.append(message)
            if message["type"] == "http.disconnect":
                raise ClientDisconnect()
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})


def _make_receive(chunks: list[bytes]):
    """Yields one `http.request` message per chunk (more_body True on all
    but the last), then `http.disconnect` if ever called again."""
    remaining = list(chunks)

    async def receive():
        if not remaining:
            return {"type": "http.disconnect"}
        body = remaining.pop(0)
        return {"type": "http.request", "body": body, "more_body": bool(remaining)}

    return receive


async def _run(app_spy, chunks, max_bytes):
    middleware = RequestBodySizeLimitMiddleware(app_spy, max_bytes=max_bytes)
    scope = {"type": "http", "method": "POST", "path": "/whatever", "headers": []}  # no content-length header
    sent = []

    async def send(message):
        sent.append(message)

    await middleware(scope, _make_receive(chunks), send)
    return sent


def test_streaming_path_allows_chunks_within_the_limit():
    app_spy = _RecordingApp()
    chunks = [b"a" * 10, b"b" * 10, b"c" * 10]  # 30 bytes total, limit is 100

    sent = anyio.run(_run, app_spy, chunks, 100)

    assert sent[0]["status"] == 200
    assert [m["body"] for m in app_spy.received_messages if m["type"] == "http.request"] == chunks


def test_streaming_path_aborts_before_forwarding_the_chunk_that_crosses_the_limit():
    app_spy = _RecordingApp()
    # 10 + 10 = 20 (within the 25-byte limit); the third chunk would bring
    # the running total to 30, crossing it -- that's the one that must
    # never reach the inner app.
    chunks = [b"a" * 10, b"b" * 10, b"c" * 10]

    sent = anyio.run(_run, app_spy, chunks, 25)

    # The inner app never saw the third (over-limit) chunk -- only the
    # first two, then a disconnect standing in for it.
    request_bodies = [m["body"] for m in app_spy.received_messages if m["type"] == "http.request"]
    assert request_bodies == [b"a" * 10, b"b" * 10]
    assert app_spy.received_messages[-1]["type"] == "http.disconnect"

    assert sent[0]["status"] == 413
    body = b"".join(m["body"] for m in sent if m["type"] == "http.response.body")
    assert json.loads(body) == {"detail": TOO_LARGE_DETAIL}


def test_genuine_client_disconnect_unrelated_to_the_limit_is_not_turned_into_a_413():
    """The real client hanging up mid-request (nothing to do with our
    limit -- `limit_exceeded` is never set) must propagate as-is, not be
    swallowed into a fabricated 413 there's no one left to receive."""

    async def receive():
        return {"type": "http.disconnect"}

    async def app_spy(scope, receive, send):
        message = await receive()
        assert message["type"] == "http.disconnect"
        raise ClientDisconnect()

    async def run():
        middleware = RequestBodySizeLimitMiddleware(app_spy, max_bytes=1000)
        scope = {"type": "http", "method": "POST", "path": "/whatever", "headers": []}
        sent = []

        async def send(message):
            sent.append(message)

        raised = False
        try:
            await middleware(scope, receive, send)
        except ClientDisconnect:
            raised = True
        return sent, raised

    sent, raised = anyio.run(run)

    assert raised is True
    assert sent == []
