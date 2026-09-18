# Production Deployment Preparation

Application-side readiness only. This document does not deploy anything, choose GPU hosting, or stand up infrastructure — it records what the application already does correctly, what an operator must configure or wire up externally, and exactly which commands/flags to use when that happens. See [ENVIRONMENT.md](ENVIRONMENT.md) for the full environment-variable reference and [ARCHITECTURE.md](ARCHITECTURE.md) for design rationale — this file is the operational checklist, not a design doc.

---

## 1. Production environment configuration

Every setting lives in `backend/app/config.py` (`pydantic-settings`, prefix `AITRYON_`), with dev-friendly defaults. Two are secrets that **must** be overridden outside local dev, and one is a startup-time behavior switch:

| Variable | Must change for production? | What happens if you forget |
|---|---|---|
| `AITRYON_ENVIRONMENT` | Set to `production` | Without this, the two checks below never run — the app would start with insecure defaults silently. |
| `AITRYON_JWT_SECRET_KEY` | Yes — generate with `python -c "import secrets; print(secrets.token_hex(32))"` | With `AITRYON_ENVIRONMENT=production` set, the app **refuses to start** (`Settings.check_production_secrets`). |
| `AITRYON_DATABASE_URL` | Yes — your real Postgres connection string | Same as above — the app refuses to start rather than run against the dev credential. |
| `AITRYON_CORS_ORIGINS` | Yes — your real frontend origin(s), as a JSON array | Refuses to start only if it's a literal wildcard (`"*"`); left at the localhost dev default it just fails closed (frontend can't reach the API) rather than being insecure — see `Settings.check_production_cors`. |
| `AITRYON_FRONTEND_BASE_URL` | Yes — your real frontend origin | Not startup-checked (a stale value is a broken password-reset link, not a vulnerability) — but will silently email broken reset links if forgotten. |

See [ENVIRONMENT.md](ENVIRONMENT.md) for every other tunable (rate limits, timeouts, TTLs) — none of those are production-required changes, only tuning.

## 2. Startup validation (already in place)

The app fails loudly at boot, before serving a single request, in every case that matters:

