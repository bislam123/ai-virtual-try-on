"""DbJobStore: the JobStore interface (see job_store.py) backed by Postgres."""

import uuid
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError

from ..db import JobRecord, get_session
from ..providers.base import GarmentCategory, GarmentPhotoType
from .job_store import Job, JobStatus, JobStore


def _to_job(record: JobRecord) -> Job:
    return Job(
        id=record.id,
        user_id=record.user_id,
        category=record.category,  # type: ignore[arg-type]
        num_timesteps=record.num_timesteps,
        guidance_scale=record.guidance_scale,
        seed=record.seed,
        client_ip=record.client_ip,
        garment_photo_type=record.garment_photo_type,  # type: ignore[arg-type]
        status=JobStatus(record.status),
        error=record.error,
        saved=record.saved,
        idempotency_scope=record.idempotency_scope,
        idempotency_key=record.idempotency_key,
        idempotency_fingerprint=record.idempotency_fingerprint,
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
        client_ip: Optional[str] = None,
        garment_photo_type: GarmentPhotoType = "flat-lay",
    ) -> Job:
        record = JobRecord(
            id=uuid.uuid4().hex,
            user_id=user_id,
            category=category,
            num_timesteps=num_timesteps,
            guidance_scale=guidance_scale,
            seed=seed,
            client_ip=client_ip,
            garment_photo_type=garment_photo_type,
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

    def try_transition_status(
        self, job_id: str, *, expected: JobStatus, new: JobStatus, error: Optional[str] = None
    ) -> bool:
        # A single UPDATE ... WHERE id = :id AND status = :expected: the
        # database's own row-level locking is what makes this race-safe
        # under real concurrency (two callers racing the same job_id),
        # not an application-level check-then-act -- same idiom as
        # password_reset_service.py's consume_reset_token. Under Postgres
        # READ COMMITTED, a second concurrent UPDATE for the same row
        # blocks behind the first's row lock, then re-evaluates the WHERE
        # clause against the now-committed row once the first commits --
        # so at most one of two racing callers ever sees rowcount == 1.
        with get_session() as session:
            result = session.execute(
                sa_update(JobRecord)
                .where(JobRecord.id == job_id, JobRecord.status == expected.value)
                .values(status=new.value, error=error)
            )
            return result.rowcount == 1

    def mark_saved(self, job_id: str) -> None:
        with get_session() as session:
            record = session.get(JobRecord, job_id)
            if record is not None:
                record.saved = True

    def get_by_idempotency_key(self, idempotency_scope: str, idempotency_key: str) -> Optional[Job]:
        with get_session() as session:
            record = session.scalar(
                select(JobRecord).where(
                    JobRecord.idempotency_scope == idempotency_scope,
                    JobRecord.idempotency_key == idempotency_key,
                    JobRecord.status != JobStatus.FAILED.value,
                )
            )
            return _to_job(record) if record is not None else None

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
        record = JobRecord(
            id=uuid.uuid4().hex,
            user_id=user_id,
            category=category,
            num_timesteps=num_timesteps,
            guidance_scale=guidance_scale,
            seed=seed,
            client_ip=client_ip,
            garment_photo_type=garment_photo_type,
            status=JobStatus.PENDING.value,
            idempotency_scope=idempotency_scope,
            idempotency_key=idempotency_key,
            idempotency_fingerprint=idempotency_fingerprint,
        )
        try:
            with get_session() as session:
                session.add(record)
                session.flush()
                session.refresh(record)
                return _to_job(record), True
        except IntegrityError:
            # Lost the race: another request for the same (scope, key)
            # committed first -- jobs.ix_jobs_idempotency_active (a partial
            # unique index, see the migration) is what makes this a real
            # guarantee under concurrency, not just "unlikely to collide in
            # practice". The winner is already committed and visible by the
            # time Postgres reports our conflict, so this lookup can't
            # legitimately come back empty.
            existing = self.get_by_idempotency_key(idempotency_scope, idempotency_key)
            if existing is None:
                raise
            return existing, False
