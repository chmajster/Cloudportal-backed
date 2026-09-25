from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from python_multipart.multipart import MultipartParser, parse_options_header
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.api.automation import validate_blueprint_references
from app.api.common import find
from app.api.outputs import BlueprintOutput
from app.appliances.service import appliance_blueprint_input, convert_ova_disks, staging_root
from app.automation.service import blueprint_public
from app.config import settings
from app.database import get_db
from app.models import Blueprint, Credential, Provider
from app.providers.proxmox import ProxmoxProvider
from app.resource_scope.http import require
from app.security.core import audit


router = APIRouter(prefix='/appliances', tags=['appliances'])


class UploadTooLarge(Exception):
    pass


def _secure_open_new(path: Path):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    return os.fdopen(descriptor, 'wb')


async def _stream_ova(request: Request, destination: Path):
    raw_content_type = request.headers.get('content-type', '')
    try:
        media_type, parameters = parse_options_header(raw_content_type.encode('latin-1'))
    except Exception:
        raise HTTPException(415, 'Oczekiwano multipart/form-data') from None
    if media_type != b'multipart/form-data' or not parameters.get(b'boundary'):
        raise HTTPException(415, 'Oczekiwano multipart/form-data z boundary')

    max_bytes = settings().appliance_max_upload_bytes
    content_length = request.headers.get('content-length')
    if content_length and content_length.isdigit() and int(content_length) > max_bytes + 1024 * 1024:
        raise HTTPException(413, 'Plik OVA przekracza skonfigurowany limit')

    headers = {}
    header_field = bytearray()
    header_value = bytearray()
    current_file = None
    current_is_file = False
    file_seen = False
    client_filename = ''
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
        disposition = headers.get(b'content-disposition', b'')
        _, options = parse_options_header(disposition)
        name = options.get(b'name')
        filename = options.get(b'filename')
        if name != b'file' or not filename:
            raise ValueError('Multipart musi zawierać wyłącznie pole file')
        if file_seen or current_file is not None:
            raise ValueError('Można wysłać tylko jeden plik OVA')
        raw_filename = filename.decode('utf-8', 'replace').replace('\\', '/')
        client_filename = Path(raw_filename).name
        if not client_filename or len(client_filename) > 255 or any(ord(ch) < 32 for ch in client_filename):
            raise ValueError('Nazwa pliku OVA jest nieprawidłowa')
        if not client_filename.lower().endswith('.ova'):
            raise ValueError('Akceptowane są wyłącznie pliki .ova')
        current_file = _secure_open_new(destination)
        current_is_file = True

    def on_part_data(data, start, end):
        nonlocal written
        if not current_is_file or current_file is None:
            if end > start:
                raise ValueError('Nieoczekiwane pole multipart')
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

    parser = MultipartParser(parameters[b'boundary'], {
        'on_part_begin': on_part_begin,
        'on_header_field': on_header_field,
        'on_header_value': on_header_value,
        'on_header_end': on_header_end,
        'on_headers_finished': on_headers_finished,
        'on_part_data': on_part_data,
        'on_part_end': on_part_end,
    })

    try:
        async for chunk in request.stream():
            parser.write(chunk)
        parser.finalize()
    except UploadTooLarge:
        destination.unlink(missing_ok=True)
        raise HTTPException(413, 'Plik OVA przekracza skonfigurowany limit') from None
    except (ValueError, OSError) as exc:
        destination.unlink(missing_ok=True)
        raise HTTPException(422, str(exc)) from None
    finally:
        if current_file is not None:
            current_file.close()

    if not file_seen or written <= 0:
        destination.unlink(missing_ok=True)
        raise HTTPException(422, 'Nie przesłano pliku OVA')
    return client_filename, written


def _supports_content(row, content_type: str) -> bool:
    raw = row.get('content') or ''
    if isinstance(raw, list):
        values = {str(value).strip() for value in raw}
    else:
        values = {value.strip() for value in str(raw).split(',') if value.strip()}
    return content_type in values


def _storage(rows, storage_id: str):
    return next((row for row in rows if row.get('storage') == storage_id and not row.get('disable')), None)


