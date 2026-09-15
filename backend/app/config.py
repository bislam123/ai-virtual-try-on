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

    # --- CORS (frontend origins allowed to call this API) ---
    cors_origins: List[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]


settings = Settings()
