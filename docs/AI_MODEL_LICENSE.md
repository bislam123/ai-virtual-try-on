# AI Model License Documentation

This file is the single source of truth for which AI models AI Try-On uses at runtime, and why each one is legally safe for commercial use. It must be kept up to date any time a model or preprocessing component changes.

**Last reviewed:** 2026-09-15 (Milestone 1)
**Reviewed by:** Claude Code, at the direction of the project owner

---

## Policy

Per the project's non-negotiable requirements:

- No paid AI inference API may ever be a runtime dependency (OpenAI, Gemini, Replicate paid tiers, Stability paid tiers, FASHN's *hosted* API, etc.).
- Every model actually shipped in production must be self-hostable and licensed for commercial use — not "free to try," not "research only," not "non-commercial."
- A model is disqualified if its code, its weights, *or* any preprocessing component it unconditionally depends on at inference time is non-commercial, even if the headline license badge looks permissive.

---

## Chosen model

### Core generative model — FASHN VTON v1.5

| | |
|---|---|
| Source | https://github.com/fashn-AI/fashn-vton-1.5 |
| Weights | https://huggingface.co/fashn-ai/fashn-vton-1.5 (`model.safetensors`, ~1.94 GB) |
| Architecture | MMDiT (Multimodal Diffusion Transformer), 972M parameters, pixel-space, maskless/segmentation-free generation |
| Code license | Apache License 2.0, Copyright 2025 FASHN AI — verified via raw `LICENSE` file in the repo |
| Weights license | Apache-2.0 (HuggingFace model card) |
| Commercial use | **Permitted.** Apache-2.0 has no field-of-use or non-commercial restriction. |

**Relationship to FASHN's paid product:** FASHN AI also sells a hosted try-on API. That paid API is explicitly out of scope for this project per the non-negotiable requirements and **is not used anywhere in this codebase.** We use only their separately, explicitly Apache-2.0-licensed open-weight release, downloaded once and run entirely on our own infrastructure. No network call to any FASHN-operated service is made by the production inference path. If this distinction ever becomes uncomfortable (e.g. branding concerns), the fallback model below can be substituted without changing the application architecture, because inference is abstracted behind `VirtualTryOnProvider`.

### Bundled pose preprocessing — DWPose

| | |
|---|---|
| Source | https://github.com/IDEA-Research/DWPose |
| License | Apache License 2.0 |
| Commercial use | Permitted. Used as shipped, no substitution needed. |

### ⚠️ Component that had to be replaced — FASHN Human Parser

FASHN VTON v1.5's reference pipeline (`src/fashn_vton/pipeline.py`) unconditionally calls `fashn_human_parser.FashnHumanParser.predict()` on both the person and garment image on every inference call — this happens even when running in the default "maskless / segmentation-free" mode. The `segmentation_free` flag only controls whether the *predicted* segmentation is used to constrain generation; it does not stop the parser model from running.

`fashn-human-parser` is a SegFormer-B4 fine-tune. SegFormer's original release (NVlabs/SegFormer) is licensed under the **NVIDIA Source Code License**, which states:

> "The Work and any derivative works thereof only may be used or intended for use non-commercially. Notwithstanding the foregoing, NVIDIA and its affiliates may use the Work and any derivative works commercially."

That is an explicit third-party non-commercial restriction — confirmed by reading `fashn-human-parser`'s own `LICENSE` file, which states it "inherits the NVIDIA Source Code License for SegFormer." **This component cannot be used in a commercial deployment.**

**Resolution:** AI Try-On does not install or call `fashn-human-parser`. It is replaced with a commercially-licensed body/garment segmentation model performing the equivalent role (producing the label map `pipeline.py` expects from `hp_model.predict()`):

| | |
|---|---|
| Replacement | U2Net-based cloth/body segmentation (Apache-2.0/MIT forks of `xuebinqin/U-2-Net`, base repo Apache-2.0 — verified) — or Meta's Segment Anything 2 (Apache-2.0, verified) if finer garment boundaries are needed |
| Commercial use | Permitted |
| Integration | See `ai/preprocessing/` — a thin adapter maps our segmentation model's output to the label schema (`CATEGORY_TO_BODY_COVERAGE`, `FASHN_LABELS_TO_IDS`) that the rest of the FASHN pipeline expects, so the generative model and DWPose are used unmodified. |

---

## Models evaluated and rejected

All of the following are widely recommended in "best open-source virtual try-on" articles, and all were rejected because their code **and** weights are explicitly non-commercial (CC BY-NC-SA 4.0), verified by reading each repository's `LICENSE` file directly rather than trusting README summaries:

| Model | License | Why rejected |
|---|---|---|
| [IDM-VTON](https://github.com/yisol/IDM-VTON) (ECCV 2024) | CC BY-NC-SA 4.0 | Non-commercial only; open GitHub issues show the authors have been asked for commercial terms with no public resolution |
| [OOTDiffusion](https://github.com/levihsu/OOTDiffusion) | CC BY-NC-SA 4.0 | Non-commercial only |
| [CatVTON](https://github.com/Zheng-Chong/CatVTON) (ICLR 2025) | CC BY-NC-SA 4.0 | Non-commercial only |
| [StableVITON](https://github.com/rlawjdghek/StableVITON) (CVPR 2024) | CC BY-NC-SA 4.0 | Non-commercial only |

### Backup candidate — Leffa

| | |
|---|---|
| Source | https://github.com/franciszzj/Leffa (CVPR 2025) |
| Code license | **MIT**, copyright Zijian Zhou — verified via raw `LICENSE` |
| Weights license | **MIT** — verified via HuggingFace `license: mit` frontmatter tag, no additional restriction in the model card |
| Trained on | VITON-HD, DressCode (virtual try-on), DeepFashion (pose transfer) |
| Blocking dependency | Reference pipeline (`densepose_predictor.py`) requires **facebookresearch/DensePose**, licensed **CC BY-NC** (verified via raw `LICENSE`) — non-commercial |

Leffa's core code and weights are commercially clean and this remains a valid fallback, but DensePose's dense body-surface correspondence is more central to Leffa's "flow fields in attention" method than the human parser is to FASHN's pipeline, so swapping it carries more quality risk. **Not chosen as primary for that reason** — kept as the documented Plan B if the FASHN parser substitution underperforms.

Other rejected preprocessing dependencies found along the way, for the record:

| Component | License | Note |
|---|---|---|
| CMU OpenPose | Academic/non-commercial (commercial license available from CMU for a $25,000/yr royalty) | Not used; DWPose (Apache-2.0) is the pose estimator in the chosen pipeline |
| facebookresearch/DensePose | CC BY-NC | Not used (see Leffa above) |

---

## Architectural safeguard

All inference is called through a `VirtualTryOnProvider` interface (see `ARCHITECTURE.md`); the application code never talks to a specific model directly. If a license changes upstream, or a better commercially-clean model appears, only the provider implementation changes — not the API contract, the frontend, or the database schema.

## Re-review triggers

This document must be re-checked before:
- Upgrading `fashn-vton-1.5` or its pinned dependencies to a new version
- Adding any new preprocessing/conditioning model to the pipeline
- Any change to the segmentation replacement component
- Production launch, and at least once every 12 months thereafter (upstream licenses can change)
