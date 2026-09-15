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


@dataclass
class TryOnRequest:
    person_image: Image.Image
    garment_image: Image.Image
    category: GarmentCategory
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
