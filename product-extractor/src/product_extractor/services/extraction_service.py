"""ExtractionService: the dispatch seam for the product-extractor module.

Milestone 7 added `extract_from_image` (an uploaded photo or screenshot).
Milestone 8 adds `extract_from_url` (Method A: a product page link) —
fetch it (see fetchers/), then run the exact same region-finding logic on
whatever image the page yields. A future Milestone 9 (Method D: browser
extension) plugs in the same way: get an image however the method
requires, then hand it to `self.extractor`. Deciding *how* to get an image
is a different concern from *finding the product within* it, which is all
ProductImageExtractor does — per docs/ARCHITECTURE.md's "no vendor
lock-in" pattern applied here too.
"""

from typing import Optional

from PIL import Image

from ..extractors.base import ExtractionResult, ProductImageExtractor
from ..fetchers.base import ProductPageFetcher


class ExtractionService:
    def __init__(self, extractor: ProductImageExtractor, page_fetcher: Optional[ProductPageFetcher] = None):
        self.extractor = extractor
        self.page_fetcher = page_fetcher

    def extract_from_image(self, image: Image.Image) -> ExtractionResult:
        return self.extractor.extract(image)

    def extract_from_url(self, url: str) -> ExtractionResult:
        """Raises fetchers.base.ProductExtractionError on any failure (blocked
        by robots.txt, unreachable, no product image found, or a request we
        refuse to make — see fetchers/ssrf_guard.py). The caller (the API
        route) is responsible for the brief's required fallback: a clear
        message pointing the user at uploading a photo/screenshot instead."""
        if self.page_fetcher is None:
            raise RuntimeError("ExtractionService was constructed without a page_fetcher — extract_from_url unavailable.")
        image = self.page_fetcher.fetch_product_image(url)
        return self.extractor.extract(image)
