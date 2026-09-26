import math
import re
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field
from sqlalchemy import or_, select

from app.api.common import Limit, Offset, find, idempotent
from app.access import (ensure_inventory_resource_access, ensure_inventory_vm_access,
                        inventory_resource_predicate, inventory_vm_predicate)
from app.api.schemas import Input, Slug
from app.catalog import template_import_target, template_public, validate_template_variables
from app.catalog_control import require_catalog_item_enabled
from app.database import get_db
from app.day2.models import Day2ActionRequest
from app.inventory_sync import repair_inventory_from_states
from app.jobs.lifecycle import release_pre_execution_allocations
from app.models import Audit, Credential, Deployment, Job, JobLog, ManagedResource, ManagedVM, Provider, now
from app.providers.registry import provider_for
from app.security.core import audit
from app.resource_scope.http import require
from app.resource_scope.authorization import Scope
from app.quotas.service import account_confirmed_absent, reconcile_terraform_presence


router = APIRouter(prefix='/inventory', tags=['inventory'])
FIELDS = (
    'tenant_id project_id id provider_id deployment_id node vm_id name management_mode lifecycle_status '
    'created_by created_at updated_at destroyed_at'
)
RESOURCE_FIELDS = (
    'tenant_id project_id id deployment_id provider_id provider resource_type external_id name primary_ip '
    'lifecycle_status metadata_json created_by created_at updated_at destroyed_at'
)


class ImportVMInput(Input):
    provider_id: int = Field(gt=0)
    vm_id: int = Field(ge=100, le=999999999)


class AdoptVMInput(Input):
    template: Slug = 'proxmox-vm'
    variables: dict
    executor: Literal['terraform', 'opentofu'] = 'terraform'


def public(row):
    return {field: getattr(row, field) for field in FIELDS.split()}


def resource_public(row):
    return {field: getattr(row, field) for field in RESOURCE_FIELDS.split()}


HISTORY_LOG_PREFIXES = (
    'job.', 'workflow.', 'terraform.', 'inventory.', 'cloud_init', 'cloud-init',
    'wait_for_', 'ansible.', 'awx.', 'provider.', 'proxmox.', 'hostname.',
    'ipam.', 'guest_', 'snapshot.', 'backup.',
)


def history_log_message(message):
    value = str(message or '').strip()
    if not value:
        return None
    normalized = value.lower()
    if not normalized.startswith(HISTORY_LOG_PREFIXES):
        return None
    return value[:500]


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


def live_disk_total_gib(live):
    total = 0.0
    for device, raw in dict(live or {}).items():
        if not re.fullmatch(r'(?:scsi|virtio|sata|ide)\d{1,2}', str(device)):
            continue
        raw = str(raw or '')
        if re.search(r'(?:^|,)media=cdrom(?:,|$)', raw, re.I):
            continue
        size_match = re.search(r'(?:^|,)size=([0-9]+(?:\.[0-9]+)?)([KMGT])(?:,|$)', raw, re.I)
        if not size_match:
            raise HTTPException(409, f'Provider did not report a size for governed disk {device}')
        size = float(size_match.group(1))
        unit = size_match.group(2).upper()
        factors = {'K': 1 / 1024 / 1024, 'M': 1 / 1024, 'G': 1, 'T': 1024}
        total += size * factors[unit]
    return max(0, math.ceil(total))


def effective_vm_lifecycle(row, present):
    if row.lifecycle_status == 'destroyed':
        return 'destroyed'
    return 'active' if present else 'missing'


