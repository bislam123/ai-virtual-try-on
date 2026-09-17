"""Service-level tests for TryOnService's job lifecycle: run_job's atomic
claim, its refined failure-message handling, and cancel_job.

Deliberately calls TryOnService methods directly (start_job/run_job/
cancel_job) rather than going through the HTTP API + TestClient: Starlette's
TestClient runs a BackgroundTask synchronously as part of the same call
that returns the response (see test_tryon_api.py's own comment on this),
so by the time a submit() call returns, an instant fake provider's job has
already finished -- there's no way to observe a genuinely still-PENDING job
through that path. Calling start_job() without scheduling run_job()
(exactly what the API route does as two separate steps) is what makes the
PENDING window observable and controllable here.

Fast, no database: InMemoryJobStore + LocalStorageService(tmp_path), same
convention as test_tryon_api.py.
"""

import threading

from PIL import Image

from backend.app.core.errors import UserFacingError
from backend.app.providers.base import (
    InferenceTimeoutError,
    ProviderBusyError,
    TryOnRequest,
    TryOnResult,
    VirtualTryOnProvider,
)
from backend.app.services.job_store import InMemoryJobStore, JobStatus
from backend.app.services.storage import LocalStorageService
from backend.app.services.tryon_service import GENERIC_FAILURE_MESSAGE, TryOnService

import pytest


class FakeProvider(VirtualTryOnProvider):
    def __init__(self):
        self.calls = 0

    def generate(self, request: TryOnRequest) -> TryOnResult:
        self.calls += 1
        return TryOnResult(image=Image.new("RGB", (64, 64), color="red"))


class BrokenProvider(VirtualTryOnProvider):
    def generate(self, request: TryOnRequest) -> TryOnResult:
        raise RuntimeError("simulated model failure with a sensitive internal detail")


class BusyProvider(VirtualTryOnProvider):
    def __init__(self, message: str):
        self.message = message

    def generate(self, request: TryOnRequest) -> TryOnResult:
        raise ProviderBusyError(self.message)


class TimeoutProvider(VirtualTryOnProvider):
    def __init__(self, message: str):
        self.message = message

    def generate(self, request: TryOnRequest) -> TryOnResult:
        raise InferenceTimeoutError(self.message)


class GatedProvider(VirtualTryOnProvider):
    """Blocks in generate() until release_gate is set -- lets a test
    control exactly when a job would move from PROCESSING to COMPLETED,
    deterministically, for the race tests below."""

    def __init__(self, release_gate: threading.Event, started: threading.Event):
        self.release_gate = release_gate
        self.started = started
        self.calls = 0

    def generate(self, request: TryOnRequest) -> TryOnResult:
        self.calls += 1
        self.started.set()
        self.release_gate.wait(timeout=5)
        return TryOnResult(image=Image.new("RGB", (64, 64), color="red"))


def _make_service(tmp_path, provider) -> TryOnService:
    return TryOnService(provider=provider, storage=LocalStorageService(str(tmp_path)), job_store=InMemoryJobStore())


def _tiny_image() -> Image.Image:
    return Image.new("RGB", (4, 4), color="blue")


def _start(service: TryOnService, user_id=None):
    return service.start_job(
        person_image=_tiny_image(),
        garment_image=_tiny_image(),
        category="tops",
        num_timesteps=4,
        guidance_scale=1.5,
        seed=42,
        user_id=user_id,
    )


# --- run_job: normal lifecycle -----------------------------------------------


def test_run_job_transitions_pending_through_processing_to_completed(tmp_path):
    provider = FakeProvider()
    service = _make_service(tmp_path, provider)
    job = _start(service)
    assert job.status == JobStatus.PENDING

    service.run_job(job.id)

    final = service.job_store.get(job.id)
    assert final.status == JobStatus.COMPLETED
    assert provider.calls == 1


def test_run_job_cleans_up_temp_files_on_success(tmp_path):
    service = _make_service(tmp_path, FakeProvider())
    job = _start(service)

    service.run_job(job.id)

    assert service.storage.load_temp_upload(job.id, "person.png") is None
    assert service.storage.load_temp_upload(job.id, "garment.png") is None


