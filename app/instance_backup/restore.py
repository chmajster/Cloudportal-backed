from __future__ import annotations

import json
import os
import shutil
import uuid
from datetime import timedelta
from pathlib import Path

from alembic import command
from alembic.config import Config
from rq import Queue
from rq.serializers import JSONSerializer
from sqlalchemy import select, text

from app.config import settings
from app.database import engine, session
from app.instance_backup.archive import extract_archive, sha256_file
from app.instance_backup.crypto import (
    install_local_key,
    restore_previous_local_key,
    validate_secret_compatibility,
)
from app.instance_backup.database import restore_database
from app.instance_backup.models import InstanceBackup, utcnow
from app.instance_backup.paths import generated_dir, restore_status_dir, staging_dir
from app.instance_backup.service import (
    create_snapshot_archive,
    migration_plan,
    verify_record,
)
from app.instance_backup.validation import validate_restore_preflight
from app.models import Audit, Credential, Token, User
from app.security.core import decrypt_secret, encryption_key, redis_client


RESTORE_PROGRESS = {
    "queued": 0,
    "preflight": 5,
    "safety_backup": 15,
    "extract": 28,
    "database_restore": 52,
    "secret_restore": 66,
    "migrations": 78,
    "session_continuity": 86,
    "health": 95,
    "completed": 100,
    "rollback": 90,
    "failed": 100,
}

RESTORE_LABELS = {
    "queued": "Oczekiwanie",
    "preflight": "Preflight backupu",
    "safety_backup": "Backup bezpieczeństwa bieżącej instancji",
    "extract": "Przygotowanie danych do odtworzenia",
    "database_restore": "Przywracanie PostgreSQL",
    "secret_restore": "Przywracanie materiału kryptograficznego",
    "migrations": "Migracje bazy danych",
    "session_continuity": "Przywracanie sesji administratora",
    "health": "Walidacja stanu aplikacji",
    "completed": "Migracja zakończona",
    "rollback": "Rollback bieżącej instancji",
    "failed": "Migracja nie powiodła się",
}


def _status_path(restore_uuid: str) -> Path:
    return restore_status_dir() / f"{restore_uuid}.json"


def write_restore_status(
    restore_uuid: str,
    *,
    backup_id: int,
    status: str,
    stage: str,
    message: str | None = None,
    rollback: str | None = None,
    reauthentication_required: bool = False,
) -> dict:
    payload = {
        "restore_uuid": restore_uuid,
        "backup_id": backup_id,
        "status": status,
        "stage_code": stage,
        "stage": RESTORE_LABELS.get(stage, stage),
        "progress": RESTORE_PROGRESS.get(stage, 0),
        "message": message,
        "rollback": rollback,
        "reauthentication_required": reauthentication_required,
        "updated_at": utcnow().isoformat() + "Z",
    }
    target = _status_path(restore_uuid)
    temporary = target.with_name(target.name + ".new")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, target)
    os.chmod(target, 0o600)
    return payload


def read_restore_status(restore_uuid: str) -> dict | None:
    if not restore_uuid or len(restore_uuid) > 64:
        return None
    path = _status_path(restore_uuid)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def restore_plan(row: InstanceBackup) -> dict:
    verified = verify_record(row)
    manifest = validate_restore_preflight(verified["path"])
    return migration_plan(manifest)


def _session_snapshot(token_id: int | None) -> dict | None:
    if not token_id:
        return None
    with session() as db:
        actor = db.get(Token, token_id)
        if actor is None:
            return None
        username = actor.user.username
        if actor.kind != "session":
            tokens = [actor]
        else:
            tokens = db.scalars(
                select(Token).where(
                    Token.family == actor.family,
                    Token.revoked_at.is_(None),
                )
            ).all()
        return {
            "username": username,
            "tokens": [
                {
                    "name": item.name,
                    "token_hash": item.token_hash,
                    "token_prefix": item.token_prefix,
                    "scopes": list(item.scopes or []),
                    "kind": item.kind,
                    "family": item.family,
                    "created_at": item.created_at,
                    "expires_at": item.expires_at,
                    "last_used_at": item.last_used_at,
                }
                for item in tokens
                if item.revoked_at is None
            ],
        }


