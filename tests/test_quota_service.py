"""Milestone 11 tests for QuotaService/QuotaStatus.

Two layers: QuotaStatus's own is_exceeded/remaining_* logic is pure
dataclass math and tested with no database at all. QuotaService.get_status
always queries the database (see its module docstring), so those tests
auto-skip if Postgres isn't reachable — same pattern as test_auth_api.py.

Anonymous (IP-based) quota tracking specifically is tested here rather than
through the full HTTP API: every TestClient request in a given process
shares the same simulated client IP, so hitting the real endpoint
repeatedly across test runs would make the free plan's daily limit flaky
for that one shared identity over time. Using a fresh, unique synthetic
"IP" string per test (cleaned up afterward) sidesteps that while still
exercising the real QuotaService/database code path — just at the service
layer instead of through the router.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from backend.app.db import JobRecord, engine, get_session
from backend.app.services.quota_service import QuotaService, QuotaStatus, quota_exceeded_message


def _db_reachable() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False


# --- QuotaStatus: pure logic, no database needed ----------------------------


def test_status_not_exceeded_when_under_both_limits():
    status = QuotaStatus(
        plan="free", limit_per_day=5, limit_per_month=None, used_today=3, used_this_month=3, max_num_timesteps=30
    )
    assert status.is_exceeded is False
    assert status.remaining_today == 2


def test_status_exceeded_when_daily_limit_reached():
    status = QuotaStatus(
        plan="free", limit_per_day=5, limit_per_month=None, used_today=5, used_this_month=5, max_num_timesteps=30
    )
    assert status.is_exceeded is True
    assert status.remaining_today == 0


def test_status_exceeded_when_monthly_limit_reached():
    status = QuotaStatus(
        plan="premium",
        limit_per_day=None,
        limit_per_month=100,
        used_today=10,
        used_this_month=100,
        max_num_timesteps=50,
    )
    assert status.is_exceeded is True
    assert status.remaining_this_month == 0


def test_status_unlimited_when_no_caps_set():
    status = QuotaStatus(
        plan="unlimited-test-plan",
        limit_per_day=None,
        limit_per_month=None,
        used_today=999,
        used_this_month=999,
        max_num_timesteps=None,
    )
    assert status.is_exceeded is False
    assert status.remaining_today is None
    assert status.remaining_this_month is None


def test_quota_exceeded_message_mentions_daily_when_daily_hit():
    status = QuotaStatus(
        plan="free", limit_per_day=5, limit_per_month=None, used_today=5, used_this_month=5, max_num_timesteps=30
    )
    assert "today" in quota_exceeded_message(status)


def test_quota_exceeded_message_mentions_monthly_when_only_monthly_hit():
    status = QuotaStatus(
        plan="premium",
        limit_per_day=None,
        limit_per_month=100,
        used_today=1,
        used_this_month=100,
        max_num_timesteps=50,
    )
    assert "month" in quota_exceeded_message(status)


# --- QuotaService: real database, synthetic per-test identities ------------

pytestmark_db = pytest.mark.skipif(
    not _db_reachable(),
    reason="Postgres isn't reachable at settings.database_url — see docs/DEVELOPMENT.md",
)


@pytest.fixture
def synthetic_ip():
    """A fake-but-unique 'IP' so this test's job rows can never collide with
    another test's, a real visitor's, or a previous run's — and are cleaned
    up unconditionally afterward, regardless of what the test asserts."""
    fake_ip = f"test-{uuid.uuid4().hex}"
    yield fake_ip
    with get_session() as session:
        session.query(JobRecord).filter(JobRecord.client_ip == fake_ip).delete()


def _make_job_row(client_ip: str, created_at: datetime) -> JobRecord:
    return JobRecord(
        id=uuid.uuid4().hex,
        user_id=None,
        client_ip=client_ip,
        status="completed",
        category="tops",
        num_timesteps=30,
        guidance_scale=1.5,
        seed=42,
        created_at=created_at,
    )


@pytestmark_db
def test_anonymous_usage_counted_by_client_ip(synthetic_ip):
    now = datetime.now(timezone.utc)
    with get_session() as session:
        for _ in range(3):
            session.add(_make_job_row(synthetic_ip, now))

    status = QuotaService().get_status(user_id=None, client_ip=synthetic_ip, plan_name="free")
    assert status.used_today == 3
    assert status.plan == "free"
    assert status.limit_per_day == 5  # the seeded default — see the Milestone 11 migration
    assert status.remaining_today == 2
    assert status.is_exceeded is False


@pytestmark_db
def test_anonymous_usage_exceeding_free_daily_limit(synthetic_ip):
    now = datetime.now(timezone.utc)
    with get_session() as session:
        for _ in range(5):  # the seeded free-plan daily limit
            session.add(_make_job_row(synthetic_ip, now))

    status = QuotaService().get_status(user_id=None, client_ip=synthetic_ip, plan_name="free")
    assert status.is_exceeded is True


@pytestmark_db
def test_jobs_older_than_today_dont_count_toward_daily_usage(synthetic_ip):
    last_month = datetime.now(timezone.utc).replace(day=1) - timedelta(days=5)
    with get_session() as session:
        for _ in range(5):
            session.add(_make_job_row(synthetic_ip, last_month))

    status = QuotaService().get_status(user_id=None, client_ip=synthetic_ip, plan_name="free")
    assert status.used_today == 0


@pytestmark_db
def test_unknown_plan_name_falls_back_to_free_defaults(synthetic_ip):
    status = QuotaService().get_status(user_id=None, client_ip=synthetic_ip, plan_name="this-plan-does-not-exist")
    assert status.limit_per_day == 5  # falls back to the 'free' row, not an unlimited default
