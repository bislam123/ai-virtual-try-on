"""Milestone 3 API tests.

Deliberately use a fake VirtualTryOnProvider instead of the real self-hosted
one: the real model takes ~70+ minutes per image on this CPU-only dev
machine (see docs/DEVELOPMENT.md), which is fine for a manual smoke test but
useless for a test suite that should run in seconds. This is exactly what
the provider abstraction (docs/ARCHITECTURE.md) is for.

Run with: ai\\.venv\\Scripts\\python.exe -m pytest
"""

import io

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from backend.app.api import tryon as tryon_module
from backend.app.core.errors import configure_exception_handlers
from backend.app.providers.base import TryOnRequest, TryOnResult, VirtualTryOnProvider
from backend.app.services.job_store import InMemoryJobStore
from backend.app.services.rate_limiter import RateLimiter
from backend.app.services.storage import LocalStorageService
from backend.app.services.tryon_service import TryOnService


class FakeProvider(VirtualTryOnProvider):
    """Returns instantly instead of running the real diffusion pipeline."""

    def generate(self, request: TryOnRequest) -> TryOnResult:
        return TryOnResult(image=Image.new("RGB", (64, 64), color="red"))


class BrokenProvider(VirtualTryOnProvider):
    def generate(self, request: TryOnRequest) -> TryOnResult:
        raise RuntimeError("simulated model failure with a sensitive internal detail")


def make_test_app(tmp_path, provider=None, rate_limit=1000):
    app = FastAPI()
    configure_exception_handlers(app)
    app.include_router(tryon_module.router)
    app.state.tryon_service = TryOnService(
        provider=provider or FakeProvider(),
        storage=LocalStorageService(str(tmp_path)),
        job_store=InMemoryJobStore(),
    )
    app.state.rate_limiter = RateLimiter(max_requests=rate_limit, window_seconds=3600)
    return app


def _png_bytes(color="white"):
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), color=color).save(buf, format="PNG")
    return buf.getvalue()


def _submit(client, **overrides):
    files = {
        "person_image": ("person.png", _png_bytes("blue"), "image/png"),
        "garment_image": ("garment.png", _png_bytes("green"), "image/png"),
    }
    files.update(overrides.pop("files", {}))
    data = {"category": "tops"}
    data.update(overrides)
    return client.post("/api/try-on", files=files, data=data)


def test_create_job_and_poll_to_completion(tmp_path):
    client = TestClient(make_test_app(tmp_path))

    resp = _submit(client)
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "pending"
    job_id = body["job_id"]

    # BackgroundTasks run as part of the same ASGI call under TestClient, so
    # by the time .post() returns above, the (fake, instant) job is done.
    status_resp = client.get(f"/api/try-on/{job_id}")
    assert status_resp.status_code == 200
    status_body = status_resp.json()
    assert status_body["status"] == "completed"
    assert status_body["result_url"] == f"/api/try-on/{job_id}/result"

    result_resp = client.get(f"/api/try-on/{job_id}/result")
    assert result_resp.status_code == 200
    assert result_resp.headers["content-type"] == "image/png"


def test_rejects_non_image_upload(tmp_path):
    client = TestClient(make_test_app(tmp_path))
    resp = _submit(client, files={"person_image": ("person.txt", b"not an image", "text/plain")})
    assert resp.status_code == 400
    assert "valid image" in resp.json()["detail"]


def test_rejects_oversized_upload(tmp_path, monkeypatch):
    from backend.app import config as config_module

    monkeypatch.setattr(config_module.settings, "max_upload_size_mb", 0)
    client = TestClient(make_test_app(tmp_path))
    resp = _submit(client)
    assert resp.status_code == 400
    assert "too large" in resp.json()["detail"]


def test_rejects_unsupported_garment_photo_type(tmp_path):
    client = TestClient(make_test_app(tmp_path))
    resp = _submit(client, garment_photo_type="model")
    assert resp.status_code == 400
    assert "plain clothing" in resp.json()["detail"]


def test_rejects_invalid_category(tmp_path):
    client = TestClient(make_test_app(tmp_path))
    resp = _submit(client, category="shoes")
    assert resp.status_code == 400


def test_unknown_job_returns_404(tmp_path):
    client = TestClient(make_test_app(tmp_path))
    assert client.get("/api/try-on/does-not-exist").status_code == 404
    assert client.get("/api/try-on/does-not-exist/result").status_code == 404


def test_provider_failure_surfaces_as_failed_job_without_leaking_internals(tmp_path):
    client = TestClient(make_test_app(tmp_path, provider=BrokenProvider()))
    job_id = _submit(client).json()["job_id"]

    status_body = client.get(f"/api/try-on/{job_id}").json()
    assert status_body["status"] == "failed"
    assert status_body["error"]
    assert "simulated model failure" not in status_body["error"]  # never leak internals to the user

    assert client.get(f"/api/try-on/{job_id}/result").status_code == 409


def test_rate_limit_blocks_after_max_requests(tmp_path):
    client = TestClient(make_test_app(tmp_path, rate_limit=1))
    assert _submit(client).status_code == 202
    second = _submit(client)
    assert second.status_code == 429
    assert "Retry-After" in second.headers
