"""The extractor abstraction (mirrors VirtualTryOnProvider — see docs/ARCHITECTURE.md).

Nothing above this interface should know or care which algorithm actually
finds the product/garment region in an uploaded image.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Tuple

from PIL import Image


@dataclass
class ExtractionResult:
    image: Image.Image
    """The extracted image — a crop of the input if `applied` is True, or
    the original image unchanged if the extractor wasn't confident enough
    to touch it. Never returning a guessed-wrong crop with no way to tell
    is the point of `applied` existing at all."""

    applied: bool
    confidence: float  # 0.0-1.0; meaningful only when applied is True
    bounding_box: Optional[Tuple[int, int, int, int]]  # (left, top, right, bottom) in input coords


class ProductImageExtractor(ABC):
    @abstractmethod
    def extract(self, image: Image.Image) -> ExtractionResult: ...
