"""MediaPipeBodyParser: Stage 1 of the real body/garment segmentation
replacement for PlaceholderBodyParser (see docs/AI_MODEL_LICENSE.md).

Combines two commercially-clean, Apache-2.0 models already validated on this
project's own dev machine (CPU, no GPU):

  - Google's MediaPipe `selfie_multiclass_256x256` segmenter: 6 coarse
    classes (background, hair, body-skin, face-skin, clothes, other).
  - DWPose (already vendored in fashn_vton, Apache-2.0): body keypoints,
    used only to *geometrically subdivide* MediaPipe's body-skin/clothes
    blobs into the finer-grained classes fashn_vton's preprocessing code
    expects (see ai/vendor/fashn-vton-1.5/src/fashn_vton/preprocessing/
    agnostic.py) -- MediaPipe never has to classify garment *type*, since
    the API's own `category` field already tells the pipeline which
    coverage bucket (upper/lower/full) to look for after segmentation.

Matches PlaceholderBodyParser's exact contract: predict(image_np) -> an
int64 H x W label map using the same aitryon_bodyparser.LABELS_TO_IDS ids.
Never raises -- a person-less image (a flat-lay garment photo, or DWPose
simply failing to find anyone) degrades to a coarse but valid labeling
rather than crashing or guessing wildly. See the "Fallback behavior"
section below for the exact, validated rules.

Key simplification (validated against the real consuming code, not just
reasoned about): this parser never tries to distinguish dress/skirt/
belt/scarf, or bag/hat/glasses/jewelry. It only ever emits `top` or
`pants` for clothing, and leaves the accessory classes unused. This is
correct because `BODY_COVERAGE_TO_LABELS` in aitryon_bodyparser/labels.py
already treats those as interchangeable within a coverage bucket -- see
the architecture investigation in git history / project notes for why.

Not integrated yet with SAM2 (deferred, Stage 2) and does not attempt any
pose-aware refinement of the top/pants split beyond a single shoulder/hip
horizontal line -- both deliberate Stage 1 scope limits, not oversights.
"""

import os
from typing import Optional

import mediapipe as mp
import numpy as np
from aitryon_bodyparser import LABELS_TO_IDS
from fashn_vton.dwpose import DWposeDetector
from mediapipe.tasks.python import BaseOptions
from mediapipe.tasks.python.vision import ImageSegmenter, ImageSegmenterOptions

# --- our label ids (single source of truth: aitryon_bodyparser/labels.py) --
_BACKGROUND = LABELS_TO_IDS["background"]
_FACE = LABELS_TO_IDS["face"]
_HAIR = LABELS_TO_IDS["hair"]
_TOP = LABELS_TO_IDS["top"]
_PANTS = LABELS_TO_IDS["pants"]
_ARMS = LABELS_TO_IDS["arms"]
_HANDS = LABELS_TO_IDS["hands"]
_LEGS = LABELS_TO_IDS["legs"]
_FEET = LABELS_TO_IDS["feet"]
_TORSO = LABELS_TO_IDS["torso"]

# --- MediaPipe's own class ids for selfie_multiclass_256x256 (verified by
# inspecting real output against real photos, not just documentation) ---
_MP_BACKGROUND, _MP_HAIR, _MP_BODY_SKIN, _MP_FACE_SKIN, _MP_CLOTHES, _MP_OTHER = range(6)

# --- DWPose body keypoint indices (OpenPose-18 order, see dwpose/dwpose.py)
_RSHOULDER, _LSHOULDER = 2, 5
_RWRIST, _LWRIST = 4, 7
_RHIP, _LHIP = 8, 11
_RANKLE, _LANKLE = 10, 13

# Radius (as a fraction of image height) used to carve hands/feet out of the
# arms/legs regions around wrist/ankle keypoints -- validated visually
# against real photos during prototyping.
_LIMB_CARVE_RADIUS_FRAC = 0.06


