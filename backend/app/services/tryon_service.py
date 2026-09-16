"""VirtualTryOnService: orchestrates a try-on job end to end.

Frontend -> API route -> **this service** -> VirtualTryOnProvider -> model
(see docs/ARCHITECTURE.md). This is the only layer that talks to the job
store, the storage service, and the provider together.
"""

import io
import logging
from typing import Optional

from PIL import Image

from ..core.errors import UserFacingError
from ..providers.base import GarmentCategory, GarmentPhotoType, TryOnRequest, VirtualTryOnProvider
from .job_store import Job, JobStatus, JobStore
from .storage import StorageService

logger = logging.getLogger(__name__)

GENERIC_FAILURE_MESSAGE = (
    "We couldn't generate your try-on result. Please try a different photo, "
    "or try again in a moment."
)

PERSON_FILENAME = "person.png"
GARMENT_FILENAME = "garment.png"


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
        user_id: Optional[int] = None,
        client_ip: Optional[str] = None,
        garment_photo_type: GarmentPhotoType = "flat-lay",
    ) -> Job:
        """Create the job record and persist the validated inputs as temp files.

        Called synchronously from the API route (fast — no model inference
        here), so the client gets a job_id back immediately. The actual
        generation happens in run_job(), invoked as a FastAPI BackgroundTask.
        Images are written to temp storage rather than kept as Python object
        state: a DbJobStore-backed job can in principle be picked up by a
        different process than the one that created it, so the job record
        alone must be enough to find everything needed to run it.
        """
        job = self.job_store.create(
            user_id=user_id,
            category=category,
            num_timesteps=num_timesteps,
            guidance_scale=guidance_scale,
            seed=seed,
            client_ip=client_ip,
            garment_photo_type=garment_photo_type,
        )
        self.storage.save_temp_upload(job.id, PERSON_FILENAME, _to_png_bytes(person_image))
        self.storage.save_temp_upload(job.id, GARMENT_FILENAME, _to_png_bytes(garment_image))
        return job

    def run_job(self, job_id: str) -> None:
        """The actual (slow) work. Runs in a background thread."""
        job = self.job_store.get(job_id)
        if job is None:
            logger.error("run_job called for unknown job_id=%s", job_id)
            return

        self.job_store.update_status(job_id, JobStatus.PROCESSING)
        try:
            person_bytes = self.storage.load_temp_upload(job_id, PERSON_FILENAME)
            garment_bytes = self.storage.load_temp_upload(job_id, GARMENT_FILENAME)
            if person_bytes is None or garment_bytes is None:
                raise FileNotFoundError(f"Missing temp uploads for job {job_id}")

            request = TryOnRequest(
                person_image=Image.open(io.BytesIO(person_bytes)).convert("RGB"),
                garment_image=Image.open(io.BytesIO(garment_bytes)).convert("RGB"),
                category=job.category,
                garment_photo_type=job.garment_photo_type,
                num_timesteps=job.num_timesteps,
                guidance_scale=job.guidance_scale,
                seed=job.seed,
            )
            result = self.provider.generate(request)
            self.storage.save_result(job_id, result.image)
            self.job_store.update_status(job_id, JobStatus.COMPLETED)
        except Exception:
            logger.exception("Try-on job %s failed", job_id)
            self.job_store.update_status(job_id, JobStatus.FAILED, error=GENERIC_FAILURE_MESSAGE)
        finally:
            self.storage.cleanup_temp(job_id)

    def get_result_path(self, job_id: str) -> Optional[str]:
        path = self.storage.get_result_path(job_id)
        return str(path) if path else None

    def save_job(self, job_id: str, user_id: int) -> Job:
        """Marks a completed job as explicitly saved by its owner (brief
        section 16: results are temporary unless the user explicitly saves
        them). Only the job's own creator can save it — there's no way to
        retroactively claim an anonymous job onto an account."""
        job = self.job_store.get(job_id)
        if job is None:
            raise UserFacingError("We couldn't find that try-on job. It may have expired.", status_code=404)
        if job.user_id != user_id:
            raise UserFacingError("You can only save your own try-on results.", status_code=403)
        if job.status != JobStatus.COMPLETED:
            raise UserFacingError(f"This job isn't ready yet (status: {job.status.value}).", status_code=409)
        self.job_store.mark_saved(job_id)
        job.saved = True
        return job


def _to_png_bytes(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()
