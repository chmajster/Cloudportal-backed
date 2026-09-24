from __future__ import annotations

import json
import secrets
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse
from python_multipart.multipart import MultipartParser, parse_options_header
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.database import get_db
from app.instance_backup.archive import sha256_file
from app.instance_backup.models import InstanceBackup
from app.instance_backup.paths import root_dir, sanitize_filename, secure_open_new, staging_dir
from app.instance_backup.restore import (
    enqueue_restore,
    read_restore_status,
    restore_plan,
    write_restore_status,
)
from app.instance_backup.schemas import RestoreRequest
from app.instance_backup.service import (
    cleanup_expired_backups,
    current_instance_metadata,
    delete_record_file,
    enqueue_backup,
    generated_filename,
    migration_plan,
    public_status,
    register_uploaded_backup,
    uploaded_path,
    verify_record,
)
from app.instance_backup.validation import validate_uploaded_backup
from app.security.core import audit, digest, redis_client, require

router = APIRouter(prefix="/instance-backups", tags=["instance-backups"])


class UploadTooLarge(Exception):
    pass


DOWNLOAD_TICKET_TTL_SECONDS = 60


def _download_ticket_key(ticket: str) -> str:
    return "cp:instance-backup:download:" + digest(ticket)


def _consume_download_ticket(ticket: str) -> dict:
    if not ticket or len(ticket) > 256:
        raise HTTPException(410, "Download ticket is invalid or expired")
    try:
        raw = redis_client().eval(
            "local v=redis.call('GET',KEYS[1]); "
            "if v then redis.call('DEL',KEYS[1]) end; return v",
            1,
            _download_ticket_key(ticket),
        )
    except Exception:
        raise HTTPException(503, "Download ticket store is unavailable") from None
    if not raw:
        raise HTTPException(410, "Download ticket is invalid or expired")
    try:
        payload = json.loads(raw)
    except Exception:
        raise HTTPException(410, "Download ticket is invalid or expired") from None
    if not isinstance(payload, dict):
        raise HTTPException(410, "Download ticket is invalid or expired")
    return payload


async def _stream_multipart_upload(request: Request, destination):
    raw_content_type = request.headers.get("content-type", "")
    try:
        media_type, parameters = parse_options_header(raw_content_type.encode("latin-1"))
    except Exception:
        raise HTTPException(415, "Expected multipart/form-data") from None
    if media_type != b"multipart/form-data" or not parameters.get(b"boundary"):
        raise HTTPException(415, "Expected multipart/form-data with boundary")

    max_bytes = settings().instance_backup_max_upload_bytes
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > max_bytes + 1024 * 1024:
        raise HTTPException(413, "Backup upload exceeds configured limit")

    headers = {}
    header_field = bytearray()
    header_value = bytearray()
    current_file = None
    current_is_file = False
    file_seen = False
    client_filename = ""
    written = 0

    def on_part_begin():
        nonlocal headers, header_field, header_value, current_is_file
        headers = {}
        header_field = bytearray()
        header_value = bytearray()
        current_is_file = False

    def on_header_field(data, start, end):
        header_field.extend(data[start:end])

    def on_header_value(data, start, end):
        header_value.extend(data[start:end])

    def on_header_end():
        nonlocal header_field, header_value
        headers[bytes(header_field).strip().lower()] = bytes(header_value).strip()
        header_field = bytearray()
        header_value = bytearray()

    def on_headers_finished():
        nonlocal current_file, current_is_file, file_seen, client_filename
        disposition = headers.get(b"content-disposition", b"")
        _, options = parse_options_header(disposition)
        name = options.get(b"name")
        filename = options.get(b"filename")
        if name != b"file" or not filename:
            raise ValueError("Multipart upload must contain only the file field")
        if file_seen or current_file is not None:
            raise ValueError("Only one backup file can be uploaded")
        client_filename = filename.decode("utf-8", "replace")
        if not client_filename.lower().endswith(".cpb"):
            raise ValueError("Only .cpb backup files are accepted")
        current_file = secure_open_new(destination)
        current_is_file = True

    def on_part_data(data, start, end):
        nonlocal written
        if not current_is_file or current_file is None:
            if end > start:
                raise ValueError("Unexpected multipart field")
            return
        chunk = data[start:end]
        written += len(chunk)
        if written > max_bytes:
            raise UploadTooLarge()
        current_file.write(chunk)

    def on_part_end():
        nonlocal current_file, current_is_file, file_seen
        if current_file is not None:
            current_file.flush()
            current_file.close()
            current_file = None
            file_seen = True
        current_is_file = False

    parser = MultipartParser(parameters[b"boundary"], {
        "on_part_begin": on_part_begin,
        "on_header_field": on_header_field,
        "on_header_value": on_header_value,
        "on_header_end": on_header_end,
        "on_headers_finished": on_headers_finished,
        "on_part_data": on_part_data,
        "on_part_end": on_part_end,
    })

    try:
        async for chunk in request.stream():
            parser.write(chunk)
        parser.finalize()
        if current_file is not None:
            current_file.flush()
            current_file.close()
            current_file = None
        if not file_seen or not destination.is_file() or written <= 0:
            raise ValueError("Multipart upload does not contain a backup file")
    except UploadTooLarge:
        if current_file is not None:
            current_file.close()
        destination.unlink(missing_ok=True)
        raise HTTPException(413, "Backup upload exceeds configured limit") from None
    except HTTPException:
        if current_file is not None:
            current_file.close()
        destination.unlink(missing_ok=True)
        raise
    except Exception as exc:
        if current_file is not None:
            current_file.close()
        destination.unlink(missing_ok=True)
        raise HTTPException(422, str(exc)[:300] or "Invalid multipart upload") from None

    return client_filename, written


