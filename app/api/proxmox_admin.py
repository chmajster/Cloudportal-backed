from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select

from app.api.common import Limit, idempotent
from app.api.proxmox_management import issue_console_session
from app.database import get_db
from app.day2.models import BulkDay2ActionRequest
from app.models import Audit, EventRecord, Job, JobLog, now
from app.proxmox_admin.schemas import (
    BackupDeleteInput,
    BackupRestoreInput,
    BackupRunInput,
    BulkInput,
    CloudInitInput,
    CloneInput,
    DeleteResourceInput,
    DiskAddInput,
    DiskMoveInput,
    DiskRemoveInput,
    DiskResizeInput,
    FirewallRuleDeleteInput,
    FirewallRuleInput,
    FirewallRuleUpdateInput,
    LXCConfigInput,
    MigrateInput,
    NICInput,
    NICRemoveInput,
    NodeServiceInput,
    PowerInput,
    ProxmoxAdminSettingsInput,
    SnapshotCreateInput,
    VMConfigInput,
)
from app.proxmox_admin.service import (
    _ensure_visible_object,
    bulk_summary,
    cancel_admin_job,
    cluster_detail,
    dashboard,
    ensure_enabled,
    firewall_snapshot,
    get_admin_job,
    get_proxmox_provider,
    list_admin_jobs,
    list_backups,
    list_containers,
    list_nodes,
    list_proxmox_providers,
    list_snapshots,
    list_storage,
    list_tasks,
    list_templates,
    list_vms,
    node_detail,
    node_syslog,
    proxmox_admin_settings,
    queue_operation,
    save_proxmox_admin_settings,
    search,
    storage_content,
    task_detail,
    vm_detail,
    container_detail,
)
from app.resource_scope.http import require
from app.security.core import audit, require as require_global


router = APIRouter(prefix='/proxmox-admin', tags=['proxmox-admin'])

ProviderID = Annotated[int, Query(ge=1)]
NodeName = Annotated[str, Query(pattern=r'^[A-Za-z0-9_.-]{1,63}$')]


def _ready(db):
    ensure_enabled(db)


def _payload(data):
    return data.model_dump(mode='json', exclude_none=True)


def _target(db, provider_id, request, node, vmid, kind):
    return _ensure_visible_object(db, provider_id, request, node, vmid, kind)


def _target_name(row):
    return str(row.get('name') or row.get('vmid') or 'resource')


def _enqueue(
    db,
    request,
    actor,
    *,
    provider_id,
    command,
    object_type,
    object_id,
    node=None,
    parameters=None,
    confirmation_expected=None,
    resource=None,
):
    fingerprint = {
        'provider_id': provider_id,
        'command': command,
        'object_type': object_type,
        'object_id': str(object_id),
        'node': node,
        'parameters': parameters or {},
    }

    def create():
        return queue_operation(
            db,
            request,
            actor,
            provider_id=provider_id,
            command=command,
            object_type=object_type,
            object_id=object_id,
            node=node,
            parameters=parameters,
            confirmation_expected=confirmation_expected,
            resource=resource,
        )

    return idempotent(db, request, actor, fingerprint, create, required=True)


@router.get('/settings')
def get_settings(
    actor=Depends(require_global('settings.read')),
    db=Depends(get_db, scope='function'),
):
    return proxmox_admin_settings(db)


@router.put('/settings')
def put_settings(
    data: ProxmoxAdminSettingsInput,
    request: Request,
    actor=Depends(require_global('settings.update')),
    db=Depends(get_db, scope='function'),
):
    result = save_proxmox_admin_settings(db, data.model_dump(mode='json'))
    audit(
        db, request, 'proxmox.admin.settings.updated', 'settings', 'proxmox_admin',
        details={'enabled': result['enabled']},
    )
    return result


@router.get('/status')
def status(
    actor=Depends(require('proxmox_admin.view')),
    db=Depends(get_db, scope='function'),
):
    return proxmox_admin_settings(db)


