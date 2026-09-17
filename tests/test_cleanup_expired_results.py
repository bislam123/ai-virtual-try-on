"""Tests for backend/scripts/cleanup_expired_results.py's core sweep.

Uses the real database (synthetic, unique-per-test job rows, cleaned up
unconditionally afterward) and a real LocalStorageService rooted at a
pytest tmp_path — same conventions as test_quota_service.py /
test_auth_api.py. Auto-skips if Postgres isn't reachable.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from backend.app.db import JobRecord, engine, get_session
from backend.app.services.storage import LocalStorageService
from backend.scripts.cleanup_expired_results import cleanup_expired_results
from PIL import Image

TTL_HOURS = 24


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
    """A unique marker (stashed in client_ip, unused otherwise by this
    script) so this test's rows can never collide with another test's or a
    previous run's, and are cleaned up unconditionally afterward."""
    tag = f"cleanup-test-{uuid.uuid4().hex}"
    yield tag
    with get_session() as session:
        session.query(JobRecord).filter(JobRecord.client_ip == tag).delete()


def _make_job(tag: str, *, updated_at: datetime, saved: bool = False, status: str = "completed") -> JobRecord:
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


def _result_image() -> Image.Image:
    return Image.new("RGB", (16, 16), color="blue")


class _FailingStorage(LocalStorageService):
    """Wraps a real LocalStorageService but raises for one specific job_id's
    delete_result call, to test that one job's failure doesn't abort the
    sweep for the rest."""

    def __init__(self, storage_dir: str, fail_for_job_id: str):
        super().__init__(storage_dir)
        self._fail_for_job_id = fail_for_job_id

    def delete_result(self, job_id: str) -> None:
        if job_id == self._fail_for_job_id:
            raise OSError("simulated failure deleting this specific job's result")
        super().delete_result(job_id)


def test_expired_unsaved_result_is_deleted(tmp_path, job_tag):
    # Note throughout this file: cleanup_expired_results() sweeps the WHOLE
    # jobs table by design (that's the point of the script), and this runs
    # against the real shared local dev database, which can genuinely have
    # other expired rows in it already. So assertions check THIS test's own
    # job_id's outcome (file state, membership in failed_job_ids), never an
    # exact total result.deleted/result.failed count.
    storage = LocalStorageService(str(tmp_path))
    expired_time = datetime.now(timezone.utc) - timedelta(hours=TTL_HOURS + 1)

    with get_session() as session:
        job = _make_job(job_tag, updated_at=expired_time)
        session.add(job)
        session.flush()
        job_id = job.id
        storage.save_result(job_id, _result_image())
        assert storage.get_result_path(job_id) is not None

        result = cleanup_expired_results(session, storage, TTL_HOURS)

    assert job_id not in result.failed_job_ids
    assert storage.get_result_path(job_id) is None


def test_non_expired_result_is_preserved(tmp_path, job_tag):
    storage = LocalStorageService(str(tmp_path))
    recent_time = datetime.now(timezone.utc) - timedelta(hours=1)

    with get_session() as session:
        job = _make_job(job_tag, updated_at=recent_time)
        session.add(job)
        session.flush()
        job_id = job.id
        storage.save_result(job_id, _result_image())

        cleanup_expired_results(session, storage, TTL_HOURS)

    assert storage.get_result_path(job_id) is not None


def test_saved_result_preserved_even_if_expired(tmp_path, job_tag):
    """The core privacy-exception invariant: an explicitly saved result is
    never swept, no matter how old."""
    storage = LocalStorageService(str(tmp_path))
    very_old = datetime.now(timezone.utc) - timedelta(hours=1000)

    with get_session() as session:
        job = _make_job(job_tag, updated_at=very_old, saved=True)
        session.add(job)
        session.flush()
        job_id = job.id
        storage.save_result(job_id, _result_image())

        cleanup_expired_results(session, storage, TTL_HOURS)

    assert storage.get_result_path(job_id) is not None


def test_missing_file_handled_gracefully(tmp_path, job_tag):
    """An expired job whose result file is already gone (a prior sweep, or
    manually cleared) must not raise and must not be reported as a failure."""
    storage = LocalStorageService(str(tmp_path))
    expired_time = datetime.now(timezone.utc) - timedelta(hours=TTL_HOURS + 1)

    with get_session() as session:
        job = _make_job(job_tag, updated_at=expired_time)
        session.add(job)
        session.flush()
        job_id = job.id
        # Deliberately never call storage.save_result() for this job.

        result = cleanup_expired_results(session, storage, TTL_HOURS)

    assert job_id not in result.failed_job_ids


