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

    def extract_from_image_url(self, url: str) -> ExtractionResult:
        """Method D's image-handoff path (added alongside Milestone 9's
        extension work): the caller (a browser extension content script)
        already knows a direct product image URL — from the same page's
        own JSON-LD/og:image it used to decide the page was a product
        page — so there's no HTML page to
        fetch or parse here, just one image resource. This is what lets
        Method D work against sites whose page route is behind a CAPTCHA/
        anti-bot wall that would reject fetch_product_image's page fetch
        (see http_fetcher.py's fetch_image_from_url docstring): the image
        CDN host is a different, typically ungated, resource. Same
        ProductExtractionError contract as extract_from_url."""
        if self.page_fetcher is None:
            raise RuntimeError(
                "ExtractionService was constructed without a page_fetcher — extract_from_image_url unavailable."
            )
        image = self.page_fetcher.fetch_image_from_url(url)
        return self.extractor.extract(image)
