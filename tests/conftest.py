"""Shared test fixtures/doubles.

FakeQuotaService lives here (not duplicated per test file) because more than
one test file needs a permissive stand-in: the real QuotaService always
hits the database (see backend/app/services/quota_service.py), and
anonymous usage is tracked by client IP — which every TestClient request in
a given test file shares, meaning repeated real-DB-backed test runs on the
same day would eventually, flakily, hit the free plan's daily limit for
that one shared identity. Tests that specifically verify quota enforcement
use a fresh per-test user (isolated by construction — a brand new user_id
has no history) or test QuotaService directly against a synthetic,
per-test-unique client_ip (see test_quota_service.py) instead.
"""

from backend.app.services.quota_service import QuotaStatus


class FakeQuotaService:
    def __init__(self, exceeded: bool = False, max_num_timesteps=None):
        self.exceeded = exceeded
        self.max_num_timesteps = max_num_timesteps

    def get_status(self, *, user_id, client_ip, plan_name):
        return QuotaStatus(
            plan=plan_name,
            limit_per_day=5 if self.exceeded else None,
            limit_per_month=None,
            used_today=5 if self.exceeded else 0,
            used_this_month=0,
            max_num_timesteps=self.max_num_timesteps,
        )
