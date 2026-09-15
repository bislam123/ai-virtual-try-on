"""Placeholder body/garment parser.

WHY THIS IS SAFE FOR MILESTONE 2, AND WHEN IT STOPS BEING SAFE
----------------------------------------------------------------
fashn_vton's TryOnPipeline calls `hp_model.predict()` on every run, but its
result is only *used* when either of these preprocessing calls is allowed to
touch it:

    create_clothing_agnostic_image(..., disable_masking=segmentation_free)
    create_garment_image(..., disable_masking=(garment_photo_type == "flat-lay"))

Both functions short-circuit and return the input image untouched when
`disable_masking=True`. So the segmentation output is a genuine no-op whenever
the pipeline is run with `segmentation_free=True` (the pipeline's own
recommended default — "better body preservation and unconstrained garment
volume") *and* `garment_photo_type="flat-lay"` (a plain product/garment photo,
not a photo of the item worn by another person) — which is exactly Milestone 2's
target case: a person photo + a clothing product image.

This class returns an all-"background" label map matching the input image's
shape. It is intentionally not a real segmentation model.

IT MUST BE REPLACED before:
  - enabling `segmentation_free=False` (masked mode), or
  - passing `garment_photo_type="model"` (garment photo shows another person
    wearing the item — e.g. many scraped product-page lifestyle photos),

because in both of those cases the pipeline actually reads the label map and a
constant zero map would produce a broken/garbage mask, not just a lower-quality
one. Commercially-clean replacement candidates (see docs/AI_MODEL_LICENSE.md):
Meta's Segment Anything 2 (Apache-2.0) driven by DWPose keypoints already
computed in this pipeline, or an Apache-2.0/MIT U2Net-based cloth segmentation
fork. Do NOT reach for SCHP/LIP-ATR checkpoints (trained on the non-commercial
LIP dataset) or any SegFormer fine-tune (NVIDIA non-commercial license) as the
"real" replacement — see docs/AI_MODEL_LICENSE.md for why both were rejected.
"""

import numpy as np

from .labels import LABELS_TO_IDS

_BACKGROUND_ID = LABELS_TO_IDS["background"]


class PlaceholderBodyParser:
    def __init__(self, device: str = "cpu"):
        self.device = device

    def predict(self, image_np: np.ndarray) -> np.ndarray:
        """Return an all-background label map matching the input's H x W."""
        h, w = image_np.shape[:2]
        return np.full((h, w), _BACKGROUND_ID, dtype=np.int64)
