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

## Monetization-ready shape

The account/usage layer is designed so limits are backend-configurable, not hardcoded:

```
User → Account → Plan (free/premium) → Usage quota (per day/month) → AI generation
```

`users.plan` (Milestone 6) is a plain string column, not an enum with limits baked into it — Milestone 11 reads quota numbers from configuration/database, never from scattered `if` statements across the frontend. No payment integration exists yet, per the brief.

## API layer: jobs, not synchronous requests

`POST /api/try-on` returns a `job_id` immediately (202) instead of blocking until an image is generated. This isn't premature complexity — on the CPU-only dev machine a generation takes 10-70+ minutes, and even on a production GPU it will take seconds, well past what's comfortable to hold an HTTP request open for. The client polls `GET /api/try-on/{job_id}` and fetches `GET /api/try-on/{job_id}/result` once complete.

Job state lives behind a `JobStore` interface with two implementations: `InMemoryJobStore` (a dict — what the fast fake-provider test suite uses, and still fine for a single-process dev run without a database) and `DbJobStore` (Postgres, via SQLAlchemy — what `app/main.py` actually wires up since Milestone 6). Neither the API routes nor `TryOnService` know or care which is active.

## Accounts & auth (Milestone 6)

Signing in is **optional everywhere it can be** — the brief is explicit that the core generate/view/download flow must never require an account, and it doesn't: `POST /api/try-on` accepts an `Authorization: Bearer <token>` header if present but works identically without one (`user_id` on the job record is just `NULL`).

```
Frontend
  ↓ Authorization: Bearer <jwt>  (optional)
API route
  ↓ Depends(get_current_user_optional)   -- never raises; None if absent/invalid
  ↓ Depends(get_current_user_required)   -- 401s; only on endpoints that need an account
TryOnService / DB
```

What an account actually unlocks today: a job submitted while signed in is attributed to that user, which is the only thing that makes `POST /api/try-on/{job_id}/save` meaningful — you can only save a job you created while authenticated (no retroactively claiming an anonymous job onto an account, and no saving someone else's job). Passwords are hashed with bcrypt directly (not passlib — recent passlib/bcrypt version pins are a known breakage source); tokens are signed JWTs (PyJWT), `backend/app/auth/security.py` is the only file that touches either.

## Product extraction (Milestone 7)

```
Frontend (optional "Auto-detect" button)
    ↓ POST /api/extract-product-image  (synchronous — see below)
ExtractionService
    ↓
ProductImageExtractor  (interface)
    ↓
SaliencyProductExtractor (implementation — classical CV, cv2.saliency)
```

Same "no vendor lock-in" shape as the AI provider above, applied to a different problem: isolating the actual clothing item out of an upload that might be a full shopping-site screenshot. `ExtractionService` is a thin pass-through today, but it's the seam Milestone 8 (Method A: product URL) and Milestone 9 (Method D: browser extension) plug into — *how* an image arrives (fetch a URL, receive a POST, get one from the extension) is a different concern from *finding the product within* it, which is all `ProductImageExtractor` does. See `product-extractor/README.md` for the Method A-D breakdown.

Unlike `/api/try-on`, this endpoint is **synchronous** — classical CV runs in milliseconds on this CPU, so the job/poll pattern (which exists specifically because of the AI model's cost) would be pure overhead here. Not every backend endpoint follows the same shape; the shape follows the actual cost of the work.

### Method A: product URL (Milestone 8)

```
ExtractionService.extract_from_url(url)
    ↓
ProductPageFetcher  (interface, fetchers/base.py)
    ↓
HttpProductPageFetcher
    ↓ ssrf_guard.assert_safe_url()  -- before the initial request AND every redirect hop
    ↓ robots.txt check
    ↓ fetch page, parse JSON-LD Product schema / Open Graph tags
    ↓ fetch the found image
    ↓
same SaliencyProductExtractor as Method B/C
```

`POST /api/extract-product-url` accepts a URL, not a file — everything downstream is identical to Method B/C, which is the point of keeping "how do we get an image" (`fetchers/`) separate from "find the product within an image" (`extractors/`). A URL-fetching feature that accepts arbitrary user input is a real SSRF surface — `fetchers/ssrf_guard.py` is the load-bearing security control here (blocks private/loopback/link-local/reserved IP ranges after DNS resolution, re-checked on every redirect, not just the first hop), and it's documented with its own known limitation (check-then-connect, not immune to DNS rebinding) rather than presented as airtight. Never bypasses robots.txt, auth, paywalls, or CAPTCHAs (brief section 7) — any of those, or simply finding no product image, surfaces as the same kind of clear, fallback-pointing error as every other failure mode in this codebase (section 25).

### Method D: browser extension (Milestone 9)

```
extension/content.js (detects a product page, injects a button)
    ↓ window.open(`${webAppUrl}/?productUrl=<page url>`)
frontend HomeScreen.tsx (reads ?productUrl= once, strips it, auto-fetches)
    ↓
same ExtractionService.extract_from_url() as the "paste a URL" flow
```

No new backend endpoint — this is a new *client* for `POST /api/extract-product-url`, which is the point: "the same backend must work with the web app, the extension, and future mobile integrations" (brief section 8). The extension itself stays deliberately thin (no `host_permissions`, no background service worker, no logic duplicated from the backend's own product-detection) — everything past "here's a URL the user wants to try on" happens in code already built for Milestones 4-8.

## Storage abstraction

All file I/O (user photos, garment images, results) goes through a `StorageService` interface (`LocalStorageService` today), not direct filesystem calls, so local disk (dev) can be swapped for object storage (production) later without touching business logic. Since Milestone 6, temp uploads are written under a deterministic path (`tmp/{job_id}/{person,garment}.png`) rather than kept only as in-memory Python objects — necessary once job state can outlive the process that created it (a `DbJobStore`-backed job, picked up after a restart, must be able to find its own inputs from the job record alone).

## Privacy boundary

Two distinct image lifecycles:

- **Temporary/processing images** — the person & garment uploads. Always deleted once a job finishes, success or failure (`StorageService.cleanup_temp`), whether or not the user is signed in.
- **Result images** — kept so the user can view/download them, but still temporary by default: `backend/scripts/cleanup_expired_results.py` deletes a completed job's result after `AITRYON_UNSAVED_RESULT_TTL_HOURS` (24h by default) **unless** `JobRecord.saved` is true — set only by the explicit "Save to my account" action, which requires being signed in. Not yet wired to a scheduler (real infra — cron/Task Scheduler/hosted cron — deliberately deferred); the script exists and is meant to be run periodically once that's set up.

No image is ever sent to a third-party AI service, and none is used for model training. Deleting a user's account (once that feature exists) cascades to delete their job records at the database level (`ON DELETE CASCADE`) — no orphaned account-linked rows survive account deletion.
