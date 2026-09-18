"""Schema-level regression tests -- things that are easy to get right once
and silently lose in a future migration (an index gets dropped, a rename
loses its `index=True`), which no application-behavior test would ever
catch since the app still works correctly, just slower.

Same real-database, auto-skip-if-unreachable convention as
test_admin_api.py/test_quota_service.py.

Run with: ai\\.venv\\Scripts\\python.exe -m pytest tests/test_db_schema.py
"""

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError

from backend.app.db import engine


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


def test_jobs_status_column_is_indexed():
    """jobs.status is filtered on the hot path of every try-on submission
    (CapacityService.get_status) and by both background sweeps
    (job_recovery.py, cleanup_expired_results.py) -- an uncovered filter
    here would silently degrade into a full table scan as the (never-
    row-deleted, see cleanup_expired_results.py's own docstring) jobs
    table grows. See migration 12cc6984ca8f."""
    inspector = inspect(engine)
    index_columns = [tuple(ix["column_names"]) for ix in inspector.get_indexes("jobs")]
    assert ("status",) in index_columns


def test_jobs_idempotency_partial_index_still_present():
    """Regression: adding the status index (migration 12cc6984ca8f) must
    not have touched the existing idempotency-guarantee index (see
    db/models.py's JobRecord.__table_args__) -- this is the database-level
    constraint idempotency correctness actually depends on, not just an
    app-level check."""
    inspector = inspect(engine)
    index_names = {ix["name"] for ix in inspector.get_indexes("jobs")}
    assert "ix_jobs_idempotency_active" in index_names
