"""Milestone 3 API tests.

Deliberately use a fake VirtualTryOnProvider instead of the real self-hosted
one: the real model takes ~70+ minutes per image on this CPU-only dev
machine (see docs/DEVELOPMENT.md), which is fine for a manual smoke test but
useless for a test suite that should run in seconds. This is exactly what
the provider abstraction (docs/ARCHITECTURE.md) is for.

Run with: ai\\.venv\\Scripts\\python.exe -m pytest
"""

import io
import threading

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
from tests.conftest import FakeQuotaService


class FakeProvider(VirtualTryOnProvider):
    """Returns instantly instead of running the real diffusion pipeline."""

    def generate(self, request: TryOnRequest) -> TryOnResult:
        return TryOnResult(image=Image.new("RGB", (64, 64), color="red"))


class BrokenProvider(VirtualTryOnProvider):
    def generate(self, request: TryOnRequest) -> TryOnResult:
        raise RuntimeError("simulated model failure with a sensitive internal detail")


class CapturingProvider(VirtualTryOnProvider):
    """Records the TryOnRequest it received instead of generating anything
    -- used to prove what actually reaches the provider layer, not just
    what the API layer claims to accept."""

    def __init__(self):
        self.received_request = None

    def generate(self, request: TryOnRequest) -> TryOnResult:
        self.received_request = request
        return TryOnResult(image=Image.new("RGB", (64, 64), color="red"))


def make_test_app(tmp_path, provider=None, rate_limit=1000, quota_service=None):
    app = FastAPI()
    configure_exception_handlers(app)
    app.include_router(tryon_module.router)
    app.state.tryon_service = TryOnService(
        provider=provider or FakeProvider(),
        storage=LocalStorageService(str(tmp_path)),
        job_store=InMemoryJobStore(),
    )
    app.state.rate_limiter = RateLimiter(max_requests=rate_limit, window_seconds=3600)
    app.state.quota_service = quota_service or FakeQuotaService()
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
    headers = overrides.pop("headers", None)
    data = {"category": "tops"}
    data.update(overrides)
    return client.post("/api/try-on", files=files, data=data, headers=headers)


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
    """"flat-lay" and "model" are the only two valid values (see
    VALID_GARMENT_PHOTO_TYPES) -- anything else is still rejected exactly
    as before this milestone's model-worn activation."""
    client = TestClient(make_test_app(tmp_path))
    resp = _submit(client, garment_photo_type="worn-by-a-cat")
    assert resp.status_code == 400
    assert "plain clothing" in resp.json()["detail"]


def test_flat_lay_garment_photo_type_reaches_the_provider(tmp_path):
    """Plumbing proof (see docs/AI_MODEL_LICENSE.md's model-worn
    investigation): garment_photo_type is threaded all the way from the
    HTTP request through TryOnService/JobStore/JobRecord to whatever
    VirtualTryOnProvider.generate() actually receives, via a capturing
    provider rather than just reasoning about the code."""
    provider = CapturingProvider()
    client = TestClient(make_test_app(tmp_path, provider=provider))

    resp = _submit(client)  # default garment_photo_type ("flat-lay")
    assert resp.status_code == 202, resp.text
    assert provider.received_request is not None
    assert provider.received_request.garment_photo_type == "flat-lay"


def test_model_garment_photo_type_is_accepted_and_reaches_the_provider(tmp_path):
    """Model-worn activation: "model" is now a legal value, accepted (202,
    not 400) and threaded through to the provider exactly like "flat-lay"
    -- same plumbing, same capturing-provider proof technique as the test
    above."""
    provider = CapturingProvider()
    client = TestClient(make_test_app(tmp_path, provider=provider))

    resp = _submit(client, garment_photo_type="model")
    assert resp.status_code == 202, resp.text
    assert provider.received_request is not None
    assert provider.received_request.garment_photo_type == "model"


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


def test_quota_exceeded_blocks_submission_with_clear_message(tmp_path):
    client = TestClient(make_test_app(tmp_path, quota_service=FakeQuotaService(exceeded=True)))
    resp = _submit(client)
    assert resp.status_code == 429
    assert "generations for today" in resp.json()["detail"]


def test_no_idempotency_key_creates_separate_jobs_as_before(tmp_path):
    """Old clients that never send Idempotency-Key must keep working
    exactly as before this feature existed -- no accidental deduplication
    just because two submissions happen to look identical."""
    client = TestClient(make_test_app(tmp_path))
    first = _submit(client).json()["job_id"]
    second = _submit(client).json()["job_id"]
    assert first != second


def test_same_idempotency_key_and_same_request_returns_same_job(tmp_path):
    provider = CapturingProvider()
    client = TestClient(make_test_app(tmp_path, provider=provider))
    headers = {"Idempotency-Key": "retry-key-1"}

    first = _submit(client, headers=headers)
    assert first.status_code == 202, first.text
    second = _submit(client, headers=headers)
    assert second.status_code == 202, second.text

    assert first.json()["job_id"] == second.json()["job_id"]
    # Only one AI generation actually ran for the two submissions.
    assert provider.received_request is not None