def reconcile_provider_vm_presence(db, request, actor, rows):
    """Persist active/missing state only when a provider inventory read succeeds."""
    by_provider = {}
    for row in rows:
        if row.lifecycle_status == 'destroyed':
            continue
        by_provider.setdefault(row.provider_id, []).append(row)

    missing = []
    restored = []
    provider_errors = []
    for provider_id, managed in by_provider.items():
        try:
            provider, adapter = provider_adapter(db, provider_id)
            if provider.type != 'proxmox':
                continue
            discovered = {
                int(vm['vmid']): vm
                for vm in adapter.discover('vms')
                if vm.get('vmid') is not None
            }
        except Exception as error:
            provider_errors.append({
                'provider_id': provider_id,
                'error': str(getattr(error, 'detail', None) or error),
            })
            continue

        for row in managed:
            present = row.vm_id in discovered
            expected = effective_vm_lifecycle(row, present)
            if row.lifecycle_status == expected:
                continue
            row.lifecycle_status = expected
            if present:
                live = discovered[row.vm_id]
                row.node = str(live.get('node', row.node))
                row.name = str(live.get('name', row.name))
                restored.append(row.id)
                audit(db, request, 'inventory.vm_restored', 'managed_vms', row.id)
            else:
                missing.append(row.id)
                audit(db, request, 'inventory.vm_missing', 'managed_vms', row.id)

            if row.deployment_id:
                reconcile_terraform_presence(
                    db,
                    Scope(row.tenant_id, row.project_id),
                    row.deployment_id,
                    present=present,
                )

    return {
        'missing': missing,
        'restored': restored,
        'provider_errors': provider_errors,
    }


def adoption_suggestion(row, live):
    disk_spec = str(live.get('scsi0') or live.get('virtio0') or live.get('sata0') or '')
    storage = disk_spec.split(':', 1)[0] if ':' in disk_spec else ''
    size_match = re.search(r'(?:^|,)size=([0-9]+(?:\.[0-9]+)?)([KMGT])(?:,|$)', disk_spec, re.I)
    disk_gib = 40
    if size_match:
        size = float(size_match.group(1))
        unit = size_match.group(2).upper()
        factors = {'K': 1 / 1024 / 1024, 'M': 1 / 1024, 'G': 1, 'T': 1024}
        disk_gib = max(1, math.ceil(size * factors[unit]))

    net_spec = str(live.get('net0') or '')
    bridge_match = re.search(r'(?:^|,)bridge=([^,]+)', net_spec)
    tag_match = re.search(r'(?:^|,)tag=([0-9]{1,4})(?:,|$)', net_spec)
    suggestion = {
        'name': str(live.get('name') or row.name or f'vm-{row.vm_id}'),
        'node': row.node,
        'template_id': row.vm_id,
        'cpu': int(live.get('cores') or 2),
        'memory': int(live.get('memory') or 4096),
        'disk': disk_gib,
        'network': bridge_match.group(1) if bridge_match else 'vmbr0',
        'storage': storage or 'local-lvm',
    }
    if tag_match:
        suggestion['vlan_id'] = int(tag_match.group(1))
    return suggestion


@router.post('/reconcile')
def reconcile_inventory(
    request: Request,
    actor=Depends(require('inventory.update')),
    db=Depends(get_db, scope='function'),
):
    result = repair_inventory_from_states(db, limit=500)
    for item in result['repaired']:
        audit(
            db,
            request,
            'inventory.recovered',
            'managed_vms' if item['vm_id'] is not None else 'managed_resources',
            item['deployment_id'],
        )

    query = select(ManagedVM)
    predicate = inventory_vm_predicate(request, actor)
    if predicate is not None:
        query = query.where(predicate)
    managed = db.scalars(query.where(ManagedVM.lifecycle_status != 'destroyed')).all()
    presence = reconcile_provider_vm_presence(db, request, actor, managed)

    return {
        'repaired': result['repaired'],
        'repaired_count': len(result['repaired']),
        'skipped_count': len(result['skipped']),
        'missing': presence['missing'],
        'missing_count': len(presence['missing']),
        'restored': presence['restored'],
        'restored_count': len(presence['restored']),
        'provider_errors': presence['provider_errors'],
    }


