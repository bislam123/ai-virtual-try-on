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


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


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
