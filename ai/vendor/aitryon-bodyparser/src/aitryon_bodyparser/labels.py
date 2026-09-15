"""Label schema for AI Try-On's body/garment parser.

This is our own definition of the label taxonomy that fashn_vton's preprocessing
code (preprocessing/agnostic.py) expects an `hp_model` to speak: a set of named
regions (background, garment types, body parts) each mapped to an integer id,
plus a couple of small lookup tables built on top of it. A label-name-to-integer
table like this is a functional interoperability schema, not creative expression
of anyone else's — we're free to define our own copy of it. It happens to use
the same names/ids as fashn-human-parser's schema (see docs/AI_MODEL_LICENSE.md)
purely so it's a drop-in replacement for fashn_vton's *unmodified*, Apache-2.0
pipeline code; nothing here is copied from, or executes, fashn-human-parser itself.
"""

from typing import Dict, List

IDS_TO_LABELS: Dict[int, str] = {
    0: "background",
    1: "face",
    2: "hair",
    3: "top",
    4: "dress",
    5: "skirt",
    6: "pants",
    7: "belt",
    8: "bag",
    9: "hat",
    10: "scarf",
    11: "glasses",
    12: "arms",
    13: "hands",
    14: "legs",
    15: "feet",
    16: "torso",
    17: "jewelry",
}

LABELS_TO_IDS: Dict[str, int] = {v: k for k, v in IDS_TO_LABELS.items()}

CATEGORY_TO_BODY_COVERAGE: Dict[str, str] = {
    "tops": "upper",
    "bottoms": "lower",
    "one-pieces": "full",
}

BODY_COVERAGE_TO_LABELS: Dict[str, List[str]] = {
    "upper": ["top", "dress", "scarf"],
    "lower": ["skirt", "pants", "belt"],
    "full": ["top", "dress", "scarf", "skirt", "pants", "belt"],
}

IDENTITY_LABELS: List[str] = ["face", "hair", "jewelry", "bag", "glasses", "hat"]
