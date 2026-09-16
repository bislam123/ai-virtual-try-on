"""Backend configuration.

Everything that should differ between dev/staging/production, or that
controls cost/quota behavior, lives here and is overridable via environment
variables (see .env.example at the project root) — never hardcoded in the
frontend or scattered across route handlers. This is the "configurable
through backend configuration/database rather than hard-coded" hook the
project brief asks for around usage limits.
"""

from pathlib import Path
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(PROJECT_ROOT / ".env"), env_prefix="AITRYON_", extra="ignore")

    # --- AI model ---
    weights_dir: str = str(PROJECT_ROOT / "ai" / "models" / "fashn-vton-1.5")
    device: str = "cpu"  # "cuda" on a GPU inference host later; never hardcode elsewhere
    default_num_timesteps: int = 30
    min_num_timesteps: int = 4
    max_num_timesteps: int = 50
    default_guidance_scale: float = 1.5

    # --- Storage ---
    storage_dir: str = str(PROJECT_ROOT / "backend" / "storage")

    # --- Upload validation (see app/core/validation.py) ---
    max_upload_size_mb: int = 10
    max_image_dimension_px: int = 4096

    # --- Rate limiting (see app/services/rate_limiter.py) ---
    # Deliberately generous dev defaults. Production per-plan quotas (free/premium)
    # are a Milestone 11 concern; this is just abuse/cost protection for the raw API.
    rate_limit_max_requests: int = 20
    rate_limit_window_seconds: int = 3600

    # Product image extraction (Milestone 7) is classical CV, not a GPU/CPU-
    # heavy diffusion job — milliseconds, not minutes — so it gets its own,
    # far more generous limit rather than sharing the try-on one.
    extraction_rate_limit_max_requests: int = 60
    extraction_rate_limit_window_seconds: int = 600

    # Product URL extraction (Milestone 8) makes an outbound request to a
    # site the caller chooses — tighter than the pure-image endpoint above,
    # since it's also a lever for pointing our server at arbitrary hosts
    # (mitigated by fetchers/ssrf_guard.py, but still worth rate-limiting
    # more conservatively than a purely local operation).
    url_extraction_rate_limit_max_requests: int = 20
    url_extraction_rate_limit_window_seconds: int = 600

    # Direct product-image-URL extraction (Method D's image handoff, added
    # alongside Milestone 9's extension work): also an outbound request to
    # a caller-chosen host, so it gets the same conservative treatment as
    # url_extraction above rather than the generous pure-upload one.
    image_url_extraction_rate_limit_max_requests: int = 20
    image_url_extraction_rate_limit_window_seconds: int = 600

    # --- CORS (frontend origins allowed to call this API) ---
    cors_origins: List[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # --- Database ---
    # Local dev default matches docs/DEVELOPMENT.md's PostgreSQL setup steps.
    # A real deployment must override this (and jwt_secret_key) via env vars —
    # never trust these defaults outside a local dev machine.
    database_url: str = "postgresql+psycopg2://postgres:devpassword@localhost:5432/aitryon"

    # --- Auth ---
    # Accounts are optional everywhere they can be (brief: "Do not force
    # users to create an account before testing the basic MVP"). This secret
    # signs JWT access tokens; MUST be overridden outside local dev.
    jwt_secret_key: str = "dev-only-insecure-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24 * 7  # 7 days

    # How long a completed job's result image is kept if the user never
    # explicitly saves it to their account (privacy requirement: temporary by
    # default). Enforced by backend/scripts/cleanup_expired_results.py.
    unsaved_result_ttl_hours: int = 24


settings = Settings()