def _get_backup(db, backup_id: int) -> InstanceBackup:
    row = db.get(InstanceBackup, backup_id)
    if row is None or row.status == "deleted":
        raise HTTPException(404, "Backup not found")
    return row


@router.get("/metadata")
def metadata(actor=Depends(require("instance_backups.read"))):
    meta = current_instance_metadata()
    return {
        **meta,
        "download_retention_hours": settings().instance_backup_download_retention_hours,
        "max_upload_bytes": settings().instance_backup_max_upload_bytes,
    }


@router.get("")
def list_backups(
    actor=Depends(require("instance_backups.read")),
    db=Depends(get_db, scope="function"),
):
    cleanup_expired_backups()
    rows = db.scalars(
        select(InstanceBackup)
        .where(InstanceBackup.status != "deleted")
        .order_by(InstanceBackup.created_at.desc())
        .limit(100)
    ).all()
    return {"items": [public_status(row) for row in rows]}


@router.post("", status_code=202)
def create_backup(
    request: Request,
    actor=Depends(require("instance_backups.create")),
    db=Depends(get_db, scope="function"),
):
    row = InstanceBackup(
        backup_uuid=str(uuid.uuid4()),
        origin="generated",
        status="queued",
        stage="queued",
        progress=0,
        filename=generated_filename(),
        created_by=actor.user_id,
        token_id=actor.id,
        source=getattr(request.state, "source", "API"),
        ip=request.client.host if request.client else "",
        request_id=request.state.request_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    try:
        enqueue_backup(row.id)
    except Exception:
        row.status = "failed"
        row.stage = "failed"
        row.progress = 100
        row.error = "Backup queue is unavailable"
        audit(db, request, "instance_backup.created", "instance_backups", row.id, result="failed")
        db.commit()
        raise HTTPException(503, "Backup queue is unavailable") from None
    audit(db, request, "instance_backup.created", "instance_backups", row.id)
    db.commit()
    return {"id": row.id, "backup_uuid": row.backup_uuid, "status": row.status}


@router.post("/upload", status_code=201)
async def upload_backup(
    request: Request,
    actor=Depends(require("instance_backups.upload")),
    db=Depends(get_db, scope="function"),
):
    upload_uuid = str(uuid.uuid4())
    destination = uploaded_path(upload_uuid)
    try:
        client_filename, size = await _stream_multipart_upload(request, destination)
        manifest = await run_in_threadpool(validate_uploaded_backup, destination)
        digest = await run_in_threadpool(sha256_file, destination)
        row = register_uploaded_backup(
            path=destination,
            original_filename=sanitize_filename(client_filename),
            manifest=manifest,
            size_bytes=size,
            sha256=digest,
            actor=actor,
            request=request,
        )
    except HTTPException:
        destination.unlink(missing_ok=True)
        audit(db, request, "instance_backup.uploaded", "instance_backups", result="failed")
        db.commit()
        raise
    except Exception as exc:
        destination.unlink(missing_ok=True)
        audit(db, request, "instance_backup.uploaded", "instance_backups", result="failed")
        db.commit()
        raise HTTPException(422, str(exc)[:500] or "Backup validation failed") from None

    audit(db, request, "instance_backup.uploaded", "instance_backups", row.id)
    db.commit()
    try:
        plan = await run_in_threadpool(restore_plan, row)
        preflight = {"ok": True, "error": None}
    except Exception as exc:
        plan = migration_plan(manifest)
        plan["warnings"] = [str(exc)[:500]]
        preflight = {"ok": False, "error": str(exc)[:500]}
    return {
        "backup": public_status(row),
        "valid": True,
        "preflight": preflight,
        "manifest": {
            "source": manifest.get("source") or {},
            "application": manifest.get("application") or {},
            "secret": {"backend": (manifest.get("secret") or {}).get("backend")},
            "counts": manifest.get("counts") or {},
            "created_at": manifest.get("created_at"),
        },
        "plan": plan,
    }


@router.get("/restores/{restore_uuid}")
def restore_status(
    restore_uuid: str,
    actor=Depends(require("instance_backups.read")),
):
    payload = read_restore_status(restore_uuid)
    if payload is None:
        raise HTTPException(404, "Restore operation not found")
    return payload


@router.get("/{backup_id}")
def backup_status(
    backup_id: int,
    actor=Depends(require("instance_backups.read")),
    db=Depends(get_db, scope="function"),
):
    return public_status(_get_backup(db, backup_id))


@router.get("/{backup_id}/restore-plan")
def backup_restore_plan(
    backup_id: int,
    actor=Depends(require("instance_backups.restore")),
    db=Depends(get_db, scope="function"),
):
    row = _get_backup(db, backup_id)
    try:
        return restore_plan(row)
    except FileNotFoundError:
        raise HTTPException(404, "Backup file is missing") from None
    except Exception as exc:
        raise HTTPException(422, str(exc)[:500]) from None


@router.post("/{backup_id}/restore", status_code=202)
def start_restore(
    backup_id: int,
    data: RestoreRequest,
    request: Request,
    actor=Depends(require("instance_backups.restore")),
    db=Depends(get_db, scope="function"),
):
    row = _get_backup(db, backup_id)
    try:
        restore_plan(row)
    except FileNotFoundError:
        raise HTTPException(404, "Backup file is missing") from None
    except Exception as exc:
        raise HTTPException(422, str(exc)[:500]) from None

    restore_uuid = str(uuid.uuid4())
    write_restore_status(
        restore_uuid,
        backup_id=backup_id,
        status="queued",
        stage="queued",
        message="Restore oczekuje na worker.",
    )
    audit(db, request, "instance_restore.started", "instance_backups", backup_id)
    db.commit()
    try:
        enqueue_restore(backup_id, restore_uuid, data.safety_backup, actor.id)
    except Exception:
        write_restore_status(
            restore_uuid,
            backup_id=backup_id,
            status="failed",
            stage="failed",
            message="Restore queue is unavailable",
        )
        raise HTTPException(503, "Restore queue is unavailable") from None
    return {"restore_uuid": restore_uuid, "status": "queued", "safety_backup": data.safety_backup}


@router.post("/{backup_id}/verify")
def verify_backup(
    backup_id: int,
    request: Request,
    actor=Depends(require("instance_backups.verify")),
    db=Depends(get_db, scope="function"),
):
    row = _get_backup(db, backup_id)
    try:
        verified = verify_record(row)
    except FileNotFoundError:
        raise HTTPException(404, "Backup file is missing") from None
    except Exception as exc:
        raise HTTPException(409, str(exc)[:500]) from None
    audit(db, request, "instance_backup.verified", "instance_backups", row.id)
    return {
        "id": row.id,
        "valid": True,
        "sha256": verified["sha256"],
        "format_version": verified["manifest"].get("format_version"),
    }


@router.post("/{backup_id}/download-ticket")
def create_download_ticket(
    backup_id: int,
    request: Request,
    actor=Depends(require("instance_backups.download")),
    db=Depends(get_db, scope="function"),
):
    row = _get_backup(db, backup_id)
    state = public_status(row)
    if not state["download_ready"]:
        status = 410 if state["status"] == "expired" else 409
        raise HTTPException(status, "Backup is not ready for download")

    candidate = Path(row.file_path).resolve(strict=False)
    managed_root = root_dir().resolve()
    if not candidate.is_relative_to(managed_root) or not candidate.is_file():
        raise HTTPException(404, "Backup file is missing on this API host")

    ticket = secrets.token_urlsafe(32)
    payload = json.dumps({
        "backup_id": row.id,
        "user_id": actor.user_id,
        "token_id": actor.id,
        "source": getattr(request.state, "source", "Cloudportal-backed"),
    }, separators=(",", ":"))
    try:
        stored = redis_client().set(
            _download_ticket_key(ticket),
            payload,
            ex=DOWNLOAD_TICKET_TTL_SECONDS,
            nx=True,
        )
    except Exception:
        raise HTTPException(503, "Download ticket store is unavailable") from None
    if not stored:
        raise HTTPException(503, "Could not create download ticket")

    return {
        "ticket": ticket,
        "expires_in": DOWNLOAD_TICKET_TTL_SECONDS,
    }


@router.post("/{backup_id}/download-browser")
def download_backup_browser(
    backup_id: int,
    request: Request,
    ticket: str = Form(...),
    db=Depends(get_db, scope="function"),
):
    payload = _consume_download_ticket(ticket)
    try:
        ticket_backup_id = int(payload.get("backup_id"))
        ticket_user_id = int(payload.get("user_id"))
    except (TypeError, ValueError):
        raise HTTPException(410, "Download ticket is invalid or expired") from None
    if ticket_backup_id != backup_id:
        raise HTTPException(410, "Download ticket is invalid or expired")

    row = _get_backup(db, backup_id)
    try:
        verified = verify_record(row)
    except FileNotFoundError:
        raise HTTPException(404, "Backup file is missing") from None
    except Exception as exc:
        message = str(exc)
        status = 410 if "expired" in message.lower() else 409
        raise HTTPException(status, message[:500]) from None

    request.state.source = str(payload.get("source") or "Cloudportal-backed")[:32]
    audit(
        db,
        request,
        "instance_backup.downloaded",
        "instance_backups",
        row.id,
        user_id=ticket_user_id,
    )
    return FileResponse(
        verified["path"],
        media_type="application/octet-stream",
        filename=sanitize_filename(row.filename),
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/{backup_id}/download")
def download_backup(
    backup_id: int,
    request: Request,
    actor=Depends(require("instance_backups.download")),
    db=Depends(get_db, scope="function"),
):
    row = _get_backup(db, backup_id)
    try:
        verified = verify_record(row)
    except FileNotFoundError:
        raise HTTPException(404, "Backup file is missing") from None
    except Exception as exc:
        message = str(exc)
        status = 410 if "expired" in message.lower() else 409
        raise HTTPException(status, message[:500]) from None

    audit(db, request, "instance_backup.downloaded", "instance_backups", row.id)
    return FileResponse(
        verified["path"],
        media_type="application/octet-stream",
        filename=sanitize_filename(row.filename),
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/{backup_id}", status_code=204)
def delete_backup(
    backup_id: int,
    request: Request,
    actor=Depends(require("instance_backups.delete")),
    db=Depends(get_db, scope="function"),
):
    row = _get_backup(db, backup_id)
    delete_record_file(row)
    shutil.rmtree(staging_dir() / row.backup_uuid, ignore_errors=True)
    audit(db, request, "instance_backup.deleted", "instance_backups", row.id)
    return None
