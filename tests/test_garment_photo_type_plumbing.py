"""Tests for the garment_photo_type plumbing added ahead of model-worn
garment support (see docs/AI_MODEL_LICENSE.md's model-worn investigation).

This is plumbing only -- backend/app/api/tryon.py still rejects anything
but "flat-lay" (tests/test_tryon_api.py's test_rejects_unsupported_
garment_photo_type, unchanged by this work, proves that gate still holds).
These tests prove the field round-trips correctly through every layer
below that gate -- TryOnRequest, Job/JobStore (both implementations),
JobRecord, and SelfHostedVTONProvider -- without needing "model" to ever
reach the API, without loading the real ~2GB model, and without running
any diffusion generation (SelfHostedVTONProvider's real fashn_vton.
TryOnPipeline is replaced with a fake that just records its call kwargs).
"""

import pytest
from PIL import Image
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from backend.app.db import engine, get_session
from backend.app.providers.base import TryOnRequest, TryOnResult
from backend.app.services.db_job_store import DbJobStore
from backend.app.services.job_store import InMemoryJobStore, Job


def _db_reachable() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False


# --- Job dataclass + InMemoryJobStore: no database needed -------------------


def test_job_defaults_to_flat_lay_when_not_specified():
    job = Job(id="x", user_id=None, category="tops", num_timesteps=30, guidance_scale=1.5, seed=42)
    assert job.garment_photo_type == "flat-lay"


def test_in_memory_job_store_round_trips_garment_photo_type():
    store = InMemoryJobStore()
    job = store.create(
        user_id=None,
        category="tops",
        num_timesteps=30,
        guidance_scale=1.5,
        seed=42,
        garment_photo_type="model",
    )
    assert job.garment_photo_type == "model"
    fetched = store.get(job.id)
    assert fetched.garment_photo_type == "model"


def test_in_memory_job_store_defaults_to_flat_lay():
    store = InMemoryJobStore()
    job = store.create(user_id=None, category="tops", num_timesteps=30, guidance_scale=1.5, seed=42)
    assert job.garment_photo_type == "flat-lay"


# --- TryOnRequest: plain dataclass field -------------------------------------


def test_tryonrequest_carries_garment_photo_type():
    img = Image.new("RGB", (8, 8))
    request = TryOnRequest(
        person_image=img,
        garment_image=img,
        category="tops",
        garment_photo_type="model",
        num_timesteps=5,
        guidance_scale=1.5,
        seed=42,
    )
    assert request.garment_photo_type == "model"


# --- DbJobStore / JobRecord: real database round-trip -----------------------

pytestmark_db = pytest.mark.skipif(
    not _db_reachable(),
    reason="Postgres isn't reachable at settings.database_url — see docs/DEVELOPMENT.md",
)


@pytest.fixture
def cleanup_job():
    from backend.app.db import JobRecord

    created_ids = []
    yield created_ids
    if created_ids:
        with get_session() as session:
            session.query(JobRecord).filter(JobRecord.id.in_(created_ids)).delete(synchronize_session=False)


@pytestmark_db
def test_db_job_store_round_trips_garment_photo_type(cleanup_job):
    store = DbJobStore()
    job = store.create(
        user_id=None,
        category="tops",
        num_timesteps=5,
        guidance_scale=1.5,
        seed=42,
        garment_photo_type="model",
    )
    cleanup_job.append(job.id)
    assert job.garment_photo_type == "model"
    fetched = store.get(job.id)
    assert fetched.garment_photo_type == "model"


@pytestmark_db
def test_db_job_store_defaults_to_flat_lay_when_not_specified(cleanup_job):
    store = DbJobStore()
    job = store.create(user_id=None, category="tops", num_timesteps=5, guidance_scale=1.5, seed=42)
    cleanup_job.append(job.id)
    assert job.garment_photo_type == "flat-lay"


# --- SelfHostedVTONProvider: real class, faked pipeline (no model load) -----


class _FakePipelineOutput:
    def __init__(self):
        self.images = [Image.new("RGB", (8, 8))]


class _FakePipeline:
    """Stands in for fashn_vton.TryOnPipeline -- records the exact kwargs
    it's called with, so the test can assert on what SelfHostedVTONProvider
    actually passes through, without loading the real model or running any
    diffusion sampling."""

    def __init__(self, weights_dir, device):
        self.last_call_kwargs = None

    def __call__(self, **kwargs):
        self.last_call_kwargs = kwargs
        return _FakePipelineOutput()


def _build_provider_with_fake_pipeline(monkeypatch):
    from backend.app.providers import selfhosted as selfhosted_module

    monkeypatch.setattr(selfhosted_module, "TryOnPipeline", _FakePipeline)
    return selfhosted_module.SelfHostedVTONProvider(weights_dir="unused", device="cpu")


def test_selfhosted_provider_passes_model_garment_photo_type_through(monkeypatch):
    provider = _build_provider_with_fake_pipeline(monkeypatch)
    img = Image.new("RGB", (8, 8))
    request = TryOnRequest(
        person_image=img,
        garment_image=img,
        category="tops",
        garment_photo_type="model",
        num_timesteps=5,
        guidance_scale=1.5,
        seed=42,
    )
    result = provider.generate(request)
    assert isinstance(result, TryOnResult)
    assert provider._pipeline.last_call_kwargs["garment_photo_type"] == "model"
    # segmentation_free stays unconditionally False (unrelated to this
    # plumbing, already validated separately) -- confirms this change
    # didn't disturb it.
    assert provider._pipeline.last_call_kwargs["segmentation_free"] is False


def test_selfhosted_provider_passes_flat_lay_garment_photo_type_through(monkeypatch):
    provider = _build_provider_with_fake_pipeline(monkeypatch)
    img = Image.new("RGB", (8, 8))
    request = TryOnRequest(
        person_image=img,
        garment_image=img,
        category="tops",
        garment_photo_type="flat-lay",
        num_timesteps=5,
        guidance_scale=1.5,
        seed=42,
    )
    provider.generate(request)
    assert provider._pipeline.last_call_kwargs["garment_photo_type"] == "flat-lay"
