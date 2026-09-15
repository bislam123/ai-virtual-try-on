from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # Ties into the future free/premium architecture (brief section 4/23) —
    # deliberately just a plain configurable string column, not an enum with
    # hardcoded limits baked in. Actual quota enforcement is Milestone 11.
    plan: Mapped[str] = mapped_column(String(32), nullable=False, default="free")
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
