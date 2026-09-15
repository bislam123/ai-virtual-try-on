"""GET /api/usage/me (Milestone 11).

Lets the frontend show "3 of 5 free generations left today" honestly,
before the user hits the limit and gets surprised by a 429 on submit — and
works for anonymous callers too (tracked by IP, same identity resolution
POST /api/try-on itself uses), matching the brief's requirement that the
core experience never requires an account.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Request

from ..auth.dependencies import get_current_user_optional
from ..db import User
from ..models.schemas import UsageStatusResponse
from ..services.quota_service import DEFAULT_PLAN_NAME, QuotaService

router = APIRouter(prefix="/api/usage", tags=["usage"])


def get_quota_service(request: Request) -> QuotaService:
    return request.app.state.quota_service


@router.get("/me", response_model=UsageStatusResponse)
async def get_my_usage(
    request: Request,
    quota_service: QuotaService = Depends(get_quota_service),
    user: Optional[User] = Depends(get_current_user_optional),
):
    client_ip = request.client.host if request.client else "unknown"
    status = quota_service.get_status(
        user_id=user.id if user else None,
        client_ip=None if user else client_ip,
        plan_name=user.plan if user else DEFAULT_PLAN_NAME,
    )
    return UsageStatusResponse(
        plan=status.plan,
        limit_per_day=status.limit_per_day,
        limit_per_month=status.limit_per_month,
        used_today=status.used_today,
        used_this_month=status.used_this_month,
        remaining_today=status.remaining_today,
        remaining_this_month=status.remaining_this_month,
        max_num_timesteps=status.max_num_timesteps,
    )
