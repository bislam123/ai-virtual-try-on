"""ProductPageFetcher: URL -> image (Method A from the brief's section 7).

Mirrors ProductImageExtractor's shape (see extractors/base.py) for a
different input: given a product page URL instead of an already-uploaded
image, produce the image to run through the same extraction pipeline.
"""

from abc import ABC, abstractmethod

from PIL import Image


class ProductExtractionError(Exception):
    """Raised whenever a URL can't be turned into a product image, for any
    reason (blocked by robots.txt, network failure, no image found, or a
    request we refuse to make at all — see ssrf_guard.py). `message` is
    always safe to show the user directly and always ends with the same
    fallback guidance (brief section 25: give a clear message *and* a
    fallback) — never a technical reason that would help someone probe
    what our SSRF protection blocks.
    """

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class ProductPageFetcher(ABC):
    @abstractmethod
    def fetch_product_image(self, url: str) -> Image.Image: ...

    @abstractmethod
    def fetch_image_from_url(self, image_url: str) -> Image.Image:
        """Fetch a URL the caller already knows points directly at a product
        image (no page to scrape) — see http_fetcher.py's implementation for
        why this is a separate method rather than a flag on
        fetch_product_image."""
        ...
