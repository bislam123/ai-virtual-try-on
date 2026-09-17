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

## Monetization-ready shape (Milestones 6 + 11)

```
User → Account → Plan (free/premium) → Usage quota (per day/month) → AI generation
       users.plan (FK)   plans table       QuotaService              POST /api/try-on
```

Every stage is real, not a placeholder: `users.plan` is a foreign key into a `plans` table (`max_generations_per_day`, `max_generations_per_month`, `max_num_timesteps` — all nullable, `NULL` = unlimited), and `QuotaService` enforces it against real database counts before every generation. Changing a limit, or moving a user between plans, is a database `UPDATE` — no code change, no redeploy; verified live, not just designed that way (see `docs/DEVELOPMENT.md`'s Milestone 11 section). `RateLimiter` (Milestone 3) is a separate, deliberately different layer — in-memory, process-local, guards short-burst abuse — that still applies alongside this; they answer different questions and neither replaces the other. No payment integration exists yet, per the brief — nothing here lets a user change their own plan.

## API layer: jobs, not synchronous requests

`POST /api/try-on` returns a `job_id` immediately (202) instead of blocking until an image is generated. This isn't premature complexity — on the CPU-only dev machine a generation takes 10-70+ minutes, and even on a production GPU it will take seconds, well past what's comfortable to hold an HTTP request open for. The client polls `GET /api/try-on/{job_id}` and fetches `GET /api/try-on/{job_id}/result` once complete.

Job state lives behind a `JobStore` interface with two implementations: `InMemoryJobStore` (a dict — what the fast fake-provider test suite uses, and still fine for a single-process dev run without a database) and `DbJobStore` (Postgres, via SQLAlchemy — what `app/main.py` actually wires up since Milestone 6). Neither the API routes nor `TryOnService` know or care which is active.

**Stuck-job recovery and inference timeout** (2026-09-17): a job whose owning process is killed mid-generation stays `processing` forever unless something else notices — `services/job_recovery.py`'s `recover_stale_processing_jobs()` is that something, using the job's own `updated_at` (already bumped the instant it enters `processing`) rather than a new column. Run once at app startup and by `scripts/cleanup_expired_results.py`'s scheduled sweep. Separately, `providers/selfhosted.py`'s `generate()` has its own in-process timeout — explicitly **not** a true hard kill (Python can't force-terminate a thread stuck in native PyTorch/CUDA code); it gives up waiting and reports failure while the provider's lock stays held until the underlying call genuinely finishes, which is what keeps "only one generation at a time" true even across a timeout. See that file's own module docstring for the full reasoning on why a true kill would need a separate, killable worker process — assessed as a materially larger change than this task, not implemented.

**Idempotency protection** (2026-09-18): `POST /api/try-on` accepts an optional client-supplied `Idempotency-Key` header (double taps, network retries, and mobile connection instability can otherwise submit the same photos twice, each becoming its own billed AI job). Scoped the same way as the rate limiter's `client_key` — `"user:<id>"` or `"ip:<ip>"`, never a single global namespace — so two different users (or anonymous callers) can never collide or reuse each other's key. A retried submission with the same key **and** the same request (judged by `TryOnService`'s `_idempotency_fingerprint`: category, garment_photo_type, num_timesteps and seed *as submitted*, plus a hash of both images) returns the original job instead of starting a second generation; the same key with materially different content is rejected with 409, never silently reused. The atomic "at most one active job per key" guarantee — including under genuinely concurrent duplicate requests, not just sequential ones — comes from a database-level constraint (`JobStore.create_idempotent`, backed by a partial unique index on `jobs(idempotency_scope, idempotency_key)`), not an in-memory dict, so it holds across worker processes and survives a restart. A job that fails during generation doesn't permanently occupy its key: the partial index excludes `status = 'failed'` rows, so a retry after a real failure creates a fresh job automatically. Clients that never send the header (old or unmodified) are entirely unaffected — see `docs/DEVELOPMENT.md`'s entry for the full request-flow and migration details.

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

What an account actually unlocks today: a job submitted while signed in is attributed to that user, which is what makes `POST /api/try-on/{job_id}/save` meaningful (you can only save a job you created while authenticated — no retroactively claiming an anonymous job onto an account, and no saving someone else's job) and also what gates `GET /api/try-on/{job_id}`/`.../result` — a job attributed to a user is only *viewable* by that same user, not by job_id alone (`TryOnService.get_job_for_viewer`); an anonymous job has no owner to restrict, so it stays viewable by anyone holding its id, exactly as before accounts existed. Passwords are hashed with bcrypt directly (not passlib — recent passlib/bcrypt version pins are a known breakage source); tokens are signed JWTs (PyJWT), `backend/app/auth/security.py` is the only file that touches either.

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

### Method D: browser extension (Milestone 9, extended)

```
extension/content.js (detects a product page, shows a "Try It On" panel)
    ↓ found a direct product image URL on the page (JSON-LD/og:image)?
    ├─ yes: window.open(`${webAppUrl}/?productImageUrl=<image url>&sourceUrl=<page url>`)
    │       ↓
    │   frontend HomeScreen.tsx (reads ?productImageUrl=, strips it, auto-fetches)
    │       ↓
    │   ExtractionService.extract_from_image_url(url)
    │       ↓
    │   HttpProductPageFetcher.fetch_image_from_url()
    │       ↓ ssrf_guard.assert_safe_url() -- fetches ONLY that image resource,
    │         no robots.txt check, no HTML/JSON-LD parsing (there is no page
    │         to scrape — the extension already read those signals itself)
    │
    └─ no (Product schema/OG matched, but no image field): window.open(`${webAppUrl}/?productUrl=<page url>`)
            ↓ Milestone 9's original path, unchanged
        frontend HomeScreen.tsx (reads ?productUrl=, strips it, auto-fetches)
            ↓
        same ExtractionService.extract_from_url() as the "paste a URL" flow
```

Milestone 9 shipped only the bottom path: a new *client* for `POST /api/extract-product-url`, which is the point of Method D — "the same backend must work with the web app, the extension, and future mobile integrations" (brief section 8). That path re-fetches the shopping site's own page HTML server-side, which fails outright against sites behind an anti-bot wall (verified against a real Flipkart product page: HTTP 403 to any non-browser fetch of the page itself, independent of headers/User-Agent — a reCAPTCHA Enterprise challenge, not a fetcher bug). The project's constraints rule out ever trying to defeat that wall.

This extension update adds the top path instead of trying to work around the wall: the extension already has, in the user's own already-rendered page, the same JSON-LD/`og:image` signals it used to detect the product page in the first place — so it can read a direct image URL and hand the backend *only that resource* to fetch, typically a separate CDN host with no anti-bot gate. `POST /api/extract-image-url` is a new, narrow endpoint alongside the existing one (not a replacement — the page-URL path remains the fallback when no image URL is found), reusing the same `SaliencyProductExtractor`, the same unmodified `ssrf_guard.py`, and its own rate limiter (mirroring `url_extraction_rate_limiter`'s reasoning: still a server-triggered fetch to a caller-influenced host). The extension itself stays deliberately thin (no `host_permissions`, no background service worker, no logic duplicated from the backend's own product-detection; the panel UI is Shadow-DOM-isolated but still pure content-script DOM/CSS/JS) — everything past "here's an image/URL the user wants to try on" happens in code already built for Milestones 4-8.

## Storage abstraction

All file I/O (user photos, garment images, results) goes through a `StorageService` interface (`LocalStorageService` today), not direct filesystem calls, so local disk (dev) can be swapped for object storage (production) later without touching business logic. Since Milestone 6, temp uploads are written under a deterministic path (`tmp/{job_id}/{person,garment}.png`) rather than kept only as in-memory Python objects — necessary once job state can outlive the process that created it (a `DbJobStore`-backed job, picked up after a restart, must be able to find its own inputs from the job record alone).

## Privacy boundary

Two distinct image lifecycles:

- **Temporary/processing images** — the person & garment uploads. Always deleted once a job finishes, success or failure (`StorageService.cleanup_temp`), whether or not the user is signed in.
- **Result images** — kept so the user can view/download them, but still temporary by default: `backend/scripts/cleanup_expired_results.py` deletes a completed job's result (and any leftover temp files) after `AITRYON_UNSAVED_RESULT_TTL_HOURS` (24h by default) **unless** `JobRecord.saved` is true — set only by the explicit "Save to my account" action, which requires being signed in. Idempotent, per-job failure isolated (see its module docstring), and tested against the real database (`tests/test_cleanup_expired_results.py`) — but genuinely **not scheduled**: this repo has no deployment infrastructure yet (no Dockerfile/CI/process manager), so the script must be invoked externally. Its own docstring documents the exact cron/Task Scheduler/hosted-cron/Kubernetes-CronJob integration point for whichever deployment target this eventually runs on.
- **Timestamp storage**: `created_at`/`updated_at` on `Plan`/`User`/`JobRecord` are `timestamptz` (timezone-aware, unambiguous instants) as of Alembic revision `34aed8f22dbb` — previously plain `timestamp without time zone`, which silently reinterpreted the app's always-aware-UTC writes using the database session's configured timezone, a latent fragility across differently-configured environments (see `docs/DEVELOPMENT.md`'s Milestone 6 section and the migration's own docstring for the full investigation and data-safety verification).

No image is ever sent to a third-party AI service, and none is used for model training. Deleting a user's account (once that feature exists) cascades to delete their job records at the database level (`ON DELETE CASCADE`) — no orphaned account-linked rows survive account deletion.

## PWA caching boundary (Milestone 10)

Same "temporary vs. durable, and be honest about which is which" instinct as the privacy boundary above, applied to the service worker: the app shell (JS/CSS/HTML/icons) is precached for installability and instant repeat loads, but `/api/*` is explicitly `NetworkOnly` — never cached, matched by request path rather than a hardcoded origin so it holds regardless of where the backend is actually deployed relative to the frontend. A cached job status or try-on result would be actively wrong, not a convenience; the offline banner (`useOnlineStatus.ts`, plain `navigator.onLine` events) tells a disconnected user that honestly instead of letting the UI fail confusingly on the first API call.
