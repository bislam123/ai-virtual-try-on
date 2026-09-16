"""Job persistence, behind a small interface with two implementations.

InMemoryJobStore (Milestone 3's original): a dict guarded by a lock. Fast,
zero setup, but doesn't survive a restart and won't work across multiple
worker processes — kept as-is because it's exactly what the fast fake-
provider test suite wants (see tests/test_tryon_api.py), and because it
still works fine for a single-process local dev run without a database.

DbJobStore (Milestone 6): the real, production-shaped implementation,
backed by db/models.py's JobRecord — this is what app/main.py wires up.

Both return the same plain `Job` dataclass, so nothing above this module
(TryOnService, the API routes) needs to know or care which one is active.
"""

import threading
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Optional

from ..providers.base import GarmentCategory, GarmentPhotoType


class JobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Job:
    id: str
    user_id: Optional[int]
    category: GarmentCategory
    num_timesteps: int
    guidance_scale: float
    seed: int
    client_ip: Optional[str] = None  # Milestone 11: anonymous-caller usage-quota tracking
    # Plumbing only (see providers/base.py's GarmentPhotoType) -- defaults to
    # "flat-lay", the only value the API layer accepts today.
    garment_photo_type: GarmentPhotoType = "flat-lay"
    status: JobStatus = JobStatus.PENDING
    error: Optional[str] = None
    saved: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class JobStore(ABC):
    @abstractmethod
    def create(
        self,
        *,
        user_id: Optional[int],
        category: GarmentCategory,
        num_timesteps: int,
        guidance_scale: float,
        seed: int,
        client_ip: Optional[str] = None,
        garment_photo_type: GarmentPhotoType = "flat-lay",
    ) -> Job: ...

    @abstractmethod
    def get(self, job_id: str) -> Optional[Job]: ...

    @abstractmethod
    def update_status(self, job_id: str, status: JobStatus, error: Optional[str] = None) -> None: ...

    @abstractmethod
    def mark_saved(self, job_id: str) -> None: ...


class InMemoryJobStore(JobStore):
    def __init__(self):
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(
        self,
        *,
        user_id: Optional[int],
        category: GarmentCategory,
        num_timesteps: int,
        guidance_scale: float,
        seed: int,
        client_ip: Optional[str] = None,
        garment_photo_type: GarmentPhotoType = "flat-lay",
    ) -> Job:
        job = Job(
            id=uuid.uuid4().hex,
            user_id=user_id,
            category=category,
            num_timesteps=num_timesteps,
            guidance_scale=guidance_scale,
            seed=seed,
            client_ip=client_ip,
            garment_photo_type=garment_photo_type,
        )
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

    def mark_saved(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.saved = True