def _restore_session_snapshot(snapshot: dict | None) -> tuple[bool, int | None]:
    if not snapshot:
        return False, None
    with session() as db:
        user = db.scalar(select(User).where(User.username == snapshot["username"]))
        if user is None or not user.is_active or user.is_locked:
            return False, None
        for item in snapshot["tokens"]:
            existing = db.scalar(select(Token).where(Token.token_hash == item["token_hash"]))
            if existing is not None:
                continue
            db.add(Token(
                name=item["name"],
                token_hash=item["token_hash"],
                token_prefix=item["token_prefix"],
                user_id=user.id,
                scopes=item["scopes"],
                kind=item["kind"],
                family=item["family"],
                created_at=item["created_at"],
                expires_at=item["expires_at"],
                last_used_at=item["last_used_at"],
            ))
        db.commit()
        return True, user.id


def _upgrade_database() -> None:
    engine().dispose()
    command.upgrade(Config(str(settings().source_dir / "alembic.ini")), "head")
    engine().dispose()


def _health_validation() -> None:
    if settings().secret_backend == "local":
        encryption_key()
    with session() as db:
        db.execute(text("SELECT 1"))
        revision = db.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        if not revision:
            raise RuntimeError("Alembic revision is missing after restore")
        credential = db.scalar(select(Credential).limit(1))
        if credential is not None:
            decrypt_secret(credential)
    if not redis_client().ping():
        raise RuntimeError("Redis health validation failed")


def _record_archive(path: Path, manifest: dict, *, origin: str, filename: str, created_by: int | None = None) -> None:
    app = manifest.get("application") or {}
    source = manifest.get("source") or {}
    with session() as db:
        if db.scalar(select(InstanceBackup.id).where(InstanceBackup.backup_uuid == manifest["backup_uuid"])):
            return
        db.add(InstanceBackup(
            backup_uuid=manifest["backup_uuid"],
            origin=origin,
            status="ready" if origin == "safety" else "validated",
            stage="ready" if origin == "safety" else "validated",
            progress=100,
            filename=filename,
            file_path=str(path),
            size_bytes=path.stat().st_size,
            sha256=sha256_file(path),
            application_version=app.get("version"),
            git_commit=app.get("commit"),
            alembic_revision=app.get("alembic_revision"),
            install_mode=source.get("install_mode"),
            secret_backend=(manifest.get("secret") or {}).get("backend"),
            source_hostname=source.get("hostname"),
            manifest=manifest,
            expires_at=utcnow() + timedelta(hours=settings().instance_backup_download_retention_hours),
            created_by=created_by,
            source="Restore",
            request_id=str(uuid.uuid4()),
        ))
        db.commit()


def _audit_restore(action: str, restore_uuid: str, backup_uuid: str, *, user_id: int | None, result: str) -> None:
    try:
        with session() as db:
            db.add(Audit(
                user_id=user_id,
                token_id=None,
                ip="",
                source="Cloudportal-backed",
                action=action,
                resource="instance_backups",
                resource_id=backup_uuid,
                result=result,
                request_id=restore_uuid,
            ))
            db.commit()
    except Exception:
        # Restore outcome is also persisted in a 0600 status file. Audit must not
        # turn a successful database recovery into a failed restore.
        pass


