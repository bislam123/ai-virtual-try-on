#!/usr/bin/env python3
"""Deletes result images for completed, unsaved jobs older than the
configured TTL (settings.unsaved_result_ttl_hours) — the enforcement side of
the privacy requirement that results are temporary unless the user
explicitly saves them (see db/models.py's JobRecord.saved).

Not wired to a scheduler yet (that's real infra — cron on Linux, Task
Scheduler on Windows, or a hosted cron in production — deliberately out of
scope for this milestone). Run manually for now:

    ai\\.venv\\Scripts\\python.exe backend\\scripts\\cleanup_expired_results.py
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.app.config import settings  # noqa: E402
from backend.app.db import JobRecord, get_session  # noqa: E402
from backend.app.services.storage import LocalStorageService  # noqa: E402


def main() -> None:
    storage = LocalStorageService(settings.storage_dir)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.unsaved_result_ttl_hours)

    deleted = 0
    with get_session() as session:
        expired = (
            session.query(JobRecord)
            .filter(JobRecord.status == "completed", JobRecord.saved.is_(False), JobRecord.updated_at < cutoff)
            .all()
        )
        for job in expired:
            storage.delete_result(job.id)
            deleted += 1

    print(f"Deleted {deleted} expired result image(s) older than {settings.unsaved_result_ttl_hours}h.")


if __name__ == "__main__":
    main()