@router.get('/vms')
def managed_vms(
    request: Request,
    provider_id: Annotated[int | None, Query(gt=0)] = None,
    management_mode: Annotated[Literal['external', 'terraform'] | None, Query()] = None,
    lifecycle_status: Annotated[Literal['active', 'missing', 'destroyed'] | None, Query()] = None,
    refresh: bool = False,
    limit: Limit = 100,
    offset: Offset = 0,
    actor=Depends(require('inventory.read')),
    db=Depends(get_db, scope='function'),
):
    query = select(ManagedVM)
    predicate = inventory_vm_predicate(request, actor)
    if predicate is not None:
        query = query.where(predicate)
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
            discovered = {
                int(vm['vmid']): vm
                for vm in adapter.discover('vms')
                if vm.get('vmid') is not None
            }
            result_by_id = {item['id']: item for item in result}
            for row in managed:
                item = result_by_id[row.id]
                present = row.vm_id in discovered
                item['live'] = live_public(discovered[row.vm_id]) if present else None
                item['lifecycle_status'] = effective_vm_lifecycle(row, present)
    return {'items': result}


@router.get('/vms/{id}')
def managed_vm(
    id: str,
    request: Request,
    refresh: bool = False,
    actor=Depends(require('inventory.read')),
    db=Depends(get_db, scope='function'),
):
    row = ensure_inventory_vm_access(db, request, actor, find(db, ManagedVM, id))
    result = public(row)
    if refresh:
        _, adapter = provider_adapter(db, row.provider_id)
        try:
            result['live'] = live_public(discover_vm(adapter, row.vm_id))
            result['lifecycle_status'] = effective_vm_lifecycle(row, True)
        except HTTPException as error:
            if error.status_code != 404:
                raise
            result['live'] = None
            result['lifecycle_status'] = effective_vm_lifecycle(row, False)
    return result


@router.get('/vms/{id}/history')
def managed_vm_history(
    id: str,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
    actor=Depends(require('inventory.read')),
    db=Depends(get_db, scope='function'),
):
    row = ensure_inventory_vm_access(db, request, actor, find(db, ManagedVM, id))
    entries = []

    def add(timestamp, kind, title, *, status=None, detail=None, job_id=None,
            request_id=None, actor_user_id=None):
        if timestamp is None:
            return
        entries.append({
            'timestamp': timestamp,
            'kind': kind,
            'title': str(title or '')[:500],
            'status': str(status)[:64] if status is not None else None,
            'detail': str(detail)[:1000] if detail else None,
            'job_id': str(job_id) if job_id else None,
            'request_id': str(request_id) if request_id else None,
            'actor_user_id': actor_user_id,
        })

    add(
        row.created_at,
        'lifecycle',
        'VM dodana do inventory',
        status=row.lifecycle_status,
        detail=f'{row.name or "VM"} / VMID {row.vm_id} / {row.management_mode}',
        actor_user_id=row.created_by,
    )
    if row.destroyed_at:
        add(row.destroyed_at, 'lifecycle', 'VM oznaczona jako usunięta', status='destroyed')

    if row.deployment_id:
        deployment = db.get(Deployment, row.deployment_id)
        if deployment is not None:
            add(
                deployment.created_at,
                'deployment',
                'Utworzono deployment',
                status=deployment.status,
                detail=f'{deployment.name} / {deployment.template}',
                actor_user_id=deployment.created_by,
            )

        if 'jobs.read' in request.state.permissions:
            jobs = db.scalars(
                select(Job)
                .where(Job.deployment_id == row.deployment_id)
                .order_by(Job.created_at.asc())
                .limit(200)
            ).all()
            for job in jobs:
                stage = str((job.payload or {}).get('_current_stage') or '').strip()
                detail = job.error or (f'Etap: {stage}' if stage else None)
                add(
                    job.created_at,
                    'job',
                    job.operation,
                    status=job.status,
                    detail=detail,
                    job_id=job.id,
                    request_id=job.request_id,
                    actor_user_id=job.created_by,
                )
                logs = db.scalars(
                    select(JobLog)
                    .where(JobLog.job_id == job.id)
                    .order_by(JobLog.timestamp.asc())
                    .limit(500)
                ).all()
                for log in logs:
                    message = history_log_message(log.message)
                    if message:
                        add(
                            log.timestamp,
                            'provisioning',
                            message,
                            status=job.status,
                            job_id=job.id,
                            request_id=job.request_id,
                            actor_user_id=job.created_by,
                        )

    if 'day2.view' in request.state.permissions:
        day2_rows = db.scalars(
            select(Day2ActionRequest)
            .where(Day2ActionRequest.resource_id == row.id)
            .order_by(Day2ActionRequest.requested_at.asc())
            .limit(200)
        ).all()
        for action in day2_rows:
            add(
                action.requested_at,
                'day2',
                f'Day-2: {action.action}',
                status=action.status,
                detail=action.error_message or action.reason,
                job_id=action.job_id,
                request_id=action.request_id,
                actor_user_id=action.requested_by,
            )

    if 'audit.read' in request.state.permissions:
        target = f'{row.provider_id}:{row.node}:{row.vm_id}'
        audit_filters = [
            Audit.resource_id == row.id,
            Audit.resource_id == target,
            Audit.resource_id.like(target + ':%'),
        ]
        if row.deployment_id:
            audit_filters.append(Audit.resource_id == row.deployment_id)
        audit_rows = db.scalars(
            select(Audit)
            .where(or_(*audit_filters))
            .order_by(Audit.timestamp.asc())
            .limit(500)
        ).all()
        for entry in audit_rows:
            add(
                entry.timestamp,
                'audit',
                entry.action,
                status=entry.result,
                detail=entry.resource,
                request_id=entry.request_id,
                actor_user_id=entry.user_id,
            )

    entries.sort(key=lambda item: item['timestamp'], reverse=True)
    return {'items': entries[:limit]}


