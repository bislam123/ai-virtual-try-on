#!/usr/bin/env python3
"""Milestone 2 smoke test: person photo + garment photo -> a first try-on image.

Uses fashn-vton-1.5's own bundled example images (examples/data/model.webp,
examples/data/garment.webp), which they publish specifically to demonstrate
this exact call. Run with garment_photo_type="flat-lay" and the default
segmentation_free=True: in that combination fashn_vton's own preprocessing
code never reads the human-parser output (see
ai/vendor/aitryon-bodyparser/src/aitryon_bodyparser/parser.py for the exact
lines proving that), so our commercially-clean PlaceholderBodyParser is a
correct stand-in for this test, not a quality-degrading shortcut.

Usage (from the ai/ directory):
    .venv\\Scripts\\python.exe inference\\test_first_tryon.py
"""

import time
from pathlib import Path

from PIL import Image

from fashn_vton import TryOnPipeline

AI_DIR = Path(__file__).resolve().parent.parent
WEIGHTS_DIR = AI_DIR / "models" / "fashn-vton-1.5"
PERSON_IMAGE = AI_DIR / "vendor" / "fashn-vton-1.5" / "examples" / "data" / "model.webp"
GARMENT_IMAGE = AI_DIR / "vendor" / "fashn-vton-1.5" / "examples" / "data" / "garment.webp"
OUTPUT_DIR = AI_DIR / "outputs"


def main():
    for path in (PERSON_IMAGE, GARMENT_IMAGE):
        if not path.exists():
            raise FileNotFoundError(path)

    print(f"Loading pipeline from {WEIGHTS_DIR} (device=cpu) ...")
    t0 = time.time()
    pipeline = TryOnPipeline(weights_dir=str(WEIGHTS_DIR), device="cpu")
    print(f"Pipeline loaded in {time.time() - t0:.1f}s")

    person_image = Image.open(PERSON_IMAGE).convert("RGB")
    garment_image = Image.open(GARMENT_IMAGE).convert("RGB")

    print("Running inference (num_timesteps=20 for a faster first test on CPU)...")
    t0 = time.time()
    result = pipeline(
        person_image=person_image,
        garment_image=garment_image,
        category="tops",
        garment_photo_type="flat-lay",  # see module docstring: keeps the placeholder parser valid
        segmentation_free=True,
        num_timesteps=20,
        seed=42,
    )
    elapsed = time.time() - t0
    print(f"Inference finished in {elapsed:.1f}s ({elapsed / 20:.2f}s/step)")

    OUTPUT_DIR.mkdir(exist_ok=True)
    out_path = OUTPUT_DIR / "milestone2_first_tryon.png"
    result.images[0].save(out_path)
    print(f"Saved: {out_path}")


if __name__ == "__main__":
    main()
