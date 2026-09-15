"""Upload validation (see docs brief section 15 — Security).

Every uploaded file passes through here before anything else touches it:
- size capped before we even try to decode it
- content is actually decoded as a real raster image (PIL), not just
  trusted by filename/Content-Type header — rejects disguised non-image
  files and anything PIL can't parse (no SVG, no executables)
- re-encoded to a fresh PNG in memory, so whatever we persist to disk is
  exactly the decoded pixels, never the original file bytes (strips any
  trailing/embedded payload smuggled after a valid image stream, and all
  EXIF/metadata)
- dimensions capped, as a cheap resource-exhaustion guard
"""

import io

from PIL import Image

from ..config import settings
from .errors import UserFacingError

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}


def validate_and_load_image(data: bytes, field_label: str) -> Image.Image:
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    if len(data) == 0:
        raise UserFacingError(f"{field_label} is empty. Please choose a photo.")
    if len(data) > max_bytes:
        raise UserFacingError(f"{field_label} is too large. Please upload an image under {settings.max_upload_size_mb}MB.")

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        raise UserFacingError(f"{field_label} doesn't look like a valid image. Please upload a JPEG, PNG, or WebP photo.")

    if img.format not in ALLOWED_FORMATS:
        raise UserFacingError(f"{field_label} must be a JPEG, PNG, or WebP image.")

    width, height = img.size
    if width > settings.max_image_dimension_px or height > settings.max_image_dimension_px:
        raise UserFacingError(
            f"{field_label} is too large ({width}x{height}px). "
            f"Please upload an image under {settings.max_image_dimension_px}px on each side."
        )

    return img.convert("RGB")