@router.post('/vms/import', status_code=201)
def import_vm(
    data: ImportVMInput,
    request: Request,
    actor=Depends(require('inventory.import')),
    db=Depends(get_db, scope='function'),
):
    from app.resource_scope.service import guard_vm_identity
    guard_vm_identity(db, data.provider_id, data.vm_id, request.state.resource_scope)
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


@router.get('/vms/{id}/adoption-preview')
def adoption_preview(
    id: str,
    request: Request,
    template: Annotated[str, Query(max_length=63)] = 'proxmox-vm',
    actor=Depends(require('deployments.adopt')),
    db=Depends(get_db, scope='function'),
):
    row = ensure_inventory_vm_access(db, request, actor, find(db, ManagedVM, id))
    if row.management_mode != 'external' or row.lifecycle_status != 'active' or row.deployment_id:
        raise HTTPException(409, 'Only an active external VM can be adopted')
    provider, adapter = provider_adapter(db, row.provider_id)
    if provider.type != 'proxmox':
        raise HTTPException(422, 'VM adoption is currently implemented for Proxmox')
    require_catalog_item_enabled(db, 'templates', template)
    template_meta = template_public(template)
    if template_meta['provider'] != provider.type or not template_meta['importable']:
        raise HTTPException(422, 'Selected Terraform template cannot import this provider resource')
    live = adapter.vm_config(row.node, row.vm_id)
    allowed = 'name cores sockets memory scsi0 virtio0 sata0 net0 tags onboot agent'.split()
    return {
        'inventory': public(row),
        'live': {key: value for key, value in live.items() if key in allowed},
        'suggested_variables': adoption_suggestion(row, live),
        'template': template_meta,
        'plan_only': True,
    }


