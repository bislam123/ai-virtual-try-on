"""In-memory job store.

Deliberately simple for Milestone 3: a dict guarded by a lock, in a single
process. This is a real known limitation, not an oversight — it means jobs
don't survive a server restart and won't work across multiple worker
processes. Milestone 6 introduces a real database for accounts anyway; job
state should move there (or to Redis) at that point. Kept behind this same
small interface so nothing above it needs to change when that happens.
"""

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

from PIL import Image


class JobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Job:
    id: str
    status: JobStatus = JobStatus.PENDING
    error: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # Working state for the background task (see TryOnService.start_job/run_job).
    # Not part of the public API response — cleared once the job finishes.
    params: Dict[str, Any] = field(default_factory=dict)
    person_image: Optional[Image.Image] = None
    garment_image: Optional[Image.Image] = None


class JobStore:
    def __init__(self):
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self) -> Job:
        job = Job(id=uuid.uuid4().hex)
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def update_status(self, job_id: str, status: JobStatus, error: Optional[str] = None) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = status
            job.error = error
            job.updated_at = datetime.now(timezone.utc)
