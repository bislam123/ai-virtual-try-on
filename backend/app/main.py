import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import auth, tryon
from .config import settings
from .core.errors import configure_exception_handlers
from .providers.selfhosted import SelfHostedVTONProvider
from .services.db_job_store import DbJobStore
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
    # Real, persistent job storage (Postgres) — see services/job_store.py for
    # why InMemoryJobStore still exists (it's what the fast test suite uses).
    # Table creation is handled by Alembic migrations (backend/alembic/),
    # not here — run `alembic upgrade head` before starting the server.
    job_store = DbJobStore()
    provider = SelfHostedVTONProvider(weights_dir=settings.weights_dir, device=settings.device)
    app.state.tryon_service = TryOnService(provider=provider, storage=storage, job_store=job_store)
    app.state.rate_limiter = RateLimiter(settings.rate_limit_max_requests, settings.rate_limit_window_seconds)
    logger.info("AI Try-On backend ready.")
    yield


app = FastAPI(title="AI Try-On API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


configure_exception_handlers(app)


@app.get("/health")
async def health():
    return {"status": "ok"}


app.include_router(tryon.router)
app.include_router(auth.router)
