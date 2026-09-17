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


class ProviderBusyError(Exception):
    """Raised when a provider couldn't accept a new generation within its
    own configured wait/acquire budget -- e.g. SelfHostedVTONProvider's
    internal lock timeout. Defined at this abstraction layer (not inside
    providers/selfhosted.py, where it originated) specifically so
    TryOnService -- which must stay provider-agnostic per this module's
    own docstring -- can recognize and handle it (see
    services/tryon_service.py's run_job) without importing anything from
    a concrete provider implementation. Every VirtualTryOnProvider
    implementation that has a notion of "temporarily can't accept work"
    is expected to raise this (or a subclass) for that condition; one that
    doesn't have such a notion simply never raises it. The message on an
    instance of this is always safe to show a user directly -- never
    internal exception details."""


class InferenceTimeoutError(Exception):
    """Raised when a generation exceeded a provider's own configured
    inference timeout. Same reasoning as ProviderBusyError above for why
    this lives here rather than in a concrete provider module -- e.g.
    SelfHostedVTONProvider raises this, but TryOnService only ever needs
    to know it as "this provider's own generic, already-safe timeout
    signal", not that a self-hosted CPU/GPU model was specifically
    involved. The message on an instance of this is always safe to show a
    user directly."""


class VirtualTryOnProvider(ABC):
    """Anything that can turn (person, garment) into a try-on image."""

    @abstractmethod
    def generate(self, request: TryOnRequest) -> TryOnResult: ...
