# Architecture

This document captures the foundational design decisions made so far. It will grow milestone by milestone — it is not a spec for an unbuilt system, only a record of what's actually decided and why.

## Guiding principle: no vendor lock-in on AI

```
Frontend (React PWA)
    ↓ HTTPS/JSON
Backend (FastAPI)
    ↓
VirtualTryOnService
    ↓
VirtualTryOnProvider  (interface)
    ↓
SelfHostedVTONProvider (implementation)
    ↓
FASHN VTON v1.5 (MMDiT weights, Apache-2.0) + DWPose + our own segmentation adapter
```

The frontend and backend API never know which model is running underneath. Swapping the model (e.g. to the Leffa fallback documented in [AI_MODEL_LICENSE.md](AI_MODEL_LICENSE.md)) means writing a new `VirtualTryOnProvider` implementation — it does not touch the API contract, database schema, or frontend.

## Dev environment vs. inference environment

The development machine (Windows, AMD integrated GPU, no CUDA) cannot run diffusion-model inference at production speed. We deliberately split:

- **Development environment**: this laptop. CPU-only PyTorch, Python 3.11 venv under `ai/`. Used to write and correctness-test the pipeline. Inference will be slow here — that's expected and fine for development.
- **Production inference environment**: a rented GPU (cloud GPU rental, e.g. RunPod/Vast.ai/Lambda — provisioned only at deployment time, and only after being flagged to the project owner as an infrastructure cost per the cost-transparency rule). This is *not* a paid AI API — it's raw compute we rent to run our own self-hosted, Apache-2.0-licensed model. No third party ever receives the image or runs inference on our behalf.

Both environments run the exact same `SelfHostedVTONProvider` code; only the hardware backing PyTorch differs (`device="cpu"` vs `device="cuda"`).

## Monetization-ready shape (not implemented yet)

The account/usage layer is designed so limits are backend-configurable, not hardcoded:

```
User → Account → Plan (free/premium) → Usage quota (per day/month) → AI generation
```

This will be built out starting at Milestone 6 (accounts) and Milestone 11 (usage limits). No payment integration exists yet, per the brief.

## Storage abstraction

All file I/O (user photos, garment images, results) goes through a storage service interface, not direct filesystem calls, so local disk (dev) can be swapped for object storage (production) later without touching business logic. Built starting Milestone 3/6.

## Privacy boundary

Two distinct image lifecycles, enforced at the storage-service level once built:

- **Temporary/processing images** — auto-deleted after the job completes (or after a short TTL on failure).
- **Saved images** — only created when the user explicitly taps "Save."

No image is ever sent to a third-party AI service, and none is used for model training.
