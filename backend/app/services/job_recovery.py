"""Stale-processing-job recovery.

A job can be left permanently in status=processing if the server process
is killed mid-generation -- nothing else would ever transition it out of
that state, since the code that would (TryOnService.run_job's own
try/except/finally) never gets to run again for that job. This sweep is
the recovery: find jobs that have sat in processing far longer than any
legitimate generation should take, and transition them to failed with a
generic, user-safe error.

Reuses JobRecord.updated_at (already bumped to "now" the moment a job
enters processing -- see JobStore.update_status/DbJobStore, and the
column's own onupdate=_utcnow) as the staleness signal. No new database
column or migration needed: a job's own status transition already
records exactly the timestamp this needs.

Run from two places, both safe to run concurrently or repeatedly with
each other (see idempotency note on recover_stale_processing_jobs):
  - app/main.py's lifespan(), once at startup -- recovers quickly after a
    real process restart, without waiting for the next scheduled sweep.
  - scripts/cleanup_expired_results.py, on whatever schedule that script
    is invoked on (see its own docstring for the scheduler integration
    point -- not automatically scheduled today).
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List

from ..db import JobRecord
from .job_store import JobStatus
from .storage import StorageService

logger = logging.getLogger(__name__)

STALE_JOB_ERROR_MESSAGE = (
    "This generation took longer than expected and was stopped. Please try again."
)


@dataclass
class StaleJobRecoveryResult:
    recovered: int = 0
    recovered_job_ids: List[str] = field(default_factory=list)


def recover_stale_processing_jobs(session, storage: StorageService, threshold_minutes: int) -> StaleJobRecoveryResult:
    """Finds jobs stuck in status=processing whose updated_at is older
    than threshold_minutes and transitions them to failed.

    Idempotent, safe under concurrent/repeated execution:
      - Only ever touches rows currently matching status=='processing'.
        Once a stale job is transitioned to failed, it no longer matches
        that filter, so re-running (immediately after, or from a second
        process running at the same time) simply finds nothing left to
        do for it -- a clean no-op, not a double-failure or an error.
      - Two processes racing on the exact same stale job (both querying
        it before either commits) would each independently set the same
        status='failed' + the same static error message -- whichever
        commits last harmlessly overwrites with an identical value, not
        a corrupting one. Neither process needs to coordinate with the
        other.
      - Never touches a job's result file: only status=='processing' jobs
        are ever matched here, and TryOnService.run_job only calls
        storage.save_result() *after* a successful generation (which
        transitions straight to completed, never processing again) -- a
        recovered job never had a result to begin with, so there is
        nothing for the existing TTL-based cleanup
        (scripts/cleanup_expired_results.py, which only ever matches
        status=='completed') to ever collide with here.
      - Also cleans up any leftover temp uploads for each recovered job
        (same defensive reasoning as cleanup_expired_results.py's own
        temp-file sweep): by the time a job reaches provider.generate(),
        the temp files have already been read into memory
        (TryOnService.run_job loads them before calling the provider), so
        removing them is always safe even if the underlying generation
        thread is, per selfhosted.py's own documented limitation, still
        actually running in the background.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=threshold_minutes)
    result = StaleJobRecoveryResult()

    stale = (
        session.query(JobRecord)
        .filter(JobRecord.status == JobStatus.PROCESSING.value, JobRecord.updated_at < cutoff)
        .all()
    )
    logger.info("Found %d stale processing job(s) older than %d minute(s).", len(stale), threshold_minutes)

    for job in stale:
        # The status transition is the part that actually matters (it's
        # what unblocks an API client polling this job) -- set it first,
        # unconditionally, so a failure in the merely-defensive temp
        # cleanup below can never prevent it or block the remaining jobs
        # in this sweep, matching cleanup_expired_results.py's own
        # per-job failure isolation.
        job.status = JobStatus.FAILED.value
        job.error = STALE_JOB_ERROR_MESSAGE
        result.recovered += 1
        result.recovered_job_ids.append(job.id)
        logger.info("Recovered stale job %s (last updated %s).", job.id, job.updated_at)

        try:
            storage.cleanup_temp(job.id)
        except Exception:
            logger.exception("Failed to clean up temp files for recovered job %s.", job.id)

    return result
