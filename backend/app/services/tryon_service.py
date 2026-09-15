"""VirtualTryOnService: orchestrates a try-on job end to end.

Frontend -> API route -> **this service** -> VirtualTryOnProvider -> model
(see docs/ARCHITECTURE.md). This is the only layer that talks to both the
job store and the storage service, and the only layer the API routes call
into for actual work.
"""

import io
import logging
from typing import Optional

from PIL import Image

from ..core.errors import UserFacingError
from ..providers.base import GarmentCategory, TryOnRequest, VirtualTryOnProvider
from .job_store import Job, JobStatus, JobStore
from .storage import StorageService

logger = logging.getLogger(__name__)

GENERIC_FAILURE_MESSAGE = (
    "We couldn't generate your try-on result. Please try a different photo, "
    "or try again in a moment."
)


class TryOnService:
    def __init__(self, provider: VirtualTryOnProvider, storage: StorageService, job_store: JobStore):
        self.provider = provider
        self.storage = storage
        self.job_store = job_store

    def start_job(
        self,
        person_image: Image.Image,
        garment_image: Image.Image,
        category: GarmentCategory,
        num_timesteps: int,
        guidance_scale: float,
        seed: int,
    ) -> Job:
        """Create the job record and persist the validated inputs as temp files.

        Called synchronously from the API route (fast — no model inference
        here), so the client gets a job_id back immediately. The actual
        generation happens in run_job(), invoked as a FastAPI BackgroundTask.
        """
        job = self.job_store.create()
        self.storage.save_temp_upload(job.id, "person.png", _to_png_bytes(person_image))
        self.storage.save_temp_upload(job.id, "garment.png", _to_png_bytes(garment_image))
        # Stash what the background task needs directly on the Job record, so
        # job state stays in one place (see Job.params/person_image/garment_image).
        job.params = dict(
            category=category,
            num_timesteps=num_timesteps,
            guidance_scale=guidance_scale,
            seed=seed,
        )
        job.person_image = person_image
        job.garment_image = garment_image
        return job

    def run_job(self, job_id: str) -> None:
        """The actual (slow) work. Runs in a background thread."""
        job = self.job_store.get(job_id)
        if job is None:
            logger.error("run_job called for unknown job_id=%s", job_id)
            return

        self.job_store.update_status(job_id, JobStatus.PROCESSING)
        try:
            assert job.person_image is not None and job.garment_image is not None
            request = TryOnRequest(person_image=job.person_image, garment_image=job.garment_image, **job.params)
            result = self.provider.generate(request)
            self.storage.save_result(job_id, result.image)
            self.job_store.update_status(job_id, JobStatus.COMPLETED)
        except Exception:
            logger.exception("Try-on job %s failed", job_id)
            self.job_store.update_status(job_id, JobStatus.FAILED, error=GENERIC_FAILURE_MESSAGE)
        finally:
            self.storage.cleanup_temp(job_id)
            # Drop image references now that we're done with them.
            job.person_image = None
            job.garment_image = None

    def get_result_path(self, job_id: str) -> Optional[str]:
        path = self.storage.get_result_path(job_id)
        return str(path) if path else None


def _to_png_bytes(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()
