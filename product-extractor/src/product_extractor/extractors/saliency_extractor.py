"""SaliencyProductExtractor: finds the most visually salient region in an
uploaded image and crops to it — the "isolate the actual product photo out
of a messier upload" step from the brief's section 7.

WHY CLASSICAL CV, NOT A NEW DEEP LEARNING MODEL
-------------------------------------------------
The obvious alternative is a learned segmentation/detection model (SAM2,
an object detector, etc.). Deliberately not used here:

  - It's a new, real dependency (a model download, more CPU time on a
    machine with no GPU — see docs/AI_MODEL_LICENSE.md's hardware notes)
    that isn't justified yet. We have no real user screenshots to
    benchmark against, so there's no evidence a heuristic is insufficient.
  - `cv2.saliency` (OpenCV's spectral-residual saliency, a classical
    signal-processing algorithm — no learned weights, so no separate
    license/training-data research needed the way every model in
    AI_MODEL_LICENSE.md required) is already available: `opencv-contrib-
    python` is a strict superset of the `opencv-python` this project
    already depended on transitively via fashn-vton-1.5, so this added
    zero new *packages*, just swapped one for its superset.
  - "Do not introduce unnecessary dependencies" (brief section 13/22).

If real usage shows this isn't accurate enough, upgrading to SAM2 (already
vetted as commercially clean in AI_MODEL_LICENSE.md for a different reason
— the Milestone 2 body-parser gap) is a drop-in: implement
ProductImageExtractor, wire it in behind ExtractionService. Nothing above
that interface would need to change.
"""

from typing import Tuple

import cv2
import numpy as np
from PIL import Image

from .base import ExtractionResult, ProductImageExtractor

# Tuned by hand against a handful of test images (see
# product-extractor/tests or backend/tests for the cases this handles) —
# not derived from a real dataset, since we don't have one yet. Revisit once
# real usage data exists.
_MIN_AREA_RATIO = 0.03  # smaller than this: probably noise/an icon, not a product
_MAX_AREA_RATIO = 0.92  # larger than this: already basically the whole image — cropping wouldn't help
# Confidence = what fraction of *all* detected salient area belongs to the single
# largest region ("dominance"), not that region's raw saliency intensity — spectral
# residual saliency's absolute output isn't a calibrated probability, but a single
# region clearly outweighing every other candidate is a reliable "this is the
# product, not scattered noise" signal. Verified against two synthetic cases (see
# product-extractor's tests): a real photo pasted into a mock screenshot scores
# ~0.86 dominance for the correct region, while an already-tight product photo
# (many similarly-sized salient sub-features, no single standout region) scores
# ~0.19 — comfortably on either side of the threshold below.
_MIN_DOMINANCE = 0.5
_PADDING_FRACTION = 0.08  # extra margin around the detected region, so we don't crop into the item
_WORKING_MAX_DIM = 800  # resize for speed; saliency detection doesn't need full resolution


class SaliencyProductExtractor(ProductImageExtractor):
    def extract(self, image: Image.Image) -> ExtractionResult:
        rgb = np.array(image.convert("RGB"))
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        working, scale = _resize_for_speed(bgr, _WORKING_MAX_DIM)

        saliency = cv2.saliency.StaticSaliencySpectralResidual_create()
        success, saliency_map = saliency.computeSaliency(working)
        if not success:
            return _unchanged(image)

        saliency_u8 = (np.clip(saliency_map, 0.0, 1.0) * 255).astype(np.uint8)
        _, binary = cv2.threshold(saliency_u8, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return _unchanged(image)

        areas = [cv2.contourArea(c) for c in contours]
        total_area = sum(areas)
        if total_area == 0:
            return _unchanged(image)

        largest_idx = max(range(len(contours)), key=lambda i: areas[i])
        largest = contours[largest_idx]
        confidence = areas[largest_idx] / total_area  # "dominance" — see the constant's docstring
        x, y, w, h = cv2.boundingRect(largest)

        working_area = working.shape[0] * working.shape[1]
        area_ratio = (w * h) / working_area
        if not (_MIN_AREA_RATIO <= area_ratio <= _MAX_AREA_RATIO):
            return _unchanged(image, confidence=confidence)
        if confidence < _MIN_DOMINANCE:
            return _unchanged(image, confidence=confidence)

        box = _scale_and_pad_box((x, y, w, h), scale, image.size, _PADDING_FRACTION)
        return ExtractionResult(image=image.crop(box), applied=True, confidence=confidence, bounding_box=box)


def _resize_for_speed(bgr: np.ndarray, max_dim: int) -> Tuple[np.ndarray, float]:
    h, w = bgr.shape[:2]
    longest = max(h, w)
    if longest <= max_dim:
        return bgr, 1.0
    scale = max_dim / longest
    resized = cv2.resize(bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    return resized, scale


def _scale_and_pad_box(
    box_xywh: Tuple[int, int, int, int],
    scale: float,
    original_size: Tuple[int, int],
    padding_fraction: float,
) -> Tuple[int, int, int, int]:
    x, y, w, h = box_xywh
    orig_w, orig_h = original_size
    # Map the working-resolution box back to original-image coordinates.
    x, y, w, h = x / scale, y / scale, w / scale, h / scale

    pad_x, pad_y = w * padding_fraction, h * padding_fraction
    left = max(0, int(x - pad_x))
    top = max(0, int(y - pad_y))
    right = min(orig_w, int(x + w + pad_x))
    bottom = min(orig_h, int(y + h + pad_y))
    return (left, top, right, bottom)


def _unchanged(image: Image.Image, confidence: float = 0.0) -> ExtractionResult:
    return ExtractionResult(image=image, applied=False, confidence=confidence, bounding_box=None)
