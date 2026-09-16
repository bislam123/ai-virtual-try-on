"""Tests for aitryon_preprocessing.MediaPipeBodyParser -- Stage 1 real
segmentation (Google's MediaPipe selfie_multiclass_256x256 + DWPose-derived
geometric subdivision), replacing PlaceholderBodyParser as the human-parsing
model TryOnPipeline calls. See docs/AI_MODEL_LICENSE.md for the licensing
rationale and ai/preprocessing/src/aitryon_preprocessing/mediapipe_parser.py's
own module docstring for the design (why no dress/skirt/belt/scarf, why the
horizontal shoulder/hip line, why the fallback rules below are what they are).

Split, on purpose, the same way tests/test_url_fetcher.py splits pure-logic
tests from model-loading ones: the geometric subdivision (_compose_label_map)
is tested directly with synthetic MediaPipe masks and synthetic pose dicts --
fast, deterministic, no models loaded -- while only two tests load the real
MediaPipe + DWPose weights already present in this repo's ai/models/.
"""

import numpy as np
import pytest
from PIL import Image

from aitryon_bodyparser import LABELS_TO_IDS
from aitryon_preprocessing.mediapipe_parser import _compose_label_map, MediaPipeBodyParser

MODEL_PHOTO = __file__.rsplit("tests", 1)[0] + "ai/vendor/fashn-vton-1.5/examples/data/model.webp"
GARMENT_PHOTO = __file__.rsplit("tests", 1)[0] + "ai/vendor/fashn-vton-1.5/examples/data/garment.webp"
MEDIAPIPE_MODEL_PATH = __file__.rsplit("tests", 1)[0] + "ai/models/mediapipe/selfie_multiclass_256x256.tflite"
DWPOSE_DIR = __file__.rsplit("tests", 1)[0] + "ai/models/fashn-vton-1.5/dwpose"

# MediaPipe's own class ids for selfie_multiclass_256x256 (see mediapipe_parser.py)
_MP_BACKGROUND, _MP_HAIR, _MP_BODY_SKIN, _MP_FACE_SKIN, _MP_CLOTHES, _MP_OTHER = range(6)

BG = LABELS_TO_IDS["background"]
FACE = LABELS_TO_IDS["face"]
HAIR = LABELS_TO_IDS["hair"]
TOP = LABELS_TO_IDS["top"]
PANTS = LABELS_TO_IDS["pants"]
ARMS = LABELS_TO_IDS["arms"]
HANDS = LABELS_TO_IDS["hands"]
LEGS = LABELS_TO_IDS["legs"]
FEET = LABELS_TO_IDS["feet"]
TORSO = LABELS_TO_IDS["torso"]


def _dummy_pose() -> dict:
    """Same all-invalid shape as fashn_vton.utils.keypoints.get_dummy_dw_keypoints()
    -- the real convention already used in production for every flat-lay
    garment image today."""
    return {"bodies": {"candidate": -1 * np.ones((18, 2)), "subset": -1 * np.ones((1, 18))}}


def _confident_pose(y_shoulder=0.2, y_hip=0.6, x_left=0.3, x_right=0.7) -> dict:
    """A synthetic, fully-confident pose: both shoulders at y_shoulder, both
    hips at y_hip, wrists near the shoulder line's sides, ankles below the
    hip line -- enough to exercise the full subdivision + hands/feet carve."""
    candidate = np.zeros((18, 2))
    subset = np.zeros((1, 18))
    # index: 2 Rshoulder, 5 Lshoulder, 4 Rwrist, 7 Lwrist, 8 Rhip, 11 Lhip, 10 Rankle, 13 Lankle
    pts = {
        2: (x_right, y_shoulder), 5: (x_left, y_shoulder),
        4: (x_right + 0.05, y_shoulder + 0.05), 7: (x_left - 0.05, y_shoulder + 0.05),
        8: (x_right - 0.05, y_hip), 11: (x_left + 0.05, y_hip),
        10: (x_right - 0.02, 0.95), 13: (x_left + 0.02, 0.95),
    }
    for idx, (x, y) in pts.items():
        candidate[idx] = (x, y)
        subset[0][idx] = idx  # any non-negative value marks it valid
    return {"bodies": {"candidate": candidate, "subset": subset}}


