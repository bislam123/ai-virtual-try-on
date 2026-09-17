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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    # cascade: deleting a user deletes their job records too — no orphaned
    # account-linked rows lingering after an account is removed.
    jobs: Mapped[list["JobRecord"]] = relationship(back_populates="user", cascade="all, delete-orphan")


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
    # Plumbing only (see providers/base.py's GarmentPhotoType docstring) --
    # the API layer (api/tryon.py) rejects anything but "flat-lay" today,
    # so every row this column is written to has that value. Not nullable,
    # with a default, so existing rows and any code path that doesn't pass
    # it explicitly still behave exactly as before this column existed.
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