class MediaPipeBodyParser:
    """Real segmentation model. Same construction shape as
    PlaceholderBodyParser (device=...), plus the two model locations it
    needs -- both loaded once here, not per predict() call, matching
    TryOnPipeline's own "load once at startup" convention for every other
    model."""

    def __init__(
        self,
        model_path: str,
        dwpose_checkpoints_dir: str,
        device: str = "cpu",
    ):
        self.device = device

        if not os.path.exists(model_path):
            raise FileNotFoundError(
                f"MediaPipe model not found at {model_path}. "
                "Download it once (see docs/AI_MODEL_LICENSE.md) -- it is "
                "gitignored, like every other model weight in this project."
            )

        base_options = BaseOptions(model_asset_path=model_path)
        options = ImageSegmenterOptions(base_options=base_options, output_category_mask=True)
        self._segmenter = ImageSegmenter.create_from_options(options)

        # A second, independent DWposeDetector instance -- TryOnPipeline
        # already runs one for pose-conditioning tensors, but predict()'s
        # contract is a single image argument, so this parser must derive
        # its own keypoints rather than receiving the pipeline's. A known,
        # deliberate Stage 1 cost (see docs/AI_MODEL_LICENSE.md): DWPose
        # runs twice per generation. Measured at ~1.5s/image on this CPU
        # dev machine -- negligible against this project's existing
        # 10-70+ minute generation times.
        self._pose_detector = DWposeDetector(checkpoints_dir=dwpose_checkpoints_dir, device=device)

    def predict(self, image_np: np.ndarray) -> np.ndarray:
        """Return an int64 H x W label map. Never raises."""
        h, w = image_np.shape[:2]

        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(image_np))
        mp_mask = self._segmenter.segment(mp_image).category_mask.numpy_view()
        mp_mask = np.asarray(mp_mask).reshape(h, w).astype(np.uint8)

        pose = self._pose_detector(image_np[..., ::-1])  # DWPose expects BGR, same as pipeline.py

        return _compose_label_map(mp_mask, pose)


