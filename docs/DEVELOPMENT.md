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
| 10 | PWA / mobile optimization | ✅ Done |
| 11 | Usage limits & premium architecture | ✅ Done |
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

### Milestone 9, extended — shopping-site-first experience & the image-URL handoff

Motivated by a real, reproduced failure: pasting a genuine Flipkart product URL into the web app returned "We couldn't reach that page." Diagnosed end-to-end (backend logs, the URL fetcher, SSRF checks, redirects, HTTP status, content type) before any fix was written. Root cause, confirmed independent of User-Agent/headers by direct `curl` reproduction: Flipkart's own reCAPTCHA Enterprise wall returns HTTP 403 to any non-browser fetch of the page itself — `<title>Flipkart reCAPTCHA</title>`, `google.com/recaptcha/enterprise.js` in the response body. Not a bug in `fetchers/http_fetcher.py`; `ssrf_guard.py`, redirect handling, and JSON-LD/OG parsing were all confirmed uninvolved. The project's constraints (never bypass CAPTCHA/anti-bot systems) rule out fixing this by trying harder to get past the wall.

The actual product ask this surfaced: the extension should let a user try on the item they're already looking at, without needing the backend to ever fetch that gated page route at all. Three pieces, all reusing existing infrastructure rather than duplicating it:

**1. Richer, still-conservative detection.** A `MutationObserver`-based re-check (debounced, capped at 8s after `document_idle`) catches single-page-app sites that render their JSON-LD/Open Graph product data in after the initial page load — the button previously never appeared on those. Deliberately did **not** add generic structural heuristics (price-regex, "Add to cart" text matching) for sites with neither JSON-LD nor Open Graph product data — that gap is accepted on purpose, to keep today's false-positive rate and avoid a per-site maintenance burden, rather than trying to close every detection gap.

**2. A polished floating panel, not just a button.** Moved from a single `#ai-tryon-button` styled via a manifest-injected `content.css` to a Shadow DOM root the content script builds itself (pill collapsed state → card expanded state with a product thumbnail preview and a dismiss control), specifically because a manifest-injected stylesheet only reaches the main document — it can't style a shadow root created at runtime. `content.css` itself is left in the repo, unreferenced, rather than deleted, since the panel's CSS is now inlined directly in `content.js` and injected into its own shadow root.

**3. The image-URL handoff — the actual fix for the Flipkart-class failure.** The extension already reads a page's JSON-LD/`og:image` to detect it as a product page; it now also extracts the product's direct **image URL** from those same signals and, on click, hands that URL to the web app instead of the page URL (`?productImageUrl=` instead of `?productUrl=`, recomputed fresh at click-time so a slow-rendering SPA still works). New backend pieces, all narrow additions alongside the existing ones, not replacements: `HttpProductPageFetcher.fetch_image_from_url()` (same `assert_safe_url()` SSRF check, but skips robots.txt and HTML/JSON-LD parsing entirely — there's no page to scrape, just one image resource), `ExtractionService.extract_from_image_url()`, and `POST /api/extract-image-url` with its own rate limiter (`AITRYON_IMAGE_URL_EXTRACTION_RATE_LIMIT_*`, mirroring the existing URL-extraction limiter's reasoning). `SaliencyProductExtractor` and the AI try-on pipeline itself are untouched — every path still converges on the same `ExtractionResult`. When no image URL is found (Product schema/OG matched, but no image field), the extension falls back to the original page-URL handoff exactly as before — no regression to that path or to direct image upload.

Why this actually solves the motivating bug without weakening anything: the backend never requests the page route that Flipkart's anti-bot wall gates — only a specific image URL, typically served from a separate CDN host built for hotlinking/embedding, which the user's own browser (with its own session) already legitimately loaded. If an image host also happens to be gated, extraction fails with the same honest, pre-existing message — never worked around. `ssrf_guard.py` was not modified; the new endpoint routes through the identical, unmodified check.

