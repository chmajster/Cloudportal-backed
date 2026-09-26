import asyncio
import json
import re
import secrets
import ssl
from pathlib import Path as FilePath
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, WebSocket
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
from websockets.asyncio.client import connect as websocket_connect
from pydantic import Field, model_validator
from sqlalchemy import select

from app.api.common import find, idempotent
from app.api.schemas import Input, Name, Slug
from app.config import settings
from app.database import get_db, session as db_session
from app.models import Credential, ManagedResource, ManagedVM, Provider
from app.providers.registry import provider_for
from app.providers.task_reconcile import track_proxmox_task
from app.security.core import audit, redis_client
from app.resource_scope.http import require
from app.quotas.service import DIMENSIONS, require_governed_legacy_mutation


router = APIRouter(tags=['proxmox-vm-management'])
VMID = Annotated[int, Path(ge=100, le=999999999)]
NODE = Annotated[str, Path(pattern=r'^[A-Za-z0-9_.-]{1,63}\z')]
SNAPSHOT = Annotated[str, Path(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\z')]
CONSOLE_SESSION_TTL = 90
CONSOLE_TOKEN = re.compile(r'^[A-Za-z0-9_-]{32,128}\Z')
LOCAL_NOVNC_ROOT = (FilePath(__file__).resolve().parents[1] / 'web' / 'vendor' / 'novnc').resolve()
NOVNC_CONTENT_TYPES = {
    'js': 'text/javascript; charset=utf-8',
    'css': 'text/css; charset=utf-8',
    'html': 'text/html; charset=utf-8',
    'svg': 'image/svg+xml',
    'png': 'image/png',
    'gif': 'image/gif',
    'jpg': 'image/jpeg',
    'jpeg': 'image/jpeg',
    'woff': 'font/woff',
    'woff2': 'font/woff2',
    'ttf': 'font/ttf',
    'map': 'application/json; charset=utf-8',
}


def console_key(session_id):
    return 'cp:console:' + session_id



def novnc_content_type(asset_path, upstream_content_type=None):
    extension = asset_path.rsplit('.', 1)[-1].lower() if '.' in asset_path else ''
    return NOVNC_CONTENT_TYPES.get(extension, upstream_content_type or 'application/octet-stream')


def local_novnc_asset(asset_path):
    if not asset_path or '\\' in asset_path or asset_path.startswith('/'):
        raise HTTPException(404, 'noVNC asset not found')
    candidate = (LOCAL_NOVNC_ROOT / asset_path).resolve()
    if not candidate.is_relative_to(LOCAL_NOVNC_ROOT) or not candidate.is_file():
        raise HTTPException(404, 'noVNC asset not found')
    return candidate


def load_console_session(session_id):
    if not CONSOLE_TOKEN.fullmatch(session_id):
        return None
    try:
        raw = redis_client().get(console_key(session_id))
    except Exception:
        return None
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    required = {'provider_id', 'node', 'vmid', 'port', 'ticket', 'user_id', 'token_id', 'tenant_id', 'project_id'}
    if not isinstance(value, dict) or not required <= set(value):
        return None
    return value


def console_adapter(record):
    from app.resource_scope.console import console_access
    return console_access(record, adapter=True)


class VMPowerInput(Input):
    action: Literal['start', 'stop', 'shutdown', 'reboot', 'reset', 'suspend', 'resume']


class SnapshotInput(Input):
    snapname: Annotated[str, Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\z')]
    description: Annotated[str, Field(max_length=1000)] = ''
    include_ram: bool = False


class CloneVMInput(Input):
    new_vm_id: int = Field(ge=100, le=999999999)
    name: Name
    target: Slug | None = None
    full: bool = True
    storage: Slug | None = None
    pool: Slug | None = None


class BackupVMInput(Input):
    storage: Slug
    mode: Literal['snapshot', 'suspend', 'stop'] = 'snapshot'
    compress: Literal['zstd', 'lzo', 'gzip', '0'] = 'zstd'
    notes: Annotated[str | None, Field(max_length=500)] = None


class RestoreVMInput(Input):
    vm_id: int = Field(ge=100, le=999999999)
    archive: Annotated[str, Field(
        pattern=r'^[A-Za-z0-9_.-]+:backup/[A-Za-z0-9_.+-]{1,240}\z',
        max_length=320,
    )]
    storage: Slug | None = None
    unique: bool = True


class ResizeDiskInput(Input):
    disk: Annotated[str, Field(pattern=r'^(?:scsi|virtio|sata|ide)\d{1,2}\z')]
    grow_gib: int = Field(ge=1, le=65536)


class VMConfigInput(Input):
    name: Name | None = None
    cores: int | None = Field(default=None, ge=1, le=128)
    memory: int | None = Field(default=None, ge=512, le=1048576)
    tags: Annotated[str | None, Field(max_length=512, pattern=r'^[A-Za-z0-9_.:;-]+\z')] = None
    onboot: bool | None = None

    @model_validator(mode='after')
    def at_least_one(self):
        if all(getattr(self, key) is None for key in ('name', 'cores', 'memory', 'tags', 'onboot')):
            raise ValueError('At least one VM configuration field is required')
        return self


class MigrateVMInput(Input):
    target: Slug
    online: bool = False
    with_local_disks: bool = False


def adapter(db, provider_id):
    provider = find(db, Provider, provider_id)
    if provider.type != 'proxmox':
        raise HTTPException(422, 'VM management is currently implemented only for Proxmox providers')
    credential = find(db, Credential, provider.credentials_id)
    if credential.type != 'proxmox':
        raise HTTPException(422, 'Proxmox provider requires Proxmox credentials')
    return provider_for(credential)


def task_result(task):
    return {'task': task} if task else {'task': None}


def managed_vm_record(db, provider_id, vmid):
    return db.scalar(select(ManagedVM).where(
        ManagedVM.provider_id == int(provider_id),
        ManagedVM.vm_id == int(vmid),
    ))


def ensure_not_terraform_managed(row, action):
    if row is not None and row.management_mode == 'terraform' and row.lifecycle_status == 'active':
        raise HTTPException(
            409,
            f'Active Terraform-managed VM cannot be {action} directly; use its deployment workflow',
        )


@router.get('/providers/{provider_id}/vms/{node}/{vmid}/status')
def vm_status(provider_id: int, node: NODE, vmid: VMID, actor=Depends(require('vms.read')),
              db=Depends(get_db, scope='function')):
    provider = adapter(db, provider_id)
    data = provider.vm_status(node, vmid)
    allowed = 'vmid name status qmpstatus cpu cpus mem maxmem disk maxdisk uptime pid lock template tags'.split()
    result = {key: value for key, value in data.items() if key in allowed}

    primary_ip = None
    managed = managed_vm_record(db, provider_id, vmid)
    if managed is not None and managed.deployment_id:
        resource = db.scalar(select(ManagedResource).where(
            ManagedResource.deployment_id == managed.deployment_id,
            ManagedResource.lifecycle_status == 'active',
        ))
        if resource is not None and resource.primary_ip:
            primary_ip = str(resource.primary_ip)

    if primary_ip is None and str(result.get('status') or '').lower() == 'running':
        try:
            addresses = provider.guest_addresses(node, vmid) or []
        except Exception:
            addresses = []
        ipv4 = [str(address) for address in addresses if ':' not in str(address)]
        if ipv4:
            primary_ip = ipv4[0]
        elif addresses:
            primary_ip = str(addresses[0])

    result['primary_ip'] = primary_ip
    return result


@router.get('/providers/{provider_id}/vms/{node}/{vmid}/monitor')
def vm_monitor(
    provider_id: int,
    node: NODE,
    vmid: VMID,
    timeframe: Annotated[Literal['hour', 'day', 'week', 'month', 'year'], Query()] = 'hour',
    actor=Depends(require('vms.read')),
    db=Depends(get_db, scope='function'),
):
    provider = adapter(db, provider_id)
    current_raw = provider.vm_status(node, vmid) or {}
    current_allowed = (
        'vmid name status qmpstatus cpu cpus mem maxmem disk maxdisk uptime pid '
        'lock template tags netin netout diskread diskwrite'
    ).split()
    current = {key: value for key, value in current_raw.items() if key in current_allowed}

    rows = provider.vm_rrddata(node, vmid, timeframe=timeframe) or []
    metric_fields = 'time cpu maxcpu mem maxmem disk maxdisk diskread diskwrite netin netout'.split()
    series = [
        {key: value for key, value in row.items() if key in metric_fields and value is not None}
        for row in rows
        if isinstance(row, dict)
    ]
    return {
        'source': 'proxmox',
        'timeframe': timeframe,
        'current': current,
        'series': series,
    }


@router.post('/providers/{provider_id}/vms/{node}/{vmid}/power')
def vm_power(provider_id: int, node: NODE, vmid: VMID, data: VMPowerInput, request: Request,
             actor=Depends(require('vms.power')), db=Depends(get_db, scope='function')):
    def execute():
        task = adapter(db, provider_id).vm_power(node, vmid, data.action)
        audit(db, request, f'vm.{data.action}', 'vms', f'{provider_id}:{node}:{vmid}')
        return task_result(task)
    return idempotent(db, request, actor, data.model_dump(), execute)


@router.get('/providers/{provider_id}/vms/{node}/{vmid}/snapshots')
def snapshots(provider_id: int, node: NODE, vmid: VMID, actor=Depends(require('snapshots.read')),
              db=Depends(get_db, scope='function')):
    rows = adapter(db, provider_id).snapshots(node, vmid)
    allowed = 'name snaptime description vmstate parent'.split()
    return {'items': [{key: value for key, value in row.items() if key in allowed} for row in rows]}


@router.post('/providers/{provider_id}/vms/{node}/{vmid}/snapshots', status_code=202)
def create_snapshot(provider_id: int, node: NODE, vmid: VMID, data: SnapshotInput, request: Request,
                    actor=Depends(require('snapshots.create')), db=Depends(get_db, scope='function')):
    def execute():
        task = adapter(db, provider_id).create_snapshot(
            node, vmid, data.snapname, data.description, data.include_ram
        )
        audit(db, request, 'snapshot.created', 'vms', f'{provider_id}:{node}:{vmid}:{data.snapname}')
        return task_result(task)
    return idempotent(db, request, actor, data.model_dump(), execute, required=True)


@router.delete('/providers/{provider_id}/vms/{node}/{vmid}/snapshots/{snapname}', status_code=202)
def delete_snapshot(provider_id: int, node: NODE, vmid: VMID, snapname: SNAPSHOT, request: Request,
                    actor=Depends(require('snapshots.delete')), db=Depends(get_db, scope='function')):
    payload = {'snapname': snapname}
    def execute():
        task = adapter(db, provider_id).delete_snapshot(node, vmid, snapname)
        audit(db, request, 'snapshot.deleted', 'vms', f'{provider_id}:{node}:{vmid}:{snapname}')
        return task_result(task)
    return idempotent(db, request, actor, payload, execute, required=True)


@router.post('/providers/{provider_id}/vms/{node}/{vmid}/snapshots/{snapname}/rollback', status_code=202)
def rollback_snapshot(provider_id: int, node: NODE, vmid: VMID, snapname: SNAPSHOT, request: Request,
                      actor=Depends(require('snapshots.rollback')), db=Depends(get_db, scope='function')):
    ensure_not_terraform_managed(managed_vm_record(db, provider_id, vmid), 'rolled back to a snapshot')
    require_governed_legacy_mutation(
        db, request.state.resource_scope, ('vcpu', 'memory_mb', 'disk_gib'), 'Snapshot rollback'
    )
    payload = {'snapname': snapname}
    def execute():
        task = adapter(db, provider_id).rollback_snapshot(node, vmid, snapname)
        audit(db, request, 'snapshot.rolled_back', 'vms', f'{provider_id}:{node}:{vmid}:{snapname}')
        return task_result(task)
    return idempotent(db, request, actor, payload, execute, required=True)


@router.get('/providers/{provider_id}/vms/{node}/{vmid}/backups')
def vm_backups(provider_id: int, node: NODE, vmid: VMID,
               storage: Annotated[str, Query(min_length=1, max_length=63)],
               actor=Depends(require('backups.read')), db=Depends(get_db, scope='function')):
    if not all(ch.isalnum() or ch in '_.-' for ch in storage):
        raise HTTPException(422, 'Invalid backup storage identifier')
    rows = adapter(db, provider_id).backups(node, storage, vmid)
    allowed = 'volid format size ctime vmid content notes protected'.split()
    return {'items': [{key: value for key, value in row.items() if key in allowed} for row in rows]}


@router.post('/providers/{provider_id}/vms/{node}/{vmid}/backups', status_code=202)
def backup_vm(provider_id: int, node: NODE, vmid: VMID, data: BackupVMInput, request: Request,
              actor=Depends(require('backups.create')), db=Depends(get_db, scope='function')):
    def execute():
        task = adapter(db, provider_id).backup_vm(
            node, vmid, storage=data.storage, mode=data.mode, compress=data.compress, notes=data.notes
        )
        audit(db, request, 'vm.backup_started', 'vms', f'{provider_id}:{node}:{vmid}')
        return task_result(task)
    return idempotent(db, request, actor, data.model_dump(), execute, required=True)


@router.post('/providers/{provider_id}/restore/{node}', status_code=202)
def restore_vm(provider_id: int, node: NODE, data: RestoreVMInput, request: Request,
               actor=Depends(require('backups.restore')), db=Depends(get_db, scope='function')):
    ensure_not_terraform_managed(managed_vm_record(db, provider_id, data.vm_id), 'restored over directly')
    require_governed_legacy_mutation(db, request.state.resource_scope, DIMENSIONS, 'VM restore')
    source_storage = data.archive.split(':', 1)[0]
    provider = adapter(db, provider_id)
    available = provider.backups(node, source_storage)
    if not any(row.get('volid') == data.archive for row in available):
        raise HTTPException(404, 'Backup archive was not found in the selected Proxmox storage')

    def execute():
        task = provider.restore_vm(
            node, vm_id=data.vm_id, archive=data.archive, storage=data.storage, unique=data.unique
        )
        track_proxmox_task(provider_id=provider_id, node=node, upid=task, action='restore',
                           target_node=node, target_vm_id=data.vm_id, created_by=actor.user_id)
        audit(db, request, 'vm.restore_started', 'vms', f'{provider_id}:{node}:{data.vm_id}')
        return task_result(task)
    return idempotent(db, request, actor, data.model_dump(), execute, required=True)


@router.post('/providers/{provider_id}/vms/{node}/{vmid}/template', status_code=202)
def convert_to_template(provider_id: int, node: NODE, vmid: VMID, request: Request,
                        actor=Depends(require('vms.template')), db=Depends(get_db, scope='function')):
    ensure_not_terraform_managed(managed_vm_record(db, provider_id, vmid), 'converted to a template')
    def execute():
        task = adapter(db, provider_id).convert_to_template(node, vmid)
        track_proxmox_task(provider_id=provider_id, node=node, upid=task, action='template',
                           vm_id=vmid, created_by=actor.user_id)
        audit(db, request, 'vm.converted_to_template', 'vms', f'{provider_id}:{node}:{vmid}')
        return task_result(task)
    return idempotent(db, request, actor, {}, execute, required=True)


@router.post('/providers/{provider_id}/vms/{node}/{vmid}/clone', status_code=202)
def clone_vm(provider_id: int, node: NODE, vmid: VMID, data: CloneVMInput, request: Request,
             actor=Depends(require('vms.clone')), db=Depends(get_db, scope='function')):
    require_governed_legacy_mutation(db, request.state.resource_scope, DIMENSIONS, 'VM clone')
    def execute():
        task = adapter(db, provider_id).clone_vm(
            node,
            vmid,
            new_vm_id=data.new_vm_id,
            name=data.name,
            target=data.target,
            full=data.full,
            storage=data.storage,
            pool=data.pool,
        )
        track_proxmox_task(provider_id=provider_id, node=node, upid=task, action='clone',
                           vm_id=vmid, target_node=data.target or node, target_vm_id=data.new_vm_id,
                           name=data.name, created_by=actor.user_id)
        audit(db, request, 'vm.cloned', 'vms', f'{provider_id}:{node}:{vmid}->{data.new_vm_id}')
        return task_result(task)
    return idempotent(db, request, actor, data.model_dump(), execute, required=True)


@router.put('/providers/{provider_id}/vms/{node}/{vmid}/disk', status_code=202)
def resize_disk(provider_id: int, node: NODE, vmid: VMID, data: ResizeDiskInput, request: Request,
                actor=Depends(require('vms.update')), db=Depends(get_db, scope='function')):
    ensure_not_terraform_managed(managed_vm_record(db, provider_id, vmid), 'resized directly')
    require_governed_legacy_mutation(db, request.state.resource_scope, ('disk_gib',), 'Disk resize')
    def execute():
        task = adapter(db, provider_id).resize_disk(
            node, vmid, disk=data.disk, grow_gib=data.grow_gib
        )
        audit(db, request, 'vm.disk_resized', 'vms', f'{provider_id}:{node}:{vmid}')
        return task_result(task)
    return idempotent(db, request, actor, data.model_dump(), execute, required=True)


@router.put('/providers/{provider_id}/vms/{node}/{vmid}/config', status_code=202)
def update_vm_config(provider_id: int, node: NODE, vmid: VMID, data: VMConfigInput, request: Request,
                     actor=Depends(require('vms.update')), db=Depends(get_db, scope='function')):
    managed = managed_vm_record(db, provider_id, vmid)
    ensure_not_terraform_managed(managed, 'reconfigured directly')
    payload = data.model_dump(exclude_none=True)
    affected = set()
    if 'cores' in payload:
        affected.add('vcpu')
    if 'memory' in payload:
        affected.add('memory_mb')
    if affected:
        require_governed_legacy_mutation(db, request.state.resource_scope, affected, 'VM compute resize')
    def execute():
        values = dict(payload)
        if 'onboot' in values:
            values['onboot'] = int(values['onboot'])
        task = adapter(db, provider_id).update_vm_config(node, vmid, **values)
        track_proxmox_task(provider_id=provider_id, node=node, upid=task, action='config',
                           vm_id=vmid, name=values.get('name'), created_by=actor.user_id)
        if not task and managed is not None and values.get('name'):
            managed.name = str(values['name'])
        audit(db, request, 'vm.config_updated', 'vms', f'{provider_id}:{node}:{vmid}')
        return task_result(task)
    return idempotent(db, request, actor, payload, execute, required=True)


@router.post('/providers/{provider_id}/vms/{node}/{vmid}/migrate', status_code=202)
def migrate_vm(provider_id: int, node: NODE, vmid: VMID, data: MigrateVMInput, request: Request,
               actor=Depends(require('vms.migrate')), db=Depends(get_db, scope='function')):
    ensure_not_terraform_managed(managed_vm_record(db, provider_id, vmid), 'migrated directly')
    def execute():
        task = adapter(db, provider_id).migrate_vm(
            node,
            vmid,
            target=data.target,
            online=data.online,
            with_local_disks=data.with_local_disks,
        )
        track_proxmox_task(provider_id=provider_id, node=node, upid=task, action='migrate',
                           vm_id=vmid, target_node=data.target, created_by=actor.user_id)
        audit(db, request, 'vm.migrated', 'vms', f'{provider_id}:{node}:{vmid}->{data.target}')
        return task_result(task)
    return idempotent(db, request, actor, data.model_dump(), execute, required=True)


@router.delete('/providers/{provider_id}/vms/{node}/{vmid}', status_code=202)
def delete_vm(provider_id: int, node: NODE, vmid: VMID, request: Request,
              purge: Annotated[bool, Query()] = False,
              destroy_unreferenced_disks: Annotated[bool, Query()] = False,
              actor=Depends(require('vms.delete')), db=Depends(get_db, scope='function')):
    ensure_not_terraform_managed(managed_vm_record(db, provider_id, vmid), 'deleted directly')
    require_governed_legacy_mutation(db, request.state.resource_scope, DIMENSIONS, 'VM delete')
    payload = {'purge': purge, 'destroy_unreferenced_disks': destroy_unreferenced_disks}
    def execute():
        task = adapter(db, provider_id).delete_vm(
            node,
            vmid,
            purge=purge,
            destroy_unreferenced_disks=destroy_unreferenced_disks,
        )
        track_proxmox_task(provider_id=provider_id, node=node, upid=task, action='delete',
                           vm_id=vmid, created_by=actor.user_id)
        audit(db, request, 'vm.deleted', 'vms', f'{provider_id}:{node}:{vmid}')
        return task_result(task)
    return idempotent(db, request, actor, payload, execute, required=True)


@router.post('/providers/{provider_id}/vms/{node}/{vmid}/console')
def console_session(provider_id: int, node: NODE, vmid: VMID, request: Request,
                    actor=Depends(require('vms.console')), db=Depends(get_db, scope='function')):
    result = adapter(db, provider_id).console_session(node, vmid)
    session_id = secrets.token_urlsafe(32)
    payload = {
        'provider_id': provider_id,
        'user_id': actor.user_id,
        'token_id': actor.id,
        'tenant_id': request.state.resource_scope.tenant_id,
        'project_id': request.state.resource_scope.project_id,
        'node': node,
        'vmid': vmid,
        'port': int(result['port']),
        'ticket': result['ticket'],
    }
    try:
        redis_client().setex(console_key(session_id), CONSOLE_SESSION_TTL, json.dumps(payload))
    except Exception:
        raise HTTPException(503, 'Console session store is unavailable') from None
    audit(db, request, 'vm.console_session_issued', 'vms', f'{provider_id}:{node}:{vmid}')
    return {
        'mode': 'novnc',
        # Keep the session-scoped field for tabs that loaded the previous UI.
        # New UI imports local_rfb_module so normal operation is independent of
        # Proxmox static-file MIME, redirects and noVNC patch level.
        'rfb_module': f'/api/v1/console-sessions/{session_id}/novnc/core/rfb.js',
        'local_rfb_module': '/ui/vendor/novnc/core/rfb.js',
        'ws_path': f'/api/v1/console-sessions/{session_id}/websocket',
        'password': result['password'],
        'expires_in': CONSOLE_SESSION_TTL,
    }


@router.get('/console-sessions/{session_id}/novnc/{asset_path:path}', include_in_schema=False)
def console_asset(session_id: str, asset_path: str):
    record = load_console_session(session_id)
    if record is None:
        raise HTTPException(410, 'Console session expired')

    # Keep the legacy session-scoped module URL working for tabs loaded before
    # an upgrade, but serve the exact vendored client instead of PVE static files.
    from app.resource_scope.console import console_access
    console_access(record)
    target = local_novnc_asset(asset_path)
    return FileResponse(
        target,
        media_type=novnc_content_type(asset_path),
        headers={
            'Cache-Control': 'no-store',
            'X-Content-Type-Options': 'nosniff',
            'Referrer-Policy': 'no-referrer',
        },
    )


@router.websocket('/console-sessions/{session_id}/websocket')
async def console_websocket(websocket: WebSocket, session_id: str):
    if websocket.url.scheme != 'wss' and not settings().allow_http:
        await websocket.close(code=4400)
        return
    record = await run_in_threadpool(load_console_session, session_id)
    if record is None:
        await websocket.close(code=4404)
        return

    try:
        proxmox = await run_in_threadpool(console_adapter, record)
        headers = await run_in_threadpool(proxmox.console_auth_headers)
        upstream_url = proxmox.console_websocket_url(
            record['node'], record['vmid'], record['port'], record['ticket']
        )
        tls = None
        if upstream_url.startswith('wss://'):
            tls = ssl.create_default_context()
            if not proxmox.verify_ssl:
                tls.check_hostname = False
                tls.verify_mode = ssl.CERT_NONE

        requested = {
            value.strip()
            for value in websocket.headers.get('sec-websocket-protocol', '').split(',')
            if value.strip()
        }
        browser_protocol = 'binary' if 'binary' in requested else None

        async with websocket_connect(
            upstream_url,
            additional_headers=headers,
            subprotocols=['binary'],
            ssl=tls,
            open_timeout=10,
            close_timeout=5,
            ping_interval=20,
            max_size=None,
        ) as upstream:
            await websocket.accept(subprotocol=browser_protocol)

            async def browser_to_proxmox():
                while True:
                    message = await websocket.receive()
                    if message['type'] == 'websocket.disconnect':
                        return
                    if message.get('bytes') is not None:
                        await upstream.send(message['bytes'])
                    elif message.get('text') is not None:
                        await upstream.send(message['text'])

            async def proxmox_to_browser():
                async for message in upstream:
                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)
                    else:
                        await websocket.send_text(message)

            async def reauthorize_console():
                from app.resource_scope.console import console_access
                while True:
                    await asyncio.sleep(5)
                    current = await run_in_threadpool(load_console_session, session_id)
                    if current is None:
                        await websocket.close(code=4401)
                        return
                    try:
                        await run_in_threadpool(console_access, current)
                    except HTTPException:
                        await websocket.close(code=4403)
                        return

            tasks = {
                asyncio.create_task(reauthorize_console()),
                asyncio.create_task(browser_to_proxmox()),
                asyncio.create_task(proxmox_to_browser()),
            }
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                if not task.cancelled():
                    task.exception()
    except Exception:
        try:
            await websocket.close(code=1011)
        except RuntimeError:
            pass


@router.get('/providers/{provider_id}/tasks/{node}/{upid}')
def task_status(provider_id: int, node: NODE,
                upid: Annotated[str, Path(min_length=1, max_length=1024)],
                actor=Depends(require('vms.read')), db=Depends(get_db, scope='function')):
    data = adapter(db, provider_id).task_status(node, upid)
    allowed = 'status exitstatus type id user node pid pstart starttime endtime'.split()
    return {key: value for key, value in data.items() if key in allowed}
