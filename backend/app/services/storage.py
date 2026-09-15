"""Storage abstraction (see docs/ARCHITECTURE.md — "Storage abstraction").

All file I/O for uploaded/generated images goes through this interface so
local disk (now) can become object storage (production) later without
touching business logic. Two lifecycles, matching the privacy requirement:

- temp/: person & garment uploads used only for processing. Always deleted
  once a job finishes (success or failure) — see cleanup_temp(). Since
  Milestone 6, job state (and thus the images needed to process it) can
  outlive a single in-process Python object — e.g. a DB-backed job picked
  up after a restart — so temp uploads are written under a deterministic
  name (`{job_id}/{name}`) and read back by name, not kept only in memory.
- results/: the generated try-on image. Deleted after unsaved_result_ttl_hours
  by backend/scripts/cleanup_expired_results.py *unless* the job's `saved`
  flag is set (see db/models.py, JobRecord.saved) — the explicit "Save to my
  account" action a signed-in user takes. Never kept indefinitely by default.
"""

import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from PIL import Image


class StorageService(ABC):
    @abstractmethod
    def save_temp_upload(self, job_id: str, name: str, data: bytes) -> Path: ...

    @abstractmethod
    def load_temp_upload(self, job_id: str, name: str) -> Optional[bytes]: ...

    @abstractmethod
    def save_result(self, job_id: str, image: Image.Image) -> Path: ...

    @abstractmethod
    def get_result_path(self, job_id: str) -> Optional[Path]: ...

    @abstractmethod
    def delete_result(self, job_id: str) -> None: ...

    @abstractmethod
    def cleanup_temp(self, job_id: str) -> None: ...


class LocalStorageService(StorageService):
    def __init__(self, storage_dir: str):
        self.root = Path(storage_dir)
        self.tmp_dir = self.root / "tmp"
        self.results_dir = self.root / "results"
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.results_dir.mkdir(parents=True, exist_ok=True)

    def _temp_path(self, job_id: str, name: str) -> Path:
        # `job_id` and `name` are always our own generated values (a uuid hex
        # job id, and a fixed literal like "person.png") — never user input —
        # so there's no path-traversal surface here.
        return self.tmp_dir / job_id / name

    def save_temp_upload(self, job_id: str, name: str, data: bytes) -> Path:
        path = self._temp_path(job_id, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def load_temp_upload(self, job_id: str, name: str) -> Optional[bytes]:
        path = self._temp_path(job_id, name)
        return path.read_bytes() if path.exists() else None

    def save_result(self, job_id: str, image: Image.Image) -> Path:
        path = self.results_dir / f"{job_id}.png"
        image.save(path, format="PNG")
        return path

    def get_result_path(self, job_id: str) -> Optional[Path]:
        path = self.results_dir / f"{job_id}.png"
        return path if path.exists() else None

    def delete_result(self, job_id: str) -> None:
        path = self.results_dir / f"{job_id}.png"
        path.unlink(missing_ok=True)

    def cleanup_temp(self, job_id: str) -> None:
        job_dir = self.tmp_dir / job_id
        shutil.rmtree(job_dir, ignore_errors=True)
