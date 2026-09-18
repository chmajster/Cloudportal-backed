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
