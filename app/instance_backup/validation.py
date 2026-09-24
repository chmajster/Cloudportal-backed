from __future__ import annotations

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.config import settings
from app.instance_backup.archive import inspect_archive
from app.instance_backup.config import FORMAT_VERSION
from app.instance_backup.crypto import validate_secret_compatibility


def _validate_application_version(manifest: dict) -> None:
    app = manifest.get("application") or {}
    if app.get("name") != "Cloudportal-backed" or app.get("api_version") not in {None, "v1"}:
        raise ValueError("Backup belongs to an unsupported application/API version")
    for field in ("version", "commit", "alembic_revision"):
        value = app.get(field)
        if not isinstance(value, str) or not value.strip() or len(value) > 128:
            raise ValueError(f"Backup application metadata {field} is invalid")

    source_revision = app["alembic_revision"]
    if source_revision == "unknown":
        raise ValueError("Backup has no verifiable Alembic revision")
    scripts = ScriptDirectory.from_config(Config(str(settings().source_dir / "alembic.ini")))
    known_revisions = {revision.revision for revision in scripts.walk_revisions()}
    if source_revision not in known_revisions:
        raise ValueError(
            "Backup database revision is newer than or unknown to this target version"
        )


def validate_uploaded_backup(path):
    manifest = inspect_archive(path)
    if manifest.get("format_version") != FORMAT_VERSION:
        raise ValueError("Backup format is not supported by this Cloudportal version")
    _validate_application_version(manifest)
    return manifest


def validate_restore_preflight(path):
    manifest = validate_uploaded_backup(path)
    validate_secret_compatibility(manifest)
    return manifest