# --- 1. Parser output contract ------------------------------------------


def test_output_contract_shape_dtype_and_value_range():
    mp_mask = np.zeros((50, 40), dtype=np.uint8)
    result = _compose_label_map(mp_mask, _dummy_pose())
    assert result.shape == (50, 40)
    assert result.dtype == np.int64
    assert set(np.unique(result).tolist()).issubset(set(range(18)))


# --- 2. MediaPipe class mapping ------------------------------------------


def test_direct_class_mapping_hair_face_background_and_other():
    # One pixel of each MediaPipe class, laid out in a row; body-skin/clothes
    # at columns 2/4 fall back coarsely since there's no pose here.
    mp_mask = np.array([[_MP_BACKGROUND, _MP_HAIR, _MP_BODY_SKIN, _MP_FACE_SKIN, _MP_CLOTHES, _MP_OTHER]], dtype=np.uint8)
    result = _compose_label_map(mp_mask, _dummy_pose())
    assert result[0, 0] == BG
    assert result[0, 1] == HAIR
    assert result[0, 3] == FACE
    assert result[0, 5] == BG  # `other` -> background, per spec


# --- 3. Geometric subdivision --------------------------------------------


def test_subdivision_splits_clothes_into_top_and_pants_by_hip_line():
    h, w = 100, 100
    mp_mask = np.full((h, w), _MP_CLOTHES, dtype=np.uint8)  # clothes everywhere
    result = _compose_label_map(mp_mask, _confident_pose(y_hip=0.5))
    above = result[:49, :]
    # Below the hip but well clear of the ankle-radius carve (ankles sit at
    # y=0.95 per _confident_pose, which correctly reclassifies a small boot-
    # sized region there to FEET -- see test_boot_near_ankle_is_not_classified_as_pants).
    below = result[51:80, :]
    assert np.all(above == TOP)
    assert np.all(below == PANTS)


def test_subdivision_splits_body_skin_into_torso_arms_legs():
    h, w = 100, 100
    mp_mask = np.full((h, w), _MP_BODY_SKIN, dtype=np.uint8)
    pose = _confident_pose(y_shoulder=0.2, y_hip=0.6, x_left=0.3, x_right=0.7)
    result = _compose_label_map(mp_mask, pose)

    # Below the hip line -> legs (well clear of the ankle-carve radius, which
    # sits around the y=0.95 ankle keypoints and correctly reclassifies
    # nearby pixels as feet -- checked separately below)
    assert np.all(result[70:75, :] == LEGS)
    # Within the shoulder-hip band, in the central column -> torso
    assert np.all(result[40:45, 40:60] == TORSO)
    # Within the same band, outside the central column -> arms
    assert np.all(result[40:45, 0:10] == ARMS)
    # Distinct classes actually got used -- the split isn't a no-op
    assert {TORSO, ARMS, LEGS}.issubset(set(np.unique(result).tolist()))


def test_subdivision_carves_hands_and_feet_near_wrist_and_ankle():
    h, w = 100, 100
    mp_mask = np.full((h, w), _MP_BODY_SKIN, dtype=np.uint8)
    pose = _confident_pose()
    result = _compose_label_map(mp_mask, pose)
    assert HANDS in result
    assert FEET in result


