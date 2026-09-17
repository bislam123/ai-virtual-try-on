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
