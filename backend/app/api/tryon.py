import logging

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from ..config import settings
from ..core.errors import UserFacingError
from ..core.validation import validate_and_load_image
from ..models.schemas import TryOnJobCreated, TryOnJobStatusResponse
from ..services.job_store import JobStatus
from ..services.rate_limiter import RateLimiter
from ..services.tryon_service import TryOnService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/try-on", tags=["try-on"])

VALID_CATEGORIES = {"tops", "bottoms", "one-pieces"}


def get_tryon_service(request: Request) -> TryOnService:
    return request.app.state.tryon_service


def get_rate_limiter(request: Request) -> RateLimiter:
    return request.app.state.rate_limiter


@router.post("", response_model=TryOnJobCreated, status_code=202)
async def create_try_on_job(
    request: Request,
    background_tasks: BackgroundTasks,
    person_image: UploadFile = File(..., description="Photo of the person"),
    garment_image: UploadFile = File(..., description="Photo of the clothing item (plain product photo)"),
    category: str = Form(..., description="tops | bottoms | one-pieces"),
    garment_photo_type: str = Form("flat-lay", description="Only 'flat-lay' is supported today"),
    num_timesteps: int = Form(settings.default_num_timesteps),
    seed: int = Form(42),
    service: TryOnService = Depends(get_tryon_service),
    limiter: RateLimiter = Depends(get_rate_limiter),
):
    client_key = request.client.host if request.client else "unknown"
    limit_result = limiter.check(client_key)
    if not limit_result.allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Too many try-on requests. Please try again in about {limit_result.retry_after_seconds} seconds.",
            headers={"Retry-After": str(limit_result.retry_after_seconds)},
        )

    if category not in VALID_CATEGORIES:
        raise HTTPException(status_code=400, detail="category must be one of: tops, bottoms, one-pieces.")

    # We only support plain product/garment photos right now — see
    # ai/vendor/aitryon-bodyparser's parser.py for exactly why "model" (garment
    # worn by another person) isn't safe yet, and docs/AI_MODEL_LICENSE.md for
    # the licensing reason a real segmentation model isn't wired in yet.
    if garment_photo_type != "flat-lay":
        raise UserFacingError(
            "We currently support plain clothing/product photos only "
            "(not photos of the item worn by another person). "
            "Please upload a clear product photo instead — support for the other kind is coming soon."
        )

    clamped_steps = max(settings.min_num_timesteps, min(settings.max_num_timesteps, num_timesteps))

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