# --- run_job: failure message handling ---------------------------------------


def test_run_job_uses_generic_message_for_an_arbitrary_provider_exception(tmp_path):
    service = _make_service(tmp_path, BrokenProvider())
    job = _start(service)

    service.run_job(job.id)

    final = service.job_store.get(job.id)
    assert final.status == JobStatus.FAILED
    assert final.error == GENERIC_FAILURE_MESSAGE
    assert "simulated model failure" not in final.error  # never leak internals


def test_run_job_surfaces_provider_busy_errors_own_safe_message(tmp_path):
    """A ProviderBusyError's own message (already validated safe -- see
    providers/base.py) is more specific and useful than the fully generic
    failure message, so it's surfaced directly instead of being
    flattened."""
    busy_message = "The AI model is still busy with a previous request after waiting 5s. Please try again shortly."
    service = _make_service(tmp_path, BusyProvider(busy_message))
    job = _start(service)

    service.run_job(job.id)

    final = service.job_store.get(job.id)
    assert final.status == JobStatus.FAILED
    assert final.error == busy_message


def test_run_job_surfaces_inference_timeout_errors_own_safe_message(tmp_path):
    timeout_message = "Generation exceeded the 3600s timeout."
    service = _make_service(tmp_path, TimeoutProvider(timeout_message))
    job = _start(service)

    service.run_job(job.id)

    final = service.job_store.get(job.id)
    assert final.status == JobStatus.FAILED
    assert final.error == timeout_message


def test_run_job_cleans_up_temp_files_on_failure(tmp_path):
    service = _make_service(tmp_path, BrokenProvider())
    job = _start(service)

    service.run_job(job.id)

    assert service.storage.load_temp_upload(job.id, "person.png") is None
    assert service.storage.load_temp_upload(job.id, "garment.png") is None


def test_run_job_for_unknown_job_id_does_not_raise(tmp_path):
    service = _make_service(tmp_path, FakeProvider())
    service.run_job("does-not-exist")  # must not raise -- see run_job's own early-return


# --- cancel_job: ownership -----------------------------------------------------


def test_cancel_nonexistent_job_returns_404(tmp_path):
    service = _make_service(tmp_path, FakeProvider())
    with pytest.raises(UserFacingError) as exc_info:
        service.cancel_job("does-not-exist", viewer_user_id=None)
    assert exc_info.value.status_code == 404


def test_cancel_someone_elses_job_returns_403(tmp_path):
    service = _make_service(tmp_path, FakeProvider())
    job = _start(service, user_id=1)

    with pytest.raises(UserFacingError) as exc_info:
        service.cancel_job(job.id, viewer_user_id=2)
    assert exc_info.value.status_code == 403

    # Refused, not silently applied -- the job is untouched.
    assert service.job_store.get(job.id).status == JobStatus.PENDING


def test_cancel_anonymous_job_by_any_viewer_succeeds(tmp_path):
    """Same anonymous-open-access policy as get_job_for_viewer/save_job --
    reused, not reinvented (see cancel_job's own docstring)."""
    service = _make_service(tmp_path, FakeProvider())
    job = _start(service, user_id=None)

    result = service.cancel_job(job.id, viewer_user_id=999)

    assert result.status == JobStatus.CANCELLED


def test_cancel_own_job_succeeds(tmp_path):
    service = _make_service(tmp_path, FakeProvider())
    job = _start(service, user_id=1)

    result = service.cancel_job(job.id, viewer_user_id=1)

    assert result.status == JobStatus.CANCELLED


# --- cancel_job: state transitions and refusal for non-pending jobs -----------


def test_cancel_pending_job_succeeds_and_cleans_up_temp_files(tmp_path):
    service = _make_service(tmp_path, FakeProvider())
    job = _start(service)
    assert service.storage.load_temp_upload(job.id, "person.png") is not None  # sanity: files exist before cancel

    result = service.cancel_job(job.id, viewer_user_id=None)

    assert result.status == JobStatus.CANCELLED
    assert service.job_store.get(job.id).status == JobStatus.CANCELLED
    assert service.storage.load_temp_upload(job.id, "person.png") is None
    assert service.storage.load_temp_upload(job.id, "garment.png") is None


