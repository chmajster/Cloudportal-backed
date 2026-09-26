import hashlib

from sqlalchemy import select, text

from app.database import session
from app.models import Deployment, ManagedVM
from app.providers.proxmox import ProxmoxProvider


def _provider_lock_key(provider_id: int) -> int:
    digest = hashlib.sha256(f'proxmox-vmid:{int(provider_id)}'.encode()).digest()[:8]
    return int.from_bytes(digest, byteorder='big', signed=True)


def _reserved_ids(db, provider_id: int, deployment_id: str) -> set[int]:
    reserved = set()

    deployments = db.scalars(
        select(Deployment).where(
            Deployment.provider_id == int(provider_id),
            Deployment.id != deployment_id,
            Deployment.status != 'destroyed',
        )
    ).all()
    for row in deployments:
        value = (row.variables or {}).get('vm_id')
        try:
            if value is not None:
                reserved.add(int(value))
        except (TypeError, ValueError):
            continue

    managed = db.scalars(
        select(ManagedVM).where(
            ManagedVM.provider_id == int(provider_id),
            ManagedVM.lifecycle_status != 'destroyed',
        )
    ).all()
    for row in managed:
        try:
            reserved.add(int(row.vm_id))
        except (TypeError, ValueError):
            continue

    return reserved


def bind_proxmox_vm_id(deployment_id: str, vm_id: int) -> int:
    """Persist a VMID already proven by Terraform state for this deployment."""
    with session() as db:
        with db.begin():
            deployment = db.scalar(
                select(Deployment).where(Deployment.id == deployment_id).with_for_update()
            )
            if deployment is None:
                raise RuntimeError('Deployment disappeared before VMID binding')
            variables = dict(deployment.variables or {})
            variables['vm_id'] = int(vm_id)
            deployment.variables = variables
    return int(vm_id)


def reserve_proxmox_vm_id(deployment_id: str, credential, context=None) -> int:
    """Reserve one explicit VMID across all CloudPortal worker processes.

    The provider-local advisory lock prevents two CloudPortal jobs from selecting
    the same free Proxmox identity concurrently. The chosen value is persisted in
    deployment.variables before Terraform plan/apply starts, so retries and
    different workers reuse exactly the same identity.
    """
    with session() as db:
        with db.begin():
            deployment = db.get(Deployment, deployment_id)
            if deployment is None:
                raise RuntimeError('Deployment disappeared before VMID reservation')

            if db.bind.dialect.name == 'postgresql':
                db.execute(
                    text('SELECT pg_advisory_xact_lock(:lock_key)'),
                    {'lock_key': _provider_lock_key(deployment.provider_id)},
                )

            deployment = db.scalar(
                select(Deployment).where(Deployment.id == deployment_id).with_for_update()
            )
            variables = dict(deployment.variables or {})
            existing = variables.get('vm_id')
            if existing not in {None, ''}:
                return int(existing)

            live_ids = ProxmoxProvider(credential).used_vm_ids()
            reserved_ids = _reserved_ids(db, deployment.provider_id, deployment.id)
            used = live_ids | reserved_ids

            candidate = 100
            while candidate in used:
                candidate += 1
                if candidate > 999999999:
                    raise RuntimeError('No free Proxmox VMID is available')

            variables['vm_id'] = candidate
            deployment.variables = variables

            if context is not None:
                context.log(f'proxmox.vmid.reserved: {candidate}')
            return candidate
