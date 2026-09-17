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

# The exact insecure local-dev defaults documented in docs/ENVIRONMENT.md as
# "Must be overridden outside local dev" -- these are the only two fields
# with that documented status; see Settings.check_production_secrets below
# for why only these two are validated, not every setting.
_INSECURE_JWT_SECRET_KEY = "dev-only-insecure-secret-change-me"
_INSECURE_DATABASE_CREDENTIAL_MARKER = "devpassword"


class InsecureProductionConfigError(RuntimeError):
    """Raised when AITRYON_ENVIRONMENT=production but a documented
    production-required secret is still at its insecure local-dev default.

    Deliberately a plain exception, not raised from inside a pydantic
    validator: pydantic's own ValidationError.__str__() includes an
    `input_value=...` fragment containing the *actual configured field
    values* (verified directly -- a model_validator that raises ValueError
    produces an error whose string form contains the real secret when one
    was set via an env var, not just a placeholder). That would leak
    credentials into any log or traceback capturing this exception. This
    type's message is always exactly the static text passed to it here --
    nothing else -- so it is always safe to log.
    """


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(PROJECT_ROOT / ".env"), env_prefix="AITRYON_", extra="ignore")

    # Set AITRYON_ENVIRONMENT=production in a real deployment's environment
    # to enable the startup secret check below (see check_production_secrets).
    # Defaults to "development" so local dev (no env vars set) and the test
    # suite (which never sets this) are completely unaffected.
    environment: str = "development"

    # --- AI model ---
    weights_dir: str = str(PROJECT_ROOT / "ai" / "models" / "fashn-vton-1.5")
    device: str = "cpu"  # "cuda" on a GPU inference host later; never hardcode elsewhere
    default_num_timesteps: int = 30
    min_num_timesteps: int = 4
    max_num_timesteps: int = 50
    default_guidance_scale: float = 1.5

    # How long a single generate() call may run before SelfHostedVTONProvider
    # gives up waiting and reports failure -- NOT a true kill of the
    # underlying computation (see providers/selfhosted.py's module
    # docstring for why that isn't possible with this architecture without
    # a separate worker process). Generous default (1h) covers this
    # project's documented CPU-dev worst case (10-70+ minutes per
    # docs/DEVELOPMENT.md); a real GPU deployment, where a legitimate
    # generation takes seconds, should override this much tighter.
    inference_timeout_seconds: int = 3600
    # How long a caller waits to acquire the provider's internal lock
    # before giving up -- covers both "a legitimately long generation is
    # already running" and "an earlier one hit inference_timeout_seconds
    # and is still holding the lock" (see selfhosted.py). Same reasoning
    # as inference_timeout_seconds for the default.
    provider_lock_acquire_timeout_seconds: int = 3600

    # How long a job may sit in status=processing before the recovery sweep
    # (services/job_recovery.py, run at app startup and by
    # scripts/cleanup_expired_results.py) treats it as abandoned -- e.g.
    # the server process was killed mid-generation -- and marks it failed.
    # Deliberately larger than inference_timeout_seconds above: the
    # in-process timeout is the fast path for a hang in a still-running
    # process; this is the slower backstop for when the process itself is
    # gone and nothing else could mark the job failed. Minutes, not
    # seconds: legitimate CPU-dev jobs can take over an hour.
    stale_job_threshold_minutes: int = 120

    # --- Storage ---
    storage_dir: str = str(PROJECT_ROOT / "backend" / "storage")

    # --- Upload validation (see app/core/validation.py) ---
    max_upload_size_mb: int = 10
    max_image_dimension_px: int = 4096

    # --- Request body size limit (see app/core/body_size_limit.py) ---
    # A different layer from max_upload_size_mb/max_image_dimension_px
    # above: those validate a single already-read image field's bytes/
    # pixels, well after the raw HTTP body has already been fully
    # received. This caps the *raw request body as a whole*, enforced
    # before Starlette/python-multipart ever parses it and without
    # buffering an oversized body first -- see that module's docstring for
    # exactly how, and why a middleware-level check is required at all
    # (the per-field checks alone can't prevent the server from spending
    # time/memory/disk receiving an effectively unbounded body before they
    # ever get a chance to run).
    #
    # Default (25 MiB) is sized for this app's actual worst-case
    # legitimate request, not picked arbitrarily: POST /api/try-on is the
    # largest real request this API accepts -- two image fields, each
    # individually capped at max_upload_size_mb (10MB default) above, so
    # 20MB of real image data -- plus a handful of small form fields
    # (category, garment_photo_type, num_timesteps, seed) and multipart
    # boundary/header overhead per part (a few hundred bytes each,
    # negligible). 25 MiB leaves comfortable headroom above that 20MB
    # legitimate ceiling (room for e.g. two ~9.5MB phone photos plus
    # overhead) without being needlessly generous to an oversized request.
    # If max_upload_size_mb is ever raised, reconsider this alongside it --
    # the two are independent settings, not derived from one another.
    #
    # Production recommendation: if a reverse proxy sits in front of this
    # app (nginx, Caddy, a cloud load balancer, ...), it should carry its
    # own aligned body-size limit too (e.g. nginx's client_max_body_size)
    # -- this application-level limit is defense in depth, not a
    # substitute for one, since a proxy-level limit can reject an
    # oversized request before it ever reaches this process at all. Keep
    # the two aligned (proxy limit >= this one) so the proxy doesn't
    # silently truncate a request this layer would otherwise have handled
    # cleanly. See docs/ENVIRONMENT.md.
    max_request_body_bytes: int = 25 * 1024 * 1024

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

    # Auth (login/signup) is a materially different threat profile than the
    # limits above -- a high-value target for automated credential-stuffing
    # and brute-force, not just cost/abuse protection -- so it gets its own,
    # deliberately tighter limits, applied per api/auth.py's own dual-key
    # strategy (see that module for why): a per-IP limit (catches one
    # source hammering many accounts) and, for login specifically, an
    # additional per-account limit (catches one account being brute-forced
    # from many/rotating IPs -- a per-IP limit alone can't catch that).
    auth_login_ip_rate_limit_max_requests: int = 10
    auth_login_ip_rate_limit_window_seconds: int = 900
    auth_login_account_rate_limit_max_requests: int = 5
    auth_login_account_rate_limit_window_seconds: int = 900
    # Signup has no existing-account identity to key a second check on --
    # its abuse profile is mass fake-account creation, which a per-IP limit
    # alone already addresses. Longer window, tighter count: a legitimate
    # user signs up once, ever, from a given IP.
    auth_signup_rate_limit_max_requests: int = 5
    auth_signup_rate_limit_window_seconds: int = 3600
    # Forgot-password gets its own dual-key strategy, same shape as login's
    # above but tuned for a different abuse profile: the per-IP limit
    # catches one source enumerating many target emails, and the
    # per-email limit caps how many reset emails a single (real or
    # made-up) address can trigger — protecting a real user's inbox from
    # being bombed, not protecting a secret. Since the endpoint's response
    # never reveals whether the email is registered (see api/auth.py), an
    # attacker keying the email-scoped limiter with a fake address only
    # ever exhausts that fake address's own separate budget.
    auth_forgot_password_ip_rate_limit_max_requests: int = 5
    auth_forgot_password_ip_rate_limit_window_seconds: int = 3600
    auth_forgot_password_email_rate_limit_max_requests: int = 3
    auth_forgot_password_email_rate_limit_window_seconds: int = 3600

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

    # How long a password reset link is valid for — short on purpose (a
    # reset link is effectively a bearer credential for taking over the
    # account while it's live). See services/password_reset_service.py.
    password_reset_token_expire_minutes: int = 30

    # The frontend origin a password reset email's link points to (e.g.
    # "https://app.example.com" -> ".../?reset_token=..."). Deliberately
    # separate from cors_origins (a *list* of origins allowed to call this
    # API) -- this is the single origin the backend itself constructs a
    # user-facing URL against, and there's no reason those need to be the
    # same setting even though they'll typically share a value. Not
    # validated by check_production_secrets/check_production_cors below:
    # leaving it at the localhost dev default in production is a
    # functional misconfiguration (emailed links point at the wrong
    # place), not a vulnerability -- same reasoning check_production_cors
    # already documents for cors_origins itself.
    frontend_base_url: str = "http://localhost:5173"

    def check_production_secrets(self) -> None:
        """Call once, right after construction (see the bottom of this
        module) -- refuses to start (raises before the app can serve a
        single request) if AITRYON_ENVIRONMENT=production is set but a
        documented production-required secret is still at its
        known-insecure local-dev default.

        Scope is deliberately narrow: only jwt_secret_key and database_url,
        because docs/ENVIRONMENT.md is the single source of truth for which
        settings are "Must be overridden outside local dev" -- as of this
        check, only those two carry that documented status. Every other
        field (rate limits, timesteps, storage paths, CORS origins, TTLs,
        ...) is an ordinary tunable, not a secret with a known-insecure
        default, and is intentionally left unvalidated here.

        Local dev and the test suite are unaffected: this entire check is
        skipped unless environment is explicitly "production", which
        neither ever sets (default is "development").

        A plain method call, not a pydantic validator -- see
        InsecureProductionConfigError's docstring for why: pydantic wraps a
        validator's raised error with metadata that includes the actual
        configured field values, which would leak credentials into any log
        or traceback capturing it. This method's own exception never does;
        its message is always exactly the static text below.
        """
        if self.environment != "production":
            return

        problems = []
        if self.jwt_secret_key == _INSECURE_JWT_SECRET_KEY:
            problems.append(
                "AITRYON_JWT_SECRET_KEY is still the insecure local-dev default. "
                'Generate a real one with: python -c "import secrets; print(secrets.token_hex(32))"'
            )
        if _INSECURE_DATABASE_CREDENTIAL_MARKER in self.database_url:
            problems.append(
                "AITRYON_DATABASE_URL still contains the insecure local-dev database credential. "
                "Set it to your production database's own connection string."
            )

        if problems:
            raise InsecureProductionConfigError(
                "Refusing to start with AITRYON_ENVIRONMENT=production while insecure "
                "development defaults are still in use:\n" + "\n".join(f"  - {p}" for p in problems)
            )

    def check_production_cors(self) -> None:
        """Refuses to start if AITRYON_ENVIRONMENT=production and
        AITRYON_CORS_ORIGINS contains a literal wildcard ("*").

        Concrete reason this is a security check, not just a style
        preference: CORS controls whether a cross-origin page's JavaScript
        can *read* the response, not just whether the request is sent. A
        wildcard origin lets any website script requests against this
        API's anonymous, unauthenticated endpoints (try-on submission,
        product extraction) as if it were the real frontend -- e.g.
        spending a visiting victim's anonymous rate-limit/quota budget
        using the victim's own browser session. This app never sets
        allow_credentials=True (Bearer-token auth, not cookies -- see
        main.py), which limits but does not eliminate that exposure, so a
        wildcard is still refused outright in production.

        Deliberately narrow, like check_production_secrets(): this does
        NOT flag cors_origins still being the localhost dev default in
        production. That is a functional misconfiguration (the real
        frontend simply can't reach the API, a "fails closed" annoyance,
        not a vulnerability -- it's *more* restrictive than intended, not
        less) and is out of scope for this check.
        """
        if self.environment != "production":
            return
        if "*" in self.cors_origins:
            raise InsecureProductionConfigError(
                'Refusing to start with AITRYON_ENVIRONMENT=production while AITRYON_CORS_ORIGINS '
                'contains a wildcard ("*"). Set it to your production frontend\'s exact origin(s).'
            )


settings = Settings()
settings.check_production_secrets()
settings.check_production_cors()
