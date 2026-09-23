from __future__ import annotations

import os
import re
from pathlib import Path

from app.config import settings

_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


def _secure_dir(path: Path) -> Path:
    if path.is_symlink():
        raise RuntimeError(f"Refusing symlinked instance-backup directory: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    return path


def root_dir() -> Path:
    return _secure_dir(settings().data_dir / "instance-backups")


def generated_dir() -> Path:
    return _secure_dir(root_dir() / "generated")


def uploaded_dir() -> Path:
    return _secure_dir(root_dir() / "uploaded")


def staging_dir() -> Path:
    return _secure_dir(root_dir() / "staging")


def restore_status_dir() -> Path:
    return _secure_dir(root_dir() / "restore-status")


def sanitize_filename(value: str, *, fallback: str = "cloudportal-backup.cpb") -> str:
    name = Path(str(value or "")).name.strip().replace("\\", "_")
    name = _SAFE_FILENAME.sub("-", name).strip(".-_")
    if not name:
        name = fallback
    if not name.lower().endswith(".cpb"):
        name += ".cpb"
    return name[:240]


def chmod_file(path: Path) -> None:
    os.chmod(path, 0o600)


def secure_open_new(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    return os.fdopen(fd, "wb")
