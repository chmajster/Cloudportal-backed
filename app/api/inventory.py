from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field
from sqlalchemy import select

from app.api.administration import Limit, Offset
from app.api.common import find, idempotent
from app.api.schemas import Input
from app.database import get_db
from app.models import Credential, ManagedResource, ManagedVM, Provider
from app.providers.registry import provider_for
from app.security.core import audit, require


router = APIRouter(prefix='/inventory', tags=['inventory'])
FIELDS = (
    'id provider_id deployment_id node vm_id name management_mode lifecycle_status '
    'created_by created_at updated_at destroyed_at'
)


class ImportVMInput(Input):
    provider_id: int = Field(gt=0)
    vm_id: int = Field(ge=100, le=999999999)


def public(row):
    return {field: getattr(row, field) for field in FIELDS.split()}


def provider_adapter(db, provider_id):
    provider = find(db, Provider, provider_id)
    credential = find(db, Credential, provider.credentials_id)
    return provider, provider_for(credential)


def discover_vm(adapter, vm_id):
    rows = adapter.discover('vms')
    matches = [row for row in rows if int(row.get('vmid', -1)) == vm_id]
    if not matches:
        raise HTTPException(404, 'VM was not found on the provider')
    if len(matches) != 1:
        raise HTTPException(409, 'Provider returned an ambiguous VM identity')
    return matches[0]


def live_public(row):
    allowed = 'vmid name node status template type tags mem maxmem cpu maxcpu disk maxdisk uptime'.split()
    return {key: value for key, value in row.items() if key in allowed}


@router.get('/vms')
def managed_vms(provider_id: Annotated[int | None, Query(gt=0)] = None,
                management_mode: Annotated[Literal['external', 'terraform'] | None, Query()] = None,
                lifecycle_status: Annotated[Literal['active', 'missing', 'destroyed'] | None, Query()] = None,
                refresh: bool = False, limit: Limit = 100, offset: Offset = 0,
                actor=Depends(require('inventory.read')), db=Depends(get_db, scope='function')):
    query = select(ManagedVM)
    if provider_id:
        query = query.where(ManagedVM.provider_id == provider_id)
    if management_mode:
        query = query.where(ManagedVM.management_mode == management_mode)
    if lifecycle_status:
        query = query.where(ManagedVM.lifecycle_status == lifecycle_status)
    rows = db.scalars(query.order_by(ManagedVM.created_at.desc()).offset(offset).limit(limit)).all()
    result = [public(row) for row in rows]
    if refresh and rows:
        by_provider = {}
        for row in rows:
            by_provider.setdefault(row.provider_id, []).append(row)
        for pid, managed in by_provider.items():
            _, adapter = provider_adapter(db, pid)
            discovered = {int(vm['vmid']): vm for vm in adapter.discover('vms') if vm.get('vmid') is not None}
            for item, row in zip([x for x in result if x['provider_id'] == pid], managed):
                if row.vm_id in discovered:
                    item['live'] = live_public(discovered[row.vm_id])
                else:
                    item['live'] = None
    return {'items': result}


@router.get('/vms/{id}')
def managed_vm(id: str, refresh: bool = False, actor=Depends(require('inventory.read')),
               db=Depends(get_db, scope='function')):
    row = find(db, ManagedVM, id)
    result = public(row)
    if refresh:
        _, adapter = provider_adapter(db, row.provider_id)
        try:
            result['live'] = live_public(discover_vm(adapter, row.vm_id))
        except HTTPException as error:
            if error.status_code != 404:
                raise
            result['live'] = None
    return result


@router.post('/vms/import', status_code=201)
def import_vm(data: ImportVMInput, request: Request, actor=Depends(require('inventory.import')),
              db=Depends(get_db, scope='function')):
    def create():
        provider, adapter = provider_adapter(db, data.provider_id)
        if provider.type != 'proxmox':
            raise HTTPException(422, 'Existing VM import is currently supported for Proxmox')
        existing = db.scalar(select(ManagedVM).where(
            ManagedVM.provider_id == data.provider_id,
            ManagedVM.vm_id == data.vm_id,
        ))
        if existing:
            raise HTTPException(409, 'VM is already present in the managed inventory')
        live = discover_vm(adapter, data.vm_id)
        row = ManagedVM(
            provider_id=data.provider_id,
            node=str(live.get('node', '')),
            vm_id=data.vm_id,
            name=str(live.get('name', '')),
            management_mode='external',
            lifecycle_status='active',
            created_by=actor.user_id,
        )
        db.add(row)
        db.flush()
        audit(db, request, 'inventory.vm_imported', 'managed_vms', row.id)
        return public(row)
    return idempotent(db, request, actor, data.model_dump(), create, required=True)


@router.post('/vms/{id}/reconcile')
def reconcile_vm(id: str, request: Request, actor=Depends(require('inventory.update')),
                 db=Depends(get_db, scope='function')):
    row = db.scalar(select(ManagedVM).where(ManagedVM.id == id).with_for_update())
    if row is None:
        raise HTTPException(404, 'Resource not found')
    _, adapter = provider_adapter(db, row.provider_id)
    try:
        live = discover_vm(adapter, row.vm_id)
    except HTTPException as error:
        if error.status_code != 404:
            raise
        row.lifecycle_status = 'missing' if row.lifecycle_status != 'destroyed' else 'destroyed'
        audit(db, request, 'inventory.vm_missing', 'managed_vms', row.id)
        return public(row)
    row.node = str(live.get('node', row.node))
    row.name = str(live.get('name', row.name))
    row.lifecycle_status = 'active'
    audit(db, request, 'inventory.vm_reconciled', 'managed_vms', row.id)
    return public(row)


@router.delete('/vms/{id}')
def unmanage_vm(id: str, request: Request, actor=Depends(require('inventory.delete')),
                db=Depends(get_db, scope='function')):
    row = find(db, ManagedVM, id)
    if row.management_mode == 'terraform' and row.lifecycle_status != 'destroyed':
        raise HTTPException(409, 'Active Terraform-managed VM must be destroyed through its deployment')
    db.delete(row)
    audit(db, request, 'inventory.vm_unmanaged', 'managed_vms', id)
    return {'deleted': True}



@router.get('/resources')
def managed_resources(provider: Annotated[str | None, Query(max_length=32)] = None,
                      lifecycle_status: Annotated[Literal['active', 'destroyed'] | None, Query()] = None,
                      limit: Limit = 100, offset: Offset = 0,
                      actor=Depends(require('inventory.read')), db=Depends(get_db, scope='function')):
    query = select(ManagedResource)
    if provider:
        query = query.where(ManagedResource.provider == provider)
    if lifecycle_status:
        query = query.where(ManagedResource.lifecycle_status == lifecycle_status)
    rows = db.scalars(query.order_by(ManagedResource.created_at.desc()).offset(offset).limit(limit)).all()
    fields = (
        'id deployment_id provider_id provider resource_type external_id name primary_ip '
        'lifecycle_status metadata_json created_by created_at updated_at destroyed_at'
    )
    return {'items': [{field: getattr(row, field) for field in fields.split()} for row in rows]}


@router.get('/resources/{id}')
def managed_resource(id: str, actor=Depends(require('inventory.read')), db=Depends(get_db, scope='function')):
    row = find(db, ManagedResource, id)
    fields = (
        'id deployment_id provider_id provider resource_type external_id name primary_ip '
        'lifecycle_status metadata_json created_by created_at updated_at destroyed_at'
    )
    return {field: getattr(row, field) for field in fields.split()}
