"""POST /api/extract-product-image and POST /api/extract-product-url
(Milestones 7 and 8 — see product-extractor/).

Both deliberately synchronous, not job-based like /api/try-on: image
extraction is classical CV (milliseconds on this CPU) and a page fetch has
its own short timeout (fetchers/http_fetcher.py) — the job/poll pattern
exists specifically because of the AI provider's cost, not as a house
style to apply everywhere. See docs/ARCHITECTURE.md.
"""

import io
import logging

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response

from product_extractor import ExtractionService, HttpProductPageFetcher, SaliencyProductExtractor
from product_extractor.fetchers.base import ProductExtractionError

from ..core.errors import UserFacingError
from ..core.validation import validate_and_load_image
from ..models.schemas import ExtractProductUrlRequest
from ..services.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["extraction"])

_extraction_service = ExtractionService(SaliencyProductExtractor(), page_fetcher=HttpProductPageFetcher())


def get_extraction_rate_limiter(request: Request) -> RateLimiter:
    return request.app.state.extraction_rate_limiter


def get_url_extraction_rate_limiter(request: Request) -> RateLimiter:
    return request.app.state.url_extraction_rate_limiter


def _check_rate_limit(request: Request, limiter: RateLimiter) -> None:
    client_key = request.client.host if request.client else "unknown"
    limit_result = limiter.check(client_key)
    if not limit_result.allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Too many requests. Please try again in about {limit_result.retry_after_seconds} seconds.",
            headers={"Retry-After": str(limit_result.retry_after_seconds)},
        )


def _image_response(result) -> Response:
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


@router.post("/extract-product-image")
async def extract_product_image(
    request: Request,
    image: UploadFile = File(..., description="A product photo or a screenshot containing one"),
    limiter: RateLimiter = Depends(get_extraction_rate_limiter),
):
    _check_rate_limit(request, limiter)
    data = await image.read()
    pil_image = validate_and_load_image(data, "Image")
    result = _extraction_service.extract_from_image(pil_image)
    return _image_response(result)


@router.post("/extract-product-url")
async def extract_product_url(
    request: Request,
    body: ExtractProductUrlRequest,
    limiter: RateLimiter = Depends(get_url_extraction_rate_limiter),
):
    """Method A from the brief's section 7. Never bypasses robots.txt,
    auth, paywalls, or anti-bot challenges (see product-extractor/fetchers/)
    — any of those, or simply no product image being findable, surfaces as
    a clear UserFacingError pointing at the upload fallback (section 25)."""
    _check_rate_limit(request, limiter)
    try:
        result = _extraction_service.extract_from_url(body.url)
    except ProductExtractionError as exc:
        raise UserFacingError(exc.message, status_code=400)
    return _image_response(result)
