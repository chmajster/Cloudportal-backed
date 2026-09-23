from __future__ import annotations

import json
import os
import shutil
import socket
import uuid
from datetime import datetime, timedelta

from rq import Queue
from rq.serializers import JSONSerializer
from sqlalchemy import select

from app.config import settings
from app.database import session
from app.instance_backup.archive import build_archive, inspect_archive, sha256_file
from app.instance_backup.config import DOWNLOADABLE_STATUSES, STAGE_LABELS, STAGE_PROGRESS
from app.instance_backup.crypto import collect_secret_material, secret_descriptor
from app.instance_backup.database import current_alembic_revision, database_counts, dump_database
from app.instance_backup.models import InstanceBackup, utcnow
from app.instance_backup.paths import generated_dir, root_dir, staging_dir, uploaded_dir
from app.security.core import redis_client
from app.version import build_commit, build_version


def installation_mode() -> str:
    configured = os.environ.get("CP_INSTALL_MODE", "").strip().lower()
    if configured in {"docker", "systemd"}:
        return configured
    return "docker" if os.path.exists("/.dockerenv") else "systemd"


def current_instance_metadata() -> dict:
    public_host = os.environ.get("CP_PUBLIC_HOST", "").strip()
    https_port = os.environ.get("CP_HTTPS_PORT", "").strip()
    return {
        "application_version": build_version(),
        "git_commit": build_commit(),
        "alembic_revision": current_alembic_revision(),
        "install_mode": installation_mode(),
        "secret_backend": settings().secret_backend,
        "hostname": socket.gethostname(),
        "public_url": f"https://{public_host}:{https_port}" if public_host and https_port else None,
        "worker_count": settings().worker_count,
    }


def public_status(row: InstanceBackup) -> dict:
    expired = bool(row.expires_at and row.expires_at <= utcnow())
    downloadable = (
        row.status in DOWNLOADABLE_STATUSES
        and not expired
        and bool(row.file_path)
        and bool(row.sha256)
    )
    return {
        "id": row.id,
        "backup_uuid": row.backup_uuid,
        "status": "expired" if expired and row.status in DOWNLOADABLE_STATUSES else row.status,
        "progress": row.progress,
        "stage": STAGE_LABELS.get(row.stage, row.stage),
        "stage_code": row.stage,
        "download_ready": downloadable,
        "filename": row.filename,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "size_bytes": row.size_bytes,
        "sha256": row.sha256,
        "application_version": row.application_version,
        "git_commit": row.git_commit,
        "alembic_revision": row.alembic_revision,
        "install_mode": row.install_mode,
        "secret_backend": row.secret_backend,
        "source_hostname": row.source_hostname,
        "error": row.error,
        "origin": row.origin,
    }


def generated_filename(moment: datetime | None = None) -> str:
    moment = moment or datetime.now()
    return "cloudportal-backup-" + moment.strftime("%Y%m%d-%H%M%S") + ".cpb"