@router.post('/vms/{id}/adopt', status_code=202)
def adopt_vm(
    id: str,
    data: AdoptVMInput,
    request: Request,
    actor=Depends(require('deployments.adopt')),
    db=Depends(get_db, scope='function'),
):
    from app.api.infrastructure import deployment_public, job_public, locked_credential, new_job

    row = db.scalar(select(ManagedVM).where(ManagedVM.id == id).with_for_update())
    ensure_inventory_vm_access(db, request, actor, row)
    if row.management_mode != 'external' or row.lifecycle_status != 'active' or row.deployment_id:
        raise HTTPException(409, 'Only an active external VM can be adopted')

    provider, adapter = provider_adapter(db, row.provider_id)
    if provider.type != 'proxmox':
        raise HTTPException(422, 'VM adoption is currently implemented for Proxmox')
    require_catalog_item_enabled(db, 'templates', data.template)
    template_meta = template_public(data.template)
    if template_meta['provider'] != provider.type or not template_meta['importable']:
        raise HTTPException(422, 'Selected Terraform template cannot import this provider resource')
    variables = validate_template_variables(data.template, data.variables)
    if variables.node != row.node:
        raise HTTPException(422, 'Adoption variables must reference the VM current Proxmox node')
    import_values = {'node': row.node, 'vm_id': row.vm_id}
    template_import_target(data.template, import_values)
    locked_credential(db, provider.credentials_id)

    def create():
        live = adapter.vm_config(row.node, row.vm_id)
        confirmed = adoption_suggestion(row, live)
        quota_dimensions = {
            'vm_count': 1,
            'vcpu': max(1, int(live.get('cores') or confirmed['cpu'])) * max(1, int(live.get('sockets') or 1)),
            'memory_mb': max(0, int(live.get('memory') or confirmed['memory'])),
            'disk_gib': live_disk_total_gib(live),
        }
        deployment = Deployment(
            name=variables.name,
            provider_id=provider.id,
            provider=provider.type,
            template=data.template,
            credentials_id=provider.credentials_id,
            variables=variables.model_dump(mode='json'),
            workflow={
                'adoption': {
                    'managed_vm_id': row.id,
                    'plan_only': True,
                    'import_values': import_values,
                }
            },
            created_by=actor.user_id,
            executor=data.executor,
        )
        db.add(deployment)
        db.flush()
        deployment.state_location = f'database://terraform-states/{deployment.id}'
        job = new_job(
            db,
            request,
            actor,
            'terraform.import',
            deployment,
            {
                'import_values': import_values,
                'adoption': {'managed_vm_id': row.id, 'plan_only': True},
                '_quota_dimensions': quota_dimensions,
            },
        )
        audit(db, request, 'inventory.vm_adoption_started', 'managed_vms', row.id)
        audit(db, request, 'deployment.created', 'deployments', deployment.id)
        return {**deployment_public(deployment), 'job': job_public(job)}

    return idempotent(
        db,
        request,
        actor,
        {'managed_vm_id': id, **data.model_dump(mode='json')},
        create,
        required=True,
    )


@router.post('/vms/{id}/reconcile')
def reconcile_vm(
    id: str,
    request: Request,
    actor=Depends(require('inventory.update')),
    db=Depends(get_db, scope='function'),
):
    row = db.scalar(select(ManagedVM).where(ManagedVM.id == id).with_for_update())
    ensure_inventory_vm_access(db, request, actor, row)
    _, adapter = provider_adapter(db, row.provider_id)
    try:
        live = discover_vm(adapter, row.vm_id)
    except HTTPException as error:
        if error.status_code != 404:
            raise
        row.lifecycle_status = 'missing' if row.lifecycle_status != 'destroyed' else 'destroyed'
        if row.deployment_id:
            reconcile_terraform_presence(
                db, Scope(row.tenant_id, row.project_id), row.deployment_id, present=False
            )
        audit(db, request, 'inventory.vm_missing', 'managed_vms', row.id)
        return public(row)
    row.node = str(live.get('node', row.node))
    row.name = str(live.get('name', row.name))
    row.lifecycle_status = 'active'
    if row.deployment_id:
        reconcile_terraform_presence(
            db, Scope(row.tenant_id, row.project_id), row.deployment_id, present=True
        )
    audit(db, request, 'inventory.vm_reconciled', 'managed_vms', row.id)
    return public(row)