@router.get('/providers')
def providers(
    actor=Depends(require('proxmox_admin.providers.view')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    return {'items': list_proxmox_providers(db)}


@router.get('/dashboard')
def dashboard_view(
    provider_id: ProviderID,
    request: Request,
    actor=Depends(require('proxmox_admin.dashboard.view')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    return dashboard(db, provider_id, request)


@router.get('/nodes')
def nodes(
    provider_id: ProviderID,
    actor=Depends(require('proxmox_admin.nodes.view')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    return list_nodes(db, provider_id)


@router.get('/nodes/{node}')
def node(
    node: str,
    provider_id: ProviderID,
    actor=Depends(require('proxmox_admin.nodes.view')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    return node_detail(db, provider_id, node)


@router.get('/nodes/{node}/syslog')
def syslog(
    node: str,
    provider_id: ProviderID,
    since: str | None = None,
    until: str | None = None,
    severity: str | None = None,
    search_text: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 500,
    actor=Depends(require('proxmox_admin.nodes.view')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    return node_syslog(
        db, provider_id, node, since=since, until=until, limit=limit,
        severity=severity, search=search_text,
    )


@router.post('/nodes/{node}/services/{service}')
def node_service(
    node: str,
    service: str,
    data: NodeServiceInput,
    provider_id: ProviderID,
    request: Request,
    actor=Depends(require('proxmox_admin.nodes.services.manage')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    values = _payload(data)
    expected = f'{node}:{service}' if data.action == 'stop' else None
    return _enqueue(
        db, request, actor,
        provider_id=provider_id,
        command='node.service',
        object_type='node',
        object_id=service,
        node=node,
        parameters=values,
        confirmation_expected=expected,
        resource={'name': node, 'service': service},
    )


@router.get('/vms')
def vms(
    provider_id: ProviderID,
    request: Request,
    actor=Depends(require('proxmox_admin.vm.view')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    return list_vms(db, provider_id, request)


@router.get('/vms/{node}/{vmid}')
def vm(
    node: str,
    vmid: int,
    provider_id: ProviderID,
    request: Request,
    actor=Depends(require('proxmox_admin.vm.view')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    return vm_detail(db, provider_id, request, node, vmid)


@router.get('/vms/{node}/{vmid}/raw')
def vm_raw(
    node: str,
    vmid: int,
    provider_id: ProviderID,
    request: Request,
    actor=Depends(require('proxmox_admin.vm.view')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    return {'raw': vm_detail(db, provider_id, request, node, vmid)['raw']}


@router.get('/vms/{node}/{vmid}/history')
def vm_history(
    node: str,
    vmid: int,
    provider_id: ProviderID,
    request: Request,
    limit: Limit = 100,
    actor=Depends(require('proxmox_admin.vm.view')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    _target(db, provider_id, request, node, vmid, 'vm')
    key = f'proxmox:{provider_id}:vm:{vmid}'
    audits = db.scalars(
        select(Audit).where(Audit.resource_id == key).order_by(Audit.timestamp.desc()).limit(limit)
    ).all()
    events = db.scalars(
        select(EventRecord).where(EventRecord.subject_id == key).order_by(EventRecord.sequence.desc()).limit(limit)
    ).all()
    jobs = []
    for job in db.scalars(
        select(Job).where(Job.operation.like('pxadmin.%')).order_by(Job.created_at.desc()).limit(500)
    ).all():
        meta = dict((job.payload or {}).get('_proxmox_admin') or {})
        if meta.get('resource_key') == key:
            jobs.append(get_admin_job(db, job.id))
        if len(jobs) >= limit:
            break
    return {
        'audit': [{
            'id': row.id,
            'timestamp': row.timestamp.isoformat() + 'Z',
            'user_id': row.user_id,
            'action': row.action,
            'result': row.result,
            'job': (row.details or {}).get('job'),
            'upid': (row.details or {}).get('proxmox_upid'),
            'details': row.details or {},
        } for row in audits],
        'events': [{
            'id': row.id,
            'sequence': row.sequence,
            'type': row.type,
            'occurred_at': row.occurred_at.isoformat() + 'Z' if row.occurred_at else None,
            'data': row.data,
        } for row in events],
        'jobs': jobs,
    }


@router.post('/vms/{node}/{vmid}/console')
def vm_console(
    node: str,
    vmid: int,
    provider_id: ProviderID,
    request: Request,
    actor=Depends(require('proxmox_admin.console.use')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    _target(db, provider_id, request, node, vmid, 'vm')
    return issue_console_session(
        provider_id, node, vmid, request, actor, db,
        object_type='vm', permission='proxmox_admin.console.use',
    )


@router.post('/vms/{node}/{vmid}/power', status_code=202)
def vm_power(
    node: str,
    vmid: int,
    data: PowerInput,
    provider_id: ProviderID,
    request: Request,
    actor=Depends(require('proxmox_admin.vm.power')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    target = _target(db, provider_id, request, node, vmid, 'vm')
    values = _payload(data)
    expected = _target_name(target) if data.action in {'stop', 'reset'} else None
    return _enqueue(
        db, request, actor, provider_id=provider_id, command='vm.power',
        object_type='vm', object_id=vmid, node=node, parameters=values,
        confirmation_expected=expected, resource=target,
    )


@router.put('/vms/{node}/{vmid}/config', status_code=202)
def vm_config(
    node: str,
    vmid: int,
    data: VMConfigInput,
    provider_id: ProviderID,
    request: Request,
    actor=Depends(require('proxmox_admin.vm.modify')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    target = _target(db, provider_id, request, node, vmid, 'vm')
    return _enqueue(
        db, request, actor, provider_id=provider_id, command='vm.config',
        object_type='vm', object_id=vmid, node=node, parameters=_payload(data), resource=target,
    )


@router.put('/vms/{node}/{vmid}/cloud-init', status_code=202)
def vm_cloudinit(
    node: str,
    vmid: int,
    data: CloudInitInput,
    provider_id: ProviderID,
    request: Request,
    actor=Depends(require('proxmox_admin.vm.modify')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    target = _target(db, provider_id, request, node, vmid, 'vm')
    return _enqueue(
        db, request, actor, provider_id=provider_id, command='vm.cloudinit',
        object_type='vm', object_id=vmid, node=node, parameters=_payload(data), resource=target,
    )


@router.post('/vms/{node}/{vmid}/clone', status_code=202)
def vm_clone(
    node: str,
    vmid: int,
    data: CloneInput,
    provider_id: ProviderID,
    request: Request,
    actor=Depends(require('proxmox_admin.vm.clone')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    target = _target(db, provider_id, request, node, vmid, 'vm')
    return _enqueue(
        db, request, actor, provider_id=provider_id, command='vm.clone',
        object_type='vm', object_id=vmid, node=node, parameters=_payload(data), resource=target,
    )


@router.post('/vms/{node}/{vmid}/migrate', status_code=202)
def vm_migrate(
    node: str,
    vmid: int,
    data: MigrateInput,
    provider_id: ProviderID,
    request: Request,
    actor=Depends(require('proxmox_admin.vm.migrate')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    target = _target(db, provider_id, request, node, vmid, 'vm')
    return _enqueue(
        db, request, actor, provider_id=provider_id, command='vm.migrate',
        object_type='vm', object_id=vmid, node=node, parameters=_payload(data), resource=target,
    )


@router.post('/vms/{node}/{vmid}/convert-to-template', status_code=202)
def vm_template(
    node: str,
    vmid: int,
    provider_id: ProviderID,
    request: Request,
    actor=Depends(require('proxmox_admin.templates.manage')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    target = _target(db, provider_id, request, node, vmid, 'vm')
    return _enqueue(
        db, request, actor, provider_id=provider_id, command='vm.template',
        object_type='vm', object_id=vmid, node=node, parameters={}, resource=target,
    )


@router.delete('/vms/{node}/{vmid}', status_code=202)
def vm_delete(
    node: str,
    vmid: int,
    data: DeleteResourceInput,
    provider_id: ProviderID,
    request: Request,
    actor=Depends(require('proxmox_admin.vm.delete')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    target = _target(db, provider_id, request, node, vmid, 'vm')
    return _enqueue(
        db, request, actor, provider_id=provider_id, command='vm.delete',
        object_type='vm', object_id=vmid, node=node, parameters=_payload(data),
        confirmation_expected=_target_name(target), resource=target,
    )


@router.post('/vms/{node}/{vmid}/disks', status_code=202)
def vm_disk_add(
    node: str,
    vmid: int,
    data: DiskAddInput,
    provider_id: ProviderID,
    request: Request,
    actor=Depends(require('proxmox_admin.vm.modify')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    target = _target(db, provider_id, request, node, vmid, 'vm')
    return _enqueue(
        db, request, actor, provider_id=provider_id, command='vm.disk.add',
        object_type='vm', object_id=vmid, node=node, parameters=_payload(data), resource=target,
    )


@router.post('/vms/{node}/{vmid}/disks/resize', status_code=202)
def vm_disk_resize(
    node: str,
    vmid: int,
    data: DiskResizeInput,
    provider_id: ProviderID,
    request: Request,
    actor=Depends(require('proxmox_admin.vm.modify')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    target = _target(db, provider_id, request, node, vmid, 'vm')
    return _enqueue(
        db, request, actor, provider_id=provider_id, command='vm.disk.resize',
        object_type='vm', object_id=vmid, node=node, parameters=_payload(data), resource=target,
    )


@router.delete('/vms/{node}/{vmid}/disks', status_code=202)
def vm_disk_remove(
    node: str,
    vmid: int,
    data: DiskRemoveInput,
    provider_id: ProviderID,
    request: Request,
    actor=Depends(require('proxmox_admin.vm.modify')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    target = _target(db, provider_id, request, node, vmid, 'vm')
    return _enqueue(
        db, request, actor, provider_id=provider_id, command='vm.disk.remove',
        object_type='vm', object_id=vmid, node=node, parameters=_payload(data),
        confirmation_expected=_target_name(target), resource=target,
    )


@router.post('/vms/{node}/{vmid}/disks/move', status_code=202)
def vm_disk_move(
    node: str,
    vmid: int,
    data: DiskMoveInput,
    provider_id: ProviderID,
    request: Request,
    actor=Depends(require('proxmox_admin.vm.modify')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    target = _target(db, provider_id, request, node, vmid, 'vm')
    return _enqueue(
        db, request, actor, provider_id=provider_id, command='vm.disk.move',
        object_type='vm', object_id=vmid, node=node, parameters=_payload(data),
        confirmation_expected=_target_name(target), resource=target,
    )


@router.put('/vms/{node}/{vmid}/nics', status_code=202)
def vm_nic_set(
    node: str,
    vmid: int,
    data: NICInput,
    provider_id: ProviderID,
    request: Request,
    actor=Depends(require('proxmox_admin.vm.modify')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    target = _target(db, provider_id, request, node, vmid, 'vm')
    return _enqueue(
        db, request, actor, provider_id=provider_id, command='vm.nic.set',
        object_type='vm', object_id=vmid, node=node, parameters=_payload(data),
        confirmation_expected=_target_name(target), resource=target,
    )


@router.delete('/vms/{node}/{vmid}/nics', status_code=202)
def vm_nic_remove(
    node: str,
    vmid: int,
    data: NICRemoveInput,
    provider_id: ProviderID,
    request: Request,
    actor=Depends(require('proxmox_admin.vm.modify')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    target = _target(db, provider_id, request, node, vmid, 'vm')
    return _enqueue(
        db, request, actor, provider_id=provider_id, command='vm.nic.remove',
        object_type='vm', object_id=vmid, node=node, parameters=_payload(data),
        confirmation_expected=_target_name(target), resource=target,
    )


@router.post('/vms/{node}/{vmid}/snapshots', status_code=202)
def vm_snapshot_create(
    node: str,
    vmid: int,
    data: SnapshotCreateInput,
    provider_id: ProviderID,
    request: Request,
    actor=Depends(require('proxmox_admin.snapshots.manage')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    target = _target(db, provider_id, request, node, vmid, 'vm')
    return _enqueue(
        db, request, actor, provider_id=provider_id, command='vm.snapshot.create',
        object_type='vm', object_id=vmid, node=node, parameters=_payload(data), resource=target,
    )


@router.delete('/vms/{node}/{vmid}/snapshots/{snapshot}', status_code=202)
def vm_snapshot_delete(
    node: str,
    vmid: int,
    snapshot: str,
    provider_id: ProviderID,
    request: Request,
    actor=Depends(require('proxmox_admin.snapshots.manage')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    target = _target(db, provider_id, request, node, vmid, 'vm')
    return _enqueue(
        db, request, actor, provider_id=provider_id, command='vm.snapshot.delete',
        object_type='vm', object_id=vmid, node=node, parameters={'name': snapshot}, resource=target,
    )


@router.post('/vms/{node}/{vmid}/snapshots/{snapshot}/rollback', status_code=202)
def vm_snapshot_rollback(
    node: str,
    vmid: int,
    snapshot: str,
    data: DeleteResourceInput,
    provider_id: ProviderID,
    request: Request,
    actor=Depends(require('proxmox_admin.snapshots.manage')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    target = _target(db, provider_id, request, node, vmid, 'vm')
    values = {'name': snapshot, 'confirmation': data.confirmation}
    return _enqueue(
        db, request, actor, provider_id=provider_id, command='vm.snapshot.rollback',
        object_type='vm', object_id=vmid, node=node, parameters=values,
        confirmation_expected=_target_name(target), resource=target,
    )


@router.get('/containers')
def containers(
    provider_id: ProviderID,
    request: Request,
    actor=Depends(require('proxmox_admin.containers.view')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    return list_containers(db, provider_id, request)


@router.get('/containers/{node}/{vmid}')
def container(
    node: str,
    vmid: int,
    provider_id: ProviderID,
    request: Request,
    actor=Depends(require('proxmox_admin.containers.view')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    return container_detail(db, provider_id, request, node, vmid)


@router.post('/containers/{node}/{vmid}/console')
def lxc_console(
    node: str,
    vmid: int,
    provider_id: ProviderID,
    request: Request,
    actor=Depends(require('proxmox_admin.console.use')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    _target(db, provider_id, request, node, vmid, 'lxc')
    return issue_console_session(
        provider_id, node, vmid, request, actor, db,
        object_type='container', permission='proxmox_admin.console.use',
    )


@router.post('/containers/{node}/{vmid}/power', status_code=202)
def lxc_power(
    node: str,
    vmid: int,
    data: PowerInput,
    provider_id: ProviderID,
    request: Request,
    actor=Depends(require('proxmox_admin.containers.power')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    target = _target(db, provider_id, request, node, vmid, 'lxc')
    values = _payload(data)
    expected = _target_name(target) if data.action == 'stop' else None
    return _enqueue(
        db, request, actor, provider_id=provider_id, command='lxc.power',
        object_type='container', object_id=vmid, node=node, parameters=values,
        confirmation_expected=expected, resource=target,
    )


@router.put('/containers/{node}/{vmid}/config', status_code=202)
def lxc_config(
    node: str,
    vmid: int,
    data: LXCConfigInput,
    provider_id: ProviderID,
    request: Request,
    actor=Depends(require('proxmox_admin.containers.modify')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    target = _target(db, provider_id, request, node, vmid, 'lxc')
    return _enqueue(
        db, request, actor, provider_id=provider_id, command='lxc.config',
        object_type='container', object_id=vmid, node=node, parameters=_payload(data), resource=target,
    )


@router.post('/containers/{node}/{vmid}/clone', status_code=202)
def lxc_clone(
    node: str,
    vmid: int,
    data: CloneInput,
    provider_id: ProviderID,
    request: Request,
    actor=Depends(require('proxmox_admin.containers.clone')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    target = _target(db, provider_id, request, node, vmid, 'lxc')
    return _enqueue(
        db, request, actor, provider_id=provider_id, command='lxc.clone',
        object_type='container', object_id=vmid, node=node, parameters=_payload(data), resource=target,
    )


@router.post('/containers/{node}/{vmid}/migrate', status_code=202)
def lxc_migrate(
    node: str,
    vmid: int,
    data: MigrateInput,
    provider_id: ProviderID,
    request: Request,
    actor=Depends(require('proxmox_admin.containers.migrate')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    target = _target(db, provider_id, request, node, vmid, 'lxc')
    return _enqueue(
        db, request, actor, provider_id=provider_id, command='lxc.migrate',
        object_type='container', object_id=vmid, node=node, parameters=_payload(data), resource=target,
    )


@router.delete('/containers/{node}/{vmid}', status_code=202)
def lxc_delete(
    node: str,
    vmid: int,
    data: DeleteResourceInput,
    provider_id: ProviderID,
    request: Request,
    actor=Depends(require('proxmox_admin.containers.delete')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    target = _target(db, provider_id, request, node, vmid, 'lxc')
    return _enqueue(
        db, request, actor, provider_id=provider_id, command='lxc.delete',
        object_type='container', object_id=vmid, node=node, parameters=_payload(data),
        confirmation_expected=_target_name(target), resource=target,
    )


@router.post('/containers/{node}/{vmid}/snapshots', status_code=202)
def lxc_snapshot_create(
    node: str,
    vmid: int,
    data: SnapshotCreateInput,
    provider_id: ProviderID,
    request: Request,
    actor=Depends(require('proxmox_admin.snapshots.manage')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    target = _target(db, provider_id, request, node, vmid, 'lxc')
    return _enqueue(
        db, request, actor, provider_id=provider_id, command='lxc.snapshot.create',
        object_type='container', object_id=vmid, node=node, parameters=_payload(data), resource=target,
    )


@router.delete('/containers/{node}/{vmid}/snapshots/{snapshot}', status_code=202)
def lxc_snapshot_delete(
    node: str,
    vmid: int,
    snapshot: str,
    provider_id: ProviderID,
    request: Request,
    actor=Depends(require('proxmox_admin.snapshots.manage')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    target = _target(db, provider_id, request, node, vmid, 'lxc')
    return _enqueue(
        db, request, actor, provider_id=provider_id, command='lxc.snapshot.delete',
        object_type='container', object_id=vmid, node=node, parameters={'name': snapshot}, resource=target,
    )


@router.post('/containers/{node}/{vmid}/snapshots/{snapshot}/rollback', status_code=202)
def lxc_snapshot_rollback(
    node: str,
    vmid: int,
    snapshot: str,
    data: DeleteResourceInput,
    provider_id: ProviderID,
    request: Request,
    actor=Depends(require('proxmox_admin.snapshots.manage')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    target = _target(db, provider_id, request, node, vmid, 'lxc')
    values = {'name': snapshot, 'confirmation': data.confirmation}
    return _enqueue(
        db, request, actor, provider_id=provider_id, command='lxc.snapshot.rollback',
        object_type='container', object_id=vmid, node=node, parameters=values,
        confirmation_expected=_target_name(target), resource=target,
    )


@router.get('/storage')
def storage(
    provider_id: ProviderID,
    actor=Depends(require('proxmox_admin.storage.view')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    return list_storage(db, provider_id)


@router.get('/storage/{node}/{storage_name}/content')
def storage_items(
    node: str,
    storage_name: str,
    provider_id: ProviderID,
    content: str | None = None,
    actor=Depends(require('proxmox_admin.storage.view')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    return storage_content(db, provider_id, node, storage_name, content=content)


@router.get('/templates')
def templates(
    provider_id: ProviderID,
    request: Request,
    actor=Depends(require('proxmox_admin.templates.view')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    return list_templates(db, provider_id, request)


@router.get('/snapshots')
def snapshots(
    provider_id: ProviderID,
    request: Request,
    actor=Depends(require('proxmox_admin.snapshots.view')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    return list_snapshots(db, provider_id, request)


@router.get('/backups')
def backups(
    provider_id: ProviderID,
    actor=Depends(require('proxmox_admin.backups.view')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    return list_backups(db, provider_id)


@router.post('/vms/{node}/{vmid}/backup', status_code=202)
def backup_run(
    node: str,
    vmid: int,
    data: BackupRunInput,
    provider_id: ProviderID,
    request: Request,
    actor=Depends(require('proxmox_admin.backups.run')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    target = _target(db, provider_id, request, node, vmid, 'vm')
    return _enqueue(
        db, request, actor, provider_id=provider_id, command='backup.run',
        object_type='vm', object_id=vmid, node=node, parameters=_payload(data), resource=target,
    )


@router.post('/backups/restore', status_code=202)
def backup_restore(
    data: BackupRestoreInput,
    provider_id: ProviderID,
    request: Request,
    actor=Depends(require('proxmox_admin.backups.restore')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    return _enqueue(
        db, request, actor, provider_id=provider_id, command='backup.restore',
        object_type='backup', object_id=data.archive, node=data.node, parameters=_payload(data),
        confirmation_expected=f'RESTORE {data.vmid}',
        resource={'name': data.archive, 'target_vmid': data.vmid},
    )


@router.delete('/backups', status_code=202)
def backup_delete(
    data: BackupDeleteInput,
    provider_id: ProviderID,
    request: Request,
    actor=Depends(require('proxmox_admin.backups.delete')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    return _enqueue(
        db, request, actor, provider_id=provider_id, command='backup.delete',
        object_type='backup', object_id=data.volume, node=data.node, parameters=_payload(data),
        confirmation_expected=data.volume,
        resource={'name': data.volume, 'storage': data.storage},
    )


@router.get('/cluster')
def cluster(
    provider_id: ProviderID,
    actor=Depends(require('proxmox_admin.cluster.view')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    return cluster_detail(db, provider_id)


@router.get('/tasks')
def tasks(
    provider_id: ProviderID,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    errors: bool = False,
    actor=Depends(require('proxmox_admin.tasks.view')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    return list_tasks(db, provider_id, limit=limit, errors=errors)


@router.get('/tasks/{node}/{upid}')
def task(
    node: str,
    upid: str,
    provider_id: ProviderID,
    actor=Depends(require('proxmox_admin.tasks.view')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    return task_detail(db, provider_id, node, upid)


@router.get('/firewall/{level}')
def firewall(
    level: Literal['datacenter', 'node', 'vm', 'container'],
    provider_id: ProviderID,
    node: str | None = None,
    object_id: int | None = None,
    actor=Depends(require('proxmox_admin.firewall.view')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    return firewall_snapshot(db, provider_id, level, node=node, object_id=object_id)


def _firewall_params(level, node, object_id, values):
    return {
        **values,
        '_level': level,
        '_node': node,
        '_object_id': object_id,
    }


@router.post('/firewall/{level}/rules', status_code=202)
def firewall_rule_create(
    level: Literal['datacenter', 'node', 'vm', 'container'],
    data: FirewallRuleInput,
    provider_id: ProviderID,
    request: Request,
    node: str | None = None,
    object_id: int | None = None,
    actor=Depends(require('proxmox_admin.firewall.manage')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    return _enqueue(
        db, request, actor, provider_id=provider_id, command='firewall.rule.create',
        object_type='firewall', object_id=f'{level}:{node or ""}:{object_id or ""}',
        node=node, parameters=_firewall_params(level, node, object_id, _payload(data)),
        confirmation_expected='FIREWALL',
        resource={'name': 'Proxmox Firewall', 'level': level},
    )


@router.put('/firewall/{level}/rules', status_code=202)
def firewall_rule_update(
    level: Literal['datacenter', 'node', 'vm', 'container'],
    data: FirewallRuleUpdateInput,
    provider_id: ProviderID,
    request: Request,
    node: str | None = None,
    object_id: int | None = None,
    actor=Depends(require('proxmox_admin.firewall.manage')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    return _enqueue(
        db, request, actor, provider_id=provider_id, command='firewall.rule.update',
        object_type='firewall', object_id=f'{level}:{node or ""}:{object_id or ""}',
        node=node, parameters=_firewall_params(level, node, object_id, _payload(data)),
        confirmation_expected='FIREWALL',
        resource={'name': 'Proxmox Firewall', 'level': level},
    )


@router.delete('/firewall/{level}/rules', status_code=202)
def firewall_rule_delete(
    level: Literal['datacenter', 'node', 'vm', 'container'],
    data: FirewallRuleDeleteInput,
    provider_id: ProviderID,
    request: Request,
    node: str | None = None,
    object_id: int | None = None,
    actor=Depends(require('proxmox_admin.firewall.manage')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    return _enqueue(
        db, request, actor, provider_id=provider_id, command='firewall.rule.delete',
        object_type='firewall', object_id=f'{level}:{node or ""}:{object_id or ""}',
        node=node, parameters=_firewall_params(level, node, object_id, _payload(data)),
        confirmation_expected='FIREWALL',
        resource={'name': 'Proxmox Firewall', 'level': level},
    )


@router.get('/search')
def global_search(
    provider_id: ProviderID,
    query: Annotated[str, Query(min_length=1, max_length=200)],
    request: Request,
    actor=Depends(require('proxmox_admin.search')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    return search(db, provider_id, request, query)


@router.get('/jobs')
def jobs(
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    actor=Depends(require('proxmox_admin.view')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    return {'items': list_admin_jobs(db, limit=limit)}


@router.get('/jobs/{job_id}')
def job(
    job_id: str,
    actor=Depends(require('proxmox_admin.view')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    return get_admin_job(db, job_id)


@router.get('/jobs/{job_id}/log')
def job_log(
    job_id: str,
    actor=Depends(require('proxmox_admin.view')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    get_admin_job(db, job_id)
    rows = db.scalars(
        select(JobLog).where(JobLog.job_id == job_id).order_by(JobLog.id.asc()).limit(5000)
    ).all()
    return {'items': [{
        'timestamp': row.timestamp.isoformat() + 'Z' if row.timestamp else None,
        'message': row.message,
    } for row in rows]}


@router.post('/jobs/{job_id}/cancel')
def cancel_job(
    job_id: str,
    request: Request,
    actor=Depends(require('jobs.cancel')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    job_row = db.get(Job, job_id)
    if job_row is None or not job_row.operation.startswith('pxadmin.'):
        raise HTTPException(404, {'code': 'JOB_NOT_FOUND', 'message': 'Proxmox Admin job not found'})
    return cancel_admin_job(db, request, job_row)


@router.post('/bulk', status_code=202)
def bulk(
    data: BulkInput,
    provider_id: ProviderID,
    request: Request,
    actor=Depends(require('proxmox_admin.bulk')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    request_id = str(getattr(request.state, 'request_id', '') or uuid.uuid4())
    payload = {'provider_id': provider_id, **data.model_dump(mode='json')}

    def create():
        bulk_row = BulkDay2ActionRequest(
            action='pxadmin.' + data.action,
            resource_ids=[f'{target.node}:{target.vmid}' for target in data.targets],
            requested_by=actor.user_id,
            status='QUEUED',
            request_id=request_id,
        )
        db.add(bulk_row)
        db.flush()
        children = []
        errors = []
        for target_ref in data.targets:
            try:
                with db.begin_nested():
                    target = _target(db, provider_id, request, target_ref.node, target_ref.vmid, 'vm')
                    if data.action in {'start', 'shutdown', 'reboot', 'stop'}:
                        values = {'action': data.action}
                        expected = _target_name(target) if data.action == 'stop' else None
                        if expected:
                            values['confirmation'] = expected
                        result = queue_operation(
                            db, request, actor, provider_id=provider_id, command='vm.power',
                            object_type='vm', object_id=target_ref.vmid, node=target_ref.node,
                            parameters=values, confirmation_expected=expected, resource=target,
                        )
                    elif data.action == 'snapshot':
                        result = queue_operation(
                            db, request, actor, provider_id=provider_id, command='vm.snapshot.create',
                            object_type='vm', object_id=target_ref.vmid, node=target_ref.node,
                            parameters={'name': data.snapshot, 'description': 'Bulk Proxmox Admin snapshot', 'include_ram': False},
                            resource=target,
                        )
                    else:
                        current_tags = [item for item in str(target.get('tags') or '').replace(',', ';').split(';') if item]
                        if data.action == 'add_tag' and data.tag not in current_tags:
                            current_tags.append(data.tag)
                        if data.action == 'remove_tag':
                            current_tags = [item for item in current_tags if item != data.tag]
                        result = queue_operation(
                            db, request, actor, provider_id=provider_id, command='vm.config',
                            object_type='vm', object_id=target_ref.vmid, node=target_ref.node,
                            parameters={'tags': ';'.join(current_tags)}, resource=target,
                        )
                    children.append(result)
            except HTTPException as error:
                detail = error.detail if isinstance(error.detail, dict) else {'message': str(error.detail)}
                errors.append({
                    'node': target_ref.node,
                    'vmid': target_ref.vmid,
                    'code': detail.get('code') or f'HTTP_{error.status_code}',
                    'message': detail.get('message') or 'Bulk child rejected',
                })
        bulk_row.status = 'PARTIAL' if errors else 'QUEUED'
        bulk_row.result = {'children': children, 'errors': errors}
        audit(
            db, request, 'proxmox.bulk.requested', 'proxmox_bulk', bulk_row.id,
            details={'provider_id': provider_id, 'action': data.action, 'children': children, 'errors': errors},
        )
        return {'id': bulk_row.id, 'status': bulk_row.status, **bulk_row.result}

    return idempotent(db, request, actor, payload, create, required=True)


@router.get('/bulk/{bulk_id}')
def bulk_status(
    bulk_id: str,
    actor=Depends(require('proxmox_admin.view')),
    db=Depends(get_db, scope='function'),
):
    _ready(db)
    row = db.get(BulkDay2ActionRequest, bulk_id)
    if row is None or not str(row.action or '').startswith('pxadmin.'):
        raise HTTPException(404, {'code': 'BULK_NOT_FOUND', 'message': 'Proxmox Admin bulk operation not found'})
    child_ids = [item.get('job_id') for item in (row.result or {}).get('children', []) if item.get('job_id')]
    summary = bulk_summary(db, child_ids)
    if summary['completed'] == summary['total'] and summary['total']:
        row.status = 'PARTIAL' if summary['failed'] else 'SUCCEEDED'
    elif summary['running']:
        row.status = 'RUNNING'
    return {'id': row.id, 'status': row.status, **summary, 'errors': (row.result or {}).get('errors', [])}
