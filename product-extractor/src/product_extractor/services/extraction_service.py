"""ExtractionService: the dispatch seam for the product-extractor module.

Today there's exactly one extractor and one input shape (an uploaded
image), so this is a thin pass-through. It exists anyway because it's
where Milestone 8 (Method A: product URL) and Milestone 9 (Method D:
browser extension) plug in — deciding *how* to get an image (fetch a URL,
accept a POSTed screenshot, receive one from the extension) is a different
concern from *finding the product within* an image, which is all
ProductImageExtractor does. Keeping them separate now avoids a rewrite
later, per docs/ARCHITECTURE.md's "no vendor lock-in" pattern applied here
too.
"""

from PIL import Image

from ..extractors.base import ExtractionResult, ProductImageExtractor


class ExtractionService:
    def __init__(self, extractor: ProductImageExtractor):
        self.extractor = extractor

    def extract_from_image(self, image: Image.Image) -> ExtractionResult:
        return self.extractor.extract(image)
