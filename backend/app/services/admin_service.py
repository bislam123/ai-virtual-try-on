"""AdminService: read (and one write: enable/disable) queries for the
Admin/Operations milestone's /api/admin/* routes.

Every method here assumes the caller is already authorized as an admin --
see auth/dependencies.py's get_current_admin_user, enforced at the route
layer (api/admin.py), not here. This service has no opinion on who's
allowed to call it; it only knows how to answer the questions an admin
view needs -- the same separation QuotaService/CapacityService already
establish between "domain logic that talks to the database" and "HTTP
concerns," which api/admin.py's routes follow too (converting these plain
dataclasses into the Pydantic Admin* response schemas, the same way
usage.py's route builds a UsageStatusResponse from QuotaService's
QuotaStatus).

Deliberately plain SQLAlchemy queries against the existing User/Plan/
JobRecord tables -- no new tables beyond the two columns this milestone's
migration adds, no new query abstraction. This is read-mostly operational
tooling layered on data that already exists.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import AdminAuditLog, JobRecord, Plan, User, get_session
from .job_store import JobStatus

# A job's duration is only a meaningful, safely-calculable figure once
# it's reached a terminal state -- "time so far" for a still-pending/
# processing job isn't the same kind of number and would be misleading
# next to a real duration in the same column.
_TERMINAL_STATUSES = {JobStatus.COMPLETED.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value}
_ACTIVE_STATUSES = [JobStatus.PENDING.value, JobStatus.PROCESSING.value]

# Bounded samples for the dashboard, not full-table aggregates that would
# only get slower as the jobs table grows -- "basic inference statistics
# ... without intrusive instrumentation" means computed from a recent
# slice of existing rows, not a new metrics pipeline.
_AVG_DURATION_SAMPLE_SIZE = 50
_RECENT_FAILED_JOBS_LIMIT = 10

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


@dataclass
class AdminUserRow:
    id: int
    email: str
    plan: str
    created_at: datetime
    is_admin: bool
    is_active: bool


@dataclass
class AdminUserList:
    users: List[AdminUserRow]
    total: int


@dataclass
class AdminJobRow:
    id: str
    user_id: Optional[int]
    status: str
    category: str
    garment_photo_type: str
    error: Optional[str]
    created_at: datetime
    updated_at: datetime
    processing_duration_seconds: Optional[float]


@dataclass
class AdminJobList:
    jobs: List[AdminJobRow]
    total: int


@dataclass
class AdminPlanRow:
    name: str
    max_generations_per_day: Optional[int]
    max_generations_per_month: Optional[int]
    max_num_timesteps: Optional[int]


@dataclass
class AdminAuditLogRow:
    id: int
    admin_user_id: Optional[int]
    action: str
    target_type: str
    target_id: str
    details: Dict[str, Any]
    created_at: datetime


@dataclass
class AdminAuditLogList:
    entries: List[AdminAuditLogRow]
    total: int


@dataclass
class AdminDashboard:
    total_users: int
    active_users: int
    jobs_by_status: Dict[str, int]
    recent_failed_jobs: List[AdminJobRow]
    active_job_count: int
    max_active_job_capacity: int
    avg_processing_duration_seconds: Optional[float]


def _job_duration_seconds(record: JobRecord) -> Optional[float]:
    if record.status not in _TERMINAL_STATUSES:
        return None
    return (record.updated_at - record.created_at).total_seconds()


def _to_user_row(user: User) -> AdminUserRow:
    return AdminUserRow(
        id=user.id,
        email=user.email,
        plan=user.plan,
        created_at=user.created_at,
        is_admin=user.is_admin,
        is_active=user.is_active,
    )


def _to_job_row(record: JobRecord) -> AdminJobRow:
    return AdminJobRow(
        id=record.id,
        user_id=record.user_id,
        status=record.status,
        category=record.category,
        garment_photo_type=record.garment_photo_type,
        error=record.error,
        created_at=record.created_at,
        updated_at=record.updated_at,
        processing_duration_seconds=_job_duration_seconds(record),
    )


def _to_audit_row(entry: AdminAuditLog) -> AdminAuditLogRow:
    return AdminAuditLogRow(
        id=entry.id,
        admin_user_id=entry.admin_user_id,
        action=entry.action,
        target_type=entry.target_type,
        target_id=entry.target_id,
        details=entry.details,
        created_at=entry.created_at,
    )


def record_admin_audit_log(
    session: Session,
    *,
    admin_user_id: Optional[int],
    action: str,
    target_type: str,
    target_id: str,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    """Appends one audit row to `session` -- does NOT commit; the caller's
    own get_session() block commits (or rolls back) it together with
    whatever mutation it's recording, so the two can never disagree (see
    AdminAuditLog's own docstring in db/models.py). `details` must only
    ever hold safe, non-secret context -- reviewed at every call site
    below, never a password/JWT/reset-token/other credential.
    """
    session.add(
        AdminAuditLog(
            admin_user_id=admin_user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details or {},
        )
    )


class AdminService:
    def list_users(
        self, *, search: Optional[str] = None, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0
    ) -> AdminUserList:
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        offset = max(0, offset)
        with get_session() as session:
            query = select(User)
            count_query = select(func.count()).select_from(User)
            search = (search or "").strip()
            if search:
                # An operational substring search over email, admin-only --
                # not the unauthenticated-facing login/forgot-password
                # surface, so none of that doctrine's enumeration-safety
                # concerns apply here (an admin is explicitly allowed to
                # know which emails exist).
                pattern = f"%{search}%"
                query = query.where(User.email.ilike(pattern))
                count_query = count_query.where(User.email.ilike(pattern))

            total = session.scalar(count_query) or 0
            records = session.scalars(query.order_by(User.created_at.desc()).limit(limit).offset(offset)).all()
            users = [_to_user_row(u) for u in records]
        return AdminUserList(users=users, total=total)

    def get_user(self, user_id: int) -> Optional[AdminUserRow]:
        with get_session() as session:
            user = session.get(User, user_id)
            return _to_user_row(user) if user is not None else None

    def set_user_active(self, user_id: int, active: bool, *, actor_id: int) -> Optional[AdminUserRow]:
        with get_session() as session:
            user = session.get(User, user_id)
            if user is None:
                return None
            user.is_active = active
            record_admin_audit_log(
                session,
                admin_user_id=actor_id,
                action="user_enabled" if active else "user_disabled",
                target_type="user",
                target_id=str(user_id),
                details={"email": user.email},
            )
            session.flush()
            session.refresh(user)
            return _to_user_row(user)

    def list_plans(self) -> List[AdminPlanRow]:
        with get_session() as session:
            plans = session.scalars(select(Plan).order_by(Plan.name)).all()
            return [
                AdminPlanRow(
                    name=p.name,
                    max_generations_per_day=p.max_generations_per_day,
                    max_generations_per_month=p.max_generations_per_month,
                    max_num_timesteps=p.max_num_timesteps,
                )
                for p in plans
            ]

    def update_plan(
        self,
        name: str,
        *,
        max_generations_per_day: Optional[int],
        max_generations_per_month: Optional[int],
        max_num_timesteps: Optional[int],
        actor_id: int,
    ) -> Optional[AdminPlanRow]:
        """Edits an existing plan's three limit fields (see db/models.py's
        Plan docstring) -- never creates or deletes a plan, matching this
        milestone's "edit existing plan limits" scope, not a redesign of
        the plan system. All three fields are always written together
        (the caller/route always supplies all three, `None` meaning
        "unlimited" for that field) -- there is no partial-update path, so
        there's no ambiguity between "leave unchanged" and "set to null."
        """
        with get_session() as session:
            plan = session.get(Plan, name)
            if plan is None:
                return None
            before = {
                "max_generations_per_day": plan.max_generations_per_day,
                "max_generations_per_month": plan.max_generations_per_month,
                "max_num_timesteps": plan.max_num_timesteps,
            }
            plan.max_generations_per_day = max_generations_per_day
            plan.max_generations_per_month = max_generations_per_month
            plan.max_num_timesteps = max_num_timesteps
            after = {
                "max_generations_per_day": max_generations_per_day,
                "max_generations_per_month": max_generations_per_month,
                "max_num_timesteps": max_num_timesteps,
            }
            # Plan limits are plain integers/None -- never a secret -- so
            # recording both old and new values in full is safe and is
            # exactly what makes this audit entry useful (see AdminAuditLog's
            # docstring on what `details` may and may not hold).
            record_admin_audit_log(
                session,
                admin_user_id=actor_id,
                action="plan_updated",
                target_type="plan",
                target_id=name,
                details={"before": before, "after": after},
            )
            session.flush()
            session.refresh(plan)
            return AdminPlanRow(
                name=plan.name,
                max_generations_per_day=plan.max_generations_per_day,
                max_generations_per_month=plan.max_generations_per_month,
                max_num_timesteps=plan.max_num_timesteps,
            )

    def list_audit_log(
        self,
        *,
        target_type: Optional[str] = None,
        target_id: Optional[str] = None,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> AdminAuditLogList:
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        offset = max(0, offset)
        with get_session() as session:
            query = select(AdminAuditLog)
            count_query = select(func.count()).select_from(AdminAuditLog)
            if target_type:
                query = query.where(AdminAuditLog.target_type == target_type)
                count_query = count_query.where(AdminAuditLog.target_type == target_type)
            if target_id:
                query = query.where(AdminAuditLog.target_id == target_id)
                count_query = count_query.where(AdminAuditLog.target_id == target_id)

            total = session.scalar(count_query) or 0
            records = session.scalars(
                query.order_by(AdminAuditLog.created_at.desc()).limit(limit).offset(offset)
            ).all()
            entries = [_to_audit_row(r) for r in records]
        return AdminAuditLogList(entries=entries, total=total)

    def list_jobs(
        self,
        *,
        status: Optional[str] = None,
        user_id: Optional[int] = None,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> AdminJobList:
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        offset = max(0, offset)
        with get_session() as session:
            query = select(JobRecord)
            count_query = select(func.count()).select_from(JobRecord)
            if status:
                query = query.where(JobRecord.status == status)
                count_query = count_query.where(JobRecord.status == status)
            if user_id is not None:
                query = query.where(JobRecord.user_id == user_id)
                count_query = count_query.where(JobRecord.user_id == user_id)

            total = session.scalar(count_query) or 0
            records = session.scalars(
                query.order_by(JobRecord.created_at.desc()).limit(limit).offset(offset)
            ).all()
            jobs = [_to_job_row(r) for r in records]
        return AdminJobList(jobs=jobs, total=total)

    def get_dashboard(self, max_active_jobs: int) -> AdminDashboard:
        with get_session() as session:
            total_users = session.scalar(select(func.count()).select_from(User)) or 0
            active_users = (
                session.scalar(select(func.count()).select_from(User).where(User.is_active.is_(True))) or 0
            )

            status_counts = session.execute(select(JobRecord.status, func.count()).group_by(JobRecord.status)).all()
            jobs_by_status = {status: count for status, count in status_counts}

            active_job_count = (
                session.scalar(
                    select(func.count()).select_from(JobRecord).where(JobRecord.status.in_(_ACTIVE_STATUSES))
                )
                or 0
            )

            recent_failed_records = session.scalars(
                select(JobRecord)
                .where(JobRecord.status == JobStatus.FAILED.value)
                .order_by(JobRecord.updated_at.desc())
                .limit(_RECENT_FAILED_JOBS_LIMIT)
            ).all()
            recent_failed_jobs = [_to_job_row(r) for r in recent_failed_records]

            recent_completed = session.scalars(
                select(JobRecord)
                .where(JobRecord.status == JobStatus.COMPLETED.value)
                .order_by(JobRecord.updated_at.desc())
                .limit(_AVG_DURATION_SAMPLE_SIZE)
            ).all()
            durations = [(r.updated_at - r.created_at).total_seconds() for r in recent_completed]
            avg_duration = (sum(durations) / len(durations)) if durations else None

        return AdminDashboard(
            total_users=total_users,
            active_users=active_users,
            jobs_by_status=jobs_by_status,
            recent_failed_jobs=recent_failed_jobs,
            active_job_count=active_job_count,
            max_active_job_capacity=max_active_jobs,
            avg_processing_duration_seconds=avg_duration,
        )
