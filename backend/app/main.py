import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import auth, extraction, tryon, usage
from .config import settings
from .core.errors import configure_exception_handlers
from .providers.selfhosted import SelfHostedVTONProvider
from .services.db_job_store import DbJobStore
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
    provider = SelfHostedVTONProvider(weights_dir=settings.weights_dir, device=settings.device)
    app.state.tryon_service = TryOnService(provider=provider, storage=storage, job_store=job_store)
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
    app.state.quota_service = QuotaService()
    logger.info("AI Try-On backend ready.")
    yield


app = FastAPI(title="AI Try-On API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST"],
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
