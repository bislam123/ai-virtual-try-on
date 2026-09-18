# AI Model License Documentation

This file is the single source of truth for which AI models AI Try-On uses at runtime, and why each one is legally safe for commercial use. It must be kept up to date any time a model or preprocessing component changes.

**Last reviewed:** 2026-09-17 (clothing-agnostic mask fix for bent/seated poses; segmentation replacement, Stage 1: MediaPipe + DWPose)
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

**Resolution — Stage 1, implemented:** AI Try-On does not install or call `fashn-human-parser`. `PlaceholderBodyParser` (still present, `ai/vendor/aitryon-bodyparser/`, kept as a fallback/test double) has been replaced as the pipeline's default human-parsing model by `MediaPipeBodyParser` (`ai/preprocessing/`), which combines two independently-licensed, verified commercially-clean models:

| | |
|---|---|
| Component | Google MediaPipe Image Segmenter — `selfie_multiclass_256x256` |
| Source | https://ai.google.dev/edge/mediapipe/solutions/vision/image_segmenter (Google AI Edge / MediaPipe Solutions); weight file downloaded from `https://storage.googleapis.com/mediapipe-models/image_segmenter/selfie_multiclass_256x256/float32/latest/selfie_multiclass_256x256.tflite` |
| Code license | Apache-2.0 (MediaPipe framework and Python package, `mediapipe` on PyPI — verified via the package's own wheel metadata) |
| Model license | Apache-2.0 — Google-trained on Google's own data, **not** a fine-tune of a third-party academic dataset (unlike the rejected U2Net path below), so there's no inherited-dataset licensing ambiguity to track |
| Commercial use | Permitted |
| Role | Primary segmentation: background / hair / body-skin / face-skin / clothes / other (6 classes) |
| Weight storage | `ai/models/mediapipe/selfie_multiclass_256x256.tflite` — downloaded once, gitignored (`ai/models/**/*.tflite`), same "download once, run locally forever after, no runtime network dependency" pattern already used for the FASHN and DWPose weights |

| | |
|---|---|
| Component | DWPose (already documented above — reused, not a new dependency) |
| Role | Provides body keypoints (shoulder/hip/wrist/ankle) used only to *geometrically subdivide* MediaPipe's coarse `body-skin`/`clothes` classes into the finer classes `preprocessing/agnostic.py` expects (`torso`/`arms`/`hands`/`legs`/`feet`, `top`/`pants`). Never used to classify *what* something is beyond that geometry — the API's own `category` field already carries that information downstream. |

**Candidates investigated and explicitly rejected for this role:**

| Candidate | Verdict |
|---|---|
| U2Net cloth segmentation (`levindabhi/cloth-segmentation`) | Code MIT, but its checkpoint is trained on the iMaterialist Fashion 2019/FGVC6 Kaggle dataset, whose terms restrict use to "non-commercial research and educational purposes" (verified via the competition's own rules page). Same disqualifying pattern as SCHP/LIP-ATR/SegFormer above — a permissive code license does not launder a non-commercial training-data restriction. Not used. |
| SCHP / LIP-ATR-family human parsers | Academic-dataset-trained (LIP/ATR/CIHP), same non-commercial pattern; not investigated further given the established precedent above. |
| Meta Segment Anything 2 (SAM 2 / 2.1) | Apache-2.0, code and weights — genuinely commercially clean, and would remain a valid option. **Deliberately deferred, not rejected** — SAM2 is a promptable, class-agnostic segmenter (would need prompts derived from DWPose geometry, more integration work for uncertain quality gain over the Stage 1 approach). Stage 1 does not use it. A candidate Stage 2. |

**Known Stage 1 limitations, carried forward on purpose** (see `ai/preprocessing/src/aitryon_preprocessing/mediapipe_parser.py`'s own docstring for the full reasoning): the `top`/`pants` split is a single horizontal line through the hip keypoints — validated against a real seated/bent-knee test photo where it held up, but a pose with a knee raised above hip height (crossed legs, hugging a knee) would misclassify that region; and `dress`/`skirt`/`belt`/`scarf`/`bag`/`hat`/`glasses`/`jewelry` are permanently unused label ids in Stage 1 (verified against the actual consuming code in `agnostic.py` that this is safe — those ids only ever appear in identity-*exclusion* sets, never in anything actively masked, so leaving them unused degrades gracefully rather than breaking).

**Activated in production, 2026-09-16** (commits `19aeec3`, `5b74a58` — corrected here 2026-09-19 after a documentation-hygiene audit found this section had gone stale): `backend/app/providers/selfhosted.py` now passes `segmentation_free=False` on every request — `MediaPipeBodyParser`'s output constrains generation for every category (tops/bottoms/one-pieces), it is no longer computed and discarded. `backend/app/api/tryon.py`'s `VALID_GARMENT_PHOTO_TYPES` also now accepts `"model"` (a garment already worn by another person in the source photo) alongside `"flat-lay"`, using the same masking approach on the garment image. Both use the same Stage 1 components documented above — no new model, no new licensing question. **Known gap, stated honestly rather than assumed away**: unlike the clothing-agnostic mask fix below (quantitative before/after measurements, regression tests in `tests/test_clothing_agnostic_mask.py`), there is no equivalent documented quality benchmark for `garment_photo_type="model"` output specifically — `tests/test_garment_photo_type_plumbing.py` and similar cover plumbing/wiring/error-handling, not generated-image quality. Treat model-worn output quality as unvalidated until a benchmark like the flat-lay one exists.

**Fixed: clothing-agnostic mask covering background on bent/seated poses.** FASHN VTON's own upstream `create_clothing_agnostic_image` (`ai/vendor/fashn-vton-1.5/src/fashn_vton/preprocessing/agnostic.py`/`masks.py`, unmodified logic until this fix) builds a "hybrid" mask from a bounding-box-filled region trimmed back toward a tight contour-following mask. Diagnosed against this project's own `MediaPipeBodyParser` output on the bundled seated/bent-knee example photo (`ai/vendor/fashn-vton-1.5/examples/data/model.webp`): (1) `create_bounded_mask` took one global bounding box over the whole mask, so a hand resting on a knee — disjoint in pixel space from the torso/arm region — stretched the box to span the empty background between them; (2) the trim's `min_distance_threshold` (100px at the 864px baseline height) was generous enough that, for an elongated/non-convex bent-pose silhouette, almost none of that oversized box actually got trimmed, so the resulting mask covered large swaths of true background (up to ~72% of the frame for `category="one-pieces"`, verified empirically, before any fix). Fixed by (a) bounding each connected component of the mask separately instead of one global box, and (b) lowering the default threshold to 5px. Note on (b): concave regions (e.g. the gap beside a bent leg) are Euclidean-close to the contour even though they're clearly not part of the body, so even 10px still let a visible rectangular artifact through in testing — 5px was the largest value with no visible artifact remaining, verified by rendering the actual masked image, not just measuring area. Both changes verified against the same real photo across `tops`/`bottoms`/`one-pieces`. See the regression tests in `tests/test_clothing_agnostic_mask.py`.

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
