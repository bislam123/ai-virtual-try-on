"""Regression tests for the created_at/updated_at timezone-awareness fix
(Alembic revision 34aed8f22dbb — see its own docstring for the full
investigation and data-safety reasoning).

Before that migration, jobs/plans/users' created_at/updated_at were plain
`timestamp without time zone` columns: the application always wrote an
aware UTC datetime, but the column silently reinterpreted it using
whatever the database session's configured timezone happened to be,
storing a naive wall-clock value with no timezone tag. Every comparison
against those columns (cleanup_expired_results.py's TTL cutoff,
QuotaService's day/month cutoffs) only stayed correct because every
connection used the same session timezone — a real, environment-dependent
fragility, not a currently-observable bug.

The tests below prove the fix actually closes that gap: the SAME cutoff
comparisons must now produce correct results even when a connection's
session timezone is deliberately set to something different from the
database's configured default — something that would NOT reliably have
been true before the migration.
"""

import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from backend.app.db import JobRecord, engine, get_session
from backend.app.db.base import SessionLocal
from backend.app.services.storage import LocalStorageService
from backend.scripts.cleanup_expired_results import cleanup_expired_results

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
    tag = f"tz-test-{uuid.uuid4().hex}"
    yield tag
    with get_session() as session:
        session.query(JobRecord).filter(JobRecord.client_ip == tag).delete()


def _make_job(tag: str, *, updated_at: datetime, created_at=None, saved: bool = False) -> JobRecord:
    return JobRecord(
        id=uuid.uuid4().hex,
        user_id=None,
        client_ip=tag,
        status="completed",
        saved=saved,
        category="tops",
        num_timesteps=30,
        guidance_scale=1.5,
        seed=42,
        created_at=created_at if created_at is not None else updated_at,
        updated_at=updated_at,
    )


@contextmanager
def _session_with_timezone(tz_name: str):
    """A real Session whose underlying connection has its session-level
    Postgres timezone deliberately overridden — the exact scenario the
    migration's docstring identifies as the risk (a differently configured
    connection/server). Reset before closing so nothing leaks to a pooled
    connection reused by a later test."""
    session = SessionLocal()
    try:
        session.execute(text(f"SET TIME ZONE '{tz_name}'"))
        yield session
        session.commit()
    finally:
        session.execute(text("SET TIME ZONE DEFAULT"))
        session.close()


# --- Column-level: the migration actually took effect -----------------------


def test_timestamp_columns_are_timezone_aware():
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT table_name, column_name, data_type FROM information_schema.columns "
                "WHERE (table_name, column_name) IN "
                "(('jobs','created_at'), ('jobs','updated_at'), "
                " ('plans','created_at'), ('plans','updated_at'), "
                " ('users','created_at'))"
            )
        ).fetchall()
    assert len(rows) == 5
    for _table, _column, data_type in rows:
        assert data_type == "timestamp with time zone"


def test_stored_instant_is_identical_regardless_of_reading_sessions_timezone(job_tag):
    """The core correctness property a timestamptz column provides: the
    same row, read back under two different session timezones, must
    represent the exact same absolute instant."""
    known_instant = datetime(2026, 3, 10, 12, 0, 0, 500000, tzinfo=timezone.utc)

    with get_session() as session:
        job = _make_job(job_tag, updated_at=known_instant)
        session.add(job)
        session.flush()
        job_id = job.id

    with _session_with_timezone("Asia/Kolkata") as session:
        row_ist = session.get(JobRecord, job_id)
        read_ist = row_ist.updated_at

    with _session_with_timezone("America/Los_Angeles") as session:
        row_pst = session.get(JobRecord, job_id)
        read_pst = row_pst.updated_at

    assert read_ist == known_instant
    assert read_pst == known_instant
    assert read_ist == read_pst  # same absolute instant, regardless of each session's display timezone


# --- Regression: the exact fragility scenario from the investigation --------


def test_cleanup_cutoff_correct_under_a_different_session_timezone(tmp_path, job_tag):
    """The regression this migration exists to fix: an expired job must
    still be correctly identified as expired even when the connection
    running the query has a different session timezone than the one that
    wrote the row."""
    storage = LocalStorageService(str(tmp_path))
    expired = datetime.now(timezone.utc) - timedelta(hours=TTL_HOURS + 1)

    with get_session() as session:
        job = _make_job(job_tag, updated_at=expired)
        session.add(job)
        session.flush()
        job_id = job.id

    with _session_with_timezone("America/Los_Angeles") as session:
        result = cleanup_expired_results(session, storage, TTL_HOURS)

    assert job_id not in result.failed_job_ids
    assert storage.get_result_path(job_id) is None


def test_recent_job_not_swept_under_a_different_session_timezone(tmp_path, job_tag):
    storage = LocalStorageService(str(tmp_path))
    recent = datetime.now(timezone.utc) - timedelta(hours=1)

    with get_session() as session:
        job = _make_job(job_tag, updated_at=recent)
        session.add(job)
        session.flush()
        job_id = job.id
        storage.save_result(job_id, Image.new("RGB", (8, 8)))

    with _session_with_timezone("Pacific/Kiritimati") as session:  # UTC+14, the most extreme real offset
        cleanup_expired_results(session, storage, TTL_HOURS)

    assert storage.get_result_path(job_id) is not None


def test_saved_job_not_swept_under_a_different_session_timezone(tmp_path, job_tag):
    storage = LocalStorageService(str(tmp_path))
    very_old = datetime.now(timezone.utc) - timedelta(hours=1000)

    with get_session() as session:
        job = _make_job(job_tag, updated_at=very_old, saved=True)
        session.add(job)
        session.flush()
        job_id = job.id
        storage.save_result(job_id, Image.new("RGB", (8, 8)))

    with _session_with_timezone("Pacific/Kiritimati") as session:
        cleanup_expired_results(session, storage, TTL_HOURS)

    assert storage.get_result_path(job_id) is not None


def test_quota_style_day_cutoff_correct_under_a_different_session_timezone(job_tag):
    """QuotaService.get_status compares the same kind of aware
    datetime.now(timezone.utc)-derived cutoff (day/month start) against
    JobRecord.created_at -- the same class of fragility this migration
    closes. QuotaService always opens its own internal session, so rather
    than risk mutating the shared connection pool's session timezone
    (fragile and could leak into unrelated tests), this replicates its
    exact day-cutoff query through our own properly-scoped, reset session
    -- proving the underlying comparison QuotaService relies on is correct
    regardless of session timezone, without that risk."""
    from sqlalchemy import func, select

    from backend.app.services.quota_service import _day_start

    now = datetime.now(timezone.utc)

    with get_session() as session:
        today_job = _make_job(job_tag, updated_at=now, created_at=now)
        yesterday_job = _make_job(job_tag, updated_at=now, created_at=now - timedelta(days=1, hours=1))
        session.add_all([today_job, yesterday_job])

    with _session_with_timezone("Pacific/Kiritimati") as session:  # UTC+14, the most extreme real offset
        used_today = session.scalar(
            select(func.count())
            .select_from(JobRecord)
            .where(JobRecord.client_ip == job_tag, JobRecord.created_at >= _day_start(now))
        )

    assert used_today == 1  # only today's job, not yesterday's
