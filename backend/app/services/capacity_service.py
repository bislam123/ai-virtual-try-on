"""CapacityService: a lightweight, global admission-control guard on
POST /api/try-on (see docs/ARCHITECTURE.md's Concurrency & capacity
section).

Distinct from QuotaService (a per-identity policy limit, e.g. "5/day on
the free tier") and RateLimiter (in-memory, short-burst abuse protection):
this answers a different question entirely -- "is the server, as a whole,
already carrying as much queued/in-flight AI work as it should," regardless
of who's asking. Neither of the other two checks that: quota and rate
limits are both scoped per user/IP, so many different identities, each
safely within their own budget, could still pile up an unbounded number of
expensive jobs. SelfHostedVTONProvider only ever runs one generation at a
time (see its own module docstring) -- every job beyond that one is
realistically just occupying a background-task thread waiting for the
provider's lock, for up to AITRYON_PROVIDER_LOCK_ACQUIRE_TIMEOUT_SECONDS
(an hour, by default) each.

Counted from the real database (JobRecord), like QuotaService -- not an
in-memory counter, which wouldn't survive a restart or coordinate across
multiple worker processes (the same limitation RateLimiter already has and
documents). The check-then-create sequence has the same small, accepted
race window QuotaService's own check already has today under genuinely
concurrent requests right at the boundary (a handful of simultaneous
submissions could all pass and all insert) -- a deliberate, proportionate
choice: this is a soft capacity guard, not a correctness invariant like
idempotency's database-enforced uniqueness (jobs.ix_jobs_idempotency_active),
and matches the one existing precedent for this class of limit in this
codebase rather than introducing a new, heavier locking mechanism (e.g. a
Postgres advisory lock) nothing else here uses.
"""

from dataclasses import dataclass

from sqlalchemy import func, select

from ..db import JobRecord, get_session
from .job_store import JobStatus

# What counts as "occupying capacity": not yet in a terminal state.
# CANCELLED is deliberately excluded (like COMPLETED/FAILED) -- a
# cancelled job was, by definition, never going to reach the provider.
_ACTIVE_STATUSES = [JobStatus.PENDING.value, JobStatus.PROCESSING.value]


@dataclass
class CapacityStatus:
    active_jobs: int
    max_active_jobs: int

    @property
    def is_at_capacity(self) -> bool:
        return self.active_jobs >= self.max_active_jobs


class CapacityService:
    def get_status(self, max_active_jobs: int) -> CapacityStatus:
        with get_session() as session:
            active = (
                session.scalar(
                    select(func.count()).select_from(JobRecord).where(JobRecord.status.in_(_ACTIVE_STATUSES))
                )
                or 0
            )
        return CapacityStatus(active_jobs=active, max_active_jobs=max_active_jobs)


CAPACITY_EXCEEDED_MESSAGE = (
    "Our AI model is at capacity right now, with too many try-on requests already queued. "
    "Please try again in a few minutes."
)
