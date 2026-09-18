from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Plan(Base):
    """A usage tier (brief sections 4/23: User -> Account -> Plan -> Usage
    quota -> AI generation). Limits live here, in the database, specifically
    so they're an operations change (an UPDATE statement) rather than a code
    change — "configurable through backend configuration/database rather
    than hard-coded throughout the frontend" (brief section 23). Seeded with
    the brief's own example numbers (free: 5/day, premium: 100/month) — see
    the Milestone 11 migration — as starting defaults, not fixed constants;
    nothing in the application code assumes these specific values.
    """

    __tablename__ = "plans"

    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    max_generations_per_day: Mapped[Optional[int]] = mapped_column(nullable=True)
    max_generations_per_month: Mapped[Optional[int]] = mapped_column(nullable=True)
    # The "Higher resolution... Advanced features" half of the brief's
    # premium description (section 4), made concrete with what already
    # exists rather than a speculative new feature: a plan can permit more
    # diffusion steps (= quality) than another, reusing num_timesteps
    # (already a per-request parameter, see providers/base.py) rather than
    # inventing a separate quality axis.
    max_num_timesteps: Mapped[Optional[int]] = mapped_column(nullable=True)
    # DateTime(timezone=True): stored as Postgres timestamptz -- an
    # unambiguous instant, not a session-timezone-dependent wall-clock
    # value. See the 2026-09-17 migration's docstring for why this matters
    # and how existing data was preserved when this was added.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    plan: Mapped[str] = mapped_column(ForeignKey("plans.name"), nullable=False, default="free")
    # Server-side JWT revocation, lightweight version: every access token
    # embeds the auth_version it was issued under (see auth/security.py's
    # create_access_token); auth/dependencies.py's get_current_user_optional
    # rejects a token whose embedded version doesn't match this column's
    # *current* value. Bumping this integer is therefore "revoke every
    # existing token for this user" without a token blocklist/session table
    # — no per-token bookkeeping, just one integer per user, checked against
    # the one row a request already has to load to authenticate at all.
    # Starts at 1 (not 0) purely so a token that's missing the claim
    # entirely can never accidentally coincide with a real value by matching
    # an unset/zero default — see decode_access_token's docstring for why a
    # missing claim is rejected outright anyway, making this belt-and-braces
    # rather than load-bearing on its own.
    auth_version: Mapped[int] = mapped_column(nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    # cascade: deleting a user deletes their job records too — no orphaned
    # account-linked rows lingering after an account is removed.
    jobs: Mapped[list["JobRecord"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    # Same reasoning as jobs above — a deleted account leaves no orphaned
    # reset tokens behind.
    password_reset_tokens: Mapped[list["PasswordResetToken"]] = relationship(cascade="all, delete-orphan")


class PasswordResetToken(Base):
    """A single-use password reset token (see services/password_reset_service.py
    and api/auth.py's forgot-password/reset-password endpoints).

    Only a SHA-256 hash of the token is stored, never the raw value — the
    same hash-not-raw-value convention JobRecord.idempotency_fingerprint
    already uses above — so a database read alone (backup, leaked dump,
    admin query) can never be used to reset an account's password; only
    the raw token the user actually received by email can.

    `used_at` (rather than a boolean flag or deleting the row on use) is
    what makes single-use enforcement atomic under real concurrent
    requests: reset_password's UPDATE ... WHERE used_at IS NULL AND
    expires_at > now() both claims and invalidates the token in one
    statement, relying on Postgres's own row-level locking rather than an
    application-level check-then-act — see password_reset_service.py's
    consume_reset_token docstring for the full reasoning.
    """

    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # DateTime(timezone=True) -- see Plan.created_at's comment above; every
    # timestamp column added since that migration follows the same
    # timestamptz convention from the start, never `timestamp without time
    # zone`.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class JobRecord(Base):
    """A try-on job. Replaces the in-memory JobStore from Milestone 3 for
    production use — see backend/app/services/job_store.py's DbJobStore.
    Anonymous jobs (user_id is NULL) are fully supported: the brief requires
    the core flow to work without an account.
    """

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # Milestone 11: how an anonymous (no account) submission's usage quota is
    # tracked — the brief requires the core flow to keep working without an
    # account, which means quota enforcement can't rely on user_id alone.
    # Recorded for every job, not just anonymous ones, so quota counting
    # logic (services/quota_service.py) doesn't need a special case.
    client_ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    error: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)

    category: Mapped[str] = mapped_column(String(16), nullable=False)
    num_timesteps: Mapped[int] = mapped_column(nullable=False)
    guidance_scale: Mapped[float] = mapped_column(nullable=False)
    seed: Mapped[int] = mapped_column(nullable=False)
    # See providers/base.py's GarmentPhotoType -- the API layer (api/tryon.py's
    # VALID_GARMENT_PHOTO_TYPES) accepts "flat-lay" (default) and "model" (a
    # garment already worn by another person in the source photo); anything
    # else is rejected before a row is ever written. Not nullable, with a
    # default, so rows written before "model" support existed still read
    # back correctly.
    garment_photo_type: Mapped[str] = mapped_column(String(16), nullable=False, default="flat-lay")

    # Privacy requirement (brief section 16): a result is temporary by
    # default and only persists past the TTL sweep if the user explicitly
    # saved it. See backend/scripts/cleanup_expired_results.py.
    saved: Mapped[bool] = mapped_column(nullable=False, default=False)

    # Idempotency protection for POST /api/try-on (double taps, network
    # retries, mobile connection instability): all three are only ever set
    # together, when the client sends an Idempotency-Key header -- see
    # services/tryon_service.py's start_job_idempotent/find_idempotent_job
    # and the migration that added these columns for the full design.
    # idempotency_scope is "user:<id>" or "ip:<ip>" (the same identity
    # string already used for rate limiting/quota, see api/tryon.py's
    # client_key), never a global namespace shared across users.
    idempotency_scope: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # Client-supplied, opaque -- capped at 255 chars at the API layer to
    # fit this column.
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # SHA-256 hex digest of the material request fields (see
    # _idempotency_fingerprint) -- what makes "same key, different
    # request" detectable rather than silently reused.
    idempotency_fingerprint: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # DateTime(timezone=True) -- see Plan.created_at's comment above. This
    # is the column cleanup_expired_results.py and QuotaService.get_status
    # both compare against a Python datetime.now(timezone.utc)-derived
    # cutoff; storing an unambiguous instant instead of a session-timezone-
    # dependent wall-clock value is what makes those comparisons correct
    # regardless of the database server's configured timezone.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    user: Mapped[Optional["User"]] = relationship(back_populates="jobs")

    __table_args__ = (
        # The atomic guarantee idempotency depends on: at most one *active*
        # (non-failed) row per (idempotency_scope, idempotency_key). A
        # concurrent duplicate INSERT for the same pair fails here at the
        # database level rather than racing in application code (see
        # DbJobStore.create_idempotent). A failed job is deliberately
        # excluded from the predicate -- once a job fails, Postgres drops
        # its entry from this partial index automatically on that UPDATE,
        # which is what lets a retry after a genuine failure create a fresh
        # job under the same key instead of being permanently stuck.
        Index(
            "ix_jobs_idempotency_active",
            "idempotency_scope",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL AND status <> 'failed'"),
        ),
    )
