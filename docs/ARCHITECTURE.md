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

**Real GPU measurement, not an estimate** (Google Colab, Tesla T4, 30 timesteps): 474.64s generation time, `torch.bfloat16` actually in use, `nvidia-smi` reporting 14,745 MiB of the T4's 15,360 MiB total in use (168 MiB free) — see `docs/DEPLOYMENT.md`'s GPU deployment section for the full figures and why a T4 specifically is not recommended as the production class despite technically completing the workload. This also chose the initial deployment topology (see `docs/DEPLOYMENT.md`'s Deployment architecture section): `SelfHostedVTONProvider` stays in-process rather than moving behind a separate GPU worker service — the `VirtualTryOnProvider` interface already keeps that reversible later without any changes here.

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

**Password reset** (2026-09-17): `POST /api/auth/forgot-password` / `POST /api/auth/reset-password`, with the same never-reveal-account-existence guarantee as login (identical response whether or not the email is registered) and the same dual per-IP + per-email `RateLimiter` strategy. A reset token is `secrets.token_urlsafe(32)`; only its SHA-256 hash is ever persisted (`password_reset_tokens` table), and it's claimed with a single atomic `UPDATE ... WHERE used_at IS NULL AND expires_at > now()` — a database-level, not application-level, single-use/race-safety guarantee, the same style as the idempotency work's partial unique index. See `backend/app/services/password_reset_service.py`.

**Email delivery** (2026-09-19): `EmailService` (`backend/app/services/email_service.py`) is an interface with two implementations, selected explicitly via `AITRYON_EMAIL_PROVIDER` (never inferred, never environment-implicit) — `ConsoleEmailService` (default; prints to stdout, dev/test only) and `SmtpEmailService` (real delivery via stdlib `smtplib` against any SMTP-speaking provider — no vendor SDK, so switching providers later is configuration, not code). `Settings.check_production_email_provider` refuses to start with `AITRYON_ENVIRONMENT=production` while still on `ConsoleEmailService`, the same fail-loud-at-boot pattern as the JWT/database/CORS checks. `forgot_password`'s token-issue and email-send happen inside one database transaction: a genuine delivery failure (`EmailDeliveryError`) propagates out and rolls the token insert back via `get_session()`'s own rollback, so a failed send never leaves a real-but-undelivered token occupying that account's single outstanding-token slot — the endpoint's response is identical whether the email was never registered, was registered and sent successfully, or was registered but delivery genuinely failed.

**Server-side JWT revocation** (2026-09-17, closes this doc's previous "JWT stays valid after a password reset" gap): a lightweight per-user version counter, not a token blocklist or session table. `users.auth_version` (integer, starts at 1) is embedded in every access token as its `"ver"` claim (`auth/security.py`'s `create_access_token`); `get_current_user_optional` rejects a token whose `ver` doesn't match the live row's current `auth_version` — one integer comparison against data a request already loads to authenticate at all, no extra query or table. Bumping the column (`reset_password`, on every successful reset) therefore revokes *every* previously issued token for that account in one write. A token missing the `"ver"` claim entirely (the shape of every token issued before this feature existed) is rejected outright, fail-closed, by `decode_access_token` itself — not treated as automatically valid. Account deletion needs no separate revocation step: the user row is gone, so `get_current_user_optional`'s existing `user is None` check already covers it. Frontend: `api/http.ts`'s `apiFetch` calls a registered session-expired listener whenever a token-bearing request comes back `401`, and `useAuth.ts` uses it to clear stored/in-memory session state globally the moment any request surfaces a revoked token — not just on the next explicit `/me` check. Account-deletion's wrong-confirmation-password response was changed from `401` to `403` specifically so it's never mistaken for a revoked session by that same mechanism (the bearer token there is still perfectly valid; only the destructive-action confirmation failed). **Known, accepted limitation**: no way to revoke a single token/device without revoking all of a user's sessions at once (there's only one counter per user, not per token) — acceptable given this app has no concept of multiple concurrent named sessions/devices to begin with.

## Admin / Operations (2026-09-19)

