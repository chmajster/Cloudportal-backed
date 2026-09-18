from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import Field, model_validator

from app.api.common import find, idempotent
from app.api.schemas import Input, Name, Slug
from app.database import get_db
from app.models import Credential, Provider
from app.providers.registry import provider_for
from app.security.core import audit, require


router = APIRouter(tags=['proxmox-vm-management'])
VMID = Annotated[int, Path(ge=100, le=999999999)]
NODE = Annotated[str, Path(pattern=r'^[A-Za-z0-9_.-]{1,63}$')]
SNAPSHOT = Annotated[str, Path(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$')]


class VMPowerInput(Input):
    action: Literal['start', 'stop', 'shutdown', 'reboot', 'reset', 'suspend', 'resume']


class SnapshotInput(Input):
    snapname: Annotated[str, Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$')]
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
        pattern=r'^[A-Za-z0-9_.-]+:backup/[A-Za-z0-9_.+-]{1,240}$',
        max_length=320,
    )]
    storage: Slug | None = None
    unique: bool = True


class ResizeDiskInput(Input):
    disk: Annotated[str, Field(pattern=r'^(?:scsi|virtio|sata|ide)\d{1,2}$')]
    grow_gib: int = Field(ge=1, le=65536)


class VMConfigInput(Input):
    name: Name | None = None
    cores: int | None = Field(default=None, ge=1, le=128)
    memory: int | None = Field(default=None, ge=512, le=1048576)
    tags: Annotated[str | None, Field(max_length=512, pattern=r'^[A-Za-z0-9_.:;-]+$')] = None
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


@router.get('/providers/{provider_id}/vms/{node}/{vmid}/status')
def vm_status(provider_id: int, node: NODE, vmid: VMID, actor=Depends(require('vms.read')),
              db=Depends(get_db, scope='function')):
    data = adapter(db, provider_id).vm_status(node, vmid)
    allowed = 'vmid name status qmpstatus cpu cpus mem maxmem disk maxdisk uptime pid lock template tags'.split()
    return {key: value for key, value in data.items() if key in allowed}


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
    payload = {'snapname': snapname}
    def execute():
        task = adapter(db, provider_id).rollback_snapshot(node, vmid, snapname)
        audit(db, request, 'snapshot.rolled_back', 'vms', f'{provider_id}:{node}:{vmid}:{snapname}')
        return task_result(task)
    return idempotent(db, request, actor, payload, execute, required=True)


@router.get('/providers/{provider_id}/vms/{node}/{vmid}/backups')
def vm_backups(provider_id: int, node: NODE, vmid: VMID,
               storage: Annotated[str, Query(pattern=r'^[A-Za-z0-9_.-]{1,63}
def convert_to_template(provider_id: int, node: NODE, vmid: VMID, request: Request,
                        actor=Depends(require('vms.template')), db=Depends(get_db, scope='function')):
    def execute():
        task = adapter(db, provider_id).convert_to_template(node, vmid)
        audit(db, request, 'vm.converted_to_template', 'vms', f'{provider_id}:{node}:{vmid}')
        return task_result(task)
    return idempotent(db, request, actor, {}, execute, required=True)


@router.post('/providers/{provider_id}/vms/{node}/{vmid}/clone', status_code=202)
def clone_vm(provider_id: int, node: NODE, vmid: VMID, data: CloneVMInput, request: Request,
             actor=Depends(require('vms.clone')), db=Depends(get_db, scope='function')):
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
        audit(db, request, 'vm.cloned', 'vms', f'{provider_id}:{node}:{vmid}->{data.new_vm_id}')
        return task_result(task)
    return idempotent(db, request, actor, data.model_dump(), execute, required=True)


@router.put('/providers/{provider_id}/vms/{node}/{vmid}/disk', status_code=202)
def resize_disk(provider_id: int, node: NODE, vmid: VMID, data: ResizeDiskInput, request: Request,
                actor=Depends(require('vms.update')), db=Depends(get_db, scope='function')):
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
    payload = data.model_dump(exclude_none=True)
    def execute():
        values = dict(payload)
        if 'onboot' in values:
            values['onboot'] = int(values['onboot'])
        task = adapter(db, provider_id).update_vm_config(node, vmid, **values)
        audit(db, request, 'vm.config_updated', 'vms', f'{provider_id}:{node}:{vmid}')
        return task_result(task)
    return idempotent(db, request, actor, payload, execute, required=True)


@router.post('/providers/{provider_id}/vms/{node}/{vmid}/migrate', status_code=202)
def migrate_vm(provider_id: int, node: NODE, vmid: VMID, data: MigrateVMInput, request: Request,
               actor=Depends(require('vms.migrate')), db=Depends(get_db, scope='function')):
    def execute():
        task = adapter(db, provider_id).migrate_vm(
            node,
            vmid,
            target=data.target,
            online=data.online,
            with_local_disks=data.with_local_disks,
        )
        audit(db, request, 'vm.migrated', 'vms', f'{provider_id}:{node}:{vmid}->{data.target}')
        return task_result(task)
    return idempotent(db, request, actor, data.model_dump(), execute, required=True)


@router.delete('/providers/{provider_id}/vms/{node}/{vmid}', status_code=202)
def delete_vm(provider_id: int, node: NODE, vmid: VMID, request: Request,
              purge: Annotated[bool, Query()] = False,
              destroy_unreferenced_disks: Annotated[bool, Query()] = False,
              actor=Depends(require('vms.delete')), db=Depends(get_db, scope='function')):
    payload = {'purge': purge, 'destroy_unreferenced_disks': destroy_unreferenced_disks}
    def execute():
        task = adapter(db, provider_id).delete_vm(
            node,
            vmid,
            purge=purge,
            destroy_unreferenced_disks=destroy_unreferenced_disks,
        )
        audit(db, request, 'vm.deleted', 'vms', f'{provider_id}:{node}:{vmid}')
        return task_result(task)
    return idempotent(db, request, actor, payload, execute, required=True)


@router.get('/providers/{provider_id}/tasks/{node}/{upid}')
def task_status(provider_id: int, node: NODE,
                upid: Annotated[str, Path(min_length=1, max_length=1024)],
                actor=Depends(require('vms.read')), db=Depends(get_db, scope='function')):
    data = adapter(db, provider_id).task_status(node, upid)
    allowed = 'status exitstatus type id user node pid pstart starttime endtime'.split()
    return {key: value for key, value in data.items() if key in allowed}
,
        max_length=320,
    )]
    storage: Slug | None = None
    unique: bool = True


class ResizeDiskInput(Input):
    disk: Annotated[str, Field(pattern=r'^(?:scsi|virtio|sata|ide)\d{1,2}$')]
    grow_gib: int = Field(ge=1, le=65536)


class VMConfigInput(Input):
    name: Name | None = None
    cores: int | None = Field(default=None, ge=1, le=128)
    memory: int | None = Field(default=None, ge=512, le=1048576)
    tags: Annotated[str | None, Field(max_length=512, pattern=r'^[A-Za-z0-9_.:;-]+$')] = None
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


@router.get('/providers/{provider_id}/vms/{node}/{vmid}/status')
def vm_status(provider_id: int, node: NODE, vmid: VMID, actor=Depends(require('vms.read')),
              db=Depends(get_db, scope='function')):
    data = adapter(db, provider_id).vm_status(node, vmid)
    allowed = 'vmid name status qmpstatus cpu cpus mem maxmem disk maxdisk uptime pid lock template tags'.split()
    return {key: value for key, value in data.items() if key in allowed}


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
    payload = {'snapname': snapname}
    def execute():
        task = adapter(db, provider_id).rollback_snapshot(node, vmid, snapname)
        audit(db, request, 'snapshot.rolled_back', 'vms', f'{provider_id}:{node}:{vmid}:{snapname}')
        return task_result(task)
    return idempotent(db, request, actor, payload, execute, required=True)


@router.post('/providers/{provider_id}/vms/{node}/{vmid}/template', status_code=202)
def convert_to_template(provider_id: int, node: NODE, vmid: VMID, request: Request,
                        actor=Depends(require('vms.template')), db=Depends(get_db, scope='function')):
    def execute():
        task = adapter(db, provider_id).convert_to_template(node, vmid)
        audit(db, request, 'vm.converted_to_template', 'vms', f'{provider_id}:{node}:{vmid}')
        return task_result(task)
    return idempotent(db, request, actor, {}, execute, required=True)


@router.post('/providers/{provider_id}/vms/{node}/{vmid}/clone', status_code=202)
def clone_vm(provider_id: int, node: NODE, vmid: VMID, data: CloneVMInput, request: Request,
             actor=Depends(require('vms.clone')), db=Depends(get_db, scope='function')):
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
        audit(db, request, 'vm.cloned', 'vms', f'{provider_id}:{node}:{vmid}->{data.new_vm_id}')
        return task_result(task)
    return idempotent(db, request, actor, data.model_dump(), execute, required=True)


@router.put('/providers/{provider_id}/vms/{node}/{vmid}/disk', status_code=202)
def resize_disk(provider_id: int, node: NODE, vmid: VMID, data: ResizeDiskInput, request: Request,
                actor=Depends(require('vms.update')), db=Depends(get_db, scope='function')):
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
    payload = data.model_dump(exclude_none=True)
    def execute():
        values = dict(payload)
        if 'onboot' in values:
            values['onboot'] = int(values['onboot'])
        task = adapter(db, provider_id).update_vm_config(node, vmid, **values)
        audit(db, request, 'vm.config_updated', 'vms', f'{provider_id}:{node}:{vmid}')
        return task_result(task)
    return idempotent(db, request, actor, payload, execute, required=True)


@router.post('/providers/{provider_id}/vms/{node}/{vmid}/migrate', status_code=202)
def migrate_vm(provider_id: int, node: NODE, vmid: VMID, data: MigrateVMInput, request: Request,
               actor=Depends(require('vms.migrate')), db=Depends(get_db, scope='function')):
    def execute():
        task = adapter(db, provider_id).migrate_vm(
            node,
            vmid,
            target=data.target,
            online=data.online,
            with_local_disks=data.with_local_disks,
        )
        audit(db, request, 'vm.migrated', 'vms', f'{provider_id}:{node}:{vmid}->{data.target}')
        return task_result(task)
    return idempotent(db, request, actor, data.model_dump(), execute, required=True)


@router.delete('/providers/{provider_id}/vms/{node}/{vmid}', status_code=202)
def delete_vm(provider_id: int, node: NODE, vmid: VMID, request: Request,
              purge: Annotated[bool, Query()] = False,
              destroy_unreferenced_disks: Annotated[bool, Query()] = False,
              actor=Depends(require('vms.delete')), db=Depends(get_db, scope='function')):
    payload = {'purge': purge, 'destroy_unreferenced_disks': destroy_unreferenced_disks}
    def execute():
        task = adapter(db, provider_id).delete_vm(
            node,
            vmid,
            purge=purge,
            destroy_unreferenced_disks=destroy_unreferenced_disks,
        )
        audit(db, request, 'vm.deleted', 'vms', f'{provider_id}:{node}:{vmid}')
        return task_result(task)
    return idempotent(db, request, actor, payload, execute, required=True)


@router.get('/providers/{provider_id}/tasks/{node}/{upid}')
def task_status(provider_id: int, node: NODE,
                upid: Annotated[str, Path(min_length=1, max_length=1024)],
                actor=Depends(require('vms.read')), db=Depends(get_db, scope='function')):
    data = adapter(db, provider_id).task_status(node, upid)
    allowed = 'status exitstatus type id user node pid pstart starttime endtime'.split()
    return {key: value for key, value in data.items() if key in allowed}
)],
               actor=Depends(require('backups.read')), db=Depends(get_db, scope='function')):
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
    source_storage = data.archive.split(':', 1)[0]
    provider = adapter(db, provider_id)
    available = provider.backups(node, source_storage)
    if not any(row.get('volid') == data.archive for row in available):
        raise HTTPException(404, 'Backup archive was not found in the selected Proxmox storage')

    def execute():
        task = provider.restore_vm(
            node, vm_id=data.vm_id, archive=data.archive, storage=data.storage, unique=data.unique
        )
        audit(db, request, 'vm.restore_started', 'vms', f'{provider_id}:{node}:{data.vm_id}')
        return task_result(task)
    return idempotent(db, request, actor, data.model_dump(), execute, required=True)


