#!/usr/bin/env python3
"""Periodic maintenance sweep: three independent jobs, run together for a
single scheduler integration point (see below).

1. Deletes result images (and any leftover temp files) for completed,
unsaved jobs older than the configured TTL (settings.unsaved_result_ttl_hours)
— the enforcement side of the privacy requirement that results are temporary
unless the user explicitly saves them (see db/models.py's JobRecord.saved).

2. Recovers jobs stuck in status=processing (e.g. the server process was
killed mid-generation) — see services/job_recovery.py for the full design.
Also run once at app startup (app/main.py's lifespan()) for faster recovery
after a real restart; running it here too covers the case where the
process keeps running but a single job's generation genuinely hung (see
providers/selfhosted.py's own timeout, which is the faster, in-process
path for that specific case — this sweep is the slower backstop).

3. Deletes password reset tokens that are expired or already used — see
services/password_reset_service.py's cleanup_expired_password_reset_tokens.
Purely tidying (consume_reset_token already refuses either kind on its
own), so this can safely run on the same loose schedule as the other two.

Idempotent by design, safe to run on any schedule, including overlapping or
repeated runs:
  - It never modifies the job's database row — only deletes files — so
    re-running finds the exact same set of expired jobs and re-attempts the
    exact same (now-already-gone) file deletions. StorageService.delete_result
    and .cleanup_temp are both no-ops for a path that doesn't exist (see
    services/storage.py), not errors.
  - A failure deleting one job's files is logged and does not stop the sweep
    from processing the remaining jobs (see cleanup_expired_results()'s
    per-job try/except) — one bad file/permission error can't silently
    starve every other expired job of cleanup.
  - Never touches unrelated files: only ever addresses paths derived from a
    specific job_id this query returned (see StorageService's own docstring
    on why job_id-derived paths are safe, never user input).

The TTL cutoff comparison (JobRecord.updated_at < cutoff) is correct
independent of the database server/session's configured timezone: as of
Alembic revision 34aed8f22dbb, created_at/updated_at are proper
`timestamptz` columns (unambiguous instants), not `timestamp without time
zone` (which silently reinterpreted an aware UTC datetime using whatever
session timezone happened to be configured -- see that migration's own
docstring for the full investigation). See
tests/test_timestamp_timezone_handling.py for regression coverage proving
this cutoff is correct even under a session timezone different from the
one that wrote the rows.

PRODUCTION SCHEDULER INTEGRATION POINT (read before deploying):
This repository has no deployment/scheduler infrastructure yet (no
Dockerfile, no CI/CD, no process manager config, no cron/systemd/Task
Scheduler wiring) -- confirmed by inspection, not assumed. This script is
NOT currently scheduled anywhere; it must be invoked externally. Exit code
is 0 if every expired job was cleaned up without error, 1 if any individual
job's cleanup failed (so a scheduler can alert on a non-zero exit) -- the
exit code is never affected by the *count* of jobs cleaned, only by errors.

Run it periodically (hourly is reasonable for a 24h default TTL) via
whatever scheduler your deployment target actually provides, e.g.:
  - Linux: a cron entry —
      0 * * * *  cd /path/to/repo && /path/to/venv/bin/python backend/scripts/cleanup_expired_results.py >> /var/log/aitryon-cleanup.log 2>&1
  - Windows: a Task Scheduler task running
      <venv>\\Scripts\\python.exe backend\\scripts\\cleanup_expired_results.py
    on an hourly trigger, working directory set to the repo root.
  - A containerized deployment: a separate scheduled/cron container or your
    orchestrator's native CronJob (e.g. Kubernetes CronJob) running this
    same command against the same database and storage volume as the API.
  - A hosted cron add-on from whatever platform hosts the API (e.g. a
    scheduled task/job feature), pointed at this command.
None of the above is wired up by this change — this is the documentation of
where to wire it in, not a claim that it's already running on a schedule.

Manual/local run:
    ai\\.venv\\Scripts\\python.exe backend\\scripts\\cleanup_expired_results.py
"""

import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.app.config import settings  # noqa: E402
from backend.app.db import JobRecord, get_session  # noqa: E402
from backend.app.services.job_recovery import recover_stale_processing_jobs  # noqa: E402
from backend.app.services.password_reset_service import cleanup_expired_password_reset_tokens  # noqa: E402
from backend.app.services.storage import LocalStorageService, StorageService  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


@dataclass
class CleanupResult:
    deleted: int = 0
    failed: int = 0
    failed_job_ids: List[str] = field(default_factory=list)


def cleanup_expired_results(session, storage: StorageService, ttl_hours: int) -> CleanupResult:
    """Core sweep, factored out of main() so it's directly unit-testable
    against a real (test) database session and storage instance, without
    needing to invoke the script as a subprocess. See module docstring for
    the idempotency/failure-handling guarantees.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=ttl_hours)
    result = CleanupResult()

    expired = (
        session.query(JobRecord)
        .filter(JobRecord.status == "completed", JobRecord.saved.is_(False), JobRecord.updated_at < cutoff)
        .all()
    )
    logger.info("Found %d expired, unsaved result(s) older than %dh.", len(expired), ttl_hours)

    for job in expired:
        try:
            storage.delete_result(job.id)
            # Defensive, not the primary path: temp uploads are already
            # deleted right after generation (TryOnService.run_job's
            # finally block) for every normal completion. This only
            # matters if the server process was killed mid-job in a way
            # that skipped that cleanup -- see docs/DEVELOPMENT.md's known
            # limitation on that gap. A no-op the rest of the time.
            storage.cleanup_temp(job.id)
        except Exception:
            result.failed += 1
            result.failed_job_ids.append(job.id)
            logger.exception("Failed to clean up job %s -- continuing with remaining jobs.", job.id)
        else:
            result.deleted += 1
            logger.info("Cleaned up expired result for job %s (last updated %s).", job.id, job.updated_at)

    logger.info("Cleanup sweep complete: %d cleaned, %d failed.", result.deleted, result.failed)
    return result


def main() -> int:
    storage = LocalStorageService(settings.storage_dir)
    with get_session() as session:
        result = cleanup_expired_results(session, storage, settings.unsaved_result_ttl_hours)
    with get_session() as session:
        recovery_result = recover_stale_processing_jobs(session, storage, settings.stale_job_threshold_minutes)
    if recovery_result.recovered:
        logger.warning("Recovered %d stale processing job(s).", recovery_result.recovered)
    with get_session() as session:
        cleanup_expired_password_reset_tokens(session)
    return 1 if result.failed else 0


if __name__ == "__main__":
    sys.exit(main())
