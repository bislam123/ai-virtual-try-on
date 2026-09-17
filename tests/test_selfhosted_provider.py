"""Tests for backend/app/providers/selfhosted.py's timeout/lock-timeout
behavior.

No real model, no real inference: SelfHostedVTONProvider's _pipeline is a
test-only injection point (never used by production code, which always
lets it construct the real TryOnPipeline) specifically so this behavior is
testable with a fast, fully-controllable fake callable instead. All
timeouts used here are small (well under a second) so this file runs
quickly; threading.Event is used wherever a test needs to deterministically
control *when* a "hung" fake pipeline actually finishes, rather than racing
a fixed sleep duration against a timeout under unpredictable system load.
"""

import threading
import time

import pytest
from PIL import Image

from backend.app.providers.base import TryOnRequest
from backend.app.providers.selfhosted import InferenceTimeoutError, ProviderBusyError, SelfHostedVTONProvider


class _FakeOutput:
    def __init__(self, image):
        self.images = [image]


def _make_request() -> TryOnRequest:
    tiny = Image.new("RGB", (1, 1))
    return TryOnRequest(
        person_image=tiny,
        garment_image=tiny,
        category="tops",
        garment_photo_type="flat-lay",
        num_timesteps=5,
        guidance_scale=1.5,
        seed=42,
    )


def _instant_pipeline(**kwargs):
    return _FakeOutput(image="the-generated-image")


def _slow_pipeline(delay_seconds):
    def _pipeline(**kwargs):
        time.sleep(delay_seconds)
        return _FakeOutput(image="the-generated-image")

    return _pipeline


def _gated_pipeline(event: threading.Event, started: threading.Event = None):
    """Blocks until `event` is set, then returns -- lets a test control
    exactly when a "hung" call finishes, deterministically."""

    def _pipeline(**kwargs):
        if started is not None:
            started.set()
        event.wait()
        return _FakeOutput(image="the-generated-image")

    return _pipeline


def _raising_pipeline(**kwargs):
    raise ValueError("simulated pipeline failure")


# --- normal operation, no timeout involved ----------------------------------


def test_generate_returns_result_within_timeout():
    provider = SelfHostedVTONProvider(
        weights_dir="unused",
        inference_timeout_seconds=5,
        lock_acquire_timeout_seconds=5,
        _pipeline=_instant_pipeline,
    )
    result = provider.generate(_make_request())
    assert result.image == "the-generated-image"


def test_pipeline_exception_propagates_and_lock_still_released():
    provider = SelfHostedVTONProvider(
        weights_dir="unused",
        inference_timeout_seconds=5,
        lock_acquire_timeout_seconds=5,
        _pipeline=_raising_pipeline,
    )
    with pytest.raises(ValueError, match="simulated pipeline failure"):
        provider.generate(_make_request())

    # Lock correctly released even though the call raised -- a subsequent
    # call must not be blocked/busy because of the previous failure.
    provider._pipeline = _instant_pipeline
    result = provider.generate(_make_request())
    assert result.image == "the-generated-image"


# --- inference timeout: honest, not a true kill ------------------------------


def test_generate_raises_timeout_error_promptly_not_after_the_full_hang():
    provider = SelfHostedVTONProvider(
        weights_dir="unused",
        inference_timeout_seconds=0.1,
        lock_acquire_timeout_seconds=5,
        _pipeline=_slow_pipeline(delay_seconds=5),
    )
    start = time.monotonic()
    with pytest.raises(InferenceTimeoutError):
        provider.generate(_make_request())
    elapsed = time.monotonic() - start

    # Raised at roughly the configured timeout, not after waiting for the
    # full 5s the fake pipeline actually sleeps for -- proves generate()
    # gives up waiting rather than blocking for the whole hang.
    assert elapsed < 2.0