@router.post('/providers/{provider_id}/vms/{node}/{vmid}/template', status_code=202)
def convert_to_template(provider_id: int, node: NODE, vmid: VMID, request: Request,
                        actor=Depends(require('vms.template')), db=Depends(get_db, scope='function')):
    def execute():
        task = adapter(db, provider_id).convert_to_template(node, vmid)
        audit(db, request, 'vm.converted_to_template', 'vms', f'{provider_id}:{node}:{vmid}')
        return task_result(task)
    return idempotent(db, request, actor, {}, execute, required=True)


@router.post('/providers/{provider_id}/vms/{node}/{vmid}/clone', status_code=202)
def clone_vm(provider_id: int, node: NODE, vmid: VMID, data: CloneVMInput, request: Request,
             actor=Depends(require('vms.clone')), db=Depends(get_db, scope='function')):
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
        audit(db, request, 'vm.cloned', 'vms', f'{provider_id}:{node}:{vmid}->{data.new_vm_id}')
        return task_result(task)
    return idempotent(db, request, actor, data.model_dump(), execute, required=True)


@router.put('/providers/{provider_id}/vms/{node}/{vmid}/disk', status_code=202)
def resize_disk(provider_id: int, node: NODE, vmid: VMID, data: ResizeDiskInput, request: Request,
                actor=Depends(require('vms.update')), db=Depends(get_db, scope='function')):
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
    payload = data.model_dump(exclude_none=True)
    def execute():
        values = dict(payload)
        if 'onboot' in values:
            values['onboot'] = int(values['onboot'])
        task = adapter(db, provider_id).update_vm_config(node, vmid, **values)
        audit(db, request, 'vm.config_updated', 'vms', f'{provider_id}:{node}:{vmid}')
        return task_result(task)
    return idempotent(db, request, actor, payload, execute, required=True)


