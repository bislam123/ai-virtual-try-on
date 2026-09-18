#!/usr/bin/env python3
"""Download AI Try-On's model weights (Apache-2.0 components only).

Unlike the upstream fashn-vton-1.5/scripts/download_weights.py, this script
never touches fashn-human-parser. See docs/AI_MODEL_LICENSE.md.

Usage:
    ai\\.venv\\Scripts\\python.exe ai\\inference\\download_weights.py --weights-dir ai\\models\\fashn-vton-1.5
"""

import argparse
import os
import urllib.error
import urllib.request

from huggingface_hub import hf_hub_download

# Same URL docs/AI_MODEL_LICENSE.md already documents as the approved source
# for this component (Google MediaPipe Image Segmenter, Apache-2.0) -- kept
# as one named constant so the script and the docs can never quietly drift
# apart on which URL is "the" approved one.
MEDIAPIPE_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/image_segmenter/"
    "selfie_multiclass_256x256/float32/latest/selfie_multiclass_256x256.tflite"
)


def download_tryon_model(weights_dir: str) -> None:
    print("Downloading TryOnModel weights (fashn-ai/fashn-vton-1.5, Apache-2.0)...")
    path = hf_hub_download(
        repo_id="fashn-ai/fashn-vton-1.5",
        filename="model.safetensors",
        local_dir=weights_dir,
    )
    print(f"  Saved to: {path}")


def download_dwpose_models(weights_dir: str) -> None:
    dwpose_dir = os.path.join(weights_dir, "dwpose")
    os.makedirs(dwpose_dir, exist_ok=True)
    print("Downloading DWPose weights (fashn-ai/DWPose, Apache-2.0)...")
    for filename in ["yolox_l.onnx", "dw-ll_ucoco_384.onnx"]:
        path = hf_hub_download(repo_id="fashn-ai/DWPose", filename=filename, local_dir=dwpose_dir)
        print(f"  Saved to: {path}")


def download_mediapipe_model(models_root: str) -> None:
    """Downloads Google's MediaPipe Image Segmenter (selfie_multiclass_256x256,
    Apache-2.0) -- see docs/AI_MODEL_LICENSE.md for the full licensing
    rationale. `models_root` is the ai/models/ directory itself (a sibling
    of --weights-dir's fashn-vton-1.5/ subdirectory, not nested under it --
    see fashn_vton.pipeline's own os.path.dirname(self.weights_dir) lookup).

    Uses only the stdlib (urllib.request) -- this is the only place in the
    project that would otherwise need a new dependency just for one file
    download, so it deliberately doesn't add one.
    """
    mediapipe_dir = os.path.join(models_root, "mediapipe")
    os.makedirs(mediapipe_dir, exist_ok=True)
    dest_path = os.path.join(mediapipe_dir, "selfie_multiclass_256x256.tflite")

    print(f"Downloading MediaPipe Image Segmenter weights (Apache-2.0) from {MEDIAPIPE_MODEL_URL} ...")
    try:
        urllib.request.urlretrieve(MEDIAPIPE_MODEL_URL, dest_path)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        raise RuntimeError(f"Failed to download MediaPipe weights from {MEDIAPIPE_MODEL_URL}: {exc}") from exc

    if not os.path.exists(dest_path) or os.path.getsize(dest_path) == 0:
        raise RuntimeError(f"MediaPipe weight download produced an empty or missing file: {dest_path}")

    print(f"  Saved to: {dest_path} ({os.path.getsize(dest_path):,} bytes)")


def main():
    parser = argparse.ArgumentParser(description="Download AI Try-On model weights")
    parser.add_argument("--weights-dir", type=str, required=True)
    parser.add_argument(
        "--dwpose-only",
        action="store_true",
        help=(
            "Skip the ~1.94GB TryOnModel diffusion weights -- download only the two DWPose "
            "ONNX models. For environments (e.g. CI) that only run DWPose-dependent tests "
            "and never construct a full TryOnPipeline."
        ),
    )
    parser.add_argument(
        "--mediapipe",
        action="store_true",
        help=(
            "Also download the MediaPipe selfie_multiclass_256x256 segmenter weights into "
            "ai/models/mediapipe/ (a sibling of --weights-dir, not nested under it). "
            "Combinable with --dwpose-only."
        ),
    )
    args = parser.parse_args()

    weights_dir = os.path.abspath(args.weights_dir)
    os.makedirs(weights_dir, exist_ok=True)
    print(f"Downloading weights to: {weights_dir}\n")

    if args.dwpose_only:
        download_dwpose_models(weights_dir)
    else:
        download_tryon_model(weights_dir)
        print()
        download_dwpose_models(weights_dir)

    if args.mediapipe:
        print()
        download_mediapipe_model(os.path.dirname(weights_dir))

    if args.dwpose_only:
        print(
            "\nDone (--dwpose-only): the TryOnModel diffusion weights were NOT downloaded. "
            "Note: no human-parser weights were downloaded either -- this project uses "
            "aitryon_bodyparser.PlaceholderBodyParser instead (see docs/AI_MODEL_LICENSE.md)."
        )
    else:
        print(
            "\nDone. Note: no human-parser weights were downloaded — this project uses "
            "aitryon_bodyparser.PlaceholderBodyParser instead (see docs/AI_MODEL_LICENSE.md)."
        )


if __name__ == "__main__":
    main()
