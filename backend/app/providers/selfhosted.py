"""SelfHostedVTONProvider: wraps our vendored, patched fashn-vton-1.5 pipeline.

See docs/AI_MODEL_LICENSE.md for what "patched" means and why (the upstream
human-parser dependency was non-commercially licensed and was replaced).
This is the *only* file in the backend that imports fashn_vton — if the model
is ever swapped, this is the only file that should need to change.
"""

import logging
import threading

from fashn_vton import TryOnPipeline

from .base import TryOnRequest, TryOnResult, VirtualTryOnProvider

logger = logging.getLogger(__name__)


class SelfHostedVTONProvider(VirtualTryOnProvider):
    def __init__(self, weights_dir: str, device: str = "cpu"):
        self._lock = threading.Lock()  # the underlying torch model is not proven thread-safe for concurrent calls
        logger.info("Loading TryOnPipeline from %s (device=%s)...", weights_dir, device)
        self._pipeline = TryOnPipeline(weights_dir=weights_dir, device=device)
        logger.info("TryOnPipeline ready.")

    def generate(self, request: TryOnRequest) -> TryOnResult:
        with self._lock:
            # garment_photo_type stays hardcoded to "flat-lay": model-worn garment
            # extraction was validated separately (see docs/AI_MODEL_LICENSE.md and the
            # MediaPipeBodyParser/create_garment_image masking it enables) but is not yet
            # exposed to production traffic — the API layer (backend/app/api/tryon.py)
            # still rejects any other value before a request reaches here, independent of
            # this hardcoded default; changing that is a separate, later decision.
            #
            # segmentation_free=False (changed from True): real person-image masking via
            # MediaPipeBodyParser + create_clothing_agnostic_image is now active for every
            # category (tops/bottoms/one-pieces), validated end-to-end including the boot-
            # vs-pants classification fix. The old PlaceholderBodyParser-only-safe reasoning
            # that used to hardcode this to True no longer applies — see
            # docs/AI_MODEL_LICENSE.md's Stage 1 section for what was actually validated.
            output = self._pipeline(
                person_image=request.person_image,
                garment_image=request.garment_image,
                category=request.category,
                garment_photo_type="flat-lay",
                segmentation_free=False,
                num_timesteps=request.num_timesteps,
                guidance_scale=request.guidance_scale,
                seed=request.seed,
            )
        return TryOnResult(image=output.images[0])
