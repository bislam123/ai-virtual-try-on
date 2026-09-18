# Production Deployment Preparation

Application-side readiness only. **Nothing in this document means the application is deployed** — no real hosting, GPU, domain, TLS certificate, or production database exists. This file records what the application already does correctly, what concrete artifacts exist for an operator to use, what still needs real operator configuration, and what needs infrastructure this repository cannot provide. See [ENVIRONMENT.md](ENVIRONMENT.md) for the full environment-variable reference and [ARCHITECTURE.md](ARCHITECTURE.md) for design rationale.

**Status key**, used throughout:
- **Implemented** — the application code already does this; verified by test and/or direct inspection.
- **Deployment-ready** — a concrete artifact exists (Dockerfile, systemd unit, nginx config, CI workflow) for an operator to use.
- **Requires operator configuration** — works once real values are supplied (env vars, secrets, DNS, a certificate).
- **Requires external infrastructure** — needs something this repository cannot provide (a real GPU host, Postgres server, domain, TLS cert, SMTP account).
- **Not yet implemented / deferred** — explicitly out of scope for this milestone (GPU purchase, payments, an actual deployment).

---

## 1. Deployment architecture

**Decision: keep `SelfHostedVTONProvider` in-process for the initial deployment — do not split inference into a separate GPU worker service yet.**

Inspected before deciding: `VirtualTryOnProvider` (`backend/app/providers/base.py`) is already an interface with one implementation, `SelfHostedVTONProvider`, constructed once in `main.py`'s `lifespan()` and injected into `TryOnService`. Nothing about "keeping the provider replaceable" requires building a second implementation now — the interface already makes that possible later with zero changes to `TryOnService`, the API layer, the database, or the frontend, exactly as it already did for every earlier provider-level change in this project.

Two topologies were weighed:

| | **A — In-process (chosen)** | **B — Separate GPU worker** |
|---|---|---|
| Shape | Backend (auth/DB/storage/rate-limiting) and `SelfHostedVTONProvider` run in one process, on one GPU-equipped host | Backend stays on a cheap CPU host; a new, separate service wraps `SelfHostedVTONProvider` behind an internal API; a new `VirtualTryOnProvider` implementation (e.g. `RemoteGPUVTONProvider`) calls it over the network |
| New code required | None | A new microservice, a new provider implementation, inter-service auth, a queue or direct-call protocol between them |
| Operational complexity | One process to deploy/monitor/restart | Two services, a network boundary between them, two sets of secrets, two failure domains |
| Cost shape | GPU billed for the whole app's uptime, not just active generation | GPU only needs to run while generating — cheaper if traffic is sporadic, *if* the platform supports scale-to-zero |
| Matches current scale | Yes — single inference lock already caps this app at one concurrent generation regardless of topology (see §5) | Would matter once real concurrent-throughput demand exists, which it doesn't yet |

Chosen A because: no genuine need has been demonstrated for B's added complexity (no real traffic yet, one physical GPU, the single in-process lock means B wouldn't even unlock more *concurrent* generations — see §5); it requires zero new code, matching this milestone's own instruction not to introduce a distributed queue unless genuinely required; and the abstraction already keeps B available later without having to build it speculatively now. This is the same reasoning the project's own GPU-hosting-planning work already reached when comparing hosting *approaches* (low-cost/dedicated/serverless, all still under topology A) — this section makes the topology choice itself explicit and permanent for this milestone, not just a hosting-cost comparison.

```
Browser/PWA
    ↓ HTTPS
Reverse proxy (TLS termination, forwarded headers — §12)
    ↓
Backend process (FastAPI + auth + DB access + storage + SelfHostedVTONProvider, one process — §5)
    ↓                                   ↓
Postgres (durable, can be                Persistent volume (results/temp — §10)
a separate managed host)
```

Revisit topology B only when real measured demand shows the single-lock ceiling is actually being hit — not speculatively.

## 2. Production environment configuration — **Implemented**