- **Insecure secrets/CORS wildcard in production** — `Settings.check_production_secrets()` / `Settings.check_production_cors()`, run at import time (`config.py`, bottom of the file).
- **AI model weights missing/corrupt** — `SelfHostedVTONProvider.__init__` (called from `main.py`'s `lifespan()`) loads the model synchronously at startup specifically so this fails at boot, not on a user's first request.
- **Storage directory not writable** — `LocalStorageService.__init__` (`services/storage.py`) calls `Path.mkdir(parents=True, exist_ok=True)` for both the temp and results subdirectories at startup; a permissions problem raises immediately.
- **Database unreachable** — `lifespan()` runs a real query (the stale-job recovery sweep, `recover_stale_processing_jobs`) against the database before the app finishes starting, so an unreachable database prevents startup rather than surfacing later as scattered request failures.

Nothing further was added here — all four already existed and were verified to actually fail closed, not just documented as if they did.

## 3. Health / readiness check

`GET /health` (no auth, no rate limit — meant for a load balancer / orchestrator / uptime check, not a browser). **Changed this milestone**: it now runs a real `SELECT 1` against the database and returns `503 {"status": "error", "detail": "Database unreachable."}` if that fails, instead of unconditionally returning `200 {"status": "ok"}` regardless of whether the app could actually serve a real request. The AI model and storage directory don't need an equivalent per-request check — both already fail loudly at startup (§2), so if this process is running and answering at all, both are known-good for its entire lifetime; only the database can fail *after* a successful startup (a network blip, a Postgres restart/failover) without the process itself going down.

One endpoint, not a separate `/health/live` + `/health/ready` split: this app has no orchestrator yet that would consume the two differently, so a single honest check is simpler than two that would answer identically today. Revisit this if/when deployed behind something (e.g. Kubernetes) that distinguishes liveness from readiness.

Tests: `tests/test_health_check.py` (both the 200 and 503 paths, against the real handler — not a duplicate of its logic).

## 4. Safe logging / error handling (already in place)

- Every unhandled exception (a genuine bug, not a `UserFacingError`/validation error) is caught by `CatchUnhandledExceptionsMiddleware` (`core/errors.py`), logged server-side with `logger.exception(...)` (full traceback in the server's own log), and returned to the client as a generic `{"detail": "An unexpected error occurred."}` — never a stack trace, path, or internal detail. Security headers still apply to this response (see the commit that fixed this from bypassing them entirely).
- `UserFacingError` (the deliberate, expected error path — bad input, rate limit, etc.) always carries a safe, pre-written message; nothing derived from an exception's own `str()` ever reaches a response body outside that.
- `logging.basicConfig(level=logging.INFO, ...)` in `main.py` — plain stdout/stderr logging, no structured/JSON logging. Deliberately not changed this milestone: this app has no log aggregator to target yet, and INFO is a reasonable default for both dev and prod. If a real deployment target needs structured logs (e.g. for a specific log-shipping pipeline), that's a genuine future change tied to *which* platform, not something to guess at speculatively now.
- Password reset tokens, JWTs, and passwords are never logged — verified directly in the auth/password-reset test suites, not just by inspection.

## 5. Database connection behavior (already in place)

- One SQLAlchemy engine per process (`db/base.py`), `pool_pre_ping=True` — a connection that's gone stale (e.g. the DB restarted, a load balancer idle-timed-out the TCP connection) is detected and transparently replaced rather than surfacing as a confusing mid-request error.
- Default pool sizing (SQLAlchemy's own defaults: 5 persistent + 10 overflow connections) was left unchanged — this is a single-worker-process app (see §12), and nothing in the current request volume/pattern indicated that default was wrong. Revisit only if real production load shows connection exhaustion.
- **Migrations are not run automatically.** `alembic upgrade head` (from `backend/`) must be run before starting the server on a fresh database or after pulling a change that adds a migration. `alembic check` (used throughout this project's own audits) confirms there's no drift between `models.py` and the migration history before every deploy.

## 6. Persistent storage requirements

`AITRYON_STORAGE_DIR` (default `backend/storage`, two subdirectories: `tmp/` for in-flight uploads, `results/` for generated images) **must be a durable, persistent volume** in any real deployment — not ephemeral container storage that's wiped on restart/redeploy. A result a user explicitly saved (`JobRecord.saved`) is expected to survive process restarts and redeploys; losing it would be a real, user-visible data-loss bug, not just an inconvenience. If the deployment target is containerized, mount a persistent volume at this path (or point `AITRYON_STORAGE_DIR` at one) — this is infrastructure provisioning, not an application change, so it's not done here.

## 7. Cleanup scheduler requirement

`backend/scripts/cleanup_expired_results.py` already has a complete, tested, idempotent implementation (safe to run repeatedly, on any schedule, including overlapping runs) and a documented exit-code contract (`0` = clean sweep, `1` = at least one individual job's cleanup failed, so a scheduler can alert on it) — see the script's own module docstring for the full per-platform wiring instructions (cron, Windows Task Scheduler, a Kubernetes CronJob, a hosted cron add-on). **It is not scheduled anywhere in this repository** — no Dockerfile, no CI/CD, no process manager config exists yet (confirmed by inspection). This is the single most concrete "must wire up before real traffic accrues" item: without it, expired unsaved results and stale processing jobs only ever get cleaned up at the next app *restart* (the same sweep also runs once in `lifespan()`), not on any regular interval. Run it hourly (reasonable against the default 24h unsaved-result TTL) via whichever scheduler the eventual deployment target provides.

## 8. Reverse-proxy / HTTPS requirements

- **No HSTS header is sent, deliberately** (`core/security_headers.py`'s own docstring) — this repo has no reverse proxy or TLS termination yet, and sending HSTS over plain HTTP would be actively wrong, not just premature. Add it (or let the reverse proxy add it) only once HTTPS is genuinely and permanently in place.
- **Body size limit should be mirrored at the proxy layer.** `AITRYON_MAX_REQUEST_BODY_BYTES` (25 MiB default) is application-level defense in depth, not a substitute for a proxy-level limit (e.g. nginx's `client_max_body_size`) — set the proxy's limit ≥ this one so it doesn't silently truncate a request this layer would otherwise reject cleanly with a proper `413`.
- **Real client IPs need `--proxy-headers`.** Rate limiting, anonymous usage quota, and idempotency scoping all key on `request.client.host` — the literal TCP peer, never a trusted `X-Forwarded-For` (this app deliberately does not parse that header itself; doing so safely is the ASGI server's job, not application code's). Deployed directly behind a reverse proxy without any configuration, every request's `client.host` would be the proxy's own address, collapsing every real visitor into one shared rate-limit/quota bucket — a self-inflicted availability bug, not a security hole, but a real one. Fix at deploy time by starting uvicorn with `--proxy-headers --forwarded-allow-ips=<reverse proxy's IP>` (never `--forwarded-allow-ips='*'`, which would let any client spoof its own IP via the header). See §10 for the full command.

## 9. CORS / frontend URL configuration

Two independent settings, already correctly separated (not the same value used for two purposes):

- `AITRYON_CORS_ORIGINS` — the list of origins allowed to *call* the API (browser CORS enforcement). Must be your real frontend origin(s), never `"*"` (startup-refused in production if it is).
- `AITRYON_FRONTEND_BASE_URL` — the one origin the backend itself builds a user-facing URL against (password-reset email links). Must also be your real frontend origin, but not startup-validated (a stale value breaks a link, it doesn't open a vulnerability).
- Frontend side: `VITE_API_BASE_URL` (build-time, `frontend/.env` or the hosting platform's env config) must point at the deployed backend's real URL.

## 10. Production service startup command

The documented dev command (`uvicorn backend.app.main:app --reload`) is dev-only — `--reload` watches the filesystem and adds overhead never wanted in production. A production start, from the repo root, with the venv's Python:

```
<venv>/bin/python -m uvicorn backend.app.main:app \
  --host 0.0.0.0 --port 8000 \
  --workers 1 \
  --proxy-headers --forwarded-allow-ips=<reverse proxy's IP>
```

(Windows: `<venv>\Scripts\python.exe -m uvicorn ...`, same flags.) Omit `--proxy-headers`/`--forwarded-allow-ips` only if this process is *not* behind a reverse proxy at all. `--workers 1` is not a placeholder — see §12 for why this app must not run with more than one worker as currently architected. Run `alembic upgrade head` (from `backend/`) before the first start against a given database.

## 11. Graceful shutdown

`main.py`'s `lifespan()` now logs on the way out (`"AI Try-On backend shutting down."`) so a shutdown is visible in the logs rather than silent — the one gap found this milestone; fixed. No other shutdown cleanup was needed or added: a try-on job left `processing` by an abrupt shutdown (a SIGTERM grace period expiring mid-generation, or a hard kill) is already designed to self-heal — the next startup's `recover_stale_processing_jobs()` (or the scheduled cleanup sweep, §7) picks it up and marks it `failed`, matching `providers/selfhosted.py`'s own documented limitation that a genuinely in-progress generation cannot be safely force-stopped from within this process. Operationally: give the process a graceful-shutdown grace period (uvicorn's `--timeout-graceful-shutdown`, default 5s) short enough to be practical — a real generation can take minutes to hours on CPU, so no reasonable grace period will let one finish before a forced restart; that's expected and already handled by the recovery sweep, not a gap to close.

## 12. Worker / process considerations

**This application must run as exactly one process per model-hosting instance** — not `uvicorn --workers N>1`, not multiple replicas sharing one GPU/CPU, without further architecture work. Concretely:

- `SelfHostedVTONProvider` loads the full model into memory once per process and holds a single in-process lock that enforces "one generation at a time." A second worker process would load a **second full copy of the model** (real memory cost) and hold its **own independent lock** — two generations could then run concurrently against what's usually one accelerator, which is the opposite of the intended behavior, not just a wasted resource.
- `CapacityService` and `QuotaService` are both database-backed specifically so the *job count* stays correct across multiple workers if this ever changes — but they only bound how many jobs exist, not how many run genuinely concurrently, which is what the provider's own in-process lock is supposed to guarantee and can't, across processes.
- `RateLimiter` (abuse-protection only, not quota) is in-memory and process-local by design (documented in `services/rate_limiter.py` and `api/auth.py`) — with more than one worker, each gets its own independent counters, so the effective rate limit is silently multiplied by the worker count. Acceptable today (single process); would need a shared backend (e.g. Redis) only if this app ever legitimately needs multiple workers, which it doesn't yet — not built speculatively.

If throughput ever genuinely requires more than one concurrent generation, that's a real architecture change (a separate worker-process pool per accelerator, a shared lock/queue), not a flag to flip — out of scope for this milestone, and not GPU-hosting/infrastructure work this task was meant to touch.

## 13. Pre-deployment checklist

Application-side only — does not include provisioning the GPU host or payments (both explicitly deferred, see project scope). Real email delivery is no longer deferred — see below.

- [ ] `AITRYON_ENVIRONMENT=production` set
- [ ] `AITRYON_JWT_SECRET_KEY` generated fresh (`secrets.token_hex(32)`), not the dev default
- [ ] `AITRYON_DATABASE_URL` points at the real production database, not the dev credential
- [ ] `AITRYON_CORS_ORIGINS` set to the real frontend origin(s), not `"*"`, not the localhost default
- [ ] `AITRYON_FRONTEND_BASE_URL` set to the real frontend origin
- [ ] `AITRYON_EMAIL_PROVIDER=smtp`, with `AITRYON_SMTP_HOST`/`AITRYON_EMAIL_FROM_ADDRESS` (and, for almost every real provider, `AITRYON_SMTP_USERNAME`/`AITRYON_SMTP_PASSWORD`) set — the app refuses to start otherwise (`Settings.check_production_email_provider`); see [ENVIRONMENT.md](ENVIRONMENT.md)'s "Email delivery" section
- [ ] `AITRYON_STORAGE_DIR` points at a durable, persistent volume
- [ ] `alembic upgrade head` run against the production database
- [ ] `alembic check` run and clean (no drift) before every deploy going forward
- [ ] `backend/scripts/cleanup_expired_results.py` wired into a real scheduler (cron/Task Scheduler/CronJob/hosted cron), running at least hourly
- [ ] Reverse proxy in front of the app terminates TLS and adds HSTS
- [ ] Reverse proxy's own body-size limit set ≥ `AITRYON_MAX_REQUEST_BODY_BYTES`
- [ ] uvicorn started with `--proxy-headers --forwarded-allow-ips=<proxy IP>` if behind a reverse proxy (never `--forwarded-allow-ips='*'`)
- [ ] uvicorn started with `--workers 1` (see §12 — do not scale workers without an architecture change)
- [ ] `GET /health` returns `200` against the real deployment before routing real traffic to it
- [ ] `VITE_API_BASE_URL` (frontend build) points at the real deployed backend URL

Deliberately not on this list, tracked separately per the project's own scope rules: GPU hosting provisioning, payments/subscriptions.