@router.post('/ova-blueprints', status_code=201, response_model=BlueprintOutput)
async def create_ova_blueprint(
    request: Request,
    provider_id: Annotated[int, Query(gt=0)],
    node: Annotated[str, Query(min_length=1, max_length=63, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$')],
    import_storage: Annotated[str, Query(min_length=1, max_length=63, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$')],
    storage: Annotated[str, Query(min_length=1, max_length=63, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$')],
    network: Annotated[str, Query(min_length=1, max_length=63, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,62}$')],
    slug: Annotated[str, Query(min_length=1, max_length=63, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$')],
    name: Annotated[str, Query(min_length=1, max_length=100)],
    disk_bus: Annotated[Literal['scsi', 'sata', 'virtio'], Query()] = 'scsi',
    vlan_id: Annotated[int | None, Query(ge=1, le=4094)] = None,
    actor=Depends(require('blueprints.create')),
    db=Depends(get_db, scope='function'),
):
    required_permissions = {'terraform.execute', 'providers.read', 'credentials.read'}
    missing_permissions = sorted(required_permissions - set(request.state.permissions))
    if missing_permissions:
        raise HTTPException(
            403,
            'Import appliance OVA wymaga uprawnień: ' + ', '.join(missing_permissions),
        )
    if db.scalar(select(Blueprint.id).where(Blueprint.slug == slug).limit(1)):
        raise HTTPException(409, 'Blueprint o takim slug już istnieje')

    provider_row = find(db, Provider, provider_id)
    if provider_row.type != 'proxmox':
        raise HTTPException(422, 'Appliance OVA jest obecnie obsługiwany tylko dla Proxmox')
    credential = find(db, Credential, provider_row.credentials_id)
    if credential.type != 'proxmox':
        raise HTTPException(422, 'Provider nie używa credentiala Proxmox')

    provider = ProxmoxProvider(credential)
    try:
        nodes = await run_in_threadpool(provider.discover, 'nodes')
        storages = await run_in_threadpool(provider.discover, 'storages', node)
        networks = await run_in_threadpool(provider.discover, 'networks', node)
    except HTTPException:
        raise
    if not any(row.get('node') == node for row in nodes):
        raise HTTPException(422, 'Wybrany node Proxmox nie istnieje lub nie jest dostępny')

    import_row = _storage(storages, import_storage)
    if import_row is None or not _supports_content(import_row, 'import'):
        raise HTTPException(
            422,
            'Storage importu musi być aktywny i mieć w Proxmox włączony content typu Import',
        )
    target_row = _storage(storages, storage)
    if target_row is None or not _supports_content(target_row, 'images'):
        raise HTTPException(422, 'Storage docelowy VM musi być aktywny i obsługiwać Disk image')
    if not any(row.get('iface') == network for row in networks):
        raise HTTPException(422, 'Wybrany bridge/sieć nie istnieje na docelowym node')

    root = staging_root()
    upload_id = uuid.uuid4().hex
    workdir = root / upload_id
    workdir.mkdir(mode=0o700)
    ova_path = workdir / 'source.ova'
    uploaded_volumes = []

    try:
        client_filename, _ = await _stream_ova(request, ova_path)
        converted = await run_in_threadpool(convert_ova_disks, ova_path, workdir / 'converted')

        for index, disk in enumerate(converted['disks'], start=1):
            file_name = f'{slug.lower()}-{converted["sha256"][:12]}-disk{index}.qcow2'
            volume = await run_in_threadpool(
                provider.upload_import_image,
                node,
                import_storage,
                disk['path'],
                file_name,
            )
            uploaded_volumes.append(volume)

        blueprint_data = appliance_blueprint_input(
            slug=slug,
            name=name,
            provider_id=provider_row.id,
            credentials_id=credential.id,
            node=node,
            storage=storage,
            network=network,
            network_count=converted.get('networks') or 1,
            import_file_ids=uploaded_volumes,
            cpu=converted['cpu'],
            memory=converted['memory'],
            disk_bus=disk_bus,
            vlan_id=vlan_id,
            source_sha256=converted['sha256'],
            source_name=client_filename,
        )
        manager_roles = validate_blueprint_references(db, blueprint_data)
        values = blueprint_data.model_dump(mode='json', exclude={'manager_role_ids'})
        row = Blueprint(**values, created_by=actor.user_id)
        row.manager_roles = manager_roles
        db.add(row)
        db.flush()
        audit(db, request, 'appliance.ova_blueprint_created', 'blueprints', row.id)
        return blueprint_public(row)
    except Exception:
        for volume in reversed(uploaded_volumes):
            try:
                await run_in_threadpool(provider.delete_storage_volume, node, import_storage, volume)
            except Exception:
                pass
        raise
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