def test_lock_remains_held_after_timeout_so_a_second_call_is_busy():
    """The core correctness property: a timeout must NOT free up the
    provider for a concurrent second generation, since the underlying
    computation is still actually running."""
    still_running = threading.Event()  # never set -- the fake pipeline hangs forever, deliberately
    provider = SelfHostedVTONProvider(
        weights_dir="unused",
        inference_timeout_seconds=0.1,
        lock_acquire_timeout_seconds=0.1,
        _pipeline=_gated_pipeline(still_running),
    )

    with pytest.raises(InferenceTimeoutError):
        provider.generate(_make_request())

    # The first call's background thread is still alive, still holding the
    # lock -- a second call must fail fast as "busy", not hang forever
    # itself or (worse) proceed concurrently.
    with pytest.raises(ProviderBusyError):
        provider.generate(_make_request())


def test_lock_is_released_once_the_hung_call_actually_finishes():
    """Once the underlying (previously "timed out") computation genuinely
    completes on its own, the lock must become available again -- proves
    release isn't lost/leaked forever after a timeout."""
    release_gate = threading.Event()
    started = threading.Event()
    provider = SelfHostedVTONProvider(
        weights_dir="unused",
        inference_timeout_seconds=0.1,
        lock_acquire_timeout_seconds=5,
        _pipeline=_gated_pipeline(release_gate, started=started),
    )

    with pytest.raises(InferenceTimeoutError):
        provider.generate(_make_request())

    assert started.is_set()  # the background thread genuinely started running

    # Let the "hung" computation finish now.
    release_gate.set()

    # A fresh call should now succeed once the lock is actually free --
    # lock_acquire_timeout_seconds=5 gives the background thread's own
    # finally-block release a moment to land; this does not wait anywhere
    # near 5s in practice since release_gate.set() above lets it finish
    # almost immediately.
    provider._pipeline = _instant_pipeline
    result = provider.generate(_make_request())
    assert result.image == "the-generated-image"


def test_provider_busy_error_raised_when_lock_wait_itself_times_out():
    release_gate = threading.Event()
    started = threading.Event()
    provider = SelfHostedVTONProvider(
        weights_dir="unused",
        inference_timeout_seconds=5,  # long enough that this call does NOT itself time out
        lock_acquire_timeout_seconds=0.1,
        _pipeline=_gated_pipeline(release_gate, started=started),
    )

    first_call_done = threading.Event()
    first_call_error = []

    def _run_first_call():
        try:
            provider.generate(_make_request())
        except Exception as e:  # noqa: BLE001
            first_call_error.append(e)
        finally:
            first_call_done.set()

    first_thread = threading.Thread(target=_run_first_call, daemon=True)
    first_thread.start()
    assert started.wait(timeout=5)  # wait for the first call to actually be holding the lock

    # A second, concurrent call must fail fast with ProviderBusyError --
    # not hang waiting for the first (legitimately still-running) call.
    with pytest.raises(ProviderBusyError):
        provider.generate(_make_request())

    # Clean up: let the first call finish normally.
    release_gate.set()
    assert first_call_done.wait(timeout=5)
    assert first_call_error == []  # the first call itself succeeded normally


def test_no_concurrent_generation_under_real_thread_contention():
    """Directly proves the "only one generation at a time" invariant under
    genuine concurrent access from multiple threads, not just sequential
    calls -- a shared counter must never be observed above 1."""
    concurrent_count = {"current": 0, "max_seen": 0}
    counter_lock = threading.Lock()

    def _tracking_pipeline(**kwargs):
        with counter_lock:
            concurrent_count["current"] += 1
            concurrent_count["max_seen"] = max(concurrent_count["max_seen"], concurrent_count["current"])
        time.sleep(0.05)
        with counter_lock:
            concurrent_count["current"] -= 1
        return _FakeOutput(image="the-generated-image")

    provider = SelfHostedVTONProvider(
        weights_dir="unused",
        inference_timeout_seconds=5,
        lock_acquire_timeout_seconds=5,
        _pipeline=_tracking_pipeline,
    )

    threads = [threading.Thread(target=lambda: provider.generate(_make_request())) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert concurrent_count["max_seen"] == 1
