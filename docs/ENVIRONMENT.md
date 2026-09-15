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
| `AITRYON_WEIGHTS_DIR` | `ai/models/fashn-vton-1.5` | Where the AI model weights live |
| `AITRYON_DEVICE` | `cpu` | `cuda` on a GPU inference host |
| `AITRYON_DEFAULT_NUM_TIMESTEPS` | `30` | Diffusion steps per generation |
| `AITRYON_MIN_NUM_TIMESTEPS` / `AITRYON_MAX_NUM_TIMESTEPS` | `4` / `50` | Clamp range for the request param |
| `AITRYON_STORAGE_DIR` | `backend/storage` | Local temp/results file root |
| `AITRYON_MAX_UPLOAD_SIZE_MB` | `10` | Upload size cap |
| `AITRYON_MAX_IMAGE_DIMENSION_PX` | `4096` | Upload dimension cap |
| `AITRYON_RATE_LIMIT_MAX_REQUESTS` / `AITRYON_RATE_LIMIT_WINDOW_SECONDS` | `20` / `3600` | Abuse-protection rate limit for `/api/try-on` (not the real per-plan quota system — that's Milestone 11) |
| `AITRYON_EXTRACTION_RATE_LIMIT_MAX_REQUESTS` / `AITRYON_EXTRACTION_RATE_LIMIT_WINDOW_SECONDS` | `60` / `600` | Separate, more generous limit for `/api/extract-product-image` — classical CV, not the AI model, so a much cheaper request |
| `AITRYON_CORS_ORIGINS` | `["http://localhost:5173", ...]` | Frontend origins allowed to call the API |
| `AITRYON_DATABASE_URL` | `postgresql+psycopg2://postgres:devpassword@localhost:5432/aitryon` | **Must be overridden outside local dev.** |
| `AITRYON_JWT_SECRET_KEY` | `dev-only-insecure-secret-change-me` | **Must be overridden outside local dev.** Signs auth tokens — generate a real one with `python -c "import secrets; print(secrets.token_hex(32))"` |
| `AITRYON_JWT_EXPIRE_MINUTES` | `10080` (7 days) | Access token lifetime |
| `AITRYON_UNSAVED_RESULT_TTL_HOURS` | `24` | How long a result image survives if never explicitly saved to an account (privacy requirement — see AI_MODEL_LICENSE.md's sibling doc, ARCHITECTURE.md's privacy boundary section) |
| `VITE_API_BASE_URL` (frontend, `.env.local`) | `http://localhost:8000` | Backend URL the frontend calls |

No AI API keys exist anywhere in this project, because no paid AI API is ever called — see [AI_MODEL_LICENSE.md](AI_MODEL_LICENSE.md).

## Secrets discipline

- `.env` is gitignored; only `.env.example` (placeholders, never real values) is committed.
- The `devpassword` / `dev-only-insecure-secret-change-me` defaults exist so the app runs out of the box on a fresh dev checkout. They are not secrets worth protecting on this machine, but they are **not acceptable** for any shared or production environment — both must be overridden there.
- Nothing frontend-side ever holds a secret; the JWT the browser stores in `localStorage` is a user's own access token, not a system credential.
