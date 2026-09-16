"""The AI provider abstraction (see docs/ARCHITECTURE.md).

Nothing above this interface — the API routes, the job service, the frontend —
is allowed to know which model is running underneath. Swapping models (e.g.
to the Leffa fallback documented in docs/AI_MODEL_LICENSE.md) means writing a
new VirtualTryOnProvider, not touching the API contract.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

from PIL import Image

GarmentCategory = Literal["tops", "bottoms", "one-pieces"]

# Plumbing only, per docs/AI_MODEL_LICENSE.md's model-worn investigation --
# "model" is a legal value at this layer and everywhere it's threaded
# through below, but backend/app/api/tryon.py still rejects any request
# that isn't "flat-lay" before one ever reaches here. Not reachable by any
# real request yet; see that file for the actual enforcement point.
GarmentPhotoType = Literal["flat-lay", "model"]


@dataclass
class TryOnRequest:
    person_image: Image.Image
    garment_image: Image.Image
    category: GarmentCategory
    garment_photo_type: GarmentPhotoType
    num_timesteps: int
    guidance_scale: float
    seed: int


@dataclass
class TryOnResult:
    image: Image.Image


class VirtualTryOnProvider(ABC):
    """Anything that can turn (person, garment) into a try-on image."""

    @abstractmethod
    def generate(self, request: TryOnRequest) -> TryOnResult: ...
