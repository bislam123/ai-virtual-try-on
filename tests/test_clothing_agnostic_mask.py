"""Regression tests for the clothing-agnostic mask covering background on
bent/seated poses (see docs/AI_MODEL_LICENSE.md's "Fixed: clothing-agnostic
mask covering background on bent/seated poses" section for the full
diagnosis). Two bugs in FASHN VTON's own upstream mask construction
(ai/vendor/fashn-vton-1.5/src/fashn_vton/preprocessing/masks.py and
agnostic.py) combined to produce a mask covering large swaths of true
background whenever the segmented region was disjoint (e.g. a hand resting
on a knee, separate in pixel space from the torso) or non-convex (a bent
knee creating a concave gap next to the body):

1. create_bounded_mask took one global bounding box over the whole mask,
   so disjoint regions stretched the box to span the empty space between
   them.
2. The hybrid trim's min_distance_threshold (100px at baseline) was too
   generous: concave background regions are Euclidean-close to the body's
   own contour even though they are clearly not part of it, so most of an
   oversized bounding box survived the trim untouched.

Split the same way tests/test_mediapipe_body_parser.py splits pure-logic
tests (synthetic, fast, no models loaded) from real-model tests (loads the
actual MediaPipeBodyParser + weights already present in this repo).
"""

import cv2
import numpy as np
import pytest
from PIL import Image

from aitryon_bodyparser import LABELS_TO_IDS
from fashn_vton.preprocessing import (
    BODY_COVERAGE_TO_FASHN_LABELS,
    FASHN_LABELS_TO_IDS,
    create_clothing_agnostic_image,
)
from fashn_vton.preprocessing.agnostic import _create_hybrid_contour_bounded_mask
from fashn_vton.preprocessing.masks import create_bounded_mask, create_contour_following_mask

MODEL_PHOTO = __file__.rsplit("tests", 1)[0] + "ai/vendor/fashn-vton-1.5/examples/data/model.webp"
MEDIAPIPE_MODEL_PATH = __file__.rsplit("tests", 1)[0] + "ai/models/mediapipe/selfie_multiclass_256x256.tflite"
DWPOSE_DIR = __file__.rsplit("tests", 1)[0] + "ai/models/fashn-vton-1.5/dwpose"

TOP = LABELS_TO_IDS["top"]


# --- pure-logic tests: synthetic masks, no models loaded ------------------


def test_bounded_mask_does_not_bridge_disjoint_components():
    """The core create_bounded_mask fix: two small, far-apart components
    must each get their own tight bounding box, not one box spanning the
    empty space between them."""
    mask = np.zeros((200, 200), dtype=bool)
    mask[10:30, 10:30] = True  # component 1
    mask[170:190, 170:190] = True  # component 2, far away

    bounded = create_bounded_mask(mask)

    assert bounded[20, 20]  # inside component 1
    assert bounded[180, 180]  # inside component 2
    assert not bounded[100, 100]  # empty middle, far from both -- must stay False


def test_bounded_mask_matches_global_box_for_single_component():
    """A single connected (even non-convex/L-shaped) component must still
    fill its own full bounding box, matching the pre-fix single-box
    behavior -- the fix only changes behavior when there are multiple
    disjoint components."""
    mask = np.zeros((100, 100), dtype=bool)
    mask[20:50, 20:80] = True  # top bar
    mask[20:80, 20:50] = True  # left bar -- joined into one L-shaped component

    bounded = create_bounded_mask(mask)

    x, y, w, h = cv2.boundingRect(mask.astype(np.uint8))
    expected = np.zeros_like(mask)
    expected[y : y + h, x : x + w] = True
    assert np.array_equal(bounded, expected)


def _u_shaped_notch_mask(h=300, w=300):
    """A U-shaped region (top bar + two arms), kept well clear of the image
    border -- create_contour_following_mask's hole-filling floods from the
    image corner outward, assuming the corner is background; placing the
    shape too close to the border (verified: within brush_radius + Gaussian
    smoothing spread) breaks that assumption and fills the whole canvas, an
    unrelated artifact this test must avoid to isolate the actual bug."""
    mask = np.zeros((h, w), dtype=bool)
    mask[60:100, 60:240] = True  # top bar (shoulders)
    mask[60:160, 60:120] = True  # left arm of the U
    mask[60:160, 180:240] = True  # right arm of the U
    # (100:160, 120:180) is the notch -- true background between the U's arms,
    # close enough to the contour (~30-18=12px+) to have been swept in by the
    # upstream default threshold (100px) but not by the fix's (5px).
    return mask


