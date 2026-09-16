import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from ..auth.dependencies import get_current_user_optional, get_current_user_required
from ..config import settings
from ..core.errors import UserFacingError
from ..core.validation import validate_and_load_image
from ..db import User
from ..models.schemas import TryOnJobCreated, TryOnJobStatusResponse
from ..services.job_store import JobStatus
from ..services.quota_service import DEFAULT_PLAN_NAME, QuotaService, quota_exceeded_message
from ..services.rate_limiter import RateLimiter
from ..services.tryon_service import TryOnService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/try-on", tags=["try-on"])

VALID_CATEGORIES = {"tops", "bottoms", "one-pieces"}
VALID_GARMENT_PHOTO_TYPES = {"flat-lay", "model"}


def get_tryon_service(request: Request) -> TryOnService:
    return request.app.state.tryon_service


def get_rate_limiter(request: Request) -> RateLimiter:
    return request.app.state.rate_limiter


def get_quota_service(request: Request) -> QuotaService:
    return request.app.state.quota_service


@router.post("", response_model=TryOnJobCreated, status_code=202)
async def create_try_on_job(
    request: Request,
    background_tasks: BackgroundTasks,
    person_image: UploadFile = File(..., description="Photo of the person"),
    garment_image: UploadFile = File(..., description="Photo of the clothing item (plain product photo)"),
    category: str = Form(..., description="tops | bottoms | one-pieces"),
    garment_photo_type: str = Form("flat-lay", description="flat-lay | model"),
    num_timesteps: int = Form(settings.default_num_timesteps),
    seed: int = Form(42),
    service: TryOnService = Depends(get_tryon_service),
    limiter: RateLimiter = Depends(get_rate_limiter),
    quota_service: QuotaService = Depends(get_quota_service),
    user: Optional[User] = Depends(get_current_user_optional),
):
    # Signing in is entirely optional here (brief: don't force an account
    # before the core flow works) — an authenticated request just gets its
    # job attributed to that account, enabling the /save endpoint below, and
    # a fairer per-user rate limit key instead of per-IP.
    client_ip = request.client.host if request.client else "unknown"
    client_key = f"user:{user.id}" if user else f"ip:{client_ip}"
    limit_result = limiter.check(client_key)
    if not limit_result.allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Too many try-on requests. Please try again in about {limit_result.retry_after_seconds} seconds.",
            headers={"Retry-After": str(limit_result.retry_after_seconds)},
        )

    # The "Usage quota" stage of User -> Account -> Plan -> Usage quota ->
    # AI generation (brief sections 4/23) — distinct from the rate limiter
    # above, which only guards short-burst abuse. See services/quota_service.py.
    plan_name = user.plan if user else DEFAULT_PLAN_NAME
    quota_status = quota_service.get_status(
        user_id=user.id if user else None,
        client_ip=None if user else client_ip,
        plan_name=plan_name,
    )
    if quota_status.is_exceeded:
        raise UserFacingError(quota_exceeded_message(quota_status), status_code=429)

    if category not in VALID_CATEGORIES:
        raise HTTPException(status_code=400, detail="category must be one of: tops, bottoms, one-pieces.")

    # "model" (garment worn by another person) is now supported alongside
    # "flat-lay" -- see docs/AI_MODEL_LICENSE.md's model-worn investigation
    # and validation for what makes this safe: real segmentation via
    # MediaPipeBodyParser (Apache-2.0, commercially clean), the same
    # create_garment_image masking already used for the person-image side,
    # validated end to end for every category (tops/bottoms/one-pieces).
    if garment_photo_type not in VALID_GARMENT_PHOTO_TYPES:
        raise UserFacingError(
            "We support plain clothing/product photos or photos of the item "
            "worn by a person. Please upload one of those instead."
        )

    # Global technical ceiling first, then the plan's own cap (never higher
    # than the global one — a plan can only restrict further, see
    # db/models.py's Plan.max_num_timesteps docstring for why this is the
    # "higher resolution" half of the premium tier rather than a new feature).
    plan_cap = quota_status.max_num_timesteps
    effective_max = settings.max_num_timesteps if plan_cap is None else min(settings.max_num_timesteps, plan_cap)
    clamped_steps = max(settings.min_num_timesteps, min(effective_max, num_timesteps))

    person_bytes = await person_image.read()
    garment_bytes = await garment_image.read()
    person_pil = validate_and_load_image(person_bytes, "Your photo")
    garment_pil = validate_and_load_image(garment_bytes, "The clothing photo")

    job = service.start_job(
        person_image=person_pil,
        garment_image=garment_pil,
        category=category,  # type: ignore[arg-type]
        num_timesteps=clamped_steps,
        guidance_scale=settings.default_guidance_scale,
        seed=seed,
        user_id=user.id if user else None,
        client_ip=client_ip,
        garment_photo_type=garment_photo_type,  # type: ignore[arg-type]
    )
    background_tasks.add_task(service.run_job, job.id)

    return TryOnJobCreated(job_id=job.id, status=job.status)


@router.get("/{job_id}", response_model=TryOnJobStatusResponse)
async def get_try_on_job(job_id: str, service: TryOnService = Depends(get_tryon_service)):
    job = service.job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="We couldn't find that try-on job. It may have expired.")

    result_url = f"/api/try-on/{job_id}/result" if job.status == JobStatus.COMPLETED else None
    return TryOnJobStatusResponse(
        job_id=job.id,
        status=job.status,
        error=job.error,
        created_at=job.created_at,
        updated_at=job.updated_at,
        result_url=result_url,
        saved=job.saved,
    )


@router.get("/{job_id}/result")
async def get_try_on_result(job_id: str, service: TryOnService = Depends(get_tryon_service)):
    job = service.job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="We couldn't find that try-on job. It may have expired.")
    if job.status != JobStatus.COMPLETED:
        raise HTTPException(status_code=409, detail=f"This job isn't ready yet (status: {job.status.value}).")

    result_path = service.get_result_path(job_id)
    if result_path is None:
        raise HTTPException(status_code=404, detail="The result image is no longer available.")

    return FileResponse(result_path, media_type="image/png")


@router.post("/{job_id}/save", response_model=TryOnJobStatusResponse)
async def save_try_on_result(
    job_id: str,
    service: TryOnService = Depends(get_tryon_service),
    user: User = Depends(get_current_user_required),
):
    """Explicitly keep a result past the unsaved-result TTL (see
    backend/scripts/cleanup_expired_results.py) by attaching it to the
    signed-in user's account. Requires auth — that's the point of the
    feature, not a violation of "don't force an account for the MVP": the
    base generate/view/download-to-device flow above never requires it.
    """
    job = service.save_job(job_id, user.id)
    return TryOnJobStatusResponse(
        job_id=job.id,
        status=job.status,
        error=job.error,
        created_at=job.created_at,
        updated_at=job.updated_at,
        result_url=f"/api/try-on/{job_id}/result",
        saved=job.saved,
    )
