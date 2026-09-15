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
            # garment_photo_type is hardcoded to "flat-lay" and segmentation_free to True:
            # those are the only two settings our current body-parser placeholder is proven
            # correct for (see ai/vendor/aitryon-bodyparser's parser.py docstring). The API
            # layer already restricts what garment_photo_type users can submit for the same
            # reason — this is belt-and-suspenders, not a silent behavior change.
            output = self._pipeline(
                person_image=request.person_image,
                garment_image=request.garment_image,
                category=request.category,
                garment_photo_type="flat-lay",
                segmentation_free=True,
                num_timesteps=request.num_timesteps,
                guidance_scale=request.guidance_scale,
                seed=request.seed,
            )
        return TryOnResult(image=output.images[0])