def test_hybrid_mask_does_not_leak_into_concave_background_notch():
    """The core _create_hybrid_contour_bounded_mask fix: a U-shaped
    (concave) clothing region -- mimicking a bent knee creating a gap next
    to the torso -- must not have that gap filled in as if it were part of
    the bounded/hybrid mask, even though the gap is geometrically closer to
    the contour than the upstream default threshold. Verified directly: with
    the upstream default (min_distance_threshold=100.0), point (140, 150)
    below is masked True; with the fix (5.0), it is False.
    """
    mask = _u_shaped_notch_mask()
    bounded = create_bounded_mask(mask)
    contour = create_contour_following_mask(mask, brush_radius=18)
    hybrid = _create_hybrid_contour_bounded_mask(contour, bounded)  # uses the real default threshold

    assert not hybrid[140, 150], "in the concave notch -- must not be masked"


def test_create_clothing_agnostic_image_does_not_leak_into_concave_notch():
    """Same scenario as above, but through the full public
    create_clothing_agnostic_image entry point (the one the pipeline
    actually calls), proving the fix holds end-to-end, not just in the
    internal hybrid-mask helper."""
    mask = _u_shaped_notch_mask()
    h, w = mask.shape
    seg_pred = np.where(mask, TOP, 0).astype(np.int64)

    ca_image = create_clothing_agnostic_image(
        img_np=np.full((h, w, 3), 200, dtype=np.uint8),
        seg_pred=seg_pred,
        labels_to_segment_indices=[TOP],
        body_coverage="upper",
        mask_limbs=False,
    )
    masked = np.all(ca_image == 127, axis=-1)
    assert not masked[140, 150]


def test_default_min_distance_threshold_is_the_fixed_small_value():
    """Guards against silently reverting to the too-generous upstream
    default (100.0) that caused this bug."""
    import inspect

    threshold = inspect.signature(create_clothing_agnostic_image).parameters["min_distance_threshold"].default
    assert threshold <= 10.0


# --- real-model tests: loads the actual MediaPipeBodyParser + weights -----


@pytest.fixture(scope="module")
def real_person_segmentation():
    from aitryon_preprocessing.mediapipe_parser import MediaPipeBodyParser

    parser = MediaPipeBodyParser(model_path=MEDIAPIPE_MODEL_PATH, dwpose_checkpoints_dir=DWPOSE_DIR, device="cpu")
    person_np = np.array(Image.open(MODEL_PHOTO).convert("RGB"))
    seg_pred = parser.predict(person_np)
    return person_np, seg_pred


@pytest.mark.parametrize("category,ceiling_pct", [("tops", 32.0), ("bottoms", 32.0), ("one-pieces", 58.0)])
def test_real_bent_pose_photo_mask_does_not_cover_most_of_frame(real_person_segmentation, category, ceiling_pct):
    """Regression test for the actual reported bug: on the bundled
    seated/bent-knee example photo, the clothing-agnostic mask must stay
    reasonably close to the person's silhouette, not balloon into a
    background-covering rectangle. Measured masked area: ~36% (tops), ~36%
    (bottoms), ~72% (one-pieces) of the frame before this fix; ~27%, ~27%,
    ~52% after. Ceilings sit strictly between the two so a regression back
    toward the old behavior fails this test on all three categories (as
    verified: reverting the fix fails all three parametrizations here).
    """
    person_np, seg_pred = real_person_segmentation
    body_coverage = {"tops": "upper", "bottoms": "lower", "one-pieces": "full"}[category]
    labels = [FASHN_LABELS_TO_IDS[label] for label in BODY_COVERAGE_TO_FASHN_LABELS[body_coverage]]

    ca_image = create_clothing_agnostic_image(
        img_np=person_np.copy(),
        seg_pred=seg_pred.copy(),
        labels_to_segment_indices=labels,
        body_coverage=body_coverage,
    )
    masked_pct = 100 * np.all(ca_image == 127, axis=-1).sum() / (person_np.shape[0] * person_np.shape[1])
    assert masked_pct < ceiling_pct
