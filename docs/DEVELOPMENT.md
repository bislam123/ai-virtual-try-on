# Development

## Milestone plan & status

| # | Milestone | Status |
|---|---|---|
| 1 | Hardware & AI model evaluation | ✅ Done |
| 2 | AI model installation & first test inference | ✅ Done |
| 3 | AI inference FastAPI service (`POST /api/try-on`) | ✅ Done |
| 4 | Mobile-first web UI | ✅ Done |
| 5 | Connect frontend to AI backend | ✅ Done as part of Milestone 4 — the frontend calls the real `/api/try-on` from the start, not mock data |
| 6 | User accounts & secure image handling | ✅ Done |
| 7 | Product image extraction | ✅ Done |
| 8 | Product URL support | ✅ Done |
| 9 | Browser extension | ✅ Done |
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

## Known limitation carried forward from Milestone 2

`aitryon_bodyparser.PlaceholderBodyParser` always returns "background" — it is not a real segmentation model. It's provably safe only when the pipeline runs with `segmentation_free=True` and `garment_photo_type="flat-lay"` (our actual MVP scenario: a person photo + a plain clothing/product photo). The backend API enforces this — see Milestone 3 below. Before we support masked mode or "garment worn by another person" product photos, it must be replaced with a real commercially-licensed segmentation model. Candidates and why: see [AI_MODEL_LICENSE.md](AI_MODEL_LICENSE.md).

## Milestone 3 — AI inference FastAPI service

`backend/` is a FastAPI app that wraps the AI pipeline behind `POST /api/try-on`. It runs in the same venv as `ai/` (`ai/.venv`) since it calls the pipeline in-process — see `backend/requirements.txt` for why, and when that would need to split.

**Install (adds to the same venv):**
```powershell
ai\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
ai\.venv\Scripts\python.exe -m pip install pytest httpx   # test-only deps
```

**Run the server:**
```powershell
ai\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload
```
Loads the real model at startup (~7s) — a missing/corrupt `ai/models/fashn-vton-1.5` fails loudly at boot, not on a user's first request.

**Run tests** (fast — uses a fake provider, no real inference; run from the project root):
```powershell
ai\.venv\Scripts\python.exe -m pytest -v
```

**API:**

| Endpoint | Purpose |
|---|---|
| `POST /api/try-on` | multipart form: `person_image`, `garment_image` (JPEG/PNG/WebP, ≤10MB, ≤4096px), `category` (`tops`\|`bottoms`\|`one-pieces`), optional `num_timesteps`/`seed`. Returns `202 {job_id, status}` immediately — generation runs in the background (this is not optional: on this CPU-only dev machine a job takes 10-70+ minutes depending on step count, so the request must not block). `garment_photo_type` other than the default `"flat-lay"` is rejected with a clear message — see the known limitation above. |
| `GET /api/try-on/{job_id}` | `{status: pending\|processing\|completed\|failed, error, result_url}`. Poll this. |
| `GET /api/try-on/{job_id}/result` | The generated PNG, once `status == completed`. |

**Verified working, twice:**
1. Full test suite (`pytest`, fake provider) — validation, job lifecycle, error handling, rate limiting, 404s, provider-failure-doesn't-leak-internals — all pass in ~1s.
2. A real end-to-end run through the actual running server (real model, `num_timesteps=4` for a faster ~13-minute check) — submit → poll (server stayed responsive to every poll throughout, confirming the background-task design works) → fetch result → confirmed temp uploads were deleted and the result PNG persisted.

**Known limitations at the time, since addressed by Milestone 6 (noted here for history, not left stale):**
- ~~Job state is in-memory~~ → `DbJobStore` (Postgres) is now what `app/main.py` wires up; `InMemoryJobStore` still exists and is still what the fast test suite uses.
- ~~No auth~~ → optional JWT auth exists; still true that the core flow never requires it, by design.
- Rate limiting is still a per-IP (or per-user, if signed in) in-memory fixed window (abuse protection only) — real per-plan quotas still need Milestone 11.
- Temp upload cleanup running in a `finally` block (skipped on a hard process kill) is still a real gap — see Milestone 6's section below for the closely-related result-image TTL story.

## Milestone 4 — Mobile-first web UI

