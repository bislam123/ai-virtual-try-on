"""POST /api/extract-product-image (Milestone 7 — see product-extractor/).

Deliberately synchronous, not job-based like /api/try-on: the extractor is
classical CV (milliseconds on this CPU), not a diffusion model (minutes) —
the job/poll pattern exists specifically because of the AI provider's cost,
not as a house style to apply everywhere. See docs/ARCHITECTURE.md.
"""

import io
import logging

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response

from product_extractor import ExtractionService, SaliencyProductExtractor

from ..config import settings
from ..core.validation import validate_and_load_image
from ..services.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["extraction"])

_extraction_service = ExtractionService(SaliencyProductExtractor())


def get_extraction_rate_limiter(request: Request) -> RateLimiter:
    return request.app.state.extraction_rate_limiter


@router.post("/extract-product-image")
async def extract_product_image(
    request: Request,
    image: UploadFile = File(..., description="A product photo or a screenshot containing one"),
    limiter: RateLimiter = Depends(get_extraction_rate_limiter),
):
    client_key = request.client.host if request.client else "unknown"
    limit_result = limiter.check(client_key)
    if not limit_result.allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Too many requests. Please try again in about {limit_result.retry_after_seconds} seconds.",
            headers={"Retry-After": str(limit_result.retry_after_seconds)},
        )

    data = await image.read()
    pil_image = validate_and_load_image(data, "Image")

    result = _extraction_service.extract_from_image(pil_image)

    buf = io.BytesIO()
    result.image.save(buf, format="PNG")
    return Response(
        content=buf.getvalue(),
        media_type="image/png",
        headers={
            "X-Extraction-Applied": "true" if result.applied else "false",
            "X-Extraction-Confidence": f"{result.confidence:.3f}",
        },
    )