A minimal, server-authoritative admin area for operating the application safely before real deployment — not a new subsystem: it's a read-mostly view over the existing `User`/`Plan`/`JobRecord` tables, plus one write action (enable/disable an account), all behind one new authorization gate.

```
users.is_admin (bool, default false) ──▶ get_current_admin_user (composes on get_current_user_required)
                                                   │
                                                   ▼
                                        /api/admin/* (7 routes, one router-level dependency)
                                                   │
                                                   ▼
                                        AdminService (plain SQLAlchemy queries, same
                                        shape as QuotaService/CapacityService)
```

**Authorization model**: `users.is_admin` and `users.is_active` (migration `e94aa22bee59`) — two booleans, not a roles table or a separate admin-users table, because this app has exactly one privilege tier above "normal user" today. `auth/dependencies.py`'s `get_current_admin_user` composes on the existing `get_current_user_required` (so an unauthenticated request gets the exact same 401 every other protected endpoint gives) and adds one check: 403 if the authenticated user's `is_admin` is false. Applied once, at the `/api/admin` router level (`dependencies=[Depends(get_current_admin_user)]`) — no individual route re-checks `is_admin` itself, the same "one shared gate, not reimplemented per-route" pattern the job-ownership model already established (`TryOnService.get_job_for_viewer`/`cancel_job`/`save_job`). `is_admin` is never accepted from any request body — no signup/login schema has such a field — so there is no API path that can ever grant it.

**`is_active`**: lets an admin disable an account without deleting it. Checked in `get_current_user_optional` alongside the existing `auth_version` comparison, so disabling revokes a live session immediately (not just future logins) — the same fail-closed pattern, not a second mechanism. `login()` rejects a disabled account with the exact same generic "Incorrect email or password." message a wrong password gets, so account status is never a new enumeration channel. An admin cannot disable their own account (a guarded 400, avoiding a self-lockout that would need direct DB access to undo).

**First-admin bootstrap — deliberately out-of-band, not an API call**: an account becomes admin only via a direct database write, by whoever already has database access — the exact same trust boundary this project already relies on for editing `plans` rows by hand (see Milestone 11 below). Two ways to do it, both requiring the target account to have already signed up normally:

```powershell
# Convenience script (backend/scripts/promote_admin.py):
ai\.venv\Scripts\python.exe backend\scripts\promote_admin.py you@example.com

# Or the raw SQL it wraps:
UPDATE users SET is_admin = true WHERE email = 'you@example.com';
```

No default admin account is created by the migration, the script, or anything else — every row starts `is_admin=false`, with no exception. There is deliberately no setup-wizard endpoint, no environment-variable-configured admin email, and no first-user-is-admin special case (all of which would be exactly the kind of implicit backdoor this milestone was told to avoid).

**API surface** (`backend/app/api/admin.py`, all under `/api/admin`, all admin-gated):