@router.post('/providers/{provider_id}/vms/{node}/{vmid}/migrate', status_code=202)
def migrate_vm(provider_id: int, node: NODE, vmid: VMID, data: MigrateVMInput, request: Request,
               actor=Depends(require('vms.migrate')), db=Depends(get_db, scope='function')):
    def execute():
        task = adapter(db, provider_id).migrate_vm(
            node,
            vmid,
            target=data.target,
            online=data.online,
            with_local_disks=data.with_local_disks,
        )
        audit(db, request, 'vm.migrated', 'vms', f'{provider_id}:{node}:{vmid}->{data.target}')
        return task_result(task)
    return idempotent(db, request, actor, data.model_dump(), execute, required=True)


@router.delete('/providers/{provider_id}/vms/{node}/{vmid}', status_code=202)
def delete_vm(provider_id: int, node: NODE, vmid: VMID, request: Request,
              purge: Annotated[bool, Query()] = False,
              destroy_unreferenced_disks: Annotated[bool, Query()] = False,
              actor=Depends(require('vms.delete')), db=Depends(get_db, scope='function')):
    payload = {'purge': purge, 'destroy_unreferenced_disks': destroy_unreferenced_disks}
    def execute():
        task = adapter(db, provider_id).delete_vm(
            node,
            vmid,
            purge=purge,
            destroy_unreferenced_disks=destroy_unreferenced_disks,
        )
        audit(db, request, 'vm.deleted', 'vms', f'{provider_id}:{node}:{vmid}')
        return task_result(task)
    return idempotent(db, request, actor, payload, execute, required=True)