def test_same_idempotency_key_and_same_request_only_runs_generation_once(tmp_path):
    call_count = {"n": 0}

    class CountingProvider(VirtualTryOnProvider):
        def generate(self, request: TryOnRequest) -> TryOnResult:
            call_count["n"] += 1
            return TryOnResult(image=Image.new("RGB", (64, 64), color="red"))

    client = TestClient(make_test_app(tmp_path, provider=CountingProvider()))
    headers = {"Idempotency-Key": "retry-key-2"}
    _submit(client, headers=headers)
    _submit(client, headers=headers)
    assert call_count["n"] == 1


def test_same_idempotency_key_with_different_category_is_rejected(tmp_path):
    client = TestClient(make_test_app(tmp_path))
    headers = {"Idempotency-Key": "conflict-key-1"}

    first = _submit(client, headers=headers, category="tops")
    assert first.status_code == 202

    second = _submit(client, headers=headers, category="bottoms")
    assert second.status_code == 409
    assert "different try-on request" in second.json()["detail"]


def test_same_idempotency_key_with_different_images_is_rejected(tmp_path):
    client = TestClient(make_test_app(tmp_path))
    headers = {"Idempotency-Key": "conflict-key-2"}

    first = _submit(client, headers=headers)
    assert first.status_code == 202

    second = _submit(client, headers=headers, files={"person_image": ("person.png", _png_bytes("yellow"), "image/png")})
    assert second.status_code == 409
    assert "different try-on request" in second.json()["detail"]


def test_invalid_request_with_idempotency_key_does_not_poison_it(tmp_path):
    """A validation failure must never reserve the key -- a follow-up
    request with the same key but now-valid content is a fresh,
    unrelated submission, not a "conflict"."""
    client = TestClient(make_test_app(tmp_path))
    headers = {"Idempotency-Key": "poison-check-1"}

    bad = _submit(client, headers=headers, category="shoes")
    assert bad.status_code == 400

    good = _submit(client, headers=headers, category="tops")
    assert good.status_code == 202, good.text


def test_failed_job_retry_with_same_key_creates_a_new_job(tmp_path):
    """A job that failed during generation must not permanently occupy its
    idempotency key -- retrying is exactly what the user should be able to
    do, and it's a genuine second attempt, not a duplicate to collapse."""
    client = TestClient(make_test_app(tmp_path, provider=BrokenProvider()))
    headers = {"Idempotency-Key": "retry-after-failure"}

    first = _submit(client, headers=headers)
    assert first.status_code == 202
    first_job_id = first.json()["job_id"]
    assert client.get(f"/api/try-on/{first_job_id}").json()["status"] == "failed"

    second = _submit(client, headers=headers)
    assert second.status_code == 202
    second_job_id = second.json()["job_id"]
    assert second_job_id != first_job_id


def test_concurrent_duplicate_requests_with_same_key_create_only_one_job(tmp_path):
    """The core double-tap/network-retry scenario, with genuine threads --
    not just two sequential calls -- racing on the exact same
    (scope, key). InMemoryJobStore.create_idempotent's lock is what must
    make this safe, not request ordering luck."""
    call_count = {"n": 0}
    lock = threading.Lock()

    class CountingProvider(VirtualTryOnProvider):
        def generate(self, request: TryOnRequest) -> TryOnResult:
            with lock:
                call_count["n"] += 1
            return TryOnResult(image=Image.new("RGB", (64, 64), color="red"))

    client = TestClient(make_test_app(tmp_path, provider=CountingProvider()))
    headers = {"Idempotency-Key": "concurrent-key-1"}
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
    assert call_count["n"] == 1


def test_different_anonymous_clients_can_independently_reuse_the_same_key(tmp_path):
    """No global idempotency-key namespace: two different anonymous
    callers (different source IPs) using the literal same key string must
    not interfere with each other."""
    app = make_test_app(tmp_path)
    client_a = TestClient(app, client=("1.1.1.1", 12345))
    client_b = TestClient(app, client=("2.2.2.2", 12345))
    headers = {"Idempotency-Key": "shared-literal-key"}

    resp_a = _submit(client_a, headers=headers)
    resp_b = _submit(client_b, headers=headers)

    assert resp_a.status_code == 202
    assert resp_b.status_code == 202
    assert resp_a.json()["job_id"] != resp_b.json()["job_id"]


def test_empty_idempotency_key_header_is_rejected(tmp_path):
    client = TestClient(make_test_app(tmp_path))
    resp = _submit(client, headers={"Idempotency-Key": "   "})
    assert resp.status_code == 400


def test_oversized_idempotency_key_header_is_rejected(tmp_path):
    client = TestClient(make_test_app(tmp_path))
    resp = _submit(client, headers={"Idempotency-Key": "x" * 256})
    assert resp.status_code == 400


def test_num_timesteps_clamped_by_plan_cap(tmp_path):
    # A plan cap of 10 must win even if the request asks for more and the
    # global settings.max_num_timesteps (50 by default) would otherwise allow it.
    captured = {}

    class RecordingProvider(VirtualTryOnProvider):
        def generate(self, request: TryOnRequest) -> TryOnResult:
            captured["num_timesteps"] = request.num_timesteps
            return TryOnResult(image=Image.new("RGB", (64, 64), color="blue"))

    client = TestClient(
        make_test_app(
            tmp_path,
            provider=RecordingProvider(),
            quota_service=FakeQuotaService(max_num_timesteps=10),
        )
    )
    _submit(client, num_timesteps=40)
    assert captured["num_timesteps"] == 10