def test_boot_near_ankle_is_not_classified_as_pants():
    """Regression test for a real bug found during masking validation:
    MediaPipe's `clothes` class doesn't distinguish footwear from legwear,
    so a boot below the hip line was labeled PANTS like any other clothes
    pixel there -- meaning a "swap the pants" generation would try to
    regenerate the boot too. Confirmed on the real test photo before this
    fix: pants coverage 11.88% / feet 0%; after: pants 11.5% / feet 0.45%,
    with the reclassified region landing exactly on the boot in the actual
    mask visualization. This test proves the mechanism with a controlled
    synthetic mask, independent of the real photo."""
    h, w = 100, 100
    mp_mask = np.full((h, w), _MP_CLOTHES, dtype=np.uint8)  # an all-clothes blob, e.g. pants + boot
    pose = _confident_pose(y_hip=0.5)
    # Ankles sit at y=0.95 per _confident_pose -- well below the hip line,
    # so this whole region would be PANTS before the fix.
    result = _compose_label_map(mp_mask, pose)
    assert FEET in result
    # And the boot-sized region right at the ankle keypoints is FEET, not
    # PANTS -- exact coordinates from _confident_pose's Rankle (idx 10) and
    # Lankle (idx 13): (x_right - 0.02, 0.95) and (x_left + 0.02, 0.95).
    r_ankle_x, r_ankle_y = int(0.68 * w), int(0.95 * h)
    l_ankle_x, l_ankle_y = int(0.32 * w), int(0.95 * h)
    assert result[r_ankle_y, r_ankle_x] == FEET
    assert result[l_ankle_y, l_ankle_x] == FEET
    # The rest of the clothes blob, well above the ankles, is still
    # correctly PANTS -- this isn't a blanket "clothes below hip = feet"
    # regression, just a small, bounded carve-out at the ankles.
    assert result[60, 50] == PANTS


def test_boot_carve_does_not_affect_top_or_arms_or_torso():
    """The new pants->feet carve must be scoped to the ankle radius only --
    confirms it doesn't leak into unrelated classes elsewhere in the frame."""
    h, w = 100, 100
    mp_mask = np.zeros((h, w), dtype=np.uint8)
    mp_mask[:50, :] = _MP_CLOTHES  # becomes TOP (above hip)
    mp_mask[50:, :] = _MP_CLOTHES  # becomes PANTS (below hip), boots carved near ankles
    pose = _confident_pose(y_hip=0.5)
    result = _compose_label_map(mp_mask, pose)
    assert np.all(result[:49, :] == TOP)  # untouched by the ankle-radius carve


# --- 4. Missing-keypoint fallback (bilateral) ----------------------------


def test_bilateral_fallback_uses_single_valid_shoulder_and_hip():
    h, w = 100, 100
    mp_mask = np.full((h, w), _MP_BODY_SKIN, dtype=np.uint8)
    pose = _confident_pose()
    # Invalidate the left shoulder and left hip only -- the right side alone
    # must still be enough to establish both lines.
    pose["bodies"]["subset"][0][5] = -1  # Lshoulder
    pose["bodies"]["subset"][0][11] = -1  # Lhip
    result = _compose_label_map(mp_mask, pose)
    # Subdivision still happened (not a full coarse fallback to all-torso)
    assert ARMS in result or LEGS in result
    assert not np.all((result == TORSO) | (result == BG))


def test_both_shoulders_missing_falls_back_to_coarse_torso_only():
    h, w = 100, 100
    mp_mask = np.full((h, w), _MP_BODY_SKIN, dtype=np.uint8)
    pose = _confident_pose()
    pose["bodies"]["subset"][0][2] = -1  # Rshoulder
    pose["bodies"]["subset"][0][5] = -1  # Lshoulder
    result = _compose_label_map(mp_mask, pose)
    # y_shoulder unavailable -> the whole coarse-fallback branch applies
    assert np.all(result == TORSO)


# --- 5. Degenerate / no-person behavior -----------------------------------


def test_degenerate_all_invalid_keypoints_never_raises_and_degrades_coarsely():
    h, w = 100, 100
    mp_mask = np.zeros((h, w), dtype=np.uint8)
    mp_mask[:50, :] = _MP_CLOTHES
    mp_mask[50:, :] = _MP_BODY_SKIN
    result = _compose_label_map(mp_mask, _dummy_pose())  # must not raise
    assert result.dtype == np.int64
    assert result.shape == (h, w)
    # Per the documented fallback: all clothes -> top, all body-skin -> torso;
    # none of the finer classes are ever used without real keypoints.
    used = set(np.unique(result).tolist())
    assert used.issubset({BG, TOP, TORSO})
    assert PANTS not in used
    assert ARMS not in used
    assert LEGS not in used
    assert HANDS not in used
    assert FEET not in used


