# Development

## Milestone plan & status

| # | Milestone | Status |
|---|---|---|
| 1 | Hardware & AI model evaluation | ✅ Done |
| 2 | AI model installation & first test inference | 🚧 In progress |
| 3 | AI inference FastAPI service (`POST /api/try-on`) | ⬜ Not started |
| 4 | Mobile-first web UI | ⬜ Not started |
| 5 | Connect frontend to AI backend | ⬜ Not started |
| 6 | User accounts & secure image handling | ⬜ Not started |
| 7 | Product image extraction | ⬜ Not started |
| 8 | Product URL support | ⬜ Not started |
| 9 | Browser extension | ⬜ Not started |
| 10 | PWA / mobile optimization | ⬜ Not started |
| 11 | Usage limits & premium architecture | ⬜ Not started |
| 12 | Payments / in-app purchases | ⬜ Not started |
| 13 | Production deployment | ⬜ Not started |

## Hardware this project was developed on

- Windows 11 Home, AMD Ryzen 3 7330U (4C/8T), 15 GB RAM, AMD integrated graphics (no CUDA).
- **No GPU acceleration available on this machine.** All local AI work runs on CPU. This is fine for development/correctness testing but too slow for production — see [ARCHITECTURE.md](ARCHITECTURE.md#dev-environment-vs-inference-environment) for the planned split.

## Python versions — do not break the system default

This machine's default `python`/`py` is **3.14.7** — left completely untouched. The AI environment uses a **separate Python 3.11.9**, installed side-by-side via:

```powershell
winget install --id Python.Python.3.11 -e --scope user --silent --override "/quiet InstallAllUsers=0 PrependPath=0 Include_launcher=1"
```

This does **not** add 3.11 to PATH or change what `python` resolves to system-wide; it's only reachable via its full path or the `py -3.11` launcher. Why 3.11: it's what `fashn-vton-1.5` is built and tested against (`requires-python = ">=3.10"`, tested on 3.10–3.12); the wider ML dependency chain (onnxruntime, opencv-python, etc.) has solid prebuilt Windows wheels for it. Python 3.14 is not used for AI work — PyTorch itself added 3.14 support recently, but several smaller pinned dependencies in this chain don't reliably ship 3.14 wheels yet.

> **Gotcha hit during setup:** the first `winget install` attempt hung indefinitely because the Windows Installer service (`msiserver`) was stopped and the sandboxed session couldn't start it implicitly. Fix: `Start-Service msiserver` once, then retry the winget install — it completes in under a minute.

## AI environment setup (from scratch)

```powershell
cd "ai"
& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
.venv\Scripts\python.exe -m pip install -e ".\vendor\aitryon-bodyparser"
.venv\Scripts\python.exe -m pip install -e ".\vendor\fashn-vton-1.5"
.venv\Scripts\python.exe inference\download_weights.py --weights-dir "models\fashn-vton-1.5"
```

Notes:
- `torch`/`torchvision` are installed from PyTorch's dedicated **CPU** wheel index — installing the default `pip install torch` would pull ~2.5GB of unneeded CUDA runtime libraries this machine can never use.
- `ai/vendor/fashn-vton-1.5` is a vendored, locally-modified clone of https://github.com/fashn-AI/fashn-vton-1.5 (Apache-2.0). The only modifications: `pyproject.toml` no longer depends on `fashn-human-parser`, and two files (`pipeline.py`, `preprocessing/agnostic.py`) import our own `aitryon_bodyparser` package instead. See [AI_MODEL_LICENSE.md](AI_MODEL_LICENSE.md) for why, and the module docstring in `ai/vendor/aitryon-bodyparser/src/aitryon_bodyparser/parser.py` for exactly when that placeholder stops being valid and must be upgraded to a real segmentation model.
- `ai/vendor/aitryon-bodyparser` is our own small MIT-licensed package — not a fork of anything.
- Weights land in `ai/models/fashn-vton-1.5/` (gitignored — ~2GB, never commit).

## Running the first test inference

```powershell
cd "ai"
.venv\Scripts\python.exe inference\test_first_tryon.py
```

Uses fashn-vton-1.5's own bundled example images (a person photo + a garment photo) and writes `ai/outputs/milestone2_first_tryon.png`. Expect this to be slow — this machine has no GPU; see the script's timing output for the actual measured figure once it's run to completion.

## Known limitation carried into Milestone 3

`aitryon_bodyparser.PlaceholderBodyParser` always returns "background" — it is not a real segmentation model. It's provably safe only when the pipeline runs with `segmentation_free=True` and `garment_photo_type="flat-lay"` (our actual MVP scenario: a person photo + a plain clothing/product photo). Before we support masked mode or "garment worn by another person" product photos, it must be replaced with a real commercially-licensed segmentation model. Candidates and why: see [AI_MODEL_LICENSE.md](AI_MODEL_LICENSE.md).
