import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import auth, extraction, tryon, usage
from .config import settings
from .core.body_size_limit import RequestBodySizeLimitMiddleware
from .core.errors import CatchUnhandledExceptionsMiddleware, configure_exception_handlers
from .core.security_headers import SecurityHeadersMiddleware
from .db import get_session
from .providers.selfhosted import SelfHostedVTONProvider
from .services.capacity_service import CapacityService
from .services.db_job_store import DbJobStore
from .services.email_service import ConsoleEmailService
from .services.job_recovery import recover_stale_processing_jobs
from .services.quota_service import QuotaService
from .services.rate_limiter import RateLimiter
from .services.storage import LocalStorageService
from .services.tryon_service import TryOnService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Loaded once at startup (not per-request): the AI model takes a few
    # seconds to load from disk, and we want a missing/corrupt weights
    # directory to fail loudly at boot, not on some user's first request.
    storage = LocalStorageService(settings.storage_dir)
    app.state.storage = storage  # used directly by api/auth.py's account-deletion endpoint
    # Real, persistent job storage (Postgres) — see services/job_store.py for
    # why InMemoryJobStore still exists (it's what the fast test suite uses).
    # Table creation is handled by Alembic migrations (backend/alembic/),
    # not here — run `alembic upgrade head` before starting the server.
    job_store = DbJobStore()
    provider = SelfHostedVTONProvider(
        weights_dir=settings.weights_dir,
        device=settings.device,
        inference_timeout_seconds=settings.inference_timeout_seconds,
        lock_acquire_timeout_seconds=settings.provider_lock_acquire_timeout_seconds,
    )
    app.state.tryon_service = TryOnService(provider=provider, storage=storage, job_store=job_store)
    # Recovers any job left in status=processing by a previous process
    # instance being killed mid-generation (see services/job_recovery.py).
    # Run once here so a restart clears these quickly, not just whenever
    # the next scheduled cleanup sweep happens to run.
    with get_session() as recovery_session:
        recovery_result = recover_stale_processing_jobs(
            recovery_session, storage, settings.stale_job_threshold_minutes
        )
    if recovery_result.recovered:
        logger.warning("Recovered %d stale processing job(s) at startup.", recovery_result.recovered)
    app.state.rate_limiter = RateLimiter(settings.rate_limit_max_requests, settings.rate_limit_window_seconds)
    app.state.extraction_rate_limiter = RateLimiter(
        settings.extraction_rate_limit_max_requests, settings.extraction_rate_limit_window_seconds
    )
    app.state.url_extraction_rate_limiter = RateLimiter(
        settings.url_extraction_rate_limit_max_requests, settings.url_extraction_rate_limit_window_seconds
    )
    app.state.image_url_extraction_rate_limiter = RateLimiter(
        settings.image_url_extraction_rate_limit_max_requests, settings.image_url_extraction_rate_limit_window_seconds
    )
    # See api/auth.py's module docstring for the dual-key (IP + account) strategy these serve.
    app.state.auth_login_ip_rate_limiter = RateLimiter(
        settings.auth_login_ip_rate_limit_max_requests, settings.auth_login_ip_rate_limit_window_seconds
    )
    app.state.auth_login_account_rate_limiter = RateLimiter(
        settings.auth_login_account_rate_limit_max_requests, settings.auth_login_account_rate_limit_window_seconds
    )
    app.state.auth_signup_rate_limiter = RateLimiter(
        settings.auth_signup_rate_limit_max_requests, settings.auth_signup_rate_limit_window_seconds
    )
    app.state.auth_forgot_password_ip_rate_limiter = RateLimiter(
        settings.auth_forgot_password_ip_rate_limit_max_requests,
        settings.auth_forgot_password_ip_rate_limit_window_seconds,
    )
    app.state.auth_forgot_password_email_rate_limiter = RateLimiter(
        settings.auth_forgot_password_email_rate_limit_max_requests,
        settings.auth_forgot_password_email_rate_limit_window_seconds,
    )
    # See services/email_service.py's module docstring for why this is a
    # console/dev stand-in, not a real send, and what a production
    # deployment needs to swap in instead.
    app.state.email_service = ConsoleEmailService()
    app.state.quota_service = QuotaService()
    app.state.capacity_service = CapacityService()
    logger.info("AI Try-On backend ready.")
    yield


app = FastAPI(title="AI Try-On API", lifespan=lifespan)

# Middleware registration order matters here: Starlette applies the
# *last*-added middleware as the *outermost* layer. CORSMiddleware is
# added last (below) specifically so it ends up outermost and gets a
# chance to add Access-Control-* headers to every response the two
# middlewares below produce -- including a 413 from
# RequestBodySizeLimitMiddleware, which builds and sends its response
# directly rather than raising something CORS could otherwise intercept
# indirectly. Verified this is actually how Starlette's middleware stack
# behaves (last add_middleware call = outermost layer), not assumed --
# see backend/app/core/body_size_limit.py's own docstring for the
# ordering requirement this satisfies, and
# tests/test_security_headers.py / tests/test_body_size_limit.py for the
# direct proof that CORS headers actually land on these middlewares'
# responses, not just on the router's.
#
# CatchUnhandledExceptionsMiddleware is added first (innermost) so it sees
# exceptions from the router before anything else does -- see its own
# docstring in core/errors.py for why it has to be a middleware here
# rather than an @app.exception_handler(Exception).
app.add_middleware(CatchUnhandledExceptionsMiddleware)
app.add_middleware(RequestBodySizeLimitMiddleware, max_bytes=settings.max_request_body_bytes)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    # Exactly the methods this API's routes actually use (confirmed by
    # inspecting every @router.get/post/delete across backend/app/api/) --
    # not "*": DELETE was missing here (added for account deletion,
    # DELETE /api/auth/me), which would fail CORS preflight for that route
    # whenever frontend and backend are on different origins. No PUT/PATCH
    # exists anywhere in this API.
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
    # Without this, the browser's fetch() API silently strips these from
    # response.headers on a cross-origin request (frontend :5173, backend
    # :8000 in dev) — only a small "simple response header" allowlist is
    # exposed to JS by default. Found by an actual failing Playwright run
    # against the real server, not by inspection: the raw HTTP response had
    # the headers (visible to Playwright's network listener), but
    # extractionClient.ts's response.headers.get(...) read null for both.
    expose_headers=["X-Extraction-Applied", "X-Extraction-Confidence"],
)


configure_exception_handlers(app)


@app.get("/health")
async def health():
    return {"status": "ok"}


app.include_router(tryon.router)
app.include_router(auth.router)
app.include_router(extraction.router)
app.include_router(usage.router)