Every setting lives in `backend/app/config.py` (`pydantic-settings`, prefix `AITRYON_`), with dev-friendly defaults.

| Variable | Must change for production? | What happens if you forget |
|---|---|---|
| `AITRYON_ENVIRONMENT` | Set to `production` | Without this, every check below never runs — the app would start with insecure/non-functional defaults silently. |
| `AITRYON_JWT_SECRET_KEY` | Yes — `python -c "import secrets; print(secrets.token_hex(32))"` | Refuses to start (`Settings.check_production_secrets`). |
| `AITRYON_DATABASE_URL` | Yes — your real Postgres connection string | Refuses to start. |
| `AITRYON_CORS_ORIGINS` | Yes — your real frontend origin(s), as a JSON array | Refuses to start only if it's a literal wildcard (`Settings.check_production_cors`); left at the localhost dev default it fails closed instead (frontend can't reach the API), not insecurely. |
| `AITRYON_EMAIL_PROVIDER` | Yes — `smtp`, plus `AITRYON_SMTP_HOST`/`AITRYON_EMAIL_FROM_ADDRESS` | Refuses to start (`Settings.check_production_email_provider`). |
| `AITRYON_FRONTEND_BASE_URL` | Yes — your real frontend origin | Not startup-checked (a stale value breaks a password-reset link, not a vulnerability). |

All four startup refusals share one shape: raise `InsecureProductionConfigError` (a plain exception with a static, secret-free message — never a pydantic `ValidationError`, whose own string form would otherwise leak the actual configured value) before the app can serve a single request. See §9 for the complete secrets/config inventory and §19 for a copy-pasteable checklist.

## 3. Startup validation — **Implemented**

The app fails loudly at boot in every case that matters:

