#!/usr/bin/env python3
"""Download AI Try-On's model weights (Apache-2.0 components only).

Unlike the upstream fashn-vton-1.5/scripts/download_weights.py, this script
never touches fashn-human-parser. See docs/AI_MODEL_LICENSE.md.

Usage:
    ai\\.venv\\Scripts\\python.exe ai\\inference\\download_weights.py --weights-dir ai\\models\\fashn-vton-1.5
"""

import argparse
import os

from huggingface_hub import hf_hub_download


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


def main():
    parser = argparse.ArgumentParser(description="Download AI Try-On model weights")
    parser.add_argument("--weights-dir", type=str, required=True)
    args = parser.parse_args()

    weights_dir = os.path.abspath(args.weights_dir)
    os.makedirs(weights_dir, exist_ok=True)
    print(f"Downloading weights to: {weights_dir}\n")

    download_tryon_model(weights_dir)
    print()
    download_dwpose_models(weights_dir)

    print(
        f"\nDone. Note: no human-parser weights were downloaded — this project uses "
        f"aitryon_bodyparser.PlaceholderBodyParser instead (see docs/AI_MODEL_LICENSE.md)."
    )


if __name__ == "__main__":
    main()
