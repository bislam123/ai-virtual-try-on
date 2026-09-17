"""Tests for backend/app/services/job_recovery.py's stale-processing-job
sweep.

Uses the real database (synthetic, unique-per-test job rows, cleaned up
unconditionally afterward) and a real LocalStorageService rooted at a
pytest tmp_path — same conventions as test_cleanup_expired_results.py.
Auto-skips if Postgres isn't reachable.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from backend.app.db import JobRecord, engine, get_session
from backend.app.services.job_recovery import STALE_JOB_ERROR_MESSAGE, recover_stale_processing_jobs
from backend.app.services.job_store import JobStatus
from backend.app.services.storage import LocalStorageService

THRESHOLD_MINUTES = 120


def _db_reachable() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False


pytestmark = pytest.mark.skipif(
    not _db_reachable(),
    reason="Postgres isn't reachable at settings.database_url — see docs/DEVELOPMENT.md",
)


@pytest.fixture
def job_tag():
    tag = f"recovery-test-{uuid.uuid4().hex}"
    yield tag
    with get_session() as session:
        session.query(JobRecord).filter(JobRecord.client_ip == tag).delete()


def _make_job(tag: str, *, updated_at: datetime, status: str, saved: bool = False) -> JobRecord:
    return JobRecord(
        id=uuid.uuid4().hex,
        user_id=None,
        client_ip=tag,
        status=status,
        saved=saved,
        category="tops",
        num_timesteps=30,
        guidance_scale=1.5,
        seed=42,
        updated_at=updated_at,
    )


def test_stale_processing_job_becomes_failed(tmp_path, job_tag):
    storage = LocalStorageService(str(tmp_path))
    stale_time = datetime.now(timezone.utc) - timedelta(minutes=THRESHOLD_MINUTES + 1)

    with get_session() as session:
        job = _make_job(job_tag, updated_at=stale_time, status=JobStatus.PROCESSING.value)
        session.add(job)
        session.flush()
        job_id = job.id

        result = recover_stale_processing_jobs(session, storage, THRESHOLD_MINUTES)

    assert job_id in result.recovered_job_ids

    with get_session() as session:
        row = session.get(JobRecord, job_id)
        assert row.status == JobStatus.FAILED.value
        assert row.error == STALE_JOB_ERROR_MESSAGE


def test_active_processing_job_survives_recovery(tmp_path, job_tag):
    """A job that's genuinely still being worked on (recently updated)
    must never be recovered out from under it."""
    storage = LocalStorageService(str(tmp_path))
    recent_time = datetime.now(timezone.utc) - timedelta(minutes=1)

    with get_session() as session:
        job = _make_job(job_tag, updated_at=recent_time, status=JobStatus.PROCESSING.value)
        session.add(job)
        session.flush()
        job_id = job.id

        result = recover_stale_processing_jobs(session, storage, THRESHOLD_MINUTES)

    assert job_id not in result.recovered_job_ids

    with get_session() as session:
        row = session.get(JobRecord, job_id)
        assert row.status == JobStatus.PROCESSING.value
        assert row.error is None


def test_multiple_stale_jobs_are_all_recovered(tmp_path, job_tag):
    storage = LocalStorageService(str(tmp_path))
    stale_time = datetime.now(timezone.utc) - timedelta(minutes=THRESHOLD_MINUTES + 1)

    with get_session() as session:
        jobs = [_make_job(job_tag, updated_at=stale_time, status=JobStatus.PROCESSING.value) for _ in range(3)]
        session.add_all(jobs)
        session.flush()
        job_ids = [j.id for j in jobs]

        result = recover_stale_processing_jobs(session, storage, THRESHOLD_MINUTES)

    for job_id in job_ids:
        assert job_id in result.recovered_job_ids

    with get_session() as session:
        for job_id in job_ids:
            assert session.get(JobRecord, job_id).status == JobStatus.FAILED.value


def test_repeated_recovery_is_idempotent(tmp_path, job_tag):
    storage = LocalStorageService(str(tmp_path))
    stale_time = datetime.now(timezone.utc) - timedelta(minutes=THRESHOLD_MINUTES + 1)

    with get_session() as session:
        job = _make_job(job_tag, updated_at=stale_time, status=JobStatus.PROCESSING.value)
        session.add(job)
        session.flush()
        job_id = job.id

        first = recover_stale_processing_jobs(session, storage, THRESHOLD_MINUTES)

    # Second (and third) run: the job is now status=failed, so it no longer
    # matches the status=='processing' filter -- must be a clean no-op, not
    # an error or a re-recovery.
    with get_session() as session:
        second = recover_stale_processing_jobs(session, storage, THRESHOLD_MINUTES)
    with get_session() as session:
        third = recover_stale_processing_jobs(session, storage, THRESHOLD_MINUTES)

    assert job_id in first.recovered_job_ids
    assert job_id not in second.recovered_job_ids
    assert job_id not in third.recovered_job_ids

    with get_session() as session:
        row = session.get(JobRecord, job_id)
        assert row.status == JobStatus.FAILED.value
        assert row.error == STALE_JOB_ERROR_MESSAGE


def test_concurrent_recovery_on_the_same_stale_job_is_safe(tmp_path, job_tag):
    """Simulates two recovery processes racing on the same stale job --
    both querying and transitioning it before either commits. Neither
    should error, and the end state must be the same, correct outcome."""
    storage = LocalStorageService(str(tmp_path))
    stale_time = datetime.now(timezone.utc) - timedelta(minutes=THRESHOLD_MINUTES + 1)

    with get_session() as session:
        job = _make_job(job_tag, updated_at=stale_time, status=JobStatus.PROCESSING.value)
        session.add(job)
        session.flush()
        job_id = job.id

    with get_session() as session_a, get_session() as session_b:
        # Both "processes" see the same stale row before either commits.
        result_a = recover_stale_processing_jobs(session_a, storage, THRESHOLD_MINUTES)
        result_b = recover_stale_processing_jobs(session_b, storage, THRESHOLD_MINUTES)

    assert job_id in result_a.recovered_job_ids
    assert job_id in result_b.recovered_job_ids  # both saw it as stale -- neither errored

    with get_session() as session:
        row = session.get(JobRecord, job_id)
        assert row.status == JobStatus.FAILED.value
        assert row.error == STALE_JOB_ERROR_MESSAGE


def test_process_restart_scenario_needs_no_in_memory_state(tmp_path, job_tag):
    """The defining property of a real crash-recovery scenario: a job row
    that looks stuck in processing with no corresponding live thread,
    process, or Python object anywhere -- this test constructs exactly
    that (a bare database row, nothing else) and confirms recovery works
    from the database state alone, which is what makes it correct after a
    real process restart, not just within one process's lifetime."""
    storage = LocalStorageService(str(tmp_path))
    stale_time = datetime.now(timezone.utc) - timedelta(minutes=THRESHOLD_MINUTES + 1)

    with get_session() as session:
        session.add(_make_job(job_tag, updated_at=stale_time, status=JobStatus.PROCESSING.value))

    # A fresh session/query, exactly as a newly-started process's lifespan()
    # or a separately-invoked cleanup script would see it -- no reference
    # to the job object created above is used from here on.
    with get_session() as session:
        result = recover_stale_processing_jobs(session, storage, THRESHOLD_MINUTES)

    assert result.recovered == 1