@router.get('/providers/{provider_id}/tasks/{node}/{upid}')
def task_status(provider_id: int, node: NODE,
                upid: Annotated[str, Path(min_length=1, max_length=1024)],
                actor=Depends(require('vms.read')), db=Depends(get_db, scope='function')):
    data = adapter(db, provider_id).task_status(node, upid)
    allowed = 'status exitstatus type id user node pid pstart starttime endtime'.split()
    return {key: value for key, value in data.items() if key in allowed}
,
        max_length=320,
    )]
    storage: Slug | None = None
    unique: bool = True


class ResizeDiskInput(Input):
    disk: Annotated[str, Field(pattern=r'^(?:scsi|virtio|sata|ide)\d{1,2}$')]
    grow_gib: int = Field(ge=1, le=65536)


class VMConfigInput(Input):
    name: Name | None = None
    cores: int | None = Field(default=None, ge=1, le=128)
    memory: int | None = Field(default=None, ge=512, le=1048576)
    tags: Annotated[str | None, Field(max_length=512, pattern=r'^[A-Za-z0-9_.:;-]+$')] = None
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


@router.get('/providers/{provider_id}/vms/{node}/{vmid}/status')
def vm_status(provider_id: int, node: NODE, vmid: VMID, actor=Depends(require('vms.read')),
              db=Depends(get_db, scope='function')):
    data = adapter(db, provider_id).vm_status(node, vmid)
    allowed = 'vmid name status qmpstatus cpu cpus mem maxmem disk maxdisk uptime pid lock template tags'.split()
    return {key: value for key, value in data.items() if key in allowed}


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
    payload = {'snapname': snapname}
    def execute():
        task = adapter(db, provider_id).rollback_snapshot(node, vmid, snapname)
        audit(db, request, 'snapshot.rolled_back', 'vms', f'{provider_id}:{node}:{vmid}:{snapname}')
        return task_result(task)
    return idempotent(db, request, actor, payload, execute, required=True)