**Verified, in order:**
1. `tests/test_url_fetcher.py`: `fetch_image_from_url()` against a mocked image response (confirms *only* the image URL is requested — no page or robots.txt fetch), still SSRF-guarded, clear error on non-image content.
2. `tests/test_extraction_image_url_api.py`: the new API layer — success, blocked/unreachable/invalid-image error surfacing, empty/missing URL validation, rate limiting — against a fake fetcher, mirroring `test_extraction_url_api.py`'s existing structure. `tests/test_extraction_url_api.py`'s `FakePageFetcher` updated for the interface's new abstract method.
3. Full suite (`pytest`) passing throughout.
4. Frontend: `npm run build` (`tsc -b && vite build`) and `npm run lint` (`oxlint`) both clean — no new warnings beyond the same pre-existing `set-state-in-effect` warning class already present on three other files using the same auto-fetch-on-mount pattern.
5. Extension: `node --check content.js` for syntax; Playwright extension-loading verification against local fixtures, same approach as Milestone 9's original verification (see `extension/README.md`'s Testing section).

## Milestone 10 — PWA / mobile optimization

Two threads: making the app genuinely installable (not just responsive — Milestone 4 already covered that), and auditing real mobile usability details that only show up once you go looking (touch target sizes, offline behavior).

**Installability, via `vite-plugin-pwa`** (Workbox under the hood, generates the service worker rather than hand-rolling one): full icon set added (`frontend/public/icons/` — 192px and 512px `any`-purpose PNGs, a 512px `maskable` variant with content kept inside the ~80% safe zone so Android's adaptive-icon masking doesn't clip it, and a 180px `apple-touch-icon.png`, since iOS Safari doesn't use the web manifest for "Add to Home Screen" the way Android does — it needs its own icon + `apple-mobile-web-app-*` meta tags in `index.html`). The old hand-written static `public/manifest.json` was removed in favor of the plugin generating `manifest.webmanifest` from `vite.config.ts`'s config, so the manifest and the precached icon list can never drift apart.

**Caching strategy — deliberately narrow:** the service worker precaches the app shell (JS/CSS/HTML/icons) for instant repeat loads and *some* offline capability, but `/api/*` is explicitly `NetworkOnly` (matched by path, not a hardcoded origin, since the backend can be same- or cross-origin depending on `VITE_API_BASE_URL`). This isn't a partial implementation of "cache the API too" — it's the correct end state: job status, results, and auth must always reflect the real server, and a stale cached AI result or job status would be actively misleading, not a helpful offline convenience. `useOnlineStatus.ts` (plain `navigator.onLine` + online/offline events, unrelated to the service worker's own lifecycle) drives a banner so a disconnected user gets an honest, clear message instead of the app just failing confusingly on the first API call.

**Touch target audit — a real fix, not a formality:** went through every interactive element and measured against the 44px minimum (Apple HIG / WCAG 2.5.5) — several were genuinely too small: the photo-preview remove button (32px), several `text-xs` buttons with only `py-2` padding (~32-36px), and multiple icon-only/plain-text buttons with no padding at all (auth modal's close button, "Sign in"/"Sign out", the URL-paste toggle link). Fixed with a consistent `min-h-11` (44px) floor plus `flex items-center` where needed to actually center content in the enlarged hit area, not just pad it — this was verified computationally (see below), not eyeballed.

**Verified, in order:**
1. `npm run build` — confirmed `dist/sw.js`, `dist/workbox-*.js`, and `dist/manifest.webmanifest` are generated (15 precached entries, ~260KB); the generated manifest itself parsed and checked for all three required icon entries.
2. Playwright against the **production build** served via `vite preview` (not `npm run dev` — a dev-mode service worker isn't representative of what actually ships): manifest link present and fetchable, all three icon files and the apple-touch-icon fetchable, `navigator.serviceWorker.ready` resolves with `active.state === "activated"`.
3. A real, computational touch-target audit: filled in both photos so every conditional button (category picker, auto-detect) rendered, measured the bounding box of every visible button, asserted `height >= 44px` — caught real failures before the fix, passed cleanly after.
4. Real offline behavior: loaded once online (letting the SW precache), then `context.setOffline(true)` and reloaded — the full app shell still rendered from cache, and the offline banner appeared. Screenshot confirms both together.
5. Layout sanity at a small phone (360×640) and a tablet (768×1024) viewport — no horizontal overflow at either size.

**What wasn't obtained: a Lighthouse PWA score.** Attempted (`npx lighthouse ... --only-categories=pwa`), but current Lighthouse has actually removed the standalone "pwa" category from automated scoring entirely (Google's own call — the PWA checklist moved to manual/DevTools-panel territory in recent versions), and a fallback run with `accessibility,best-practices` hit a Windows-specific `chrome-launcher` temp-directory cleanup permission error (`EPERM` on `rmSync`) unrelated to this app. Not chased further: every concrete criterion Lighthouse's old PWA category checked — valid manifest with correct icons, registered and active service worker, offline load — was already verified directly and more precisely above.

**Known limitations, carried forward on purpose:**
- No update-available UI — `registerType: 'autoUpdate'` means a new service worker version activates silently on the next load rather than prompting the user. Fine for this app's low deploy-frequency stage; a "New version available, refresh?" prompt (via `vite-plugin-pwa`'s `virtual:pwa-register` hooks) is a natural addition once deploys are frequent enough to matter.
- Precaching is app-shell only — no attempt to make the actual try-on generation work offline (it fundamentally can't, per requirement: it needs the backend and the AI model), and the UI is honest about that via the offline banner rather than pretending otherwise.
- Real device testing (an actual iPhone/Android phone, not just Chromium viewport emulation) hasn't been done — emulation covers layout/sizing but not every iOS Safari PWA quirk (e.g. its historically limited service-worker/storage guarantees). Worth doing before shipping, flagged rather than assumed away.

## Milestone 11 — Usage limits & premium architecture

The `User -> Account -> Plan -> Usage quota -> AI generation` pipeline from brief sections 4/23, wired up for real: `users.plan` (a plain column since Milestone 6, unenforced until now) now actually gates generation, against limits that live in the database, not in code.

**New `plans` table** (`backend/app/db/models.py`): `max_generations_per_day`, `max_generations_per_month`, `max_num_timesteps` — nullable columns (`NULL` = unlimited), one row per plan name, `users.plan` is now a real foreign key into it. Seeded (migration `bb26ae740580`) with the brief's own example numbers — free: 5/day, premium: 100/month — as **starting defaults an operator can change with a single UPDATE statement**, not fixed constants; verified live below, not just asserted. `max_num_timesteps` (free: 30, premium: 50 — the existing global ceiling) is the concrete answer to the brief's "higher resolution... advanced features" for premium: reusing the diffusion step count that already existed as a per-request parameter, rather than inventing a new axis that doesn't exist yet.

**Two separate layers, on purpose, not redundant:** `RateLimiter` (Milestone 3) still guards short-burst abuse and is in-memory/process-local. The new `QuotaService` (`backend/app/services/quota_service.py`) enforces the actual plan allowance and is **database-backed** — it has to be, to survive a restart and stay correct across multiple worker processes, which the abuse-guard rate limiter never promised. Both apply to the same request; they answer different questions.

**Anonymous usage is tracked too** (brief: the core flow must keep working without an account) — `jobs.client_ip` (new column) is recorded on every job, and `QuotaService` counts by `user_id` when signed in, by `client_ip` otherwise. Counts **every** job regardless of status (pending/processing/completed/failed) — a failed generation still spent real compute, so it counts the same way most usage-based billing treats a failed API call: the resource was consumed either way.

**New endpoint:** `GET /api/usage/me` — works for both authenticated and anonymous callers (same identity resolution as `/api/try-on` itself), returns current usage/limits/remaining. Lets the frontend show "3 of 5 free generations left today" honestly *before* the user hits the wall, not only as a 429 surprise on submit.

**Frontend:** `UsageIndicator.tsx` on the home screen — a small, unobtrusive status line, amber and explicit ("0 of 5 free generations left today — premium plans are coming soon") once exhausted, but no upgrade button or payment UI anywhere (that's explicitly Milestone 12, not this one — a button that led nowhere would be worse than no button). The actual quota-exceeded error on submit needed **zero new frontend error-handling code**: the existing `ApiError`/error-banner plumbing from Milestone 3 already surfaces any non-2xx `detail` message, 429 included.

**A real test-isolation bug caught before it caused flaky failures, not after:** anonymous quota tracking by IP means every `TestClient` request in a test file shares one identity. Wiring the real `QuotaService` into `test_auth_api.py`'s existing test app by default would have made anonymous-submitting tests (`test_save_requires_auth` and others, already in that file since Milestone 6) accumulate real usage across every test run **for the rest of the day**, eventually hitting the free plan's 5/day cap and failing unpredictably depending on how many times the suite had already run — not on that day's first run, which is exactly the kind of bug that's invisible until it isn't. Caught by tracing through the shared-identity implication before running anything, not by hitting the flake. Fixed with a `FakeQuotaService` (moved to a new shared `tests/conftest.py`, since both `test_tryon_api.py` and `test_auth_api.py` need it) as the *default* for every existing test, and real quota enforcement tested two different, both-correct ways instead: authenticated-user tests use a fresh per-test user (a brand-new `user_id` has no history — naturally isolated, no special handling needed) and anonymous-IP tests (`test_quota_service.py`) construct a synthetic, per-test-unique fake "IP" string directly against the database, cleaned up unconditionally afterward, never touching the shared TestClient identity at all.

**Verified, in order:**
1. `tests/test_quota_service.py` (10 tests): `QuotaStatus`'s `is_exceeded`/`remaining_*` logic tested with no database at all (pure dataclass math); `QuotaService.get_status` tested against the real database with synthetic per-test IPs — counts correctly, correctly excludes jobs from a prior month, correctly falls back to the `free` row for an unrecognized plan name.
2. `tests/test_tryon_api.py` (2 new tests, `FakeQuotaService`, no database): quota-exceeded blocks submission with the right message; a plan's `max_num_timesteps` cap actually reaches the provider call, overriding a larger requested value.
3. `tests/test_auth_api.py` (3 new tests, **real** `QuotaService`, real database): submitting exactly the free plan's seeded daily limit succeeds every time, one more is blocked with the same message; `/api/usage/me` numbers move by exactly 1 after a real submission; the endpoint works with no `Authorization` header at all.
4. The full 64-test suite run **twice in a row** specifically to confirm the isolation fix actually works, not just that it compiles — both runs passed identically.
5. The real running server, `curl`: confirmed a fresh anonymous IP starts at 5/5, submitted 5 real jobs watching `used_today` climb to 5, confirmed a 6th is blocked with `HTTP 429` and the exact user-facing message; signed up a real user and confirmed their quota was independent of the now-exhausted anonymous IP; **updated that user's `plan` to `'premium'` with a single `UPDATE` statement** (no code change, no restart) and confirmed `/api/usage/me` immediately reflected the new limits (`null`/unlimited daily, 100/month, `max_num_timesteps: 50`) — this is the concrete proof of "configurable through backend configuration/database rather than hard-coded."
6. Playwright against the real frontend+backend: the usage indicator renders the correct count on load, turns amber with the "premium plans are coming soon" copy once exhausted, and a real submission attempt past the limit shows the full, correct error banner with the photos still in place (so the user doesn't lose their work, just has to wait) — zero console errors.

**Known limitations, carried forward on purpose:**
- No admin UI for editing plan limits — it's a normal database row, editable with `psql`/any DB tool today. A real admin panel is out of scope until there's an operator workflow that needs one.
- No plan-change endpoint — `users.plan` is only ever changed directly in the database (as done above) or, eventually, by the payment flow Milestone 12 owns. Nothing today lets a user request their own upgrade, which is correct for this milestone (no payments yet) but means "premium" is currently unreachable through the product itself.
- `used_this_month` for a user who changes plans mid-month counts everything from the 1st of the month regardless of which plan was active for each job — the simplest, most defensible semantic ("how much has this account consumed this period"), verified live above, but worth naming as a real design choice rather than an oversight if it ever needs to change.
