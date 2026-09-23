from __future__ import annotations

from app.instance_backup.archive import inspect_archive
from app.instance_backup.config import FORMAT_VERSION
from app.instance_backup.crypto import validate_secret_compatibility


def validate_uploaded_backup(path):
    manifest = inspect_archive(path)
    if manifest.get("format_version") != FORMAT_VERSION:
        raise ValueError("Backup format is not supported by this Cloudportal version")
    app = manifest.get("application") or {}
    if app.get("api_version") not in {None, "v1"}:
        raise ValueError("Backup application API version is not supported")
    return manifest


def validate_restore_preflight(path):
    manifest = validate_uploaded_backup(path)
    validate_secret_compatibility(manifest)
    return manifest
