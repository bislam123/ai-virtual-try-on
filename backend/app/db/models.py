from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import ForeignKey, String
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
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    plan: Mapped[str] = mapped_column(ForeignKey("plans.name"), nullable=False, default="free")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)

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

    # Privacy requirement (brief section 16): a result is temporary by
    # default and only persists past the TTL sweep if the user explicitly
    # saved it. See backend/scripts/cleanup_expired_results.py.
    saved: Mapped[bool] = mapped_column(nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)

    user: Mapped[Optional["User"]] = relationship(back_populates="jobs")
