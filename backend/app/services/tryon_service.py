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
from ..providers.base import (
    GarmentCategory,
    GarmentPhotoType,
    InferenceTimeoutError,
    ProviderBusyError,
    TryOnRequest,
    VirtualTryOnProvider,
)
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
        """The actual (slow) work. Runs in a background thread.

        Claims the job via an atomic PENDING -> PROCESSING transition
        (try_transition_status), not an unconditional update: this is
        what makes cancel_job's own PENDING -> CANCELLED transition
        race-safe against this method starting to run at the same moment
        -- exactly one of the two can ever win a given job, at the
        database level, not by luck of scheduling order. For every job
        created normally, nothing else touches its status before this
        runs, so the claim always succeeds; it only ever fails here when
        cancel_job won the race first, in which case there is nothing
        left to do but clean up and return -- the job is meant to stay
        CANCELLED, not be silently overwritten back to PROCESSING/FAILED.
        """
        job = self.job_store.get(job_id)
        if job is None:
            logger.error("run_job called for unknown job_id=%s", job_id)
            return

        claimed = self.job_store.try_transition_status(
            job_id, expected=JobStatus.PENDING, new=JobStatus.PROCESSING
        )
        if not claimed:
            current = self.job_store.get(job_id)
            if current is not None and current.status != JobStatus.CANCELLED:
                # Should not happen in normal operation -- nothing else
                # ever writes a job's status before run_job does, so a
                # non-cancelled claim failure means something unexpected
                # raced this call. Logged, not raised: there is no caller
                # here to propagate an error to (this runs as a fire-and-
                # forget background task), and the job's own status
                # already reflects whatever that other writer set.
                logger.warning(
                    "run_job could not claim job %s for processing (status is %s, not pending) -- "
                    "leaving it as-is.",
                    job_id,
                    current.status.value,
                )
            self.storage.cleanup_temp(job_id)
            return

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
        except (ProviderBusyError, InferenceTimeoutError) as e:
            # Both carry their own already-safe, already-clear message
            # (see providers/base.py) -- an operational "couldn't run this
            # right now" condition, not a bug, so it's surfaced directly
            # instead of being flattened into the fully generic message
            # below, and logged at warning level (no stack trace needed
            # for an expected condition).
            logger.warning("Try-on job %s could not run: %s", job_id, e)
            self.job_store.update_status(job_id, JobStatus.FAILED, error=str(e))
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

    def cancel_job(self, job_id: str, viewer_user_id: Optional[int]) -> Job:
        """Cancels a job that is still PENDING -- the narrow window
        between job creation and run_job's background task claiming it
        (see run_job's own docstring for the atomic transition both share).

        Deliberately does NOT attempt to cancel a job that's already
        PROCESSING: by that point run_job has already handed it to the
        provider, which may genuinely be running the model, or may be
        blocked waiting for the provider's single lock to free up (see
        providers/selfhosted.py) -- either way, this architecture has no
        safe way to stop it (no forceful thread termination, no separate
        killable process), so cancellation is refused with a clear,
        honest 409 rather than pretending to have stopped something it
        didn't. The window this *can* cancel is real but typically short:
        run_job normally starts running within milliseconds of job
        creation, so this mostly matters when many jobs are queued (the
        background-task thread pool itself is saturated) or when a
        cancel request lands in a very tight race with submission.

        Same ownership rule as get_job_for_viewer/save_job (404 if the job
        doesn't exist, 403 if it belongs to someone else, an anonymous
        job cancellable by anyone holding its id) -- reused, not
        reinvented, so cancellation follows the exact same access model
        as viewing already does.
        """
        job = self.job_store.get(job_id)
        if job is None:
            raise UserFacingError("We couldn't find that try-on job. It may have expired.", status_code=404)
        if job.user_id is not None and job.user_id != viewer_user_id:
            raise UserFacingError("You can only cancel your own try-on jobs.", status_code=403)

        cancelled = self.job_store.try_transition_status(
            job_id, expected=JobStatus.PENDING, new=JobStatus.CANCELLED
        )
        if not cancelled:
            current = self.job_store.get(job_id) or job
            raise UserFacingError(
                f"This job can no longer be cancelled (status: {current.status.value}). "
                "Generation may already be in progress.",
                status_code=409,
            )

        # Safe even if run_job's own finally-block also calls this for the
        # same job_id (e.g. a race where run_job's claim failed right
        # after this committed) -- cleanup_temp is idempotent and safe
        # against an already-missing path.
        self.storage.cleanup_temp(job_id)
        job.status = JobStatus.CANCELLED
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
