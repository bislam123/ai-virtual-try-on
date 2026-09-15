"""QuotaService: the "Usage quota" stage of the brief's User -> Account ->
Plan -> Usage quota -> AI generation pipeline (sections 4/23).

Deliberately separate from RateLimiter (services/rate_limiter.py): that one
guards against short-burst abuse (e.g. 20 requests/hour) and is in-memory,
process-local. This one enforces the actual business-model allowance tied
to a plan (e.g. "5/day on the free tier"), counted from real, durable data
(the jobs table) so it survives a restart and works correctly across
multiple worker processes — the in-memory rate limiter explicitly doesn't
promise either of those, and doesn't need to for its purpose. Both layers
apply to the same request; they answer different questions.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select

from ..db import JobRecord, Plan, get_session

DEFAULT_PLAN_NAME = "free"  # what an anonymous visitor, or a user with no plan row, is treated as


@dataclass
class QuotaStatus:
    plan: str
    limit_per_day: Optional[int]
    limit_per_month: Optional[int]
    used_today: int
    used_this_month: int
    max_num_timesteps: Optional[int]

    @property
    def remaining_today(self) -> Optional[int]:
        return None if self.limit_per_day is None else max(0, self.limit_per_day - self.used_today)

    @property
    def remaining_this_month(self) -> Optional[int]:
        return None if self.limit_per_month is None else max(0, self.limit_per_month - self.used_this_month)

    @property
    def is_exceeded(self) -> bool:
        day_ok = self.limit_per_day is None or self.used_today < self.limit_per_day
        month_ok = self.limit_per_month is None or self.used_this_month < self.limit_per_month
        return not (day_ok and month_ok)


def _day_start(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


class QuotaService:
    def get_status(self, *, user_id: Optional[int], client_ip: Optional[str], plan_name: str) -> QuotaStatus:
        """Counts every job (any status — a failed generation still spent
        compute, see the module-level note in docs/DEVELOPMENT.md's
        Milestone 11 section for the reasoning) attributed to this user, or
        to this IP for an anonymous caller, within the current day/month."""
        now = datetime.now(timezone.utc)

        with get_session() as session:
            plan = session.get(Plan, plan_name) or session.get(Plan, DEFAULT_PLAN_NAME)
            limit_per_day = plan.max_generations_per_day if plan else None
            limit_per_month = plan.max_generations_per_month if plan else None
            max_num_timesteps = plan.max_num_timesteps if plan else None

            identity_filter = (
                (JobRecord.user_id == user_id) if user_id is not None else (JobRecord.client_ip == client_ip)
            )

            used_today = session.scalar(
                select(func.count())
                .select_from(JobRecord)
                .where(identity_filter, JobRecord.created_at >= _day_start(now))
            ) or 0
            used_this_month = session.scalar(
                select(func.count())
                .select_from(JobRecord)
                .where(identity_filter, JobRecord.created_at >= _month_start(now))
            ) or 0

        return QuotaStatus(
            plan=plan_name,
            limit_per_day=limit_per_day,
            limit_per_month=limit_per_month,
            used_today=used_today,
            used_this_month=used_this_month,
            max_num_timesteps=max_num_timesteps,
        )


def quota_exceeded_message(status: QuotaStatus) -> str:
    if status.limit_per_day is not None and status.used_today >= status.limit_per_day:
        return (
            f"You've used all {status.limit_per_day} of your {status.plan} plan's generations for today. "
            "Please try again tomorrow."
        )
    return (
        f"You've used all {status.limit_per_month} of your {status.plan} plan's generations for this month. "
        "Please try again next month."
    )