def test_cancel_already_completed_job_is_refused_with_a_clear_message(tmp_path):
    provider = FakeProvider()
    service = _make_service(tmp_path, provider)
    job = _start(service)
    service.run_job(job.id)  # completes instantly (FakeProvider)
    assert service.job_store.get(job.id).status == JobStatus.COMPLETED

    with pytest.raises(UserFacingError) as exc_info:
        service.cancel_job(job.id, viewer_user_id=None)

    assert exc_info.value.status_code == 409
    assert "already be in progress" in exc_info.value.message or "no longer be cancelled" in exc_info.value.message
    # The completed job/result must be entirely unaffected by the refused
    # cancel attempt.
    assert service.job_store.get(job.id).status == JobStatus.COMPLETED


def test_cancel_already_failed_job_is_refused(tmp_path):
    service = _make_service(tmp_path, BrokenProvider())
    job = _start(service)
    service.run_job(job.id)
    assert service.job_store.get(job.id).status == JobStatus.FAILED

    with pytest.raises(UserFacingError) as exc_info:
        service.cancel_job(job.id, viewer_user_id=None)
    assert exc_info.value.status_code == 409


def test_cancel_twice_the_second_attempt_is_refused(tmp_path):
    service = _make_service(tmp_path, FakeProvider())
    job = _start(service)

    first = service.cancel_job(job.id, viewer_user_id=None)
    assert first.status == JobStatus.CANCELLED

    with pytest.raises(UserFacingError) as exc_info:
        service.cancel_job(job.id, viewer_user_id=None)
    assert exc_info.value.status_code == 409


# --- The core safety property: no result after cancellation -------------------


def test_run_job_after_cancellation_never_calls_the_provider_or_publishes_a_result(tmp_path):
    """Simulates the race where cancel_job wins before run_job's
    background task gets a chance to claim the job (see run_job's own
    docstring for the atomic transition this depends on)."""
    provider = FakeProvider()
    service = _make_service(tmp_path, provider)
    job = _start(service)

    cancelled = service.cancel_job(job.id, viewer_user_id=None)
    assert cancelled.status == JobStatus.CANCELLED

    service.run_job(job.id)  # as if the background task now runs anyway

    assert provider.calls == 0  # never reached the AI model
    final = service.job_store.get(job.id)
    assert final.status == JobStatus.CANCELLED  # not silently overwritten
    assert service.get_result_path(job.id) is None  # no result was ever published


def test_cancel_race_with_run_job_is_mutually_exclusive_never_both(tmp_path):
    """Real concurrent threads racing run_job's claim against cancel_job's
    claim for the exact same job -- the atomic try_transition_status
    (InMemoryJobStore's own lock) must make these mutually exclusive: the
    job either completes normally (provider ran exactly once, cancel was
    refused) or is cancelled (provider never ran) -- never a mixed or
    corrupted outcome. Run several times to actually exercise both orderings,
    not just whichever one happens to win by luck once."""
    for _ in range(20):
        provider = FakeProvider()
        service = _make_service(tmp_path, provider)
        job = _start(service)

        barrier = threading.Barrier(2)
        cancel_error = []

        def do_run():
            barrier.wait()
            service.run_job(job.id)

        def do_cancel():
            barrier.wait()
            try:
                service.cancel_job(job.id, viewer_user_id=None)
            except UserFacingError as e:
                cancel_error.append(e)

        t1 = threading.Thread(target=do_run)
        t2 = threading.Thread(target=do_cancel)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        final = service.job_store.get(job.id)
        if final.status == JobStatus.CANCELLED:
            assert provider.calls == 0
            assert cancel_error == []  # cancellation itself succeeded
        elif final.status == JobStatus.COMPLETED:
            assert provider.calls == 1
            assert len(cancel_error) == 1  # cancellation was correctly refused (409)
            assert cancel_error[0].status_code == 409
        else:
            pytest.fail(f"unexpected final status: {final.status}")