`frontend/` is a Vite + React 19 + TypeScript + Tailwind CSS v4 PWA-shaped app (full PWA installability — manifest icons, service worker — is Milestone 10; this milestone is the responsive UI itself). Talks to the backend over plain `fetch`, configured via `VITE_API_BASE_URL` (`.env.example`).

**Install & run:**
```powershell
cd frontend
npm install
npm run dev          # http://localhost:5173 — CORS-allowed by the backend's default config
```
Needs the backend running too (see Milestone 3 above) — the app calls it directly, no proxy.

**Structure:**
| Path | Purpose |
|---|---|
| `src/screens/{Home,Processing,Result}Screen.tsx` | The three screens from the brief's UI spec |
| `src/hooks/useTryOnFlow.ts` | The whole client-side state machine: selected photos → submit → poll → result/error |
| `src/api/tryOnClient.ts` | Thin fetch wrapper matching the backend's schemas exactly |
| `src/components/PhotoPicker.tsx` | Shared camera/gallery picker; "Take Your Photo" uses `capture="user"` to jump straight to the camera on mobile, "Choose Your Photo"/"Upload Clothing" omit it so the OS offers camera+gallery+files |
| `src/utils/resultActions.ts` | Save (blob download — works cross-origin, unlike a plain `<a download>` on a cross-origin URL) and Share (Web Share API with a file, falling back to download where unsupported, e.g. most desktop browsers) |

**Deliberately not built yet** (later milestones per the brief): "Paste Product URL" (Milestone 8 — the backend has no URL extraction to call), full PWA installability (Milestone 10). Accounts/login shipped in Milestone 6, below — still entirely optional, never forced.