def _write_json(path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def _runtime_snapshot(meta: dict) -> dict:
    return {
        "source": {
            "hostname": meta["hostname"],
            "install_mode": meta["install_mode"],
            "public_url": meta["public_url"],
            "worker_count": meta["worker_count"],
        },
        "application": {
            "version": meta["application_version"],
            "commit": meta["git_commit"],
            "alembic_revision": meta["alembic_revision"],
        },
        "secret_backend": meta["secret_backend"],
    }


_WORKSPACE_EXCLUDED_DIRS = {".terraform"}
_WORKSPACE_EXCLUDED_FILES = {
    ".execution.lock",
    "terraform.tfvars.json",
    ".cloudportal-terraform-init.tmp",
    ".terraform.tfstate.restore",
    ".execution.tfplan.restore",
}


def collect_workspace_material(staging) -> list[str]:
    source_root = settings().data_dir / "workspaces"
    if not source_root.exists():
        return []
    if source_root.is_symlink() or not source_root.is_dir():
        raise RuntimeError("Workspace root must be a real directory")

    destination_root = staging / "workspaces"
    members: list[str] = []
    for root_value, dir_names, file_names in os.walk(source_root, topdown=True, followlinks=False):
        root = Path(root_value)
        kept_dirs = []
        for name in dir_names:
            path = root / name
            if path.is_symlink():
                raise RuntimeError(f"Refusing symlink in workspace tree: {path.relative_to(source_root)}")
            if name in _WORKSPACE_EXCLUDED_DIRS:
                continue
            kept_dirs.append(name)
        dir_names[:] = kept_dirs

        for name in file_names:
            if name in _WORKSPACE_EXCLUDED_FILES:
                continue
            source = root / name
            if source.is_symlink() or not source.is_file():
                raise RuntimeError(f"Refusing non-regular workspace file: {source.relative_to(source_root)}")
            relative = source.relative_to(source_root)
            destination = destination_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(destination.parent, 0o700)
            with source.open("rb") as input_stream, destination.open("xb") as output_stream:
                os.chmod(destination, 0o600)
                shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
                output_stream.flush()
                os.fsync(output_stream.fileno())
            members.append("workspaces/" + relative.as_posix())
    return sorted(members)


def _manifest(backup_uuid: str, meta: dict) -> dict:
    return {
        "format": "cloudportal-instance-backup",
        "format_version": 1,
        "backup_uuid": backup_uuid,
        "created_at": utcnow().isoformat() + "Z",
        "application": {
            "name": "Cloudportal-backed",
            "version": meta["application_version"],
            "commit": meta["git_commit"],
            "api_version": "v1",
            "alembic_revision": meta["alembic_revision"],
        },
        "source": {
            "hostname": meta["hostname"],
            "install_mode": meta["install_mode"],
            "public_url": meta["public_url"],
            "worker_count": meta["worker_count"],
        },
        "secret": secret_descriptor(),
        "counts": database_counts(),
        "local_settings_preserved_on_restore": [
            "hostname",
            "public URL",
            "HTTPS port",
            "worker count",
            "Docker host configuration",
        ],
    }


def create_snapshot_archive(
    output,
    backup_uuid: str,
    *,
    stage_callback=None,
) -> tuple[dict, int, str]:
    work = staging_dir() / backup_uuid
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(mode=0o700)
    os.chmod(work, 0o700)
    try:
        if stage_callback:
            stage_callback("preparing")
        meta = current_instance_metadata()

        if stage_callback:
            stage_callback("database_dump")
        dump_database(work / "database.dump")

        if stage_callback:
            stage_callback("configuration")
        _write_json(work / "configuration" / "runtime.json", _runtime_snapshot(meta))
        workspace_members = collect_workspace_material(work)

        if stage_callback:
            stage_callback("secret_material")
        secret_members = collect_secret_material(work)

        if stage_callback:
            stage_callback("manifest")
        manifest = _manifest(backup_uuid, meta)
        manifest["workspace_files"] = len(workspace_members)
        members = [
            "database.dump",
            "configuration/runtime.json",
            *workspace_members,
            *secret_members,
        ]

        size, archive_sha = build_archive(
            work,
            output,
            manifest,
            members,
            on_stage=stage_callback,
        )
        return manifest, size, archive_sha
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _update_stage(backup_id: int, stage: str, error: str | None = None) -> None:
    with session() as db:
        row = db.get(InstanceBackup, backup_id)
        if row is None:
            return
        row.stage = stage
        row.status = "failed" if stage == "failed" else stage
        row.progress = STAGE_PROGRESS.get(stage, row.progress)
        row.error = error
        db.commit()


def build_backup_job(backup_id: int) -> None:
    with session() as db:
        row = db.get(InstanceBackup, backup_id)
        if row is None or row.status not in {"queued", "preparing"}:
            return
        backup_uuid = row.backup_uuid
        filename = row.filename

    destination = generated_dir() / f"{backup_uuid}.cpb"
    destination.unlink(missing_ok=True)
    try:
        manifest, size, digest = create_snapshot_archive(
            destination,
            backup_uuid,
            stage_callback=lambda stage: _update_stage(backup_id, stage),
        )
        meta = manifest["application"]
        source = manifest["source"]
        with session() as db:
            row = db.get(InstanceBackup, backup_id)
            if row is None:
                destination.unlink(missing_ok=True)
                return
            row.status = "ready"
            row.stage = "ready"
            row.progress = 100
            row.file_path = str(destination)
            row.size_bytes = size
            row.sha256 = digest
            row.application_version = meta.get("version")
            row.git_commit = meta.get("commit")
            row.alembic_revision = meta.get("alembic_revision")
            row.install_mode = source.get("install_mode")
            row.secret_backend = (manifest.get("secret") or {}).get("backend")
            row.source_hostname = source.get("hostname")
            row.manifest = manifest
            row.expires_at = utcnow() + timedelta(hours=settings().instance_backup_download_retention_hours)
            row.error = None
            row.filename = filename
            db.commit()
    except Exception as exc:
        destination.unlink(missing_ok=True)
        _update_stage(backup_id, "failed", str(exc)[:500])


def enqueue_backup(backup_id: int) -> None:
    queue = Queue("cloudportal", connection=redis_client(), serializer=JSONSerializer)
    queue.enqueue(
        "app.instance_backup.service.build_backup_job",
        backup_id,
        job_id=f"instance-backup:{backup_id}:{uuid.uuid4()}",
        job_timeout=max(settings().execution_timeout, 7200),
        result_ttl=86400,
        failure_ttl=86400,
    )


def verify_record(row: InstanceBackup) -> dict:
    if row.status not in DOWNLOADABLE_STATUSES:
        raise RuntimeError("Backup is not ready")
    if row.expires_at and row.expires_at <= utcnow():
        raise RuntimeError("Backup download has expired")
    if not row.file_path:
        raise FileNotFoundError("Backup file is missing")
    path = _safe_record_path(row.file_path)
    if not path.is_file():
        raise FileNotFoundError("Backup file is missing")
    digest = sha256_file(path)
    if not row.sha256 or digest != row.sha256:
        raise RuntimeError("Backup archive checksum does not match its record")
    manifest = inspect_archive(path)
    return {"path": path, "sha256": digest, "manifest": manifest}


def _safe_record_path(value: str):
    from pathlib import Path

    candidate = Path(value)
    root = root_dir().resolve()
    resolved = candidate.resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise RuntimeError("Backup path is outside the managed data directory")
    return resolved


def register_uploaded_backup(
    *,
    path,
    original_filename: str,
    manifest: dict,
    size_bytes: int,
    sha256: str,
    actor,
    request,
) -> InstanceBackup:
    app = manifest.get("application") or {}
    source = manifest.get("source") or {}
    row = InstanceBackup(
        backup_uuid=str(manifest.get("backup_uuid") or uuid.uuid4()),
        origin="uploaded",
        status="validated",
        stage="validated",
        progress=100,
        filename=original_filename,
        file_path=str(path),
        size_bytes=size_bytes,
        sha256=sha256,
        application_version=app.get("version"),
        git_commit=app.get("commit"),
        alembic_revision=app.get("alembic_revision"),
        install_mode=source.get("install_mode"),
        secret_backend=(manifest.get("secret") or {}).get("backend"),
        source_hostname=source.get("hostname"),
        manifest=manifest,
        expires_at=utcnow() + timedelta(hours=settings().instance_backup_download_retention_hours),
        created_by=actor.user_id,
        token_id=actor.id,
        source=getattr(request.state, "source", "API"),
        ip=request.client.host if request.client else "",
        request_id=request.state.request_id,
    )
    with session() as db:
        existing = db.scalar(select(InstanceBackup).where(InstanceBackup.backup_uuid == row.backup_uuid))
        if existing is not None:
            # Keep UUIDs unique even when the same source backup is uploaded repeatedly.
            row.backup_uuid = str(uuid.uuid4())
        db.add(row)
        db.commit()
        db.refresh(row)
        return row


def migration_plan(manifest: dict) -> dict:
    return {
        "source": manifest.get("source") or {},
        "target": current_instance_metadata(),
        "counts": manifest.get("counts") or {},
        "preserved_local_settings": [
            "hostname",
            "public URL",
            "HTTPS port",
            "worker count",
            "Docker host configuration",
        ],
        "warnings": [],
    }


def cleanup_expired_backups(limit: int = 100) -> int:
    removed = 0
    with session() as db:
        rows = db.scalars(
            select(InstanceBackup)
            .where(
                InstanceBackup.file_path.is_not(None),
                InstanceBackup.expires_at.is_not(None),
                InstanceBackup.expires_at <= utcnow(),
                InstanceBackup.status.in_(list(DOWNLOADABLE_STATUSES)),
            )
            .limit(limit)
        ).all()
        for row in rows:
            try:
                path = _safe_record_path(row.file_path)
                path.unlink(missing_ok=True)
            except Exception:
                continue
            row.file_path = None
            row.status = "expired"
            row.stage = "expired"
            row.progress = 100
            removed += 1
        db.commit()
    return removed


def delete_record_file(row: InstanceBackup) -> None:
    if row.file_path:
        _safe_record_path(row.file_path).unlink(missing_ok=True)
    row.file_path = None
    row.status = "deleted"
    row.stage = "deleted"
    row.progress = 100


def uploaded_path(upload_uuid: str):
    return uploaded_dir() / f"{upload_uuid}.cpb"