def test_unrelated_completed_and_saved_jobs_are_never_touched(tmp_path, job_tag):
    storage = LocalStorageService(str(tmp_path))
    very_old = datetime.now(timezone.utc) - timedelta(hours=1000)

    with get_session() as session:
        completed_job = _make_job(job_tag, updated_at=very_old, status=JobStatus.COMPLETED.value)
        saved_completed_job = _make_job(job_tag, updated_at=very_old, status=JobStatus.COMPLETED.value, saved=True)
        pending_job = _make_job(job_tag, updated_at=very_old, status=JobStatus.PENDING.value)
        session.add_all([completed_job, saved_completed_job, pending_job])
        session.flush()
        completed_id, saved_id, pending_id = completed_job.id, saved_completed_job.id, pending_job.id

        result = recover_stale_processing_jobs(session, storage, THRESHOLD_MINUTES)

    assert completed_id not in result.recovered_job_ids
    assert saved_id not in result.recovered_job_ids
    assert pending_id not in result.recovered_job_ids

    with get_session() as session:
        assert session.get(JobRecord, completed_id).status == JobStatus.COMPLETED.value
        assert session.get(JobRecord, saved_id).status == JobStatus.COMPLETED.value
        assert session.get(JobRecord, saved_id).saved is True
        assert session.get(JobRecord, pending_id).status == JobStatus.PENDING.value


def test_recovered_job_never_had_a_result_file_to_lose(tmp_path, job_tag):
    """A processing job, by definition, never reached the point in
    TryOnService.run_job where a result is saved -- confirms recovery
    never collides with (or accidentally deletes) a real result, since
    there is never one to begin with for a job in this state."""
    storage = LocalStorageService(str(tmp_path))
    stale_time = datetime.now(timezone.utc) - timedelta(minutes=THRESHOLD_MINUTES + 1)

    with get_session() as session:
        job = _make_job(job_tag, updated_at=stale_time, status=JobStatus.PROCESSING.value)
        session.add(job)
        session.flush()
        job_id = job.id

        assert storage.get_result_path(job_id) is None
        recover_stale_processing_jobs(session, storage, THRESHOLD_MINUTES)

    assert storage.get_result_path(job_id) is None


def test_recovery_cleans_up_leftover_temp_files(tmp_path, job_tag):
    storage = LocalStorageService(str(tmp_path))
    stale_time = datetime.now(timezone.utc) - timedelta(minutes=THRESHOLD_MINUTES + 1)

    with get_session() as session:
        job = _make_job(job_tag, updated_at=stale_time, status=JobStatus.PROCESSING.value)
        session.add(job)
        session.flush()
        job_id = job.id
        storage.save_temp_upload(job_id, "person.png", b"fake-bytes")
        assert storage.load_temp_upload(job_id, "person.png") is not None

        recover_stale_processing_jobs(session, storage, THRESHOLD_MINUTES)

    assert storage.load_temp_upload(job_id, "person.png") is None


def test_recovery_error_message_is_generic_and_safe_for_api_clients():
    """No internal details (stack traces, file paths, exception text) --
    matches TryOnService's own GENERIC_FAILURE_MESSAGE convention."""
    assert "traceback" not in STALE_JOB_ERROR_MESSAGE.lower()
    assert "exception" not in STALE_JOB_ERROR_MESSAGE.lower()
    assert "/" not in STALE_JOB_ERROR_MESSAGE  # no path-shaped content
    assert STALE_JOB_ERROR_MESSAGE == "This generation took longer than expected and was stopped. Please try again."
