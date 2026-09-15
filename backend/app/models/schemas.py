from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from ..services.job_store import JobStatus


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


class ErrorResponse(BaseModel):
    detail: str
