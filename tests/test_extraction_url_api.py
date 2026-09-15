"""Milestone 8 API tests for POST /api/extract-product-url.

Monkeypatches the route module's page_fetcher to a fake — the real
HttpProductPageFetcher's fetch/parse/SSRF logic is already thoroughly
covered against mocked HTTP in tests/test_url_fetcher.py; this file is
about the API layer itself: request validation, error-message surfacing,
response headers, and rate limiting.
"""

import io

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from backend.app.api import extraction as extraction_module
from backend.app.core.errors import configure_exception_handlers
from backend.app.services.rate_limiter import RateLimiter
from product_extractor.fetchers.base import ProductExtractionError, ProductPageFetcher


class FakePageFetcher(ProductPageFetcher):
    def fetch_product_image(self, url: str) -> Image.Image:
        if "blocked" in url:
            raise ProductExtractionError("This site doesn't allow automatic product extraction.")
        if "empty" in url:
            raise ProductExtractionError("We couldn't find a product image on that page.")
        return Image.new("RGB", (300, 400), color="purple")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(extraction_module._extraction_service, "page_fetcher", FakePageFetcher())
    app = FastAPI()
    configure_exception_handlers(app)
    app.include_router(extraction_module.router)
    app.state.url_extraction_rate_limiter = RateLimiter(max_requests=1000, window_seconds=600)
    return TestClient(app)


def test_extracts_from_url(client):
    resp = client.post("/api/extract-product-url", json={"url": "http://example.com/product"})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    img = Image.open(io.BytesIO(resp.content))
    assert img.size == (300, 400)


def test_robots_blocked_url_returns_clear_message(client):
    resp = client.post("/api/extract-product-url", json={"url": "http://blocked.example.com/product"})
    assert resp.status_code == 400
    assert "doesn't allow automatic product extraction" in resp.json()["detail"]


def test_no_image_found_returns_clear_message(client):
    resp = client.post("/api/extract-product-url", json={"url": "http://empty.example.com/product"})
    assert resp.status_code == 400
    assert "couldn't find a product image" in resp.json()["detail"]


def test_rejects_empty_url(client):
    resp = client.post("/api/extract-product-url", json={"url": ""})
    assert resp.status_code == 422  # pydantic validation, min_length=1


def test_rejects_missing_url_field(client):
    resp = client.post("/api/extract-product-url", json={})
    assert resp.status_code == 422


def test_rate_limit_blocks_after_max_requests(monkeypatch):
    monkeypatch.setattr(extraction_module._extraction_service, "page_fetcher", FakePageFetcher())
    app = FastAPI()
    configure_exception_handlers(app)
    app.include_router(extraction_module.router)
    app.state.url_extraction_rate_limiter = RateLimiter(max_requests=1, window_seconds=600)
    client = TestClient(app)

    assert client.post("/api/extract-product-url", json={"url": "http://example.com/product"}).status_code == 200
    second = client.post("/api/extract-product-url", json={"url": "http://example.com/product"})
    assert second.status_code == 429
    assert "Retry-After" in second.headers