def test_unrelated_files_are_preserved(tmp_path, job_tag):
    """A result file belonging to a job outside this sweep's query (a
    different, unrelated job id) must never be touched."""
    storage = LocalStorageService(str(tmp_path))
    expired_time = datetime.now(timezone.utc) - timedelta(hours=TTL_HOURS + 1)
    unrelated_job_id = f"unrelated-{uuid.uuid4().hex}"
    storage.save_result(unrelated_job_id, _result_image())

    with get_session() as session:
        job = _make_job(job_tag, updated_at=expired_time)
        session.add(job)
        session.flush()

        cleanup_expired_results(session, storage, TTL_HOURS)

    assert storage.get_result_path(unrelated_job_id) is not None


def test_repeated_cleanup_is_idempotent(tmp_path, job_tag):
    storage = LocalStorageService(str(tmp_path))
    expired_time = datetime.now(timezone.utc) - timedelta(hours=TTL_HOURS + 1)

    with get_session() as session:
        job = _make_job(job_tag, updated_at=expired_time)
        session.add(job)
        session.flush()
        job_id = job.id
        storage.save_result(job_id, _result_image())

        first = cleanup_expired_results(session, storage, TTL_HOURS)
        second = cleanup_expired_results(session, storage, TTL_HOURS)  # same job still matches -- re-processed safely
        third = cleanup_expired_results(session, storage, TTL_HOURS)

    assert job_id not in first.failed_job_ids
    assert job_id not in second.failed_job_ids
    assert job_id not in third.failed_job_ids
    assert storage.get_result_path(job_id) is None


def test_database_job_row_remains_consistent_after_cleanup(tmp_path, job_tag):
    """Cleanup only ever touches files -- the job's own database row must
    be completely unchanged afterward (same status, same saved flag, same
    updated_at), not deleted, corrupted, or silently mutated. Compares
    against a DB-round-tripped read taken *before* cleanup runs (rather
    than the original Python-side aware datetime) so this isn't sensitive
    to how the DB driver represents timezone-naive timestamp columns --
    only whether cleanup itself changes anything."""
    storage = LocalStorageService(str(tmp_path))
    expired_time = datetime.now(timezone.utc) - timedelta(hours=TTL_HOURS + 1)

    with get_session() as session:
        job = _make_job(job_tag, updated_at=expired_time)
        session.add(job)
        session.flush()
        job_id = job.id

    with get_session() as session:
        before = session.get(JobRecord, job_id)
        before_updated_at, before_status, before_saved = before.updated_at, before.status, before.saved

    with get_session() as session:
        cleanup_expired_results(session, storage, TTL_HOURS)

    with get_session() as session:
        after = session.get(JobRecord, job_id)
        assert after is not None
        assert after.status == before_status == "completed"
        assert after.saved == before_saved is False
        assert after.updated_at == before_updated_at


def test_partial_failure_does_not_abort_remaining_jobs(tmp_path, job_tag):
    expired_time = datetime.now(timezone.utc) - timedelta(hours=TTL_HOURS + 1)

    with get_session() as session:
        failing_job = _make_job(job_tag, updated_at=expired_time)
        ok_job = _make_job(job_tag, updated_at=expired_time)
        session.add_all([failing_job, ok_job])
        session.flush()
        failing_id, ok_id = failing_job.id, ok_job.id

        storage = _FailingStorage(str(tmp_path), fail_for_job_id=failing_id)
        storage.save_result(failing_id, _result_image())
        storage.save_result(ok_id, _result_image())

        result = cleanup_expired_results(session, storage, TTL_HOURS)

    assert failing_id in result.failed_job_ids
    assert ok_id not in result.failed_job_ids
    assert storage.get_result_path(ok_id) is None  # the other job was still cleaned up
    assert storage.get_result_path(failing_id) is not None  # the failing one's file was left alone, not corrupted


def test_leftover_temp_files_are_also_cleaned_up(tmp_path, job_tag):
    """Requirement: cleanup removes 'associated temporary files' too -- a
    defensive sweep for the case where a hard-killed process skipped
    TryOnService.run_job's own temp cleanup (see module docstring)."""
    storage = LocalStorageService(str(tmp_path))
    expired_time = datetime.now(timezone.utc) - timedelta(hours=TTL_HOURS + 1)

    with get_session() as session:
        job = _make_job(job_tag, updated_at=expired_time)
        session.add(job)
        session.flush()
        job_id = job.id
        storage.save_result(job_id, _result_image())
        storage.save_temp_upload(job_id, "person.png", b"fake-bytes")
        assert storage.load_temp_upload(job_id, "person.png") is not None

        cleanup_expired_results(session, storage, TTL_HOURS)

    assert storage.load_temp_upload(job_id, "person.png") is None
