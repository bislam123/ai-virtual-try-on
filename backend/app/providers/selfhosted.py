"""SelfHostedVTONProvider: wraps our vendored, patched fashn-vton-1.5 pipeline.

See docs/AI_MODEL_LICENSE.md for what "patched" means and why (the upstream
human-parser dependency was non-commercially licensed and was replaced).
This is the *only* file in the backend that imports fashn_vton — if the model
is ever swapped, this is the only file that should need to change.

Timeout design -- read before changing this file, and see docs/DEVELOPMENT.md
for the production-hardening context this was added under:

This is NOT a true hard timeout. Python cannot safely force-terminate a
running thread, and a pathological hang here is very likely stuck inside
native PyTorch/CUDA/MKL code that wouldn't yield back to Python (or a
signal handler) until that call returns on its own -- so there is no
mechanism in this architecture that can *actually* stop a hung generate()
call. A true hard kill would need the inference to run in a genuinely
separate OS process (killable unconditionally), which would mean either
reloading the multi-GB model fresh for every single request (a major
performance regression) or standing up a persistent worker process with
its own supervision/IPC/restart story -- a materially larger architecture
change than fits this task; not implemented here.

What this DOES do, honestly: run the actual pipeline call in a background
thread and only *wait* up to inference_timeout_seconds for it. If it
doesn't finish in time, generate() raises InferenceTimeoutError and
returns control to the caller (so the job can be marked failed and the
API stays responsive) -- but the background thread keeps running
un-terminated, and the provider's lock is deliberately NOT released until
that thread actually finishes on its own, however long that takes. This
guarantees the "only one generation at a time" invariant still holds
across a timeout: a second caller will correctly block (or itself time
out via lock_acquire_timeout_seconds) rather than starting a concurrent
generation against the same model instance. A worker process that
genuinely hangs this way needs a restart to fully recover generation
capacity -- services/job_recovery.py's stale-job sweep bounds how long a
*job* can appear stuck to API clients, but does not free up the
underlying stuck thread/lock.
"""

import logging
import threading
from typing import Optional

from fashn_vton import TryOnPipeline

from .base import InferenceTimeoutError, ProviderBusyError, TryOnRequest, TryOnResult, VirtualTryOnProvider

logger = logging.getLogger(__name__)

# Re-exported for backward compatibility -- existing code (and
# tests/test_selfhosted_provider.py) imports these two names from this
# module; the actual definitions now live in providers/base.py so
# services/tryon_service.py can reference them without importing anything
# provider-specific (see base.py's docstring on both classes for why).
__all__ = ["InferenceTimeoutError", "ProviderBusyError", "SelfHostedVTONProvider"]


class SelfHostedVTONProvider(VirtualTryOnProvider):
    def __init__(
        self,
        weights_dir: str,
        device: str = "cpu",
        inference_timeout_seconds: float = 3600,
        lock_acquire_timeout_seconds: float = 3600,
        _pipeline: Optional[object] = None,
    ):
        # the underlying torch model is not proven thread-safe for concurrent calls
        self._lock = threading.Lock()
        self._inference_timeout_seconds = inference_timeout_seconds
        self._lock_acquire_timeout_seconds = lock_acquire_timeout_seconds
        if _pipeline is not None:
            # Test-only injection point (see tests/test_selfhosted_provider.py)
            # -- never passed by production code (app/main.py), which always
            # lets this construct the real pipeline below.
            self._pipeline = _pipeline
        else:
            logger.info("Loading TryOnPipeline from %s (device=%s)...", weights_dir, device)
            self._pipeline = TryOnPipeline(weights_dir=weights_dir, device=device)
            logger.info("TryOnPipeline ready.")

    def generate(self, request: TryOnRequest) -> TryOnResult:
        if not self._lock.acquire(timeout=self._lock_acquire_timeout_seconds):
            raise ProviderBusyError(
                f"The AI model is still busy with a previous request after waiting "
                f"{self._lock_acquire_timeout_seconds}s. Please try again shortly."
            )

        result_box: dict = {}

        def _run_inference():
            try:
                # garment_photo_type reads from the request rather than a hardcoded literal:
                # backend/app/api/tryon.py now accepts both "flat-lay" and "model"
                # (VALID_GARMENT_PHOTO_TYPES) and rejects anything else, so
                # request.garment_photo_type is one of those two values for every real
                # request. See docs/AI_MODEL_LICENSE.md's model-worn investigation and
                # validation for what makes "model" safe: real segmentation via
                # MediaPipeBodyParser, the same create_garment_image masking already used
                # for the person-image side, validated end-to-end for every category.
                #
                # segmentation_free=False: real person-image masking via MediaPipeBodyParser +
                # create_clothing_agnostic_image is active for every category (tops/bottoms/
                # one-pieces), validated end-to-end including the boot-vs-pants classification
                # fix. See docs/AI_MODEL_LICENSE.md's Stage 1 section for what was validated.
                result_box["output"] = self._pipeline(
                    person_image=request.person_image,
                    garment_image=request.garment_image,
                    category=request.category,
                    garment_photo_type=request.garment_photo_type,
                    segmentation_free=False,
                    num_timesteps=request.num_timesteps,
                    guidance_scale=request.guidance_scale,
                    seed=request.seed,
                )
            except Exception as e:  # noqa: BLE001 -- re-raised on the calling thread below
                result_box["error"] = e
            finally:
                # Always released here, by whichever call actually finishes the
                # work -- not by generate() below, which may give up waiting
                # long before this. See module docstring for why this is what
                # keeps "only one generation at a time" true across a timeout.
                self._lock.release()

        worker = threading.Thread(target=_run_inference, daemon=True)
        worker.start()
        worker.join(timeout=self._inference_timeout_seconds)

        if worker.is_alive():
            logger.error(
                "Inference exceeded the %ss timeout; the underlying computation was NOT "
                "forcibly stopped (not possible with this architecture -- see this module's "
                "docstring) and may still be running, holding the model lock until it finishes "
                "on its own.",
                self._inference_timeout_seconds,
            )
            raise InferenceTimeoutError(
                f"Generation exceeded the {self._inference_timeout_seconds}s timeout."
            )

        if "error" in result_box:
            raise result_box["error"]
        return TryOnResult(image=result_box["output"].images[0])
