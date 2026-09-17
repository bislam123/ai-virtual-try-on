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
from typing import Dict, Optional, Tuple

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
    # Idempotency protection for POST /api/try-on -- see JobStore.create_idempotent
    # and services/tryon_service.py's start_job_idempotent/find_idempotent_job.
    # Only ever set together, and only when the client sent an Idempotency-Key.
    idempotency_scope: Optional[str] = None
    idempotency_key: Optional[str] = None
    idempotency_fingerprint: Optional[str] = None
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

    @abstractmethod
    def get_by_idempotency_key(self, idempotency_scope: str, idempotency_key: str) -> Optional[Job]:
        """The *active* (non-FAILED) job for this (scope, key) pair, if any
        -- a failed job never blocks a fresh retry under the same key (see
        create_idempotent). Read-only; used both as create_idempotent's own
        atomic check and as TryOnService.find_idempotent_job's cheap,
        non-mutating pre-check ahead of the quota check."""
        ...

    @abstractmethod
    def create_idempotent(
        self,
        *,
        idempotency_scope: str,
        idempotency_key: str,
        idempotency_fingerprint: str,
        user_id: Optional[int],
        category: GarmentCategory,
        num_timesteps: int,
        guidance_scale: float,
        seed: int,
        client_ip: Optional[str] = None,
        garment_photo_type: GarmentPhotoType = "flat-lay",
    ) -> Tuple[Job, bool]:
        """Atomically create a new job for (idempotency_scope,
        idempotency_key) unless an active (non-FAILED) one already exists,
        in which case that existing job is returned unchanged instead.

        Returns (job, created). Exactly one concurrent caller for a given
        (scope, key) pair may ever observe created=True; every other
        caller -- whether it arrives before, after, or genuinely
        simultaneously with the winner -- observes created=False and the
        winner's job. This is what makes duplicate submissions (double
        taps, network retries) safe under real concurrency, not just
        "unlikely to collide in practice": DbJobStore enforces it with a
        database-level unique constraint (jobs.ix_jobs_idempotency_active),
        InMemoryJobStore with its existing lock.

        Callers are responsible for comparing the returned job's
        idempotency_fingerprint against their own when created is False --
        this method only enforces "at most one active job per key", not
        "the key was used for the same request" (see
        TryOnService.start_job_idempotent).
        """
        ...


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

    def _find_active_by_idempotency_key_locked(self, idempotency_scope: str, idempotency_key: str) -> Optional[Job]:
        for job in self._jobs.values():
            if (
                job.idempotency_scope == idempotency_scope
                and job.idempotency_key == idempotency_key
                and job.status != JobStatus.FAILED
            ):
                return job
        return None

    def get_by_idempotency_key(self, idempotency_scope: str, idempotency_key: str) -> Optional[Job]:
        with self._lock:
            return self._find_active_by_idempotency_key_locked(idempotency_scope, idempotency_key)

    def create_idempotent(
        self,
        *,
        idempotency_scope: str,
        idempotency_key: str,
        idempotency_fingerprint: str,
        user_id: Optional[int],
        category: GarmentCategory,
        num_timesteps: int,
        guidance_scale: float,
        seed: int,
        client_ip: Optional[str] = None,
        garment_photo_type: GarmentPhotoType = "flat-lay",
    ) -> Tuple[Job, bool]:
        # The check and the insert happen under the same lock -- that's the
        # entire atomicity guarantee here (no separate unique-constraint
        # machinery needed for the single-process in-memory store, unlike
        # DbJobStore).
        with self._lock:
            existing = self._find_active_by_idempotency_key_locked(idempotency_scope, idempotency_key)
            if existing is not None:
                return existing, False
            job = Job(
                id=uuid.uuid4().hex,
                user_id=user_id,
                category=category,
                num_timesteps=num_timesteps,
                guidance_scale=guidance_scale,
                seed=seed,
                client_ip=client_ip,
                garment_photo_type=garment_photo_type,
                idempotency_scope=idempotency_scope,
                idempotency_key=idempotency_key,
                idempotency_fingerprint=idempotency_fingerprint,
            )
            self._jobs[job.id] = job
            return job, True
