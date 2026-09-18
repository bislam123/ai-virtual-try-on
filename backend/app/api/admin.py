"""Admin/Operations routes -- every one of these is gated by
get_current_admin_user (auth/dependencies.py), which itself composes on
get_current_user_required: an unauthenticated request gets the same 401
every other protected endpoint gives, an authenticated non-admin gets
403, and only an authenticated admin ever reaches a route body. No route
here re-checks is_admin itself -- the dependency is the single point of
truth, the same way every job route shares one ownership-gate method
rather than each re-implementing it.

Mostly read-only, plus a small set of narrow, audited mutations: account
enable/disable, and editing an *existing* plan's limit fields (never
creating/deleting a plan, never touching a try-on job). Every one of
those mutations is recorded to admin_audit_log in the same database
transaction as the mutation itself -- see services/admin_service.py's
record_admin_audit_log() and db/models.py's AdminAuditLog docstring --
and the log itself is readable back through GET /audit-log below, gated
by the same router-level admin dependency as everything else here.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..auth.dependencies import get_current_admin_user
from ..config import settings
from ..core.errors import UserFacingError
from ..db import User
from ..models.schemas import (
    AdminAuditLogEntry,
    AdminAuditLogListResponse,
    AdminDashboardResponse,
    AdminJobListResponse,
    AdminJobSummary,
    AdminPlanResponse,
    AdminUserDetail,
    AdminUserListResponse,
    AdminUserSummary,
    PlanUpdateRequest,
    UsageStatusResponse,
)
from ..services.admin_service import AdminAuditLogRow, AdminJobRow, AdminPlanRow, AdminService, AdminUserRow
from ..services.job_store import JobStatus
from ..services.quota_service import QuotaService

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(get_current_admin_user)])


def get_admin_service(request: Request) -> AdminService:
    return request.app.state.admin_service


def get_quota_service(request: Request) -> QuotaService:
    return request.app.state.quota_service


def _user_summary(row: AdminUserRow) -> AdminUserSummary:
    return AdminUserSummary(
        id=row.id, email=row.email, plan=row.plan, created_at=row.created_at, is_admin=row.is_admin,
        is_active=row.is_active,
    )


def _job_summary(row: AdminJobRow) -> AdminJobSummary:
    return AdminJobSummary(
        job_id=row.id,
        user_id=row.user_id,
        status=JobStatus(row.status),
        category=row.category,
        garment_photo_type=row.garment_photo_type,
        error=row.error,
        created_at=row.created_at,
        updated_at=row.updated_at,
        processing_duration_seconds=row.processing_duration_seconds,
    )


def _plan_response(row: AdminPlanRow) -> AdminPlanResponse:
    return AdminPlanResponse(
        name=row.name,
        max_generations_per_day=row.max_generations_per_day,
        max_generations_per_month=row.max_generations_per_month,
        max_num_timesteps=row.max_num_timesteps,
    )


def _audit_entry(row: AdminAuditLogRow) -> AdminAuditLogEntry:
    return AdminAuditLogEntry(
        id=row.id,
        admin_user_id=row.admin_user_id,
        action=row.action,
        target_type=row.target_type,
        target_id=row.target_id,
        details=row.details,
        created_at=row.created_at,
    )


@router.get("/users", response_model=AdminUserListResponse)
async def list_users(
    search: Optional[str] = Query(None, max_length=320, description="Substring match on email"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    admin_service: AdminService = Depends(get_admin_service),
):
    result = admin_service.list_users(search=search, limit=limit, offset=offset)
    return AdminUserListResponse(
        users=[_user_summary(u) for u in result.users], total=result.total, limit=limit, offset=offset
    )


@router.get("/users/{user_id}", response_model=AdminUserDetail)
async def get_user(
    user_id: int,
    admin_service: AdminService = Depends(get_admin_service),
    quota_service: QuotaService = Depends(get_quota_service),
):
    row = admin_service.get_user(user_id)
    if row is None:
        raise UserFacingError("We couldn't find that user.", status_code=404)
    # Exact same call/shape GET /api/usage/me makes for a user's own
    # account (see api/usage.py) -- reused, not reimplemented.
    status = quota_service.get_status(user_id=row.id, client_ip=None, plan_name=row.plan)
    usage = UsageStatusResponse(
        plan=status.plan,
        limit_per_day=status.limit_per_day,
        limit_per_month=status.limit_per_month,
        used_today=status.used_today,
        used_this_month=status.used_this_month,
        remaining_today=status.remaining_today,
        remaining_this_month=status.remaining_this_month,
        max_num_timesteps=status.max_num_timesteps,
    )
    return AdminUserDetail(
        id=row.id,
        email=row.email,
        plan=row.plan,
        created_at=row.created_at,
        is_admin=row.is_admin,
        is_active=row.is_active,
        usage=usage,
    )


def _set_active(user_id: int, active: bool, admin: User, admin_service: AdminService) -> AdminUserSummary:
    # A confusing, easy-to-fumble self-lockout, not a security bug -- but
    # cheap to guard against outright, and doing so avoids ever needing a
    # "reactivate yourself" recovery path (an admin who disabled their own
    # only account would need direct DB access again anyway).
    if user_id == admin.id:
        raise UserFacingError("You can't disable your own admin account.", status_code=400)
    row = admin_service.set_user_active(user_id, active, actor_id=admin.id)
    if row is None:
        raise UserFacingError("We couldn't find that user.", status_code=404)
    return _user_summary(row)


@router.post("/users/{user_id}/disable", response_model=AdminUserSummary)
async def disable_user(
    user_id: int, admin: User = Depends(get_current_admin_user), admin_service: AdminService = Depends(get_admin_service)
):
    return _set_active(user_id, False, admin, admin_service)


@router.post("/users/{user_id}/enable", response_model=AdminUserSummary)
async def enable_user(
    user_id: int, admin: User = Depends(get_current_admin_user), admin_service: AdminService = Depends(get_admin_service)
):
    return _set_active(user_id, True, admin, admin_service)


@router.get("/plans", response_model=list[AdminPlanResponse])
async def list_plans(admin_service: AdminService = Depends(get_admin_service)):
    return [_plan_response(p) for p in admin_service.list_plans()]


_VALID_JOB_STATUSES = {s.value for s in JobStatus}


@router.get("/jobs", response_model=AdminJobListResponse)
async def list_jobs(
    status: Optional[str] = Query(None),
    user_id: Optional[int] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    admin_service: AdminService = Depends(get_admin_service),
):
    if status is not None and status not in _VALID_JOB_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of: {', '.join(sorted(_VALID_JOB_STATUSES))}.")
    result = admin_service.list_jobs(status=status, user_id=user_id, limit=limit, offset=offset)
    return AdminJobListResponse(
        jobs=[_job_summary(j) for j in result.jobs], total=result.total, limit=limit, offset=offset
    )


@router.post("/plans/{name}", response_model=AdminPlanResponse)
async def update_plan(
    name: str,
    body: PlanUpdateRequest,
    admin: User = Depends(get_current_admin_user),
    admin_service: AdminService = Depends(get_admin_service),
):
    # AITRYON_MIN_NUM_TIMESTEPS/AITRYON_MAX_NUM_TIMESTEPS are this
    # deployment's own runtime clamp on every try-on request (see
    # api/tryon.py's effective_max = min(settings.max_num_timesteps,
    # plan_cap)) -- a plan cap outside that range would either be silently
    # overridden (confusing) or make the plan impossible to ever use
    # (a floor below the global minimum), so it's rejected here rather
    # than silently accepted.
    if body.max_num_timesteps is not None and not (
        settings.min_num_timesteps <= body.max_num_timesteps <= settings.max_num_timesteps
    ):
        raise UserFacingError(
            f"max_num_timesteps must be between {settings.min_num_timesteps} and {settings.max_num_timesteps}, or null for unlimited.",
            status_code=422,
        )
    row = admin_service.update_plan(
        name,
        max_generations_per_day=body.max_generations_per_day,
        max_generations_per_month=body.max_generations_per_month,
        max_num_timesteps=body.max_num_timesteps,
        actor_id=admin.id,
    )
    if row is None:
        raise UserFacingError("We couldn't find that plan.", status_code=404)
    return _plan_response(row)


@router.get("/audit-log", response_model=AdminAuditLogListResponse)
async def list_audit_log(
    target_type: Optional[str] = Query(None, max_length=32),
    target_id: Optional[str] = Query(None, max_length=64),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    admin_service: AdminService = Depends(get_admin_service),
):
    result = admin_service.list_audit_log(target_type=target_type, target_id=target_id, limit=limit, offset=offset)
    return AdminAuditLogListResponse(
        entries=[_audit_entry(e) for e in result.entries], total=result.total, limit=limit, offset=offset
    )


@router.get("/dashboard", response_model=AdminDashboardResponse)
async def get_dashboard(admin_service: AdminService = Depends(get_admin_service)):
    stats = admin_service.get_dashboard(settings.max_active_tryon_jobs)
    return AdminDashboardResponse(
        total_users=stats.total_users,
        active_users=stats.active_users,
        jobs_by_status=stats.jobs_by_status,
        recent_failed_jobs=[_job_summary(j) for j in stats.recent_failed_jobs],
        active_job_count=stats.active_job_count,
        max_active_job_capacity=stats.max_active_job_capacity,
        avg_processing_duration_seconds=stats.avg_processing_duration_seconds,
    )
