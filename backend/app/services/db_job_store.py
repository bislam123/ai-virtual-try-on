"""DbJobStore: the JobStore interface (see job_store.py) backed by Postgres."""

import uuid
from typing import Optional

from ..db import JobRecord, get_session
from ..providers.base import GarmentCategory
from .job_store import Job, JobStatus, JobStore


def _to_job(record: JobRecord) -> Job:
    return Job(
        id=record.id,
        user_id=record.user_id,
        category=record.category,  # type: ignore[arg-type]
        num_timesteps=record.num_timesteps,
        guidance_scale=record.guidance_scale,
        seed=record.seed,
        status=JobStatus(record.status),
        error=record.error,
        saved=record.saved,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


class DbJobStore(JobStore):
    def create(
        self,
        *,
        user_id: Optional[int],
        category: GarmentCategory,
        num_timesteps: int,
        guidance_scale: float,
        seed: int,
    ) -> Job:
        record = JobRecord(
            id=uuid.uuid4().hex,
            user_id=user_id,
            category=category,
            num_timesteps=num_timesteps,
            guidance_scale=guidance_scale,
            seed=seed,
            status=JobStatus.PENDING.value,
        )
        with get_session() as session:
            session.add(record)
            session.flush()
            session.refresh(record)
            return _to_job(record)

    def get(self, job_id: str) -> Optional[Job]:
        with get_session() as session:
            record = session.get(JobRecord, job_id)
            return _to_job(record) if record else None

    def update_status(self, job_id: str, status: JobStatus, error: Optional[str] = None) -> None:
        with get_session() as session:
            record = session.get(JobRecord, job_id)
            if record is None:
                return
            record.status = status.value
            record.error = error

    def mark_saved(self, job_id: str) -> None:
        with get_session() as session:
            record = session.get(JobRecord, job_id)
            if record is not None:
                record.saved = True
