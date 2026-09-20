from sqlalchemy import select

from app.models import Deployment, ManagedResource, ManagedVM
from app.terraform.state import read_stored_state


def state_outputs(payload):
    outputs = (payload or {}).get('outputs', {})
    if not isinstance(outputs, dict):
        raise RuntimeError('Terraform state outputs are invalid')
    return outputs


def sync_deployment_inventory(db, deployment, outputs):
    external_id = outputs.get('resource_id', {}).get('value')
    vm_id = outputs.get('vm_id', {}).get('value')
    if external_id is None:
        external_id = vm_id
    if external_id is None:
        raise RuntimeError('Managed resource identity is missing from Terraform state')

    primary_ip = outputs.get('primary_ip', {}).get('value')
    node = str((deployment.variables or {}).get('node') or '')
    normalized_vm_id = None
    metadata = {}
    managed_vm = None

    if deployment.provider == 'proxmox':
        if vm_id is None:
            raise RuntimeError('Proxmox VM ID missing from Terraform state')
        if not node:
            raise RuntimeError('Proxmox node missing from deployment variables')
        normalized_vm_id = int(vm_id)
        metadata = {'node': node, 'vm_id': normalized_vm_id}

        by_identity = db.scalar(select(ManagedVM).where(
            ManagedVM.provider_id == deployment.provider_id,
            ManagedVM.vm_id == normalized_vm_id,
        ))
        by_deployment = db.scalar(select(ManagedVM).where(
            ManagedVM.deployment_id == deployment.id
        ))

        if by_identity and by_identity.deployment_id not in {None, deployment.id}:
            raise RuntimeError('VM identity is already linked to another deployment')
        if by_identity and by_deployment and by_identity.id != by_deployment.id:
            raise RuntimeError('Deployment inventory points to a different VM identity')
        managed_vm = by_identity or by_deployment

    resource = db.scalar(select(ManagedResource).where(
        ManagedResource.deployment_id == deployment.id
    ))
    if resource is None:
        resource = ManagedResource(
            deployment_id=deployment.id,
            provider_id=deployment.provider_id,
            provider=deployment.provider,
            resource_type='vm',
            external_id=str(external_id),
            name=deployment.name,
            primary_ip=str(primary_ip) if primary_ip else None,
            lifecycle_status='active',
            metadata_json=metadata,
            created_by=deployment.created_by,
        )
        db.add(resource)
    else:
        resource.provider_id = deployment.provider_id
        resource.provider = deployment.provider
        resource.resource_type = 'vm'
        resource.external_id = str(external_id)
        resource.name = deployment.name
        resource.primary_ip = str(primary_ip) if primary_ip else None
        resource.lifecycle_status = 'active'
        resource.metadata_json = metadata
        resource.destroyed_at = None

    if deployment.provider == 'proxmox':
        if managed_vm is None:
            managed_vm = ManagedVM(
                provider_id=deployment.provider_id,
                deployment_id=deployment.id,
                node=node,
                vm_id=normalized_vm_id,
                name=deployment.name,
                management_mode='terraform',
                lifecycle_status='active',
                created_by=deployment.created_by,
            )
            db.add(managed_vm)
        else:
            managed_vm.provider_id = deployment.provider_id
            managed_vm.deployment_id = deployment.id
            managed_vm.node = node
            managed_vm.vm_id = normalized_vm_id
            managed_vm.name = deployment.name
            managed_vm.management_mode = 'terraform'
            managed_vm.lifecycle_status = 'active'
            managed_vm.destroyed_at = None

    return {
        'external_id': str(external_id),
        'vm_id': normalized_vm_id,
        'node': node or None,
        'managed_vm': managed_vm,
        'resource': resource,
    }


def repair_inventory_from_states(db, limit=200):
    repaired = []
    skipped = []
    deployments = db.scalars(
        select(Deployment)
        .where(Deployment.status != 'destroyed')
        .order_by(Deployment.updated_at.desc())
        .limit(limit)
    ).all()

    for deployment in deployments:
        existing_resource = db.scalar(select(ManagedResource.id).where(
            ManagedResource.deployment_id == deployment.id
        ))
        existing_vm = None
        if deployment.provider == 'proxmox':
            existing_vm = db.scalar(select(ManagedVM.id).where(
                ManagedVM.deployment_id == deployment.id
            ))
        if existing_resource and (deployment.provider != 'proxmox' or existing_vm):
            continue

        try:
            payload = read_stored_state(db, deployment.id)
            if payload is None:
                continue
            outputs = state_outputs(payload)
            result = sync_deployment_inventory(db, deployment, outputs)
            repaired.append({
                'deployment_id': deployment.id,
                'provider': deployment.provider,
                'external_id': result['external_id'],
                'vm_id': result['vm_id'],
                'node': result['node'],
            })
        except (RuntimeError, TypeError, ValueError) as exc:
            skipped.append({'deployment_id': deployment.id, 'reason': str(exc)[:200]})

    return {'repaired': repaired, 'skipped': skipped}
