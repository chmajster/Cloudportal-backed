from fastapi import HTTPException
from sqlalchemy import select

from app.models import Deployment, Job, ManagedResource, ManagedVM


def request_permissions(request):
    return set(getattr(request.state, 'permissions', set()))


def deployment_predicate(request, actor):
    if 'deployments.read_all' in request_permissions(request):
        return None
    return Deployment.created_by == actor.user_id


def job_predicate(request, actor):
    if 'jobs.read_all' in request_permissions(request):
        return None
    return Job.created_by == actor.user_id


def inventory_vm_predicate(request, actor):
    if 'inventory.read_all' in request_permissions(request):
        return None
    return ManagedVM.created_by == actor.user_id


def inventory_resource_predicate(request, actor):
    if 'inventory.read_all' in request_permissions(request):
        return None
    return ManagedResource.created_by == actor.user_id


def ensure_deployment_access(request, actor, deployment):
    if deployment is None:
        raise HTTPException(404, 'Deployment not found')
    if 'deployments.read_all' in request_permissions(request) or deployment.created_by == actor.user_id:
        return deployment
    raise HTTPException(404, 'Deployment not found')


def ensure_job_access(request, actor, job):
    if job is None:
        raise HTTPException(404, 'Job not found')
    if 'jobs.read_all' in request_permissions(request) or job.created_by == actor.user_id:
        return job
    raise HTTPException(404, 'Job not found')


def managed_vm_for_access(db, request, actor, provider_id, vm_id, *, node=None, manage=False):
    row = db.scalar(select(ManagedVM).where(
        ManagedVM.provider_id == provider_id,
        ManagedVM.vm_id == int(vm_id),
    ))
    permission = 'vms.manage_all' if manage else 'vms.read_all'
    if permission in request_permissions(request):
        return row
    if row is None:
        raise HTTPException(404, 'VM not found')
    if node is not None and row.node != node:
        raise HTTPException(404, 'VM not found')
    if row.created_by == actor.user_id:
        return row
    if row.deployment_id:
        deployment = db.get(Deployment, row.deployment_id)
        if deployment is not None and deployment.created_by == actor.user_id:
            return row
    raise HTTPException(404, 'VM not found')


def ensure_not_terraform_managed(row, action):
    if row is not None and row.management_mode == 'terraform' and row.lifecycle_status != 'destroyed':
        raise HTTPException(
            409,
            f'Terraform-managed VM cannot be {action} directly; use its deployment workflow',
        )


def ensure_inventory_vm_access(db, request, actor, row):
    if row is None:
        raise HTTPException(404, 'Resource not found')
    if 'inventory.read_all' in request_permissions(request) or row.created_by == actor.user_id:
        return row
    if row.deployment_id:
        deployment = db.get(Deployment, row.deployment_id)
        if deployment is not None and deployment.created_by == actor.user_id:
            return row
    raise HTTPException(404, 'Resource not found')


def ensure_inventory_resource_access(db, request, actor, row):
    if row is None:
        raise HTTPException(404, 'Resource not found')
    if 'inventory.read_all' in request_permissions(request) or row.created_by == actor.user_id:
        return row
    deployment = db.get(Deployment, row.deployment_id)
    if deployment is not None and deployment.created_by == actor.user_id:
        return row
    raise HTTPException(404, 'Resource not found')
