"""Storage abstraction (see docs/ARCHITECTURE.md — "Storage abstraction").

All file I/O for uploaded/generated images goes through this interface so
local disk (now) can become object storage (production) later without
touching business logic. Two lifecycles, matching the privacy requirement:

- temp/: person & garment uploads used only for processing. Always deleted
  once a job finishes (success or failure) — see cleanup_temp().
- results/: the generated try-on image, kept so the user can view/download
  it for the current session. Not yet "saved" in the account/permanent sense
  (that's a Milestone 6 concern) — just not wiped mid-request.
"""

import shutil
import uuid
from abc import ABC, abstractmethod
from pathlib import Path

from PIL import Image


class StorageService(ABC):
    @abstractmethod
    def save_temp_upload(self, job_id: str, name: str, data: bytes) -> Path: ...

    @abstractmethod
    def save_result(self, job_id: str, image: Image.Image) -> Path: ...

    @abstractmethod
    def get_result_path(self, job_id: str) -> Path | None: ...

    @abstractmethod
    def cleanup_temp(self, job_id: str) -> None: ...


class LocalStorageService(StorageService):
    def __init__(self, storage_dir: str):
        self.root = Path(storage_dir)
        self.tmp_dir = self.root / "tmp"
        self.results_dir = self.root / "results"
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.results_dir.mkdir(parents=True, exist_ok=True)

    def save_temp_upload(self, job_id: str, name: str, data: bytes) -> Path:
        job_dir = self.tmp_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        # `name` is our own fixed label ("person"/"garment"), never a user-supplied
        # filename — no path-traversal surface here.
        safe_name = f"{uuid.uuid4().hex}_{name}"
        path = job_dir / safe_name
        path.write_bytes(data)
        return path

    def save_result(self, job_id: str, image: Image.Image) -> Path:
        path = self.results_dir / f"{job_id}.png"
        image.save(path, format="PNG")
        return path

    def get_result_path(self, job_id: str) -> Path | None:
        path = self.results_dir / f"{job_id}.png"
        return path if path.exists() else None

    def cleanup_temp(self, job_id: str) -> None:
        job_dir = self.tmp_dir / job_id
        shutil.rmtree(job_dir, ignore_errors=True)
