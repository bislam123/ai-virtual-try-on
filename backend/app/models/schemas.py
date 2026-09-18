from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field

from ..services.job_store import JobStatus


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class DeleteAccountRequest(BaseModel):
    # Requiring the current password (not just a valid access token) means a
    # leaked/stolen token alone can't trigger this irreversible action.
    password: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=1, max_length=512)
    # Same policy as SignupRequest.password above — the app's one existing
    # password requirement, reused rather than a second, divergent one.
    new_password: str = Field(min_length=8, max_length=128)


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class MessageResponse(BaseModel):
    """Generic {"message": ...} shape for endpoints whose whole point is a
    constant, non-revealing response — forgot-password (never confirms
    whether the email is registered) and reset-password (one message for
    every invalid/expired/already-used token, see api/auth.py)."""

    message: str


class UserResponse(BaseModel):
    id: int
    email: str
    plan: str
    created_at: datetime
    # Display-only (see api/auth.py's me()) -- the frontend uses this to
    # decide whether to show admin navigation; every actual admin endpoint
    # re-derives it server-side from the database, never from this or any
    # other client-supplied value.
    is_admin: bool = False


class TryOnJobCreated(BaseModel):
    job_id: str
    status: JobStatus


class TryOnJobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    error: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    result_url: Optional[str] = None
    saved: bool = False


class ErrorResponse(BaseModel):
    detail: str


class ExtractProductUrlRequest(BaseModel):
    # A plain `str`, not pydantic's `HttpUrl` — HttpUrl normalizes/reformats
    # the value (and rejects things like a bare IP a bit differently than we
    # want), and fetchers/ssrf_guard.py is the actual authority on whether a
    # URL is safe to fetch, not this schema. This just requires *a* string.
    url: str = Field(min_length=1, max_length=2048)


class UsageStatusResponse(BaseModel):
    plan: str
    limit_per_day: Optional[int]
    limit_per_month: Optional[int]
    used_today: int
    used_this_month: int
    remaining_today: Optional[int]
    remaining_this_month: Optional[int]
    max_num_timesteps: Optional[int]


# --- Admin/Operations (see services/admin_service.py, api/admin.py) -------
#
# None of these ever include password_hash, auth_version, a reset token,
# or a JWT -- every field below is either already shown to the user's own
# account (email, plan, created_at, usage numbers) or an operational
# field (job status/timestamps/category) with no PII beyond a user_id/
# email already visible elsewhere in this same API surface.


class AdminUserSummary(BaseModel):
    """One row of GET /api/admin/users. Deliberately no usage/quota here
    -- that would mean one extra query per row on every list page; see
    AdminUserDetail for the per-account usage breakdown."""

    id: int
    email: str
    plan: str
    created_at: datetime
    is_admin: bool
    is_active: bool


class AdminUserListResponse(BaseModel):
    users: list[AdminUserSummary]
    total: int


class AdminUserDetail(AdminUserSummary):
    # Exact same shape GET /api/usage/me already returns for a user's own
    # account -- reused via QuotaService, not reimplemented.
    usage: UsageStatusResponse


class AdminPlanResponse(BaseModel):
    name: str
    max_generations_per_day: Optional[int]
    max_generations_per_month: Optional[int]
    max_num_timesteps: Optional[int]


class AdminJobSummary(BaseModel):
    job_id: str
    # Minimal association only -- no email/other user PII on the job view
    # itself; cross-reference GET /api/admin/users/{id} if needed. None
    # for an anonymous job, same as everywhere else in this API.
    user_id: Optional[int]
    status: JobStatus
    category: str
    garment_photo_type: str
    # Already the same safe, pre-written message a normal user would see
    # (UserFacingError/GENERIC_FAILURE_MESSAGE) -- never a raw exception
    # or traceback; see services/tryon_service.py.
    error: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    # Only computed for a job in a terminal state (see admin_service.py)
    # -- for a still-pending/processing job, "duration so far" isn't a
    # meaningful, safely-calculable figure the way a finished job's is.
    processing_duration_seconds: Optional[float] = None


class AdminJobListResponse(BaseModel):
    jobs: list[AdminJobSummary]
    total: int


class AdminDashboardResponse(BaseModel):
    total_users: int
    active_users: int
    jobs_by_status: dict[str, int]
    recent_failed_jobs: list[AdminJobSummary]
    active_job_count: int
    max_active_job_capacity: int
    # From existing created_at/updated_at data on recently completed jobs
    # -- no new instrumentation, no timing code added anywhere in the
    # inference path (see admin_service.py).
    avg_processing_duration_seconds: Optional[float] = None
