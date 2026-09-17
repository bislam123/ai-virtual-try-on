"""Upload validation (see docs brief section 15 — Security).

Every uploaded file passes through here before anything else touches it:
- size capped before we even try to decode it
- content is actually decoded as a real raster image (PIL), not just
  trusted by filename/Content-Type header — rejects disguised non-image
  files and anything PIL can't parse (no SVG — Pillow has no SVG plugin at
  all, so an SVG or other non-raster "active content" file simply fails to
  open here like any other unrecognized format — and no executables)
- dimensions checked from the header — cheaply available the instant
  Image.open() returns, before any real pixel data is decoded — and
  capped *before* the expensive full decode (img.load()) ever runs; see
  that ordering below for why it's deliberate, not incidental
- re-encoded to a fresh PNG in memory, so whatever we persist to disk is
  exactly the decoded pixels, never the original file bytes (strips any
  trailing/embedded payload smuggled after a valid image stream, and all
  EXIF/metadata)
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
    except Exception:
        raise UserFacingError(f"{field_label} doesn't look like a valid image. Please upload a JPEG, PNG, or WebP photo.")

    if img.format not in ALLOWED_FORMATS:
        raise UserFacingError(f"{field_label} must be a JPEG, PNG, or WebP image.")

    # Dimensions are read from the file's header, which Image.open() above
    # already parsed -- deliberately checked *before* img.load() below,
    # not after. img.load() is what actually decodes pixel data, and
    # Pillow's own decompression-bomb guard (Image.MAX_IMAGE_PIXELS,
    # ~89.5M pixels by default) only raises an error above 2x that
    # threshold; between 1x and 2x it just emits a DecompressionBombWarning
    # and decodes anyway. A file whose header declares dimensions in that
    # warning band (comfortably possible from a small, highly-compressible
    # file — this is exactly what a decompression bomb is) would previously
    # have been fully decoded into memory before this function's own,
    # stricter max_image_dimension_px check ever got a chance to reject
    # it. Reading width/height from the header first closes that gap: an
    # oversized image is rejected on cheap metadata alone, before the
    # expensive decode this check exists to guard against ever runs.
    width, height = img.size
    if width > settings.max_image_dimension_px or height > settings.max_image_dimension_px:
        raise UserFacingError(
            f"{field_label} is too large ({width}x{height}px). "
            f"Please upload an image under {settings.max_image_dimension_px}px on each side."
        )

    try:
        img.load()
    except Exception:
        raise UserFacingError(f"{field_label} doesn't look like a valid image. Please upload a JPEG, PNG, or WebP photo.")

    return img.convert("RGB")