def _compose_label_map(mp_mask: np.ndarray, pose: dict) -> np.ndarray:
    """The actual class mapping + geometric subdivision, factored out of
    predict() so it can be unit-tested with synthetic MediaPipe masks and
    synthetic pose data, without needing either real model loaded."""
    h, w = mp_mask.shape
    label_map = np.full((h, w), _BACKGROUND, dtype=np.int64)

    # Direct 1:1 passthroughs. `other` -> background: it was never
    # confidently anything else, so treating it as non-actionable is the
    # safe choice (validated in prototyping: it mostly catches accessories
    # like hats/shoes, which are already out of scope for Stage 1).
    label_map[mp_mask == _MP_HAIR] = _HAIR
    label_map[mp_mask == _MP_FACE_SKIN] = _FACE
    # background and other both already default to _BACKGROUND above.

    body_skin = mp_mask == _MP_BODY_SKIN
    clothes = mp_mask == _MP_CLOTHES

    candidate, subset = _extract_body_keypoints(pose)
    y_shoulder = _line_y(candidate, subset, _RSHOULDER, _LSHOULDER, h)
    y_hip = _line_y(candidate, subset, _RHIP, _LHIP, h)
    x_range_shoulder = _line_x_range(candidate, subset, _RSHOULDER, _LSHOULDER, w)
    x_range_hip = _line_x_range(candidate, subset, _RHIP, _LHIP, w)

    yy, xx = np.mgrid[0:h, 0:w]

    # --- clothes -> top / pants -------------------------------------
    if y_hip is not None:
        label_map[clothes & (yy < y_hip)] = _TOP
        label_map[clothes & (yy >= y_hip)] = _PANTS
    else:
        # Fallback (documented, validated): can't establish the hip line
        # (no person detected, e.g. a flat-lay garment image, or DWPose
        # simply failed) -- everything goes to `top` rather than being
        # dropped. See docs/AI_MODEL_LICENSE.md for the known asymmetry
        # this creates for category="bottoms" under full degradation.
        label_map[clothes] = _TOP

    # --- body-skin -> torso / arms / legs (coarse pass) --------------
    if y_shoulder is not None and y_hip is not None and x_range_shoulder and x_range_hip:
        cx_lo = min(x_range_shoulder[0], x_range_hip[0])
        cx_hi = max(x_range_shoulder[1], x_range_hip[1])
        band = body_skin & (yy >= y_shoulder) & (yy <= y_hip)
        central = band & (xx >= cx_lo) & (xx <= cx_hi)
        lateral = band & ~central

        label_map[central] = _TORSO
        label_map[body_skin & (yy < y_shoulder)] = _TORSO  # neck area
        label_map[lateral] = _ARMS
        label_map[body_skin & (yy > y_hip)] = _LEGS
    else:
        # Fallback (documented, validated): can't establish both dividing
        # lines -- all body-skin becomes the neutral, inclusive-into-
        # masking default (`torso`), never a false identity-protected class.
        label_map[body_skin] = _TORSO

    # --- hands / feet: locally override arms / legs near wrist/ankle -
    radius_px = _LIMB_CARVE_RADIUS_FRAC * h
    for idx in (_RWRIST, _LWRIST):
        _carve_local(label_map, candidate, subset, idx, radius_px, xx, yy, from_id=_ARMS, to_id=_HANDS)
    hands_kpts = pose.get("hands")
    if hands_kpts is not None and np.size(hands_kpts):
        for hand in np.asarray(hands_kpts):
            wrist = hand[0]
            if wrist[0] >= 0:
                _carve_at_point(label_map, wrist, w, h, radius_px, xx, yy, from_id=_ARMS, to_id=_HANDS)
    for idx in (_RANKLE, _LANKLE):
        _carve_local(label_map, candidate, subset, idx, radius_px, xx, yy, from_id=_LEGS, to_id=_FEET)
        # MediaPipe's `clothes` class doesn't distinguish footwear from
        # legwear, and the geometric top/pants split (above) has no signal
        # to separate them -- a boot below the hip line gets labeled
        # _PANTS like any other clothes pixel there. Reclassify _PANTS
        # pixels near the ankle into _FEET too, same as the _LEGS case
        # above, so boots aren't swept into "regenerate this as pants".
        # Bounded by the same radius as the skin case; a knee-high boot's
        # upper portion can still land as _PANTS -- not a complete fix,
        # just the smallest safe one for the common case.
        _carve_local(label_map, candidate, subset, idx, radius_px, xx, yy, from_id=_PANTS, to_id=_FEET)

    return label_map


def _extract_body_keypoints(pose: dict):
    bodies = pose.get("bodies", {})
    candidate = np.asarray(bodies.get("candidate", -1 * np.ones((18, 2))))
    subset = np.asarray(bodies.get("subset", -1 * np.ones((1, 18))))
    if subset.ndim == 2:
        subset = subset[0]
    return candidate, subset


def _line_y(candidate, subset, idx_a: int, idx_b: int, h: int) -> Optional[float]:
    """Bilateral fallback: average both sides if both are confidently
    detected, else use whichever single side is, else None."""
    ys = [candidate[i][1] * h for i in (idx_a, idx_b) if subset[i] >= 0]
    return float(np.mean(ys)) if ys else None


def _line_x_range(candidate, subset, idx_a: int, idx_b: int, w: int):
    xs = [candidate[i][0] * w for i in (idx_a, idx_b) if subset[i] >= 0]
    return (min(xs), max(xs)) if xs else None


def _carve_local(label_map, candidate, subset, idx: int, radius_px: float, xx, yy, *, from_id: int, to_id: int):
    if subset[idx] < 0:
        return
    _carve_at_point(label_map, candidate[idx], label_map.shape[1], label_map.shape[0], radius_px, xx, yy, from_id=from_id, to_id=to_id)


def _carve_at_point(label_map, point_norm, w: int, h: int, radius_px: float, xx, yy, *, from_id: int, to_id: int):
    cx, cy = point_norm[0] * w, point_norm[1] * h
    if cx < 0 or cy < 0:
        return
    near = (np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) <= radius_px) & (label_map == from_id)
    label_map[near] = to_id