@router.post('/providers/{provider_id}/vms/{node}/{vmid}/template', status_code=202)
def convert_to_template(provider_id: int, node: NODE, vmid: VMID, request: Request,
                        actor=Depends(require('vms.template')), db=Depends(get_db, scope='function')):
    def execute():
        task = adapter(db, provider_id).convert_to_template(node, vmid)
        audit(db, request, 'vm.converted_to_template', 'vms', f'{provider_id}:{node}:{vmid}')
        return task_result(task)
    return idempotent(db, request, actor, {}, execute, required=True)


@router.post('/providers/{provider_id}/vms/{node}/{vmid}/clone', status_code=202)
def clone_vm(provider_id: int, node: NODE, vmid: VMID, data: CloneVMInput, request: Request,
             actor=Depends(require('vms.clone')), db=Depends(get_db, scope='function')):
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
        audit(db, request, 'vm.cloned', 'vms', f'{provider_id}:{node}:{vmid}->{data.new_vm_id}')
        return task_result(task)
    return idempotent(db, request, actor, data.model_dump(), execute, required=True)


@router.put('/providers/{provider_id}/vms/{node}/{vmid}/disk', status_code=202)
def resize_disk(provider_id: int, node: NODE, vmid: VMID, data: ResizeDiskInput, request: Request,
                actor=Depends(require('vms.update')), db=Depends(get_db, scope='function')):
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
    payload = data.model_dump(exclude_none=True)
    def execute():
        values = dict(payload)
        if 'onboot' in values:
            values['onboot'] = int(values['onboot'])
        task = adapter(db, provider_id).update_vm_config(node, vmid, **values)
        audit(db, request, 'vm.config_updated', 'vms', f'{provider_id}:{node}:{vmid}')
        return task_result(task)
    return idempotent(db, request, actor, payload, execute, required=True)


@router.post('/providers/{provider_id}/vms/{node}/{vmid}/migrate', status_code=202)
def migrate_vm(provider_id: int, node: NODE, vmid: VMID, data: MigrateVMInput, request: Request,
               actor=Depends(require('vms.migrate')), db=Depends(get_db, scope='function')):
    def execute():
        task = adapter(db, provider_id).migrate_vm(
            node,
            vmid,
            target=data.target,
            online=data.online,
            with_local_disks=data.with_local_disks,
        )
        audit(db, request, 'vm.migrated', 'vms', f'{provider_id}:{node}:{vmid}->{data.target}')
        return task_result(task)
    return idempotent(db, request, actor, data.model_dump(), execute, required=True)


@router.delete('/providers/{provider_id}/vms/{node}/{vmid}', status_code=202)
def delete_vm(provider_id: int, node: NODE, vmid: VMID, request: Request,
              purge: Annotated[bool, Query()] = False,
              destroy_unreferenced_disks: Annotated[bool, Query()] = False,
              actor=Depends(require('vms.delete')), db=Depends(get_db, scope='function')):
    payload = {'purge': purge, 'destroy_unreferenced_disks': destroy_unreferenced_disks}
    def execute():
        task = adapter(db, provider_id).delete_vm(
            node,
            vmid,
            purge=purge,
            destroy_unreferenced_disks=destroy_unreferenced_disks,
        )
        audit(db, request, 'vm.deleted', 'vms', f'{provider_id}:{node}:{vmid}')
        return task_result(task)
    return idempotent(db, request, actor, payload, execute, required=True)


@router.get('/providers/{provider_id}/tasks/{node}/{upid}')
def task_status(provider_id: int, node: NODE,
                upid: Annotated[str, Path(min_length=1, max_length=1024)],
                actor=Depends(require('vms.read')), db=Depends(get_db, scope='function')):
    data = adapter(db, provider_id).task_status(node, upid)
    allowed = 'status exitstatus type id user node pid pstart starttime endtime'.split()
    return {key: value for key, value in data.items() if key in allowed}
