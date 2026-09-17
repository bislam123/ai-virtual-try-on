"""VirtualTryOnService: orchestrates a try-on job end to end.

Frontend -> API route -> **this service** -> VirtualTryOnProvider -> model
(see docs/ARCHITECTURE.md). This is the only layer that talks to the job
store, the storage service, and the provider together.
"""

import hashlib
import io
import logging
from typing import Optional, Tuple

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

IDEMPOTENCY_CONFLICT_MESSAGE = (
    "This Idempotency-Key was already used for a different try-on request. "
    "Use a new Idempotency-Key for a new request."
)

PERSON_FILENAME = "person.png"
GARMENT_FILENAME = "garment.png"


def _idempotency_fingerprint(
    category: str,
    garment_photo_type: str,
    requested_num_timesteps: int,
    seed: int,
    person_bytes: bytes,
    garment_bytes: bytes,
) -> str:
    """What "the same request" means for POST /api/try-on's Idempotency-Key
    handling: every field the client actually controls, hashed together.
    Deliberately requested_num_timesteps -- the value as submitted, before
    any plan-cap clamping -- not the clamped value a job actually runs
    with: clamping is a server-side policy detail, not something that
    should make two otherwise-identical client requests look "different".
    """
    hasher = hashlib.sha256()
    hasher.update(category.encode())
    hasher.update(b"|")
    hasher.update(garment_photo_type.encode())
    hasher.update(b"|")
    hasher.update(str(requested_num_timesteps).encode())
    hasher.update(b"|")
    hasher.update(str(seed).encode())
    hasher.update(b"|")
    hasher.update(hashlib.sha256(person_bytes).digest())
    hasher.update(hashlib.sha256(garment_bytes).digest())
    return hasher.hexdigest()


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

    def find_idempotent_job(
        self,
        *,
        idempotency_scope: str,
        idempotency_key: str,
        category: str,
        garment_photo_type: str,
        requested_num_timesteps: int,
        seed: int,
        person_bytes: bytes,
        garment_bytes: bytes,
    ) -> Optional[Job]:
        """Cheap, read-only pre-check for POST /api/try-on's Idempotency-Key
        handling, called *before* the quota check -- so a genuine retry of
        an already-successful (or already-in-flight) request is never
        incorrectly blocked by a quota limit that request itself already
        counts against. Returns the existing job on a match, None if this
        key hasn't been used yet, or raises 409 if it *has* been used but
        for a materially different request (see _idempotency_fingerprint).

        This is a courtesy fast path only, with its own (harmless) TOCTOU
        gap: true correctness under genuinely concurrent duplicate
        requests comes from start_job_idempotent/JobStore.create_idempotent
        below, not from this method.
        """
        job = self.job_store.get_by_idempotency_key(idempotency_scope, idempotency_key)
        if job is None:
            return None
        fingerprint = _idempotency_fingerprint(
            category, garment_photo_type, requested_num_timesteps, seed, person_bytes, garment_bytes
        )
        if job.idempotency_fingerprint != fingerprint:
            raise UserFacingError(IDEMPOTENCY_CONFLICT_MESSAGE, status_code=409)
        return job

    def start_job_idempotent(
        self,
        person_image: Image.Image,
        garment_image: Image.Image,
        category: GarmentCategory,
        num_timesteps: int,
        guidance_scale: float,
        seed: int,
        idempotency_scope: str,
        idempotency_key: str,
        requested_num_timesteps: int,
        person_bytes: bytes,
        garment_bytes: bytes,
        user_id: Optional[int] = None,
        client_ip: Optional[str] = None,
        garment_photo_type: GarmentPhotoType = "flat-lay",
    ) -> Tuple[Job, bool]:
        """Same contract as start_job, but deduplicates by
        (idempotency_scope, idempotency_key): a concurrent or retried
        submission of the *same* request (per _idempotency_fingerprint)
        reuses the original job instead of starting a second, expensive AI
        generation; the same key used for a materially different request
        is rejected with 409 rather than silently reused.

        Returns (job, created) -- created is False when an existing job
        was reused, so the caller knows not to schedule run_job() again
        (avoiding it is the entire point). Atomicity under truly
        concurrent duplicate requests comes from
        JobStore.create_idempotent, not from this method or from
        find_idempotent_job above.
        """
        fingerprint = _idempotency_fingerprint(
            category, garment_photo_type, requested_num_timesteps, seed, person_bytes, garment_bytes
        )
        job, created = self.job_store.create_idempotent(
            idempotency_scope=idempotency_scope,
            idempotency_key=idempotency_key,
            idempotency_fingerprint=fingerprint,
            user_id=user_id,
            category=category,
            num_timesteps=num_timesteps,
            guidance_scale=guidance_scale,
            seed=seed,
            client_ip=client_ip,
            garment_photo_type=garment_photo_type,
        )
        if not created:
            if job.idempotency_fingerprint != fingerprint:
                raise UserFacingError(IDEMPOTENCY_CONFLICT_MESSAGE, status_code=409)
            return job, False
        self.storage.save_temp_upload(job.id, PERSON_FILENAME, _to_png_bytes(person_image))
        self.storage.save_temp_upload(job.id, GARMENT_FILENAME, _to_png_bytes(garment_image))
        return job, True

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

    def get_job_for_viewer(self, job_id: str, viewer_user_id: Optional[int]) -> Job:
        """Authorization gate for GET /api/try-on/{job_id} and .../result:
        a job_id is an unguessable uuid4 (122 bits), which was previously
        the *only* protection on these endpoints -- anyone who learned the
        id (browser history, a referrer header, a shared link, a server
        log) could view that job's status and result image. Now:
          - An anonymous job (user_id is None) remains viewable by anyone
            holding the id, unchanged -- there is no account to restrict
            access to, and the brief requires the core flow to keep
            working without one (an anonymous submitter has no token to
            prove "ownership" with beyond the id itself, which is exactly
            today's accepted mechanism for anonymous jobs specifically).
          - A job created while signed in is only viewable by that same
            user. Same 404-then-403 shape as save_job above (not a new
            convention): 404 if the job doesn't exist at all, 403 if it
            exists but belongs to someone else -- including an
            unauthenticated caller, treated the same as a mismatched
            user_id. The 403 message never reveals whose job it is or any
            of its content.
        """
        job = self.job_store.get(job_id)
        if job is None:
            raise UserFacingError("We couldn't find that try-on job. It may have expired.", status_code=404)
        if job.user_id is not None and job.user_id != viewer_user_id:
            raise UserFacingError("You can only view your own try-on jobs.", status_code=403)
        return job

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