**Verified, not just written** — `npm run build` and `npm run lint` both clean, then a headless-Chromium pass (Playwright, 390×844 mobile viewport, no project `run` skill existed yet so this used the generic browser-driven pattern) against the actual running frontend+backend:
- Home screen renders correctly at mobile width, matches the brief's wireframe (screenshot below)
- Submit button correctly starts disabled, enables once both photos are selected, disables again if a photo is removed
- Selecting photos shows live previews with a working remove button; picking a garment reveals the category selector
- Submitting calls the real `POST /api/try-on`, transitions to the processing screen, zero console errors throughout
- Did *not* wait for a real submission to finish in this pass (proven separately and repeatedly in Milestones 2-3 — a generation takes 10-70+ minutes on this CPU-only machine, which the processing screen's copy sets expectations for)

![Home screen, filled in](../frontend/docs-assets/home_screen_filled.png)

**Known gap:** killing the dev server mid-job (as happened once while smoke-testing) leaves that job's temp files behind — same root cause as the backend gap noted above.

## Milestone 6 — User accounts & secure image handling

Real persistence (PostgreSQL + SQLAlchemy + Alembic) and optional accounts (JWT auth). See [ARCHITECTURE.md](ARCHITECTURE.md#accounts--auth-milestone-6) for the design, [ENVIRONMENT.md](ENVIRONMENT.md) for what got installed.

**One-time setup (already done on this machine, documented for a fresh one):**
```powershell
winget install --id PostgreSQL.PostgreSQL.17 -e --silent --accept-package-agreements --accept-source-agreements --override "--mode unattended --superpassword devpassword --servicename postgresql-x64-17 --serverport 5432"
$env:PGPASSWORD = "devpassword"
& "C:\Program Files\PostgreSQL\17\bin\psql.exe" -U postgres -h localhost -c "CREATE DATABASE aitryon;"

ai\.venv\Scripts\python.exe -m pip install sqlalchemy alembic psycopg2-binary bcrypt pyjwt email-validator
```

**Every time you pull schema changes:**
```powershell
cd backend
..\ai\.venv\Scripts\python.exe -m alembic upgrade head
```

**New endpoints:**

| Endpoint | Auth | Purpose |
|---|---|---|
| `POST /api/auth/signup` | — | `{email, password}` → `{access_token}` |
| `POST /api/auth/login` | — | Same response shape. Wrong password and unknown email return the *identical* message/status, so a login attempt can't be used to enumerate registered emails |
| `GET /api/auth/me` | required | Current user's `{id, email, plan, created_at}` |
| `POST /api/try-on` | optional | Unchanged contract — an `Authorization: Bearer` header just attributes the job to that user |
| `POST /api/try-on/{job_id}/save` | required | Marks a completed job `saved` (exempt from the TTL cleanup below). Only the job's own creator can call this — 403 for anyone else, 403 for an anonymous job even from a now-signed-in user (no retroactive claiming) |

**Frontend:** `useAuth.ts` (token in `localStorage`, wrapped in try/catch — see the hook for why), `AuthBar`/`AuthModal` components (a small "Sign in" link on the home screen, not a gate on anything), and `ResultScreen`'s new "Save to my account" button, shown only when signed in, alongside the pre-existing device "Save".

**A real bug caught mid-build, not shipped:** the first migration had no `ON DELETE` behavior on `jobs.user_id`, so deleting a user with existing jobs would have failed with a foreign-key violation. Caught while writing the *test cleanup fixture* (deleting a test user after a test that created a job for them failed) — fixed by adding `ondelete="CASCADE"` to the FK (and a SQLAlchemy naming convention on `Base.metadata`, since the fix also surfaced that Alembic can't autogenerate a migration for an unnamed constraint). Migration history was reset once, cleanly, since this was still pre-any-real-data.

**Verified, in order:**
1. `pytest` — 8 pre-existing tests (unaffected, still DB-free) + 9 new tests in `tests/test_auth_api.py` (signup, duplicate-email rejection, login success/failure with the identical-message check, `/me`, save-by-owner, save-requires-auth, can't-save-someone-else's-or-an-anonymous-job, save-unknown-job). The new file auto-skips (not fails) if Postgres isn't reachable, so the suite stays runnable without it.
2. The real running server: signup → login → `/me` → duplicate signup (409) → wrong password (401, identical message to unknown-email) — all via `curl`.
3. A real authenticated generation (`num_timesteps=4`, real model) through to `/save`, then confirmed a second account gets 403 trying to save the same job.
4. Playwright against the real frontend+backend: opened the sign-in modal, switched to sign-up, created an account, confirmed the header updates to show the signed-in email — zero console errors.
5. Confirmed the `ON DELETE CASCADE` fix for real: deleting a test user whose job was still in the database succeeded and took the job row with it (checked in `psql` before and after).

**Known limitations, carried forward on purpose:**
- `backend/scripts/cleanup_expired_results.py` (the "unsaved images are temporary" enforcement) exists and works but isn't wired to a scheduler yet — needs real infra (cron/Task Scheduler/hosted cron), out of scope for this milestone.
- Account deletion isn't a feature yet (no endpoint) — the cascade behavior is in place for when it is.
- `plan` is a free-text column defaulting to `"free"`; nothing reads or enforces it yet (Milestone 11).

## Milestone 7 — Product image extraction

`product-extractor/` (see its own README) is the modular system from the brief's section 7: isolate the actual clothing item out of whatever a user uploads, whether that's already a clean product photo or a full shopping-site screenshot. This milestone covers Methods B and C (upload / screenshot); Method A (product URL) is Milestone 8, Method D (browser extension) is Milestone 9 — both will call into the same `ExtractionService`.

**Why classical CV, not a new deep learning model** (full reasoning in `saliency_extractor.py`'s docstring — same rigor as every model choice in [AI_MODEL_LICENSE.md](AI_MODEL_LICENSE.md), applied to a case where the honest answer was "don't add one yet"): `cv2.saliency` (OpenCV's spectral-residual saliency — a classical algorithm, not learned weights) finds the most visually distinctive region in an image with no model download and no new license research. Required switching `opencv-python` → `opencv-contrib-python` in the vendored `fashn-vton-1.5` (a strict superset, same `cv2` import — OpenCV's own guidance is to never have both installed at once).

**Install:**
```powershell
ai\.venv\Scripts\python.exe -m pip uninstall -y opencv-python
ai\.venv\Scripts\python.exe -m pip install opencv-contrib-python
ai\.venv\Scripts\python.exe -m pip install -e product-extractor
```

**Tuning the confidence metric — a real dead end, corrected before it shipped:** the first version scored confidence as the raw mean saliency intensity inside the detected region. Tested against a synthetic "screenshot" (a real product photo pasted into a mock shopping page) and a control (an already-clean product photo, which should *not* trigger cropping) — the screenshot case scored only 0.264, below the sensible-looking 0.35 threshold, even though the detected bounding box was already landing in the right place. Root cause: spectral residual saliency's absolute output isn't a calibrated probability. Switched to **dominance** — what fraction of *all* detected salient area belongs to the single largest region — which separates the two cases cleanly (~0.86 for the correct screenshot detection vs. ~0.19 for the clean-photo control, which has many similarly-sized salient sub-features and no single standout region). This is why the tests in `tests/test_product_extractor.py` assert on both cases, not just the happy path.

**API:** `POST /api/extract-product-image` — multipart `image` upload, returns the (possibly cropped) PNG directly with `X-Extraction-Applied`/`X-Extraction-Confidence` headers. Deliberately **synchronous**, not job/poll like `/api/try-on`: classical CV runs in milliseconds on this CPU, so the job pattern (which exists because of the AI model's cost) would be pure overhead here. Its own, more generous rate limit (`AITRYON_EXTRACTION_RATE_LIMIT_*`) reflects that different cost profile.

**Frontend:** an "✂ Auto-detect clothing in photo" button appears once a clothing photo is selected (`src/api/extractionClient.ts`, wired into `HomeScreen.tsx`) — optional, never automatic, so a already-good upload is never silently altered without the user asking.

**A real bug caught by the E2E pass, not the unit tests:** `X-Extraction-Applied`/`X-Extraction-Confidence` never showed up in the frontend (`response.headers.get(...)` returned `null`) even though `curl` and the backend test suite both saw them fine. Cause: on a cross-origin request (frontend `:5173`, backend `:8000` in dev), the browser's `fetch()` API silently strips any response header not explicitly exposed via CORS — `curl` and Playwright's own network listener both bypass that browser-only restriction, which is exactly why the bug was invisible to every check except an actual click in an actual browser. Fixed with `expose_headers=[...]` on the `CORSMiddleware` in `main.py`. Worth remembering for any future endpoint that puts data in custom response headers.

**Verified, in order:**
1. Interactive tuning against two synthetic scenarios (see above), saved as `tests/test_product_extractor.py` (3 tests: extracts correctly, leaves a clean photo alone, doesn't crash on a blank image).
2. `tests/test_extraction_api.py` (4 tests: the same two scenarios through the actual HTTP endpoint, non-image rejection, rate limiting) — all fast, no AI model or database needed.
3. The real running server via `curl`: mock screenshot → applied, clean photo → not applied, non-image → 400. All matched the unit-test expectations exactly.
4. Playwright against the real frontend+backend — this is the pass that caught the CORS bug above. After the fix: uploading the mock screenshot and clicking "Auto-detect" correctly replaced the preview with the cropped image and showed "✓ Cropped to the clothing item."; uploading an already-clean photo correctly showed "no changes made" instead. Zero console errors in both cases.

**Known limitations, carried forward on purpose:**
- Tuned against two synthetic test images, not a corpus of real shopping-site screenshots (none exist yet in this project). The dominance-ratio approach should generalize reasonably, but the exact thresholds (`_MIN_DOMINANCE`, `_MIN_AREA_RATIO`, `_MAX_AREA_RATIO`) are first-pass estimates, flagged as such in the code, and worth revisiting once real usage data exists.
- Only finds one candidate region. A screenshot with multiple product thumbnails (e.g. a search results grid) would currently just get the single most dominant one — reasonable for a single-product page, not yet handled for a listing page.
- Not wired into the try-on submission flow automatically — it's an optional button the user chooses to press, not a preprocessing step forced on every upload.

## Milestone 8 — Product URL support (brief section 7, Method A)

Given a product page link, fetch it and find its product image — reusing Milestone 7's `ExtractionService`/`SaliencyProductExtractor` unchanged for the actual cropping. New pieces live in `product-extractor/src/product_extractor/fetchers/`.

**How a product image is found, in order:** JSON-LD `Product` schema (`<script type="application/ld+json">`, handling the bare-object/array/`@graph`-wrapper shapes real sites use, and `image` as a string, an `ImageObject`, or a list of either) → Open Graph `<meta property="og:image">` → give up with a clear message. Both are public, documented conventions sites publish specifically so external services can read a page's canonical image — the opposite of scraping fragile, undocumented CSS selectors, and why no per-site scrapers were built for this milestone (the brief's explicit "Do not assume every website has the same HTML structure" cuts against hand-tuned per-site parsing as much as it argues for one).

**What this deliberately never does, per brief section 7:** log in, solve a CAPTCHA, retry around a block, or use a browser-spoofing User-Agent to evade detection — it identifies itself honestly (`AITryOnBot/0.1 (+...)`) and accepts that some sites will refuse it. `robots.txt` is checked (fetched and parsed ourselves, not via `urllib.robotparser`'s own unprotected fetch) before ever requesting the actual page; a `Disallow` match is treated exactly like every other failure — the brief's required fallback (a clear message pointing at Method B/C upload) via `ProductExtractionError`.

**SSRF protection (`fetchers/ssrf_guard.py`) — the real security work in this milestone:** accepting an arbitrary URL from any visitor and having the server fetch it is a classic vector for turning our server into a proxy into networks it can't otherwise reach (cloud metadata endpoints, internal services, `localhost`). `assert_safe_url()` resolves the hostname and rejects private/loopback/link-local/multicast/reserved ranges — called before the initial request *and* before every redirect hop (redirects are followed manually, capped at 3, specifically so each one gets re-checked rather than trusting the first check to cover a chain that could end up somewhere different). Documented honest limitation: this is a check-then-connect design, not immune to DNS rebinding between the two — a fully airtight version pins the checked IP for the actual connection, which httpx doesn't make trivial and this milestone doesn't implement.

**A real false-positive bug found and fixed during verification, not left in:** the very first real-URL test (against a real public site, not a mock) came back blocked with the generic SSRF message. Root cause: this network reaches IPv4-only sites over IPv6 via NAT64 (RFC 6052) — DNS resolution returned a `64:ff9b::/96`-prefixed address, which Python's `ipaddress.is_reserved` correctly flags as IANA-reserved but which isn't actually risky: it's a well-known mechanism for synthesizing a route to a real, checkable public IPv4 address, embedded in the address's low 32 bits. Fixed by unwrapping that embedded address and checking *it* instead of trusting `.is_reserved` for this one specific, legitimate prefix — with a regression test (`test_ssrf_guard_unwraps_nat64_synthesized_addresses`) covering both the "safe embedded address" and "unsafe embedded address" cases, not just the one that broke.

**API:** `POST /api/extract-product-url` — JSON `{"url": "..."}`, same response shape as `/api/extract-product-image` (PNG bytes + `X-Extraction-Applied`/`X-Extraction-Confidence` headers). Its own rate limit, tighter than the pure-image endpoint's: it's a lever for making our server issue outbound requests, which — SSRF mitigations aside — is still worth limiting more conservatively than a purely local operation.

**Frontend:** the brief's wireframe's "[ Paste Product URL ]" button, built as a collapsed "Or paste a product URL" link (`src/components/ProductUrlInput.tsx`) below "Upload Clothing" — expands to a URL field + Fetch button only when tapped, so it doesn't compete with the primary upload action for attention. A successful fetch behaves exactly like a completed upload (same preview, same auto-detect status display, same category picker) since it already went through the identical server-side extraction pipeline.

**Verified, in order:**
1. `tests/test_url_fetcher.py` (19 tests): SSRF guard against 11 unsafe targets and 2 safe ones, the NAT64 regression, JSON-LD extraction, OG fallback, no-image and unreachable-page errors, robots.txt disallow — all against `httpx.MockTransport`, zero real network calls, fully deterministic.
2. `tests/test_extraction_url_api.py` (6 tests): the API layer (request validation, error surfacing, rate limiting) against a fake fetcher.
3. The real running server via `curl`: confirmed SSRF blocking for a cloud metadata IP and `localhost` both return the generic message; then the NAT64 bug above, found on the very first real-URL attempt against a real public site (`books.toscrape.com` — chosen specifically because it's a site built for scraping practice, not a live commercial one, for this exact kind of verification); after the fix, that same site correctly reached the "no product image found" fallback (it has no OG/JSON-LD product data — an honest, correct outcome, not a bug) and a Wikipedia page's `og:image` was correctly found, downloaded, and cropped by the reused Milestone 7 extractor.
4. Playwright against the real frontend+backend: pasting a URL and fetching populated the clothing photo exactly like an upload would, and a blocked URL showed the fallback message in the UI with the "Upload Clothing" button still right there — zero console errors in both cases.

**Known limitations, carried forward on purpose:**
- DNS-rebinding gap noted above (check-then-connect, not IP-pinned) — a real residual risk, not a theoretical one, worth closing before this handles untrusted traffic at real scale.
- No per-site extractors — every site goes through the same JSON-LD/OG-tag path. A site with neither (unfortunately common) always falls back to asking for an upload. Improving coverage without hand-tuning fragile per-site CSS selectors (which the brief's "don't assume every site has the same structure" argues against maintaining long-term) is future work, not solved here.
- A single candidate image per page, same as Milestone 7's single-region limitation — no handling yet for a listing/search-results page with multiple products.

## Milestone 9 — Browser extension (brief sections 7/8, Method D)

`extension/` — see its own README for load/config instructions. Deliberately thin, per the brief ("Do NOT make the extension the foundation of the application"): a content script detects a likely product page and injects a **✨ Try It On** button; clicking it opens the web app with the page's URL attached, and the web app (already built) does everything else. **No new backend endpoint exists for this milestone** — it's a new client for the exact same `POST /api/extract-product-url` Milestone 8 built, which is precisely what the brief's "the same backend must work with the web app, the extension, and future mobile integrations" asks for.

**Detection** (`content.js`, client-side JS mirroring the same signals the backend's Method A already looks for): JSON-LD `Product` schema, or `og:type=product`, or (`og:image` + a price meta tag together, since an image alone is too weak a signal on its own). No per-site logic, no fixed domain list — same reasoning as Milestone 8's extractor.

**Permission footprint, deliberately minimal:** no `host_permissions`, no background service worker, no storage access — just a content script reading the current page's own DOM and calling `window.open()`. Worth being deliberate about, since this script runs on every page the user visits; smaller footprint means less to review and less to trust blindly.

**Frontend change to support the handoff:** `HomeScreen.tsx` reads `?productUrl=` once via a lazy `useState` initializer (same "runs exactly once at mount" pattern as `ProcessingScreen.tsx`'s elapsed-timer), strips it from the URL immediately via `history.replaceState` (so returning to this screen later, e.g. "Try Another", never re-triggers the same fetch), and passes it to `ProductUrlInput` as `initialUrl`, which auto-runs the exact same fetch its manual "paste a URL" flow already does.

**A lint issue worth recording:** the auto-fetch effect's first draft used an empty dependency array with a `// eslint-disable-next-line` comment to silence the "missing dependency" warning — which doesn't actually work, because oxlint's disable-comment syntax is `// oxlint-disable-next-line`, not the ESLint one. Rather than chase the right magic comment, switched to the more idiomatic fix: a `useRef` guard flag plus a complete, honest dependency array (`[initialUrl, fetchUrl]`, with `fetchUrl` wrapped in `useCallback`) — passes the linter because it's actually correct, not because a warning was suppressed.

**Verified, in order:**
1. Playwright loading the *actual* unpacked extension into a real Chromium instance (`chromium.launchPersistentContext` with `--load-extension`) against local static HTML fixtures with real `Product` JSON-LD: the button appears on a product-like fixture and is absent on a plain one; clicking it opens a new tab whose URL contains the correctly `encodeURIComponent`-escaped source page URL.
2. The full handoff through the real running frontend+backend: navigating directly to the same URL the extension's `window.open()` produces (a real extractable page — reused the Wikipedia URL already proven in Milestone 8) auto-triggered extraction with zero manual interaction, correctly populated the clothing photo, and the query param was gone from the address bar afterward — zero console errors.

Given the project's established pattern of using Playwright as an ad-hoc, documented verification tool for each milestone rather than committed CI infrastructure (no `npm test` exists for the frontend either), this extension test was run the same way and is not checked into the repo as a permanent test file — reproduce it with the exact commands in `extension/README.md`'s Testing section if needed again.

**Known limitations, carried forward on purpose:**
- No `MutationObserver` — a single-page-app site that swaps in product content without a full navigation won't get re-detected. Real complexity not yet justified without evidence it's needed against real shopping sites.
- Content script runs its (cheap) detection on every HTTP/HTTPS page visited, not a fixed shopping-site allowlist — a deliberate choice (the brief argues against assuming a fixed set of sites) but worth being upfront about, same as noted in the extension's own README.
- `WEB_APP_URL` is a hardcoded constant (`http://localhost:5173`) with a `window.__AI_TRYON_WEB_APP_URL__` override hook — fine for local dev, needs a real build-time configuration story before packaging for an actual store listing (Milestone 13 territory).