@router.delete('/vms/{id}/missing')
def remove_confirmed_missing_vm(
    id: str,
    request: Request,
    purge: bool = False,
    actor=Depends(require('inventory.delete')),
    db=Depends(get_db, scope='function'),
):
    """Remove a stale VM from active inventory only after provider-side absence is confirmed."""
    row = db.scalar(select(ManagedVM).where(ManagedVM.id == id).with_for_update())
    ensure_inventory_vm_access(db, request, actor, row)

    provider, adapter = provider_adapter(db, row.provider_id)
    if provider.type != 'proxmox':
        raise HTTPException(422, 'Missing-VM cleanup is currently supported only for Proxmox')

    try:
        discover_vm(adapter, row.vm_id)
    except HTTPException as error:
        if error.status_code != 404:
            raise
    else:
        raise HTTPException(
            409,
            'VM still exists on Proxmox. Refresh inventory instead of removing it.',
        )

    if row.deployment_id:
        deployment = db.scalar(select(Deployment).where(
            Deployment.id == row.deployment_id
        ).with_for_update())
        if deployment is not None and deployment.active_job_id:
            raise HTTPException(
                409,
                'Cannot remove a missing VM while its deployment has an active job',
            )

    when = now()
    resource = None
    deployment_id = row.deployment_id
    vm_record_id = row.id
    vm_id = row.vm_id

    if row.deployment_id:
        resource = db.scalar(select(ManagedResource).where(
            ManagedResource.deployment_id == row.deployment_id
        ).with_for_update())

        scope = Scope(row.tenant_id, row.project_id)
        reconcile_terraform_presence(
            db,
            scope,
            row.deployment_id,
            present=False,
        )

        if deployment is not None:
            # Provider-side absence is stronger evidence than stale Terraform
            # state. Finalize the deployment as destroyed so the automatic
            # inventory repair loop cannot recreate this VM from old state.
            deployment.status = 'destroyed'
            deployment.destroyed_at = deployment.destroyed_at or when
            deployment.active_job_id = None
            release_pre_execution_allocations(db, deployment.id)
            account_confirmed_absent(
                db,
                scope,
                'deployment',
                deployment.id,
                f'inventory-missing-purge:{row.id}',
                actor.user_id,
            )

    if purge:
        audit(db, request, 'inventory.vm_missing_purged', 'managed_vms', row.id)
        if resource is not None:
            db.delete(resource)
        db.delete(row)
    else:
        row.lifecycle_status = 'destroyed'
        row.destroyed_at = when
        if resource is not None:
            resource.lifecycle_status = 'destroyed'
            resource.destroyed_at = when
        audit(db, request, 'inventory.vm_missing_removed', 'managed_vms', row.id)

    return {
        'deleted': True,
        'purged': purge,
        'provider_absent': True,
        'id': vm_record_id,
        'vm_id': vm_id,
        'deployment_id': deployment_id,
    }


@router.delete('/vms/{id}')
def unmanage_vm(
    id: str,
    request: Request,
    actor=Depends(require('inventory.delete')),
    db=Depends(get_db, scope='function'),
):
    row = ensure_inventory_vm_access(db, request, actor, find(db, ManagedVM, id))
    if row.management_mode == 'terraform' and row.lifecycle_status != 'destroyed':
        raise HTTPException(409, 'Active Terraform-managed VM must be destroyed through its deployment')
    db.delete(row)
    audit(db, request, 'inventory.vm_unmanaged', 'managed_vms', id)
    return {'deleted': True}


@router.get('/resources')
def managed_resources(
    request: Request,
    provider: Annotated[str | None, Query(max_length=32)] = None,
    lifecycle_status: Annotated[Literal['active', 'destroyed'] | None, Query()] = None,
    limit: Limit = 100,
    offset: Offset = 0,
    actor=Depends(require('inventory.read')),
    db=Depends(get_db, scope='function'),
):
    query = select(ManagedResource)
    predicate = inventory_resource_predicate(request, actor)
    if predicate is not None:
        query = query.where(predicate)
    if provider:
        query = query.where(ManagedResource.provider == provider)
    if lifecycle_status:
        query = query.where(ManagedResource.lifecycle_status == lifecycle_status)
    rows = db.scalars(query.order_by(ManagedResource.created_at.desc()).offset(offset).limit(limit)).all()
    return {'items': [resource_public(row) for row in rows]}


@router.get('/resources/{id}')
def managed_resource(
    id: str,
    request: Request,
    actor=Depends(require('inventory.read')),
    db=Depends(get_db, scope='function'),
):
    row = ensure_inventory_resource_access(db, request, actor, find(db, ManagedResource, id))
    return resource_public(row)