| Endpoint | Purpose |
|---|---|
| `GET /users` | Paginated list, optional `?search=` (email substring) |
| `GET /users/{id}` | Full detail including usage/quota (reuses `QuotaService.get_status` — the exact same call `GET /api/usage/me` makes for a user's own account, not reimplemented) |
| `POST /users/{id}/disable` / `/enable` | Toggles `is_active`; self-disable refused |
| `GET /plans` | Read-only list of configured plans and their limits — no payment/billing logic |
| `GET /jobs` | Paginated, optional `?status=`/`?user_id=` filters, across *every* user — the one intentional exception to normal job-ownership scoping, gated by the same admin check everything else here uses |
| `GET /dashboard` | `total_users`, `active_users`, `jobs_by_status`, `recent_failed_jobs`, `active_job_count`/`max_active_job_capacity` (reuses `CapacityService`'s own configured limit), and `avg_processing_duration_seconds` computed from a bounded recent sample of completed jobs' own `created_at`/`updated_at` — no new timing instrumentation added anywhere in the inference path |

**What every admin response deliberately excludes**: `password_hash`, `auth_version`, reset-token hashes, JWTs, and (on the job list specifically) the associated user's email — jobs carry only `user_id`, cross-referenced via the user endpoints if needed, not re-embedded. Regression-tested directly (`tests/test_admin_api.py::test_no_sensitive_auth_fields_in_any_admin_response`), not just reasoned about.

**Frontend**: `AdminScreen.tsx`, reachable only via an "Admin" button in `AuthBar` that itself only renders when `user.is_admin` is true (read from `GET /api/auth/me`, display-only — every admin API call the screen makes is independently re-authorized server-side regardless of what the client believes, so a stale or spoofed client-side flag can't grant anything real). No router exists in this app (same as the password-reset/extension-handoff screens); `AdminScreen` is a local `App.tsx` state branch, the established pattern here.

**Deliberately not built**: no audit log of admin actions (two actions exist — disable/enable — and both are already visible as the account's own `is_active` state; a dedicated log was judged unnecessary infrastructure for this milestone's scope). No bulk actions, no plan-editing UI (plans stay a direct-DB-edit operation, unchanged from Milestone 11), no payment/billing surface anywhere in this feature.

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

## Security headers & request limits (2026-09-17)

Two small, dedicated ASGI/Starlette middlewares in `backend/app/core/`, wired into `main.py` in a specific order that matters:

```
Request
  ↓
CORSMiddleware            -- outermost: must wrap the other two so it can still
  ↓                          add Access-Control-* headers to whatever they produce,
SecurityHeadersMiddleware    including a 413 or 401 they generate directly
  ↓
RequestBodySizeLimitMiddleware
  ↓
ExceptionMiddleware / Router / route handlers
```

**`SecurityHeadersMiddleware`** (`core/security_headers.py`) adds `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, `X-Frame-Options: DENY`, a restrictive `Permissions-Policy`, and a strict `Content-Security-Policy` (`default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'`) to every response — this API only ever returns JSON or a PNG, never a page meant to load scripts/styles/frames, so the strictest CSP is also the correct one, not just a cautious default. No `'unsafe-inline'`/`'unsafe-eval'` anywhere. The one exception: FastAPI's own `/docs`/`/redoc`/`/docs/oauth2-redirect` pages load their JS/CSS from a CDN and run inline bootstrap scripts this project doesn't control, so CSP is simply not sent on those specific paths (every other header still is) rather than weakening it everywhere to accommodate a developer tool. **No `Strict-Transport-Security` header** — deliberately not added: HSTS is only safe once HTTPS is guaranteed, and this repository has no reverse proxy or TLS termination yet (same "no deployment infrastructure exists" conclusion `docs/DEVELOPMENT.md` already draws elsewhere). Adding it now would be actively wrong, not just premature; it's a real production deployment responsibility, documented in `docs/ENVIRONMENT.md`, not pretended to already exist.

**`RequestBodySizeLimitMiddleware`** (`core/body_size_limit.py`) caps the raw HTTP request body (`AITRYON_MAX_REQUEST_BODY_BYTES`, 25 MiB default) at the ASGI layer, before Starlette/python-multipart ever parses it — closing a real gap: the existing per-image-field checks (`AITRYON_MAX_UPLOAD_SIZE_MB`/`AITRYON_MAX_IMAGE_DIMENSION_PX`, `core/validation.py`) only ever ran *after* `await file.read()` had already pulled the entire body into memory, so nothing previously stopped a client from sending a multi-gigabyte request and having the server spend real time/memory/disk receiving all of it first. Two layers, neither of which buffers an oversized body before rejecting: a `Content-Length` fast path (the common case — verified directly against a real ~62MB multipart upload, rejected in under 5ms, not by inspection) and a streaming byte-counter for a missing/lying `Content-Length` (chunked transfer encoding), which injects a synthetic `http.disconnect` the instant the running total would cross the limit — exactly the message Starlette already treats a real client disconnect as — so whatever's mid-parse downstream stops cleanly instead of receiving more data. Rejected requests never reach job creation, quota consumption, or the AI provider at all (verified directly with a spy provider/quota service, not assumed) and leave no temp files behind. The 413 response is a safe, generic JSON `{"detail": "..."}` — no stack trace, no filesystem path, no uploaded filename — matching the same `{"detail": ...}` contract `UserFacingError`'s handler already uses, which is also exactly what `frontend/src/api/http.ts`'s existing generic error-surfacing already knows how to show the user without any frontend code change.

**Upload validation hardening** (`core/validation.py`): dimensions are now read from the image's header and checked *before* the expensive full pixel decode (`img.load()`), not after. Pillow's own decompression-bomb guard (`Image.MAX_IMAGE_PIXELS`, ~89.5M pixels by default) only raises an error above 2× that threshold; between 1× and 2× it just warns and decodes anyway — a file whose header declares dimensions in that band (easily achievable from a small, highly-compressible file, which is what a decompression bomb is) would previously have been fully decoded into memory before this app's own, stricter `max_image_dimension_px` check ever got a chance to reject it. Reading width/height from the header first (cheap, always available immediately after `Image.open()`) closes that gap. SVG and other non-raster "active content" were already safe by construction, not by this change: Pillow has no SVG plugin at all, so such a file simply fails to open as an image, the same as any other unrecognized format.

## Try-on job lifecycle, concurrency & capacity (2026-09-17)

**State machine.** `PENDING` → `PROCESSING` → `COMPLETED` | `FAILED`, plus `CANCELLED` (new — see below). `JobStore.create`/`create_idempotent` always start a job at `PENDING`. `TryOnService.run_job` — scheduled as a FastAPI `BackgroundTask` from `POST /api/try-on`, so it starts almost immediately after the HTTP response is prepared, not after some separate queue delay — is the *only* code that ever moves a job out of `PENDING`, via an atomic `try_transition_status(expected=PENDING, new=PROCESSING)` (see Cancellation below for why this needs to be atomic, not a plain overwrite). `updated_at` is bumped on every status write (the column's own `onupdate`), which is what `services/job_recovery.py`'s stale-job sweep keys off. From `PROCESSING`, `run_job` calls the provider; on success it saves the result and sets `COMPLETED`, on failure it sets `FAILED` with a safe error message (see Failure handling below) — either way, `finally: storage.cleanup_temp(job_id)` runs regardless of outcome.

**Important, easily-missed nuance**: a job showing `PROCESSING` does not necessarily mean the AI model is actively computing it right now. `run_job` marks the job `PROCESSING` *before* calling `provider.generate()`, and `SelfHostedVTONProvider.generate()` may then block for a long time acquiring its own internal lock if another generation is already running (see below) — so `PROCESSING` covers both "the model is actively working on this" and "this is waiting its turn for the one model instance." Deliberately not split into a separate `QUEUED` state: doing so would need the provider to report an intermediate "lock acquired, now actually running" signal back up through `TryOnService`, a genuine architecture change this task's scope didn't call for once the actual question (does polling/capacity/cancellation behave correctly either way) was verified to already hold under both interpretations.

**Concurrency / provider lock — reviewed, unchanged.** `SelfHostedVTONProvider`'s single `threading.Lock` (one generation at a time, a background thread that keeps running un-terminated past `inference_timeout_seconds` since Python cannot safely force-kill it, the lock held until that thread genuinely finishes) was already correct and already thoroughly tested (`tests/test_selfhosted_provider.py`, 7 tests covering timeout-without-kill, lock-held-across-timeout, lock-released-once-the-hang-finishes, `ProviderBusyError` on a lock-acquire timeout, and no-concurrent-generation under real thread contention) — see `providers/selfhosted.py`'s own module docstring for the full reasoning, unchanged by this milestone. What *was* missing: the job's own recorded error message for a lock-timeout/busy failure was being flattened into the fully generic `GENERIC_FAILURE_MESSAGE`, discarding `ProviderBusyError`/`InferenceTimeoutError`'s own already-safe, more specific text ("The AI model is still busy... please try again shortly" / "Generation exceeded the Ns timeout."). Fixed narrowly: `run_job` now special-cases those two exception types (both relocated to `providers/base.py` so `TryOnService` can recognize them without importing anything provider-specific, preserving the "nothing above the provider interface knows which model is running" rule) and surfaces their own message directly; any other exception still gets the fully generic message, never internal details.

**Capacity protection — genuinely needed, added.** Rate limiting (`RateLimiter`) and quota (`QuotaService`) are both scoped *per identity* (per user or per IP) — many different identities, each safely within their own budget, could previously still pile up an unbounded number of `PENDING`/`PROCESSING` jobs, each a background thread blocked for up to `AITRYON_PROVIDER_LOCK_ACQUIRE_TIMEOUT_SECONDS` (an hour, by default) waiting for the one lock only one of them can hold. `CapacityService` (`services/capacity_service.py`) closes this with a *global* cap, `AITRYON_MAX_ACTIVE_TRYON_JOBS` (10 by default), checked in `api/tryon.py` right after the existing quota check, in both the idempotent and non-idempotent submission paths, before any image validation/decoding or job-row creation — so a capacity-rejected request never creates a job, never consumes quota (which counts jobs), and never reserves an Idempotency-Key (verified directly: retrying the same key after capacity frees up succeeds as a fresh submission, not a 409 conflict). Counted from the real database (`JobRecord`), like `QuotaService`, not an in-memory counter — the check-then-create sequence has the same small, accepted race window `QuotaService`'s own check already has today (a handful of simultaneous requests right at the boundary could all pass and all insert): a deliberate, proportionate choice matching this codebase's one existing precedent for this class of soft limit, not a new heavier mechanism (e.g. a Postgres advisory lock) nothing else here uses. An idempotent *retry* of an already-existing job is unaffected by capacity pressure, verified directly — `find_idempotent_job`'s early return needs no new capacity, since it never creates a row.

**Cancellation — implemented narrowly, for `PENDING` jobs only.** `TryOnService.cancel_job` cancels a job via the same atomic `try_transition_status(expected=PENDING, new=CANCELLED)` `run_job` uses to claim a job — at most one of the two can ever win a given job (verified directly under real concurrent threads racing both, 20 repetitions, `tests/test_tryon_service.py::test_cancel_race_with_run_job_is_mutually_exclusive_never_both`), so a job is never both cancelled and processed. Once cancelled, `run_job`'s own claim fails, so it never calls the provider and never publishes a result — cancellation is a real, database-enforced guarantee, not a flag hoped to be checked in time. Temp files are cleaned up immediately on cancellation. Same ownership model as viewing/saving a job (404 → 403 → the actual action; an anonymous job cancellable by anyone holding its id) — reused, not reinvented. **Deliberately does NOT attempt to cancel a `PROCESSING` job** — refused with a clear 409, not faked: by that point `run_job` has already handed the job to the provider, which may be genuinely running the model or blocked waiting for its lock, and this architecture has no safe way to stop either (no forceful thread termination, no separate killable process — same limitation `providers/selfhosted.py` already documents for the inference timeout). **Practical scope, stated honestly**: because `run_job` starts within milliseconds of job creation in the current architecture (confirmed directly against the real running server: a job was already `PROCESSING` by the time its own creation response was inspected), the cancellable `PENDING` window is real but usually very short — this mostly matters when the background-task thread pool itself is saturated or a cancel request lands in a very tight race with submission, not as a general "stop my job whenever I want" control.

**Timeout interaction — reviewed, coherent, unchanged.** `AITRYON_INFERENCE_TIMEOUT_SECONDS` (provider gives up *waiting*, thread keeps running) → `AITRYON_PROVIDER_LOCK_ACQUIRE_TIMEOUT_SECONDS` (a second caller's own wait for the lock, which a timed-out-but-still-running first call keeps held) → `AITRYON_STALE_JOB_THRESHOLD_MINUTES` (the slower backstop for when the *process* itself is gone, not just a hung thread within a live one) — three independent bounds at three different layers, already coherent (each strictly longer than what it backstops) and already covered by `tests/test_selfhosted_provider.py`/`tests/test_job_recovery.py`. Nothing here needed to change; see `providers/selfhosted.py`'s and `services/job_recovery.py`'s own module docstrings for the full reasoning, which this milestone's audit confirmed still holds.

**Resource cleanup — every terminal path verified.** `COMPLETED` and `FAILED` both flow through `run_job`'s `finally: storage.cleanup_temp(job_id)`, unchanged. `CANCELLED` cleans up temp files immediately in `cancel_job` itself (and defensively again, safely, if `run_job`'s own claim happens to fail right after — `cleanup_temp` is idempotent). A job recovered by the stale-job sweep (the closest thing to a "timeout" terminal path at the database level) already cleaned up its temp files, unchanged. None of this changes the existing, deliberate "not automatically scheduled yet" decision for `scripts/cleanup_expired_results.py` — still a later, separate deployment-infrastructure milestone.

**Frontend**: `useTryOnFlow.ts` gained a bounded polling lifetime (`MAX_POLL_DURATION_MS`, 90 minutes — generous on purpose, sized against this CPU-dev machine's documented 10-70+ minute worst case plus the backend's own hour-scale timeouts, not arbitrary) so an abandoned tab doesn't poll forever; hitting it stops polling with an honest "taking longer than expected" message that never claims the job itself failed. Polling now also recognizes `"cancelled"` as a terminal status (for a job cancelled from elsewhere, e.g. another tab). A new `cancel()` function calls the new cancel endpoint and stops polling on success; on the backend's 409 refusal (already processing), it resumes polling rather than leaving the screen stuck. `ProcessingScreen` gained a small "Cancel" button. No redesign: the same `{status: "failed", message}` submission shape already used for a genuine failure is reused for "cancelled"/"timed out waiting", just with accurate wording — avoiding a new UI state/branch for what the existing error-display slot already handles correctly.

## PWA update handling, cache safety & mobile/accessibility hardening (2026-09-17)

**Existing PWA architecture found, before changing anything**: `vite-plugin-pwa` (`vite.config.ts`) with `registerType: 'autoUpdate'`, no explicit service-worker registration code anywhere in `src/` — meaning the plugin's default auto-injected `registerSW.js` (a bare `navigator.serviceWorker.register('/sw.js')` call, confirmed by building and reading the actual generated file, not assumed) was the only registration happening. `/api/*` was already correctly routed through Workbox's `NetworkOnly` handler (confirmed in the generated `dist/sw.js`) — result images, job status, and auth responses were never at risk of being cached; this part needed no change. The manifest (name/short_name/icons/theme_color/`display: 'standalone'`), the apple-touch-icon + `apple-mobile-web-app-*` meta tags, and the mobile viewport meta tag were all already present and correct. No custom install-prompt code existed anywhere (no `beforeinstallprompt` listener) — the app already relies entirely on each browser's own native install UI, which is the *correct* minimal choice here, not a gap: a hand-rolled install button would be exactly the kind of "platform-specific hack" this milestone was told to avoid adding without genuine need.

**The real gap, found by building and inspecting the actual generated service worker, not assumed**: under `registerType: 'autoUpdate'`, the generated `sw.js` called `self.skipWaiting()` **unconditionally at top level** — the instant a browser fetched a new version of the worker, it activated and (via `clientsClaim()`) took control of every open tab's future requests immediately, with no reload and no signal to the page at all (the bare auto-injected registration script has no listeners for this). An already-open tab could end up with its network requests served by a new worker while still running old, already-loaded JS — a real stale-version mismatch risk, not just a missed "notify the user" nicety.

**Fix**: `registerType: 'prompt'` + `injectRegister: false`, paired with `vite-plugin-pwa`'s own `virtual:pwa-register/react` hook (`hooks/usePwaUpdate.ts`) registered explicitly in `App.tsx`. Rebuilding and re-inspecting `dist/sw.js` confirmed the change: `self.skipWaiting()` is now called only from inside a `message` event listener, gated on receiving `{type: "SKIP_WAITING"}` — the worker installs and *waits*. `usePwaUpdate()` exposes `needRefresh` (true once a new worker is waiting) and `applyUpdate()` (sends that message); a new, small `UpdatePrompt` component renders an unobtrusive banner (same shape as `OfflineBanner`) only while `needRefresh` is true, with an "Update" button as the *only* thing that ever triggers `applyUpdate()` — nothing reloads the page on its own, so a user mid-upload or mid-try-on is never interrupted, and there is no loop: `needRefresh` only ever transitions false→true once per detected update, and applying it re-initializes the whole app (including a fresh `needRefresh=false`) against the new worker. Verified against the real built output, not just the source config (`src/pwaConfig.test.ts` encodes the same invariants — `registerType: 'prompt'`, `injectRegister: false`, the exact `/api/` `NetworkOnly` rule, and a closed static-file-extension `globPatterns` list — so a future change that silently breaks one of them fails a fast test rather than needing someone to remember to rebuild and re-inspect by hand).

**Accessibility audit and fixes** (focused, not a redesign): `AuthModal` and `DeleteAccountModal` were plain `<div>` overlays with no dialog semantics at all — no `role="dialog"`, no focus management, no Escape handling. Added a small shared hook, `hooks/useDialogA11y.ts` (the WAI-ARIA dialog pattern, without a full manual tab-cycling focus trap — nothing behind an open modal here is inert, so native Tab order already stays sane, and a hand-rolled trap is exactly the "focus becomes trapped incorrectly" risk this was written to avoid): moves focus to the dialog *panel* on open (not the first input, which would pop the mobile keyboard immediately and unexpectedly), restores focus to whatever had it before on close, and closes on Escape. Both modals now declare `role="dialog"` / `aria-modal="true"` / `aria-labelledby`, and gained `max-h-[85vh] overflow-y-auto` so a short mobile viewport (e.g. landscape, or the on-screen keyboard covering half the screen) can never clip the submit button below an unreachable fold. Every text input across `AuthModal`, `DeleteAccountModal`, `ResetPasswordScreen`, and `ProductUrlInput` had a `placeholder` but no real accessible name — added a visually-hidden (`sr-only`) `<label>` for each, native HTML rather than an ARIA attribute. Error messages across every form now use `role="alert"`; async confirmations (forgot-password sent, reset-password success, account-deleted) use `role="status"`. `ProcessingScreen`'s stage label (which changes on a real, discrete state transition) is `aria-live="polite"` — deliberately *not* applied to the elapsed-time counter next to it, which ticks every second and would spam a screen reader if it were. `HomeScreen`'s category buttons gained `aria-pressed` (the fill-vs-outline visual distinction already avoided a color-only signal, but nothing told a screen reader which one was selected); `ProductUrlInput`'s collapse/expand toggle gained `aria-expanded`. Seven places used `text-slate-400` for informational body text and one close-icon button — measured against its white/near-white backgrounds, that combination is under the WCAG AA 4.5:1 (text) and 3:1 (UI component) contrast minimums; changed to `text-slate-500` everywhere it appeared, the smallest fix that keeps the same muted-gray visual language while actually being legible.

**Mobile responsiveness**: reviewed at 320/375/390/412/768px via a real headless-Chromium pass (see below) — no horizontal overflow found at any of them (the app's existing `max-w-sm`/`max-w-md` + relative-width convention already handled this correctly); the one genuine, concrete issue found was the modal viewport-height clipping risk described above, now fixed. No other layout change was made — there was nothing else to fix.

**Real-browser verification** (Playwright, against the actual dev server + actual backend, no AI generation waited on): desktop and 320/375/390/412/768px mobile widths (no overflow at any); the auth modal's dialog role/label/Escape/viewport-fit; a real signup creating a real account and the AuthBar reflecting it; the delete-account modal's dialog role/viewport-fit/Escape-without-deleting; the real upload flow (file inputs, category selector, enabled submit); a real `POST /api/try-on` submission reaching the processing screen with a live, non-percentage stage label; and a real cancel-button click against the live backend. 21/21 checks passed. The submitted job (as expected — see the job-lifecycle milestone's own finding that a job's `PENDING` window is typically sub-second) had already moved to `PROCESSING` by the time cancel was clicked and was correctly refused with 409; the resulting real (harmless, CPU-only, never awaited) generation and its temp files were cleaned up manually afterward, the same way earlier milestones' own live-server smoke tests were. The PWA update banner itself was **not** live-tested end-to-end (that needs a full old-build→new-build service-worker update cycle against a real static file server, a materially larger one-off rig than this check) — verified instead via the direct `dist/sw.js` build-artifact inspection above plus `hooks/usePwaUpdate.test.ts`/`components/UpdatePrompt.test.tsx`'s unit coverage.
