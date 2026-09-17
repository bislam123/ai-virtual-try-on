"""Tests for backend/app/core/validation.py's validate_and_load_image.

Pure unit tests -- no app, no database, no network. Covers the existing
behavior (accepted formats, size cap, corrupted-data safety) plus the
hardening added in this milestone: dimensions are checked from the image
header *before* the expensive full pixel decode (img.load()), not after,
closing a decompression-bomb-style resource-exhaustion gap. See that
module's own docstring for the full reasoning.
"""

import io
import struct
import zlib

import pytest
from PIL import Image

from backend.app.config import settings
from backend.app.core.errors import UserFacingError
from backend.app.core.validation import validate_and_load_image


def _png_bytes(size=(32, 32), color="blue") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_bytes(size=(32, 32), color="green") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="JPEG")
    return buf.getvalue()


def _webp_bytes(size=(32, 32), color="red") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="WEBP")
    return buf.getvalue()


def _fake_png_with_declared_size(width: int, height: int) -> bytes:
    """A syntactically valid PNG whose IHDR chunk declares (width, height)
    but whose IDAT chunk is deliberately empty/undecodable -- enough for
    PIL's Image.open() to parse the header (populating img.size/img.format)
    without ever successfully decoding real pixel data via img.load().

    Used to prove validate_and_load_image checks dimensions *before*
    calling img.load(), not after: if the order were reversed, this file
    would fail at img.load() time with the generic "doesn't look like a
    valid image" message instead of the specific "too large (WxHpx)" one
    -- see test_dimension_check_runs_before_the_expensive_decode below.
    """
    sig = b"\x89PNG\r\n\x1a\n"

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit depth, RGB color type
    idat = chunk(b"IDAT", zlib.compress(b""))  # empty compressed stream -- .load() would fail on this
    return sig + chunk(b"IHDR", ihdr) + idat + chunk(b"IEND", b"")


# --- Accepted formats ------------------------------------------------------------


def test_accepts_valid_png():
    img = validate_and_load_image(_png_bytes(), "Photo")
    assert img.mode == "RGB"


def test_accepts_valid_jpeg():
    img = validate_and_load_image(_jpeg_bytes(), "Photo")
    assert img.mode == "RGB"


def test_accepts_valid_webp():
    img = validate_and_load_image(_webp_bytes(), "Photo")
    assert img.mode == "RGB"


# --- Rejected content -------------------------------------------------------------


def test_rejects_empty_upload():
    with pytest.raises(UserFacingError, match="empty"):
        validate_and_load_image(b"", "Photo")


def test_rejects_non_image_content_disguised_with_an_image_looking_call():
    """Content type is never trusted from a header/filename -- only from
    what PIL actually decodes. This is plain text, not an image at all."""
    with pytest.raises(UserFacingError, match="valid image"):
        validate_and_load_image(b"not an image, just plain text bytes", "Photo")


def test_rejects_corrupted_image_data():
    """A truncated/corrupted file that still starts with a real PNG
    signature -- must fail safely (a clear UserFacingError), never an
    unhandled exception."""
    corrupted = _png_bytes()[:20]  # valid signature + partial IHDR, nothing else
    with pytest.raises(UserFacingError, match="valid image"):
        validate_and_load_image(corrupted, "Photo")


def test_rejects_unsupported_format_bmp():
    """BMP is a real, PIL-decodable raster format -- but not one of the
    three this app explicitly allows, so it must still be rejected by the
    explicit ALLOWED_FORMATS check, not silently accepted just because PIL
    could open it."""
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), color="white").save(buf, format="BMP")
    with pytest.raises(UserFacingError, match="JPEG, PNG, or WebP"):
        validate_and_load_image(buf.getvalue(), "Photo")


def test_rejects_svg_masquerading_as_an_image():
    """Pillow has no SVG plugin at all -- an SVG (or any other XML/active-
    content file) simply fails to open as a raster image, the same as any
    other unrecognized format. Confirms this by construction, not just by
    reading ALLOWED_FORMATS."""
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    with pytest.raises(UserFacingError, match="valid image"):
        validate_and_load_image(svg, "Photo")


def test_rejects_oversized_byte_size(monkeypatch):
    monkeypatch.setattr(settings, "max_upload_size_mb", 0)
    with pytest.raises(UserFacingError, match="too large"):
        validate_and_load_image(_png_bytes(), "Photo")


# --- Dimension cap: existing behavior, now checked before the expensive decode ----


def test_rejects_dimensions_over_the_configured_limit():
    oversized = Image.new("RGB", (settings.max_image_dimension_px + 1, 100), color="white")
    buf = io.BytesIO()
    oversized.save(buf, format="PNG")

    with pytest.raises(UserFacingError, match="too large"):
        validate_and_load_image(buf.getvalue(), "Photo")


def test_accepts_dimensions_exactly_at_the_configured_limit():
    at_limit = Image.new("RGB", (settings.max_image_dimension_px, 100), color="white")
    buf = io.BytesIO()
    at_limit.save(buf, format="PNG")

    img = validate_and_load_image(buf.getvalue(), "Photo")
    assert img.size == (settings.max_image_dimension_px, 100)


def test_legitimate_mobile_photo_dimensions_are_accepted():
    """A realistic modern phone photo resolution (e.g. a 12MP portrait
    shot) — comfortably within max_image_dimension_px — must not be
    rejected. Guards against an overly aggressive limit breaking real
    phone photos, not just synthetic test images."""
    mobile_photo = Image.new("RGB", (3024, 4032), color="white")
    buf = io.BytesIO()
    mobile_photo.save(buf, format="JPEG")

    img = validate_and_load_image(buf.getvalue(), "Photo")
    assert img.size == (3024, 4032)


def test_dimension_check_runs_before_the_expensive_decode(monkeypatch):
    """The actual hardening this milestone adds: a file whose *header*
    declares dimensions over the limit is rejected on that cheap metadata
    alone -- never reaching img.load(), the expensive full-pixel decode
    this check exists to guard against. Proven precisely (not just
    plausibly) by using a file that would fail at .load() time with a
    *different* error if the check order were reversed: this test asserts
    on the specific "too large (WxHpx)" message, which only the dimension
    check produces -- the generic "doesn't look like a valid image"
    message is what .load() failing first would have produced instead."""
    huge_w, huge_h = settings.max_image_dimension_px + 5000, 100
    fake = _fake_png_with_declared_size(huge_w, huge_h)

    with pytest.raises(UserFacingError) as exc_info:
        validate_and_load_image(fake, "Photo")

    assert f"{huge_w}x{huge_h}px" in str(exc_info.value)


def test_a_file_with_a_valid_declared_size_but_corrupted_pixel_data_still_fails_safely():
    """The counterpart to the test above: once dimensions pass (within
    the limit), img.load() still runs and its own failure path is still
    reachable and still safe -- this reordering must not accidentally
    skip corrupted-data detection for files that pass the dimension gate."""
    small_but_corrupted = _fake_png_with_declared_size(100, 100)

    with pytest.raises(UserFacingError, match="valid image"):
        validate_and_load_image(small_but_corrupted, "Photo")