def execute_restore(backup_id: int, restore_uuid: str, safety_backup: bool = True) -> None:
    source_path: Path | None = None
    source_manifest: dict | None = None
    safety_path: Path | None = None
    safety_manifest: dict | None = None
    previous_key: bytes | None = None
    extracted: Path | None = None
    rollback_extracted: Path | None = None
    destructive_started = False
    actor_snapshot = None
    restored_user_id = None
    backup_uuid = ""
    source_filename = ""
    try:
        write_restore_status(restore_uuid, backup_id=backup_id, status="running", stage="preflight")
        with session() as db:
            row = db.get(InstanceBackup, backup_id)
            if row is None:
                raise RuntimeError("Uploaded backup no longer exists")
            verified = verify_record(row)
            source_path = verified["path"]
            source_manifest = validate_restore_preflight(source_path)
            validate_secret_compatibility(source_manifest)
            actor_snapshot = _session_snapshot(row.token_id)
            backup_uuid = str(source_manifest.get("backup_uuid") or row.backup_uuid)
            source_filename = row.filename

        if safety_backup:
            write_restore_status(restore_uuid, backup_id=backup_id, status="running", stage="safety_backup")
            safety_uuid = str(uuid.uuid4())
            safety_path = generated_dir() / f"{safety_uuid}.cpb"
            safety_manifest, _, _ = create_snapshot_archive(safety_path, safety_uuid)

        write_restore_status(restore_uuid, backup_id=backup_id, status="running", stage="extract")
        extracted = staging_dir() / f"restore-{restore_uuid}"
        if extracted.exists():
            shutil.rmtree(extracted)
        extract_archive(source_path, extracted)

        write_restore_status(restore_uuid, backup_id=backup_id, status="running", stage="database_restore")
        destructive_started = True
        restore_database(extracted / "database.dump")

        write_restore_status(restore_uuid, backup_id=backup_id, status="running", stage="secret_restore")
        previous_key = install_local_key(extracted)

        write_restore_status(restore_uuid, backup_id=backup_id, status="running", stage="migrations")
        _upgrade_database()

        write_restore_status(restore_uuid, backup_id=backup_id, status="running", stage="session_continuity")
        session_restored, restored_user_id = _restore_session_snapshot(actor_snapshot)

        write_restore_status(restore_uuid, backup_id=backup_id, status="running", stage="health")
        _health_validation()

        # The instance_backups table data is intentionally excluded from database.dump.
        # Re-register only the archive that was uploaded and the safety archive made locally.
        if source_path is not None and source_manifest is not None and source_path.is_file():
            _record_archive(source_path, source_manifest, origin="uploaded", filename=source_filename, created_by=restored_user_id)
        if safety_path is not None and safety_manifest is not None and safety_path.is_file():
            _record_archive(
                safety_path,
                safety_manifest,
                origin="safety",
                filename="cloudportal-safety-" + restore_uuid[:8] + ".cpb",
                created_by=restored_user_id,
            )

        _audit_restore(
            "instance_restore.completed",
            restore_uuid,
            backup_uuid,
            user_id=restored_user_id,
            result="success",
        )
        write_restore_status(
            restore_uuid,
            backup_id=backup_id,
            status="completed",
            stage="completed",
            message="Restore, migracje i health validation zakończone poprawnie.",
            reauthentication_required=not session_restored,
        )
    except Exception as exc:
        rollback_state = "not-required"
        if destructive_started and safety_path is not None and safety_path.is_file():
            rollback_state = "running"
            try:
                write_restore_status(
                    restore_uuid,
                    backup_id=backup_id,
                    status="running",
                    stage="rollback",
                    message="Restore nie powiódł się; przywracany jest safety backup.",
                    rollback=rollback_state,
                )
                rollback_extracted = staging_dir() / f"rollback-{restore_uuid}"
                if rollback_extracted.exists():
                    shutil.rmtree(rollback_extracted)
                extract_archive(safety_path, rollback_extracted)
                restore_database(rollback_extracted / "database.dump")
                restore_previous_local_key(previous_key)
                _upgrade_database()
                _health_validation()
                rollback_state = "completed"
            except Exception:
                rollback_state = "failed"
        _audit_restore(
            "instance_restore.failed",
            restore_uuid,
            backup_uuid or str(backup_id),
            user_id=None,
            result="failed",
        )
        write_restore_status(
            restore_uuid,
            backup_id=backup_id,
            status="failed",
            stage="failed",
            message=str(exc)[:500],
            rollback=rollback_state,
            reauthentication_required=True,
        )
    finally:
        if extracted is not None:
            shutil.rmtree(extracted, ignore_errors=True)
        if rollback_extracted is not None:
            shutil.rmtree(rollback_extracted, ignore_errors=True)


def enqueue_restore(backup_id: int, restore_uuid: str, safety_backup: bool) -> None:
    queue = Queue("cloudportal", connection=redis_client(), serializer=JSONSerializer)
    queue.enqueue(
        "app.instance_backup.restore.execute_restore",
        backup_id,
        restore_uuid,
        safety_backup,
        job_id=f"instance-restore:{restore_uuid}",
        job_timeout=max(settings().execution_timeout, 7200),
        result_ttl=86400,
        failure_ttl=86400,
    )
