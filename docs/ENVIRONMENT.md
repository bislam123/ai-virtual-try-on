# Environment

Canonical reference for what's installed and what every environment variable does. For step-by-step setup/run commands, see [DEVELOPMENT.md](DEVELOPMENT.md).

## Development machine

| | |
|---|---|
| OS | Windows 11 Home, 64-bit |
| CPU | AMD Ryzen 3 7330U (4C/8T) |
| RAM | 15 GB |
| GPU | AMD integrated graphics — **no CUDA, no GPU acceleration available**. All local AI inference runs on CPU. See [AI_MODEL_LICENSE.md](AI_MODEL_LICENSE.md) and [ARCHITECTURE.md](ARCHITECTURE.md#dev-environment-vs-inference-environment) for why production inference needs a separate rented-GPU host. |

## Installed software

| Tool | Version | Notes |
|---|---|---|
| Python (system default) | 3.14.7 | Untouched — never used for AI/backend work; too new for parts of the ML dependency chain |
| Python (AI/backend) | 3.11.9 | Installed side-by-side via winget, isolated in `ai/.venv`. See DEVELOPMENT.md for the exact install command and a winget/Windows-Installer-service gotcha hit during setup |
| Node.js | 24.19.0 | Frontend |
| npm | 11.17.0 | Frontend |
| Git | 2.55.0 | |
| PostgreSQL | 17.11 | Installed via winget (`PostgreSQL.PostgreSQL.17`), running as the `postgresql-x64-17` Windows service on port 5432. Local dev superuser: `postgres` / `devpassword` (dev-only — see below) |

## Environment variables

All read by `backend/app/config.py` (`pydantic-settings`, prefix `AITRYON_`), with dev-friendly defaults baked in — copy [.env.example](../.env.example) to `.env` only to override them.

| Variable | Default (dev) | Purpose |
|---|---|---|
| `AITRYON_ENVIRONMENT` | `development` | Set to `production` in a real deployment. Enables a startup check (`backend/app/config.py`'s `_refuse_insecure_production_secrets`) that refuses to start if `AITRYON_JWT_SECRET_KEY`/`AITRYON_DATABASE_URL` below are still at their insecure dev defaults. Never set by local dev or the test suite, so neither is affected. |
| `AITRYON_WEIGHTS_DIR` | `ai/models/fashn-vton-1.5` | Where the AI model weights live |
| `AITRYON_DEVICE` | `cpu` | `cuda` on a GPU inference host |
| `AITRYON_DEFAULT_NUM_TIMESTEPS` | `30` | Diffusion steps per generation |
| `AITRYON_MIN_NUM_TIMESTEPS` / `AITRYON_MAX_NUM_TIMESTEPS` | `4` / `50` | Clamp range for the request param |
| `AITRYON_INFERENCE_TIMEOUT_SECONDS` | `3600` | How long `SelfHostedVTONProvider.generate()` waits before giving up and reporting failure — **not a true hard kill**, see `providers/selfhosted.py`'s module docstring. Lower this substantially (e.g. `120`) on a real GPU deployment. |
| `AITRYON_PROVIDER_LOCK_ACQUIRE_TIMEOUT_SECONDS` | `3600` | How long a request waits to acquire the provider's internal lock before failing as "busy" |
| `AITRYON_STALE_JOB_THRESHOLD_MINUTES` | `120` | How long a job may sit in `processing` (e.g. the process was killed mid-generation) before `services/job_recovery.py` marks it `failed` |
| `AITRYON_STORAGE_DIR` | `backend/storage` | Local temp/results file root |
| `AITRYON_MAX_UPLOAD_SIZE_MB` | `10` | Per-image-field size cap, checked after the file is already read (`app/core/validation.py`) |
| `AITRYON_MAX_IMAGE_DIMENSION_PX` | `4096` | Per-image-field dimension cap, checked from the file's header *before* the expensive full pixel decode (`app/core/validation.py`) |
| `AITRYON_MAX_REQUEST_BODY_BYTES` | `26214400` (25 MiB) | Hard cap on the raw HTTP request body as a whole, enforced at the ASGI layer before Starlette/python-multipart ever parses it and without buffering an oversized body first (`app/core/body_size_limit.py`). A different, earlier layer than the two settings above — see that module's docstring. **Production recommendation**: if a reverse proxy sits in front of this app (nginx, Caddy, a cloud load balancer, ...), give it its own aligned body-size limit too (e.g. nginx's `client_max_body_size`) — this application-level limit is defense in depth, not a substitute for one. Keep the proxy's limit ≥ this one so it doesn't silently truncate a request this layer would otherwise handle cleanly with a proper 413 response. |
| `AITRYON_RATE_LIMIT_MAX_REQUESTS` / `AITRYON_RATE_LIMIT_WINDOW_SECONDS` | `20` / `3600` | Abuse-protection rate limit for `/api/try-on` (not the real per-plan quota system — that's Milestone 11) |
| `AITRYON_EXTRACTION_RATE_LIMIT_MAX_REQUESTS` / `AITRYON_EXTRACTION_RATE_LIMIT_WINDOW_SECONDS` | `60` / `600` | Separate, more generous limit for `/api/extract-product-image` — classical CV, not the AI model, so a much cheaper request |
| `AITRYON_URL_EXTRACTION_RATE_LIMIT_MAX_REQUESTS` / `AITRYON_URL_EXTRACTION_RATE_LIMIT_WINDOW_SECONDS` | `20` / `600` | Tighter limit for `/api/extract-product-url` — it triggers an outbound request to a caller-chosen host, mitigated but not eliminated by `fetchers/ssrf_guard.py` |
| `AITRYON_AUTH_LOGIN_IP_RATE_LIMIT_MAX_REQUESTS` / `_WINDOW_SECONDS` | `10` / `900` | Per-IP limit on `POST /api/auth/login` |
| `AITRYON_AUTH_LOGIN_ACCOUNT_RATE_LIMIT_MAX_REQUESTS` / `_WINDOW_SECONDS` | `5` / `900` | Per-account (submitted email) limit on `POST /api/auth/login`, in addition to the per-IP one above — see `backend/app/api/auth.py` for why login needs both |
| `AITRYON_AUTH_SIGNUP_RATE_LIMIT_MAX_REQUESTS` / `_WINDOW_SECONDS` | `5` / `3600` | Per-IP limit on `POST /api/auth/signup` |
| `AITRYON_AUTH_FORGOT_PASSWORD_IP_RATE_LIMIT_MAX_REQUESTS` / `_WINDOW_SECONDS` | `5` / `3600` | Per-IP limit on `POST /api/auth/forgot-password` |
| `AITRYON_AUTH_FORGOT_PASSWORD_EMAIL_RATE_LIMIT_MAX_REQUESTS` / `_WINDOW_SECONDS` | `3` / `3600` | Per-submitted-email limit on the same endpoint, in addition to the per-IP one above — caps how many reset emails one address (real or made up) can trigger, protecting a real user's inbox rather than a secret; see `backend/app/api/auth.py` |
| `AITRYON_CORS_ORIGINS` | `["http://localhost:5173", ...]` | Frontend origins allowed to call the API. **Must not be a wildcard (`"*"`) in production** — enforced at startup when `AITRYON_ENVIRONMENT=production` (`Settings.check_production_cors`). Allowed methods are fixed in code (`main.py`: `GET`, `POST`, `DELETE` — exactly what this API's routes use), not configurable via env var. |
| `AITRYON_DATABASE_URL` | `postgresql+psycopg2://postgres:devpassword@localhost:5432/aitryon` | **Must be overridden outside local dev** — enforced at startup when `AITRYON_ENVIRONMENT=production`. |
| `AITRYON_JWT_SECRET_KEY` | `dev-only-insecure-secret-change-me` | **Must be overridden outside local dev** — enforced at startup when `AITRYON_ENVIRONMENT=production`. Signs auth tokens — generate a real one with `python -c "import secrets; print(secrets.token_hex(32))"` |
| `AITRYON_JWT_EXPIRE_MINUTES` | `10080` (7 days) | Access token lifetime |
| `AITRYON_UNSAVED_RESULT_TTL_HOURS` | `24` | How long a result image survives if never explicitly saved to an account (privacy requirement — see AI_MODEL_LICENSE.md's sibling doc, ARCHITECTURE.md's privacy boundary section) |
| `AITRYON_PASSWORD_RESET_TOKEN_EXPIRE_MINUTES` | `30` | How long a `POST /api/auth/forgot-password` reset link stays valid — see `backend/app/services/password_reset_service.py` |
| `AITRYON_FRONTEND_BASE_URL` | `http://localhost:5173` | Frontend origin a password reset email's link points to. Separate from `AITRYON_CORS_ORIGINS` (a list of origins allowed to *call* the API) — this is the one origin the backend itself builds a user-facing URL against. Not production-secret-checked, same reasoning as `AITRYON_CORS_ORIGINS` staying at its dev default in production: a stale value is a functional misconfiguration (a broken link), not a vulnerability. |
| `VITE_API_BASE_URL` (frontend, `.env.local`) | `http://localhost:8000` | Backend URL the frontend calls |

No AI API keys exist anywhere in this project, because no paid AI API is ever called — see [AI_MODEL_LICENSE.md](AI_MODEL_LICENSE.md).

## Secrets discipline

- `.env` is gitignored; only `.env.example` (placeholders, never real values) is committed.
- The `devpassword` / `dev-only-insecure-secret-change-me` defaults exist so the app runs out of the box on a fresh dev checkout. They are not secrets worth protecting on this machine, but they are **not acceptable** for any shared or production environment — both must be overridden there.
- Nothing frontend-side ever holds a secret; the JWT the browser stores in `localStorage` is a user's own access token, not a system credential.