- **Insecure secrets/CORS wildcard/console email provider in production** — the three checks in §2, run at import time (`config.py`, bottom of the file).
- **AI model weights missing/corrupt** — `SelfHostedVTONProvider.__init__` (`main.py`'s `lifespan()`) loads the model synchronously at startup specifically so this fails at boot.
- **Storage directory not writable** — `LocalStorageService.__init__` (`services/storage.py`) creates both subdirectories at startup; a permissions problem raises immediately.
- **Database unreachable** — `lifespan()` runs a real query (the stale-job recovery sweep) before startup finishes.

## 4. Containerization — **Deployment-ready, build unverified**

**Decision: Docker for the backend, not blindly, for a specific reason: reproducible CUDA/torch/onnxruntime-gpu version pinning.** This isn't "conventional, so why not" — this project's own docs already document real version-mismatch pain in this exact dependency stack (the Colab multi-interpreter gotcha, the CPU-vs-CUDA torch wheel-index difference, `docs/DEVELOPMENT.md`'s own Colab section). A pinned image closes that class of problem for a GPU deployment specifically, and most GPU rental platforms (RunPod, Vast.ai, ...) expect a Docker image as their native deployment unit anyway.

**`Dockerfile`** (repo root), multi-stage:
- Builder stage installs the exact sequence `docs/DEVELOPMENT.md` already documents and has verified working locally (torch/torchvision from the CUDA wheel index → `aitryon-bodyparser` → `fashn-vton-1.5` → `ai/preprocessing` → the `opencv-python`→`opencv-contrib-python` swap → `product-extractor` → `backend/requirements.txt` → `onnxruntime`→`onnxruntime-gpu`), adapted for a non-interactive Linux container.
- Runtime stage copies only the built venv and application source — no tests, no docs, no `.git`, no frontend, no `ai/outputs` debug images, no model weights (see below). Runs as a non-root user.
- **Model weights (~2.3GB) are deliberately NOT baked into the image** — same "download once, gitignored, never committed" treatment they already get locally. Mount a persistent volume at `/app/ai/models` and populate it once:
  ```
  docker run --rm -v aitryon-models:/app/ai/models --entrypoint python <image> \
    ai/inference/download_weights.py --weights-dir /app/ai/models/fashn-vton-1.5
  ```
- Migrations are **not** run automatically by the image's `CMD` (see §9) — run them as a separate step:
  ```
  docker run --rm --env-file backend.env <image> python -m alembic -c backend/alembic.ini upgrade head
  ```
- Run:
  ```
  docker run -d --gpus all \
    -v aitryon-models:/app/ai/models \
    -v aitryon-storage:/app/backend/storage \
    --env-file backend.env \
    -p 127.0.0.1:8000:8000 \
    aitryon-backend:latest
  ```

**Honest verification status**: this Dockerfile was written by adapting the already-verified local install sequence, and its base image (`nvidia/cuda:12.1.1-cudnn8-*`) was chosen specifically because `onnxruntime-gpu` — unlike torch's CUDA wheels, which bundle their own runtime — historically needs a matching *system* CUDA+cuDNN install. **It has not been build-tested** — no Docker daemon was available in the environment that wrote it. Before relying on it: build the image, run it against a real GPU instance with `--gpus all`, and repeat the real generation smoke test that produced this project's own T4 benchmark (§14) — confirm `nvidia-smi` shows the GPU genuinely in use inside the container, not just that the image builds.

**`.dockerignore`** (repo root) excludes `.git`, real `.env` files, tests, docs, the frontend, the extension, `ai/.venv`, `ai/models`, and every `__pycache__`/`node_modules`.

**If Docker turns out not to fit a chosen host** (e.g. a bare VM without container tooling): `deploy/systemd/aitryon-backend.service` is the direct alternative — same `--workers 1 --proxy-headers` command, run against the same shared venv `docs/DEVELOPMENT.md` already documents, with `ProtectSystem=strict`/`NoNewPrivileges=true` hardening and an explicit `ReadWritePaths` for the storage volume. Neither is "the" answer — pick whichever matches the eventually-chosen GPU host's own deployment model (§14 notes this varies by provider).

## 5. Process / worker configuration — **Implemented + documented**

**This application must run as exactly one process per model-hosting instance** — not `uvicorn --workers N>1`, not multiple replicas sharing one GPU, without further architecture work:

- `SelfHostedVTONProvider` loads the full model into memory once per process and holds a single in-process lock enforcing "one generation at a time." A second worker would load a **second full copy of the model** and hold its **own independent lock** — two generations could run concurrently against what's usually one accelerator, the opposite of the intended behavior.
- `CapacityService`/`QuotaService` are database-backed specifically so the *job count* stays correct across multiple workers if this ever changes — but they only bound how many jobs exist, not how many run genuinely concurrently, which only the provider's own in-process lock currently guarantees, and can't across processes.
- `RateLimiter` is in-memory and process-local by design — more than one worker silently multiplies the effective rate limit by the worker count. Acceptable at one process; would need a shared backend (e.g. Redis) only if this app ever legitimately needs multiple workers, which it doesn't yet.

Production start command (from the repo root, with the venv's Python — Dockerfile's `CMD` and `deploy/systemd/aitryon-backend.service` both use this same shape):
```
<venv>/bin/python -m uvicorn backend.app.main:app \
  --host 0.0.0.0 --port 8000 \
  --workers 1 \
  --proxy-headers --forwarded-allow-ips=<reverse proxy's IP>
```
`--workers 1` is not a placeholder. Scaling inference horizontally requires a real architecture change (a worker-process pool per accelerator, a shared lock/queue) — deliberately not built speculatively; see §1's topology discussion for where that would plug in.

## 6. Health / readiness check — **Implemented**

`GET /health` runs a real `SELECT 1` against the database, returning `503 {"status": "error", "detail": "Database unreachable."}` if that fails, `200 {"status": "ok"}` otherwise. The AI model and storage directory don't need an equivalent per-request check — both already fail loudly at startup (§3), so if this process is answering at all, both are known-good for its entire lifetime. One endpoint, not a live/ready split — this app has no orchestrator yet that would consume the two differently. Tests: `tests/test_health_check.py`.

## 7. Graceful shutdown — **Implemented**

`lifespan()` logs on the way out. No other shutdown cleanup was needed: a job left `processing` by an abrupt shutdown self-heals via the stale-job recovery sweep at next startup (`services/job_recovery.py`), matching `providers/selfhosted.py`'s documented limitation that a genuinely in-progress generation can't be safely force-stopped. Give the process a short graceful-shutdown grace period (uvicorn's `--timeout-graceful-shutdown`, default 5s) — no reasonable grace period lets a real generation (minutes to hours) finish before a forced restart, and that's expected, handled by the recovery sweep, not a gap to close.

## 8. Safe logging / error handling — **Implemented**

- Every unhandled exception is caught by `CatchUnhandledExceptionsMiddleware`, logged server-side with a full traceback, and returned to the client as `{"detail": "An unexpected error occurred."}` — never a stack trace or internal detail.
- Passwords, JWTs, password-reset tokens, and SMTP credentials are never logged — verified directly in the auth/password-reset/email test suites (`tests/test_password_reset.py`'s `test_no_raw_token_reaches_application_logs`/`test_console_email_service_never_logs_the_raw_token`, `tests/test_email_service.py`'s `test_failure_log_never_contains_the_password_recipient_or_url`), not just by inspection. `SmtpEmailService` logs only an exception's *type* on a failed send, never `str(exception)` (which can echo back SMTP server/auth detail).
- `logging.basicConfig(level=logging.INFO, ...)` — plain stdout/stderr, no structured/JSON logging and no verbose debug logging added for this milestone; INFO remains the right default until a real log-shipping target exists to justify a format change.

## 9. Database — **Implemented + requires external infrastructure**

- **Timestamps**: every `DateTime` column uses `DateTime(timezone=True)` (Postgres `timestamptz`) — confirmed by grep, zero naive-datetime usage anywhere in `backend/app`. Fixed and regression-tested in an earlier milestone (`tests/test_timestamp_timezone_handling.py`); unchanged and reverified this milestone.
- **Connection pooling**: one SQLAlchemy engine per process (`db/base.py`), `pool_pre_ping=True` — confirmed still present. A stale connection (DB restart, an idle-timed-out proxy connection) is detected and transparently replaced. Default pool sizing (SQLAlchemy's own: 5 persistent + 10 overflow) is unchanged — this is a single-process app (§5); revisit only if real load shows exhaustion.
- **Schema is never auto-created**: confirmed by grep — `Base.metadata.create_all()` is never called anywhere in `backend/app`. Every table exists only because a migration created it. `alembic upgrade head` (from `backend/`) must be run explicitly before first start against a fresh database, and after pulling any commit that adds a migration — this application will never do it for you, in a container or otherwise (see §4's separate `docker run ... alembic upgrade head` step).
- **Migration-state discipline**: `alembic check` (used throughout this project's own milestones) confirms zero drift between `models.py` and the migration history before every deploy — wire it into CI (§16) and re-run it manually before any production deploy as a final gate.
- **Requires external infrastructure**: a real, durable, backed-up (§15) PostgreSQL 17-compatible server — this repository provisions none.

**Admin audit log table — production migration requirement**: `admin_audit_log` (migration `e1f5538acd61`) is created by the same `alembic upgrade head` this section already requires — no separate migration step, but also no admin mutation (account enable/disable, a plan-limit edit) will work until it's been run, since every one of those writes an audit row in the same database transaction as the mutation itself (`services/admin_service.py`'s `record_admin_audit_log`; see `docs/ARCHITECTURE.md`'s Admin/Operations section for the full design). Nothing this table stores is ever a secret — it's reviewed at every writer to hold only safe, non-secret context (an affected account's email, a plan's before/after limit values) — so it needs no handling beyond whatever this database already gets (§15's backup requirement covers it like any other table; there is no separate retention/redaction concern).

**Admin bootstrap** (a one-time step after this deployment's first successful start, before `/api/admin/*` is useful): no admin account is created by any migration or by application code — every row starts `is_admin=false`, with no exception. Promote an existing, already-signed-up account directly against the production database:
```powershell
# From the repo root, same venv as the rest of the backend, pointed at production via AITRYON_DATABASE_URL:
ai\.venv\Scripts\python.exe backend\scripts\promote_admin.py you@example.com
```
This is deliberately not an API call or an environment-variable-configured default admin — see `docs/ARCHITECTURE.md`'s "First-admin bootstrap" for why. The promotion itself is recorded to `admin_audit_log` (`admin_user_id=NULL`, `action="admin_promoted"` — there's no HTTP-authenticated admin session to attribute an out-of-band bootstrap to).

## 10. Persistent storage — **Implemented + requires operator configuration**

`AITRYON_STORAGE_DIR` (two subdirectories: `tmp/` for in-flight uploads, `results/` for generated images) **must be a durable, persistent volume**, never ephemeral container storage wiped on restart/redeploy — a result a user explicitly saved (`JobRecord.saved`) is expected to survive both.

- **Permissions**: the Dockerfile's runtime user (`aitryon`, non-root) must own this path — the image creates and `chown`s `/app/backend/storage` itself, but a bind-mounted host directory must be pre-created with matching ownership, or the container's own `chown` at build time won't apply to it. For `deploy/systemd/aitryon-backend.service`, `ReadWritePaths` must point at wherever `AITRYON_STORAGE_DIR` (in the referenced `EnvironmentFile`) actually resolves, owned by that unit's `User=`.
- **Cleanup behavior**: `tmp/` entries are deleted the moment their job finishes (success, failure, or cancellation — `TryOnService.run_job`'s `finally` block); `results/` entries older than `AITRYON_UNSAVED_RESULT_TTL_HOURS` and never explicitly saved are deleted by the cleanup sweep (§11), not on any other schedule.
- Object storage (S3-compatible or similar) was deliberately not introduced this milestone — the existing `StorageService` interface (`services/storage.py`) already abstracts this exactly the way `VirtualTryOnProvider` abstracts the model, so a future `S3StorageService` is a new implementation behind the same interface whenever real scale genuinely needs it, not a speculative addition now.

## 11. Cleanup scheduler — **Deployment-ready**

`backend/scripts/cleanup_expired_results.py` already has a complete, tested, idempotent implementation (safe to run repeatedly or overlapping — see the script's own module docstring) doing all three required things in one run: stale-`PROCESSING`-job recovery, expired-unsaved-result + leftover-temp-file deletion, and expired/used password-reset-token cleanup. Exit code `0` = clean sweep, `1` = at least one individual job's cleanup failed (never affected by *how many* jobs were cleaned, only by errors) — logged safely (job IDs and counts only, never file contents/tokens/credentials).

**New this milestone**: `deploy/systemd/aitryon-cleanup.service` + `.timer` — an hourly systemd timer (reasonable against the default 24h unsaved-result TTL and 120-minute stale-job threshold), chosen as the smallest reliable mechanism for a systemd-based host already running `aitryon-backend.service` (free logging via `journalctl`, no extra package). A plain cron entry is an equally valid, slightly more minimal alternative on a host that doesn't otherwise use systemd:
```
0 * * * *  cd /opt/aitryon && ai/.venv/bin/python backend/scripts/cleanup_expired_results.py >> /var/log/aitryon-cleanup.log 2>&1
```
For a container deployment, either the orchestrator's own scheduler (e.g. a Kubernetes `CronJob` running the same image with `docker run`'s entrypoint overridden to this script) or a host-level systemd timer calling `docker exec` into the running backend container works equally well — neither is provided as a file here since it depends on which orchestrator, if any, is eventually chosen.

Install: `systemctl daemon-reload && systemctl enable --now aitryon-cleanup.timer`. Verify it actually runs: `systemctl list-timers aitryon-cleanup.timer`.

## 12. Reverse proxy / HTTPS — **Deployment-ready + requires external infrastructure**

- **No HSTS is sent by the application, deliberately** (`core/security_headers.py`'s own docstring) — sending it before HTTPS is genuinely, permanently in place would be actively wrong. `deploy/nginx/aitryon.example.conf` includes the HSTS directive **commented out**, with an explicit note to enable it only once TLS is confirmed working end to end.
- **Body size limit mirrored at the proxy layer**: the example config sets `client_max_body_size 26M`, matching (slightly above) `AITRYON_MAX_REQUEST_BODY_BYTES`'s 25 MiB default — keep the two aligned if that setting ever changes.
- **Real client IPs**: `deploy/nginx/aitryon.example.conf` sets `X-Forwarded-For`/`X-Real-IP`/`X-Forwarded-Proto`; the backend must be started with the matching `--proxy-headers --forwarded-allow-ips=<this proxy's IP>` (§5) — never `--forwarded-allow-ips='*'`. Without both sides configured, rate limiting/anonymous quota/idempotency scoping all silently collapse onto the proxy's own IP.
- **No WebSocket configuration** — this app has no WebSocket/SSE usage anywhere (job status is plain HTTP polling); `deploy/nginx/aitryon.example.conf` deliberately has no `Upgrade`/`Connection: upgrade` directives. Add them only if a future milestone genuinely introduces persistent-connection traffic.
- **Requires external infrastructure**: a real domain and a real TLS certificate (e.g. via Let's Encrypt/certbot, referenced but not obtained by the example config) — neither exists yet.

## 13. CORS / frontend URL configuration — **Implemented + requires operator configuration**

- `AITRYON_CORS_ORIGINS` — origins allowed to *call* the API. Must be the real frontend origin(s); never `"*"` (refused in production, §2). Confirmed the check also catches a wildcard mixed with real origins, not just a bare `"*"` (`tests/test_config_validation.py`).
- `AITRYON_FRONTEND_BASE_URL` — the one origin the backend builds a user-facing (password-reset) URL against. Separate setting, same real value in practice, not startup-validated (a stale value breaks a link, not a vulnerability).
- `VITE_API_BASE_URL` (frontend build-time) must point at the real deployed backend URL.
- Localhost is never required for production — both backend settings default to the local dev origin purely so a fresh checkout works out of the box; neither has any dependency on `localhost` remaining reachable once real values are set.

## 14. GPU deployment — **Measured baseline recorded; no provider chosen**

**Real measurement** (Google Colab, Tesla T4 — not an estimate):

| | Measured |
|---|---|
| GPU | Tesla T4, 15,360 MiB total VRAM |
| dtype actually used | `torch.bfloat16` (device `cuda:0`) |
| Generation time, 30 timesteps | 474.64s (7.91 min) |
| PyTorch peak allocated | 10,916.7 MiB |
| PyTorch peak reserved | 11,880 MiB |
| `nvidia-smi` total used | 14,745 MiB / 15,360 MiB |
| Free memory measured | 168 MiB |
| DWPose warm inference | 0.109s |

**A T4 is explicitly not recommended as sufficient for production**, despite technically completing the workload: 168 MiB free out of 15,360 MiB is essentially zero safety margin. A production service needs headroom for image-size variation (up to the application's own 4096px cap), PyTorch memory fragmentation across many sequential generations without a process restart, and CUDA/framework overhead beyond the main model's own tensors (the ~2.87GB gap between PyTorch's own 11,880 MiB "reserved" figure and `nvidia-smi`'s 14,745 MiB total is exactly that overhead — DWPose's `onnxruntime-gpu` allocation, CUDA context, driver bookkeeping). A single unlucky request could plausibly OOM the whole process.

Also worth noting as a real discrepancy, not smoothed over: `ai/vendor/fashn-vton-1.5/README.md` states bf16 requires an Ampere-or-newer GPU (RTX 30xx/40xx, A10G, L4, A100/H100) and that older hardware falls back to `float32`. The T4 is Turing (pre-Ampere), yet the real benchmark measured `torch.bfloat16` actually in use. This project trusts the **measurement** over the README's general claim — current PyTorch versions may support bf16 arithmetic more broadly than the README's original assumption — but flags the discrepancy explicitly rather than silently picking one source as correct.

**Engineering estimate, clearly distinguished from the measurement above, not purchased or committed to**: given the T4's ~14.7GB actual usage leaves no margin, a GPU class with materially more headroom — 24GB (RTX 4090, A10G, L4, RTX 3090) — is the recommended minimum practical production class, not because bf16 requires it (the T4 measurement contradicts that), but because 24GB leaves a real, defensible safety margin (~9GB) over the measured ~14.7GB working set. This is an estimate pending a real measurement on a 24GB card, not a second verified data point.

**No GPU host, provider, or instance has been chosen or purchased** — see the project's own earlier GPU-hosting-planning work for a comparison of hosting *approaches* (on-demand/dedicated/serverless), all still compatible with the in-process topology chosen in §1. The AI provider abstraction (§1) keeps this decision reversible: whichever host is eventually chosen just needs to run this same `Dockerfile` (§4) or the systemd alternative with a GPU attached — no application code depends on which one.

## 15. Backups — **Documented, not implemented**

No backup currently exists — this section documents the minimum requirement, it does not claim one is running.

- **PostgreSQL**: the single source of truth for users, jobs, plans, and reset tokens. Minimum: automated daily `pg_dump` (or the hosting provider's managed-backup equivalent, e.g. RDS/Cloud SQL automated snapshots) retained for a documented window (a start: 7 daily + 4 weekly), stored somewhere other than the database host itself. Untested backups are not backups — a restore drill belongs in the same runbook that sets this up, not assumed to work.
- **Persistent result storage** (`AITRYON_STORAGE_DIR`'s `results/` subtree): lower priority than the database by design — unsaved results are *already* meant to be temporary (§10's TTL), so only explicitly-**saved** results represent real, expected-to-persist user data worth backing up. A volume-level snapshot (matching whatever the chosen storage backend/cloud provider offers) on the same cadence as the database backup is sufficient; this repository doesn't implement or schedule one.
- **Recovery expectation**: restoring the database without the storage volume (or vice versa) leaves `JobRecord.saved=True` rows pointing at missing files, or orphaned files with no owning row — restore both together, from backups taken close together in time, not independently.

## 16. CI/CD — **Deployment-ready**

`.github/workflows/ci.yml` — two jobs, both test-only, **no deploy step**:
- **backend**: a Postgres 17 service container, the same AI-pipeline dependency install sequence as §4/local dev (CPU torch — no GPU in CI, and none needed: every test that would otherwise require a real model injects a fake provider instead — confirmed by inspection of `backend/app/providers/base.py`'s test-only injection point before leaving weight download out of CI entirely), `alembic upgrade head` against the CI database, `alembic check`, then the full `pytest` suite.
- **frontend**: `npm ci`, the full Vitest suite, `npm run build`, `npm run lint`.

Runs on every push/PR to `master`. **Not verified by an actual GitHub Actions run** — no `gh`/GitHub access was available in the environment that wrote it; reviewed carefully against this repo's own documented install sequence and existing test conventions, but an operator should watch the first real run before trusting it fully.

## 17. Pre-production smoke test — **Deployment-ready, verified working**

`deploy/smoke_test.py` — exercises a **real, running instance** end to end over the network (distinct from the pytest suite's in-process `TestClient`): health, CORS (both a disallowed and, if given, the configured origin), request body-size rejection, signup, login, password-reset request (enumeration-safety), try-on submission + status polling + cross-user ownership rejection + result-endpoint sanity, admin-access refusal for a non-admin account, and account deletion (including confirming the token stops working immediately after). Creates and deletes two throwaway accounts of its own; never waits for a real AI generation to finish (§14's benchmark: minutes on GPU, potentially over an hour on CPU) — it confirms submission and polling work, not full generation.

**Actually run against the real local backend during this milestone** (not just written): all checks passed. Usage:
```
ai\.venv\Scripts\python.exe deploy\smoke_test.py --base-url http://localhost:8000
ai\.venv\Scripts\python.exe deploy\smoke_test.py --base-url https://your-deployment.example.com --cors-origin https://your-frontend.example.com
```
Not run by CI (§16) — it needs a fully running instance including the real AI pipeline, which CI deliberately never boots.

## 18. Secrets / required production configuration — **Reference list**

Every value below either must be set for production, or is a secret that must never be committed. See [ENVIRONMENT.md](ENVIRONMENT.md) for defaults and full descriptions.

| Category | Variable(s) | Secret? |
|---|---|---|
| Environment | `AITRYON_ENVIRONMENT=production` | No |
| Auth | `AITRYON_JWT_SECRET_KEY` | **Yes** |
| Database | `AITRYON_DATABASE_URL` (embeds the DB password) | **Yes** |
| Email | `AITRYON_EMAIL_PROVIDER=smtp`, `AITRYON_SMTP_HOST`/`_PORT`/`_USERNAME`, `AITRYON_EMAIL_FROM_ADDRESS`/`_FROM_NAME` | No |
| Email | `AITRYON_SMTP_PASSWORD` | **Yes** |
| Frontend URLs | `AITRYON_CORS_ORIGINS`, `AITRYON_FRONTEND_BASE_URL`, `VITE_API_BASE_URL` (frontend build) | No |
| Storage | `AITRYON_STORAGE_DIR` (a persistent volume path, §10) | No |
| Inference | `AITRYON_WEIGHTS_DIR`, `AITRYON_DEVICE=cuda`, `AITRYON_INFERENCE_TIMEOUT_SECONDS` (lower substantially from the CPU-sized 3600s default once real GPU timing is known, §14) | No |

Never committed anywhere in this repository (`.env` is gitignored; only `.env.example`, placeholders only, is tracked). No default production credential exists for any of the above — `check_production_secrets`/`check_production_email_provider` (§2) exist specifically so a forgotten secret fails startup instead of silently running insecurely.

## 19. Pre-deployment checklist

- [ ] `AITRYON_ENVIRONMENT=production` set
- [ ] `AITRYON_JWT_SECRET_KEY` generated fresh, not the dev default
- [ ] `AITRYON_DATABASE_URL` points at the real production database
- [ ] `AITRYON_CORS_ORIGINS` set to the real frontend origin(s), not `"*"`
- [ ] `AITRYON_FRONTEND_BASE_URL` set to the real frontend origin
- [ ] `AITRYON_EMAIL_PROVIDER=smtp` with `AITRYON_SMTP_*`/`AITRYON_EMAIL_FROM_*` set
- [ ] `AITRYON_STORAGE_DIR` points at a durable, persistent, correctly-owned volume (§10)
- [ ] `alembic upgrade head` run against the production database (includes `admin_audit_log`, migration `e1f5538acd61` — §9)
- [ ] `alembic check` clean before this and every future deploy
- [ ] First admin account promoted via `backend/scripts/promote_admin.py` (§9) — not before the account has signed up normally
- [ ] Cleanup scheduler installed and confirmed running (§11)
- [ ] Reverse proxy terminates real TLS, adds HSTS only after confirming TLS works, mirrors the body-size limit (§12)
- [ ] `--proxy-headers --forwarded-allow-ips=<proxy IP>` set on the backend (never `'*'`)
- [ ] `--workers 1` (§5 — do not scale workers without an architecture change)
- [ ] `GET /health` returns `200` against the real deployment
- [ ] `VITE_API_BASE_URL` (frontend build) points at the real deployed backend
- [ ] `deploy/smoke_test.py` run against the real deployment and passes (§17)
- [ ] Database + storage backup mechanism actually running, not just documented (§15)
- [ ] Docker image (or systemd alternative) build/run actually verified against a real GPU host (§4 — not yet done)

Deliberately not on this list, out of scope per the project's own rules: GPU hosting purchase, payments/subscriptions, actually deploying.
