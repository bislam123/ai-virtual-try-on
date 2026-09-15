"""Milestone 7 API tests for POST /api/extract-product-image.

Fast and self-contained — no AI model, no database — since the extractor
is classical CV. Reuses the same mock-screenshot/clean-photo scenarios as
tests/test_product_extractor.py, now through the actual HTTP endpoint.
"""

import io

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from backend.app.api import extraction as extraction_module
from backend.app.core.errors import configure_exception_handlers
from backend.app.services.rate_limiter import RateLimiter

GARMENT_PHOTO = __file__.rsplit("tests", 1)[0] + "ai/vendor/fashn-vton-1.5/examples/data/garment.webp"


def make_test_app(rate_limit=1000):
    app = FastAPI()
    configure_exception_handlers(app)
    app.include_router(extraction_module.router)
    app.state.extraction_rate_limiter = RateLimiter(max_requests=rate_limit, window_seconds=600)
    return app


def _real_product_photo() -> Image.Image:
    return Image.open(GARMENT_PHOTO).convert("RGB").resize((400, 500))


def _mock_screenshot() -> Image.Image:
    product = _real_product_photo()
    canvas = Image.new("RGB", (900, 1300), color=(250, 250, 250))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle([0, 0, 900, 70], fill=(20, 20, 20))
    canvas.paste(product, (250, 150))
    for i, yy in enumerate(range(680, 760, 20)):
        draw.rectangle([250, yy, 250 + (300 if i else 500), yy + 10], fill=(180, 180, 180))
    draw.rectangle([250, 800, 500, 850], fill=(90, 90, 220))
    return canvas


def _upload(client, pil_image, filename="image.png"):
    buf = io.BytesIO()
    pil_image.save(buf, format="PNG")
    return client.post("/api/extract-product-image", files={"image": (filename, buf.getvalue(), "image/png")})


def test_extracts_from_mock_screenshot():
    client = TestClient(make_test_app())
    resp = _upload(client, _mock_screenshot())

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.headers["x-extraction-applied"] == "true"
    assert float(resp.headers["x-extraction-confidence"]) > 0.5

    result_img = Image.open(io.BytesIO(resp.content))
    assert result_img.size != (900, 1300)  # actually cropped


def test_leaves_clean_photo_unchanged():
    client = TestClient(make_test_app())
    clean = _real_product_photo()
    resp = _upload(client, clean)

    assert resp.status_code == 200
    assert resp.headers["x-extraction-applied"] == "false"
    result_img = Image.open(io.BytesIO(resp.content))
    assert result_img.size == clean.size


def test_rejects_non_image_upload():
    client = TestClient(make_test_app())
    resp = client.post(
        "/api/extract-product-image",
        files={"image": ("not-an-image.txt", b"hello world", "text/plain")},
    )
    assert resp.status_code == 400
    assert "valid image" in resp.json()["detail"]


def test_rate_limit_blocks_after_max_requests():
    client = TestClient(make_test_app(rate_limit=1))
    assert _upload(client, _real_product_photo()).status_code == 200
    second = _upload(client, _real_product_photo())
    assert second.status_code == 429
    assert "Retry-After" in second.headers