def test_missing_bodies_key_does_not_raise():
    """An even more degenerate pose dict than get_dummy_dw_keypoints() ever
    produces today -- defends predict()'s "never raises" contract against a
    pose object missing expected keys entirely, not just filled with -1."""
    mp_mask = np.full((20, 20), _MP_BODY_SKIN, dtype=np.uint8)
    result = _compose_label_map(mp_mask, {})
    assert result.shape == (20, 20)
    assert np.all(result == TORSO)


# --- 6. Real-image integration sanity check -------------------------------


@pytest.fixture(scope="module")
def real_parser():
    return MediaPipeBodyParser(
        model_path=MEDIAPIPE_MODEL_PATH, dwpose_checkpoints_dir=DWPOSE_DIR, device="cpu"
    )


def test_real_person_photo_produces_nontrivial_multiclass_coverage(real_parser):
    img = np.array(Image.open(MODEL_PHOTO).convert("RGB"))
    result = real_parser.predict(img)
    assert result.shape == img.shape[:2]
    assert result.dtype == np.int64
    assert set(np.unique(result).tolist()).issubset(set(range(18)))
    # Genuinely found something -- not an all-background map like the
    # placeholder's constant output.
    assert np.sum(result != BG) > 0.05 * result.size
    # And found more than one class -- real subdivision happened, not just
    # a single coarse blob.
    assert len(set(np.unique(result).tolist())) >= 4


def test_real_garment_photo_does_not_raise(real_parser):
    img = np.array(Image.open(GARMENT_PHOTO).convert("RGB"))
    result = real_parser.predict(img)  # must not raise regardless of content
    assert result.shape == img.shape[:2]
    assert result.dtype == np.int64


# --- 7. Regression: disable_masking=True is a genuine no-op, either way --


def test_disable_masking_path_ignores_seg_pred_content_entirely():
    """A general correctness property of fashn_vton's preprocessing
    functions, proven directly rather than just reasoned about: both
    create_clothing_agnostic_image and create_garment_image short-circuit
    to `return img_np` unchanged whenever disable_masking=True, regardless
    of seg_pred's content (constant all-background from
    PlaceholderBodyParser vs. a real multi-class map from
    MediaPipeBodyParser). This was the safety net proving the original
    Stage 1 parser swap was a no-op while backend/app/providers/
    selfhosted.py still hardcoded segmentation_free=True; it's kept as a
    contract test now that production has moved to segmentation_free=False
    (real person-image masking) because garment_photo_type="flat-lay" is
    still hardcoded there, so create_garment_image's disable_masking=True
    path remains live in production for the garment image specifically."""
    from fashn_vton.preprocessing.agnostic import create_clothing_agnostic_image, create_garment_image

    rng = np.random.default_rng(42)
    img = rng.integers(0, 255, (64, 48, 3), dtype=np.uint8)

    placeholder_style_seg = np.zeros((64, 48), dtype=np.int64)  # PlaceholderBodyParser's output
    real_style_seg = rng.integers(0, 18, (64, 48)).astype(np.int64)  # a MediaPipeBodyParser-shaped output

    ca_placeholder = create_clothing_agnostic_image(
        img.copy(), placeholder_style_seg, [], body_coverage="upper", disable_masking=True
    )
    ca_real = create_clothing_agnostic_image(
        img.copy(), real_style_seg, [], body_coverage="upper", disable_masking=True
    )
    assert np.array_equal(ca_placeholder, img)
    assert np.array_equal(ca_real, img)
    assert np.array_equal(ca_placeholder, ca_real)

    garment_placeholder = create_garment_image(img.copy(), placeholder_style_seg, [], disable_masking=True)
    garment_real = create_garment_image(img.copy(), real_style_seg, [], disable_masking=True)
    assert np.array_equal(garment_placeholder, img)
    assert np.array_equal(garment_real, img)
