from fastapi import HTTPException
from sqlalchemy import and_, exists, or_, select

from app.models import Deployment, Job, ManagedResource, ManagedVM


def _entity_scope(request):
    scope = getattr(request.state, 'resource_scope', None)
    if scope is None or not getattr(scope, 'entity_key', None):
        return None
    return scope


def _deployment_entity_predicate(scope):
    return or_(
        and_(
            Deployment.workflow['entity']['apmid'].as_string() == scope.apmid,
            Deployment.workflow['entity']['environment'].as_string() == scope.environment,
        ),
        and_(
            Deployment.workflow['blueprint']['variables']['apmid'].as_string() == scope.apmid,
            Deployment.workflow['blueprint']['variables']['environment'].as_string() == scope.environment,
        ),
    )


def _resource_entity_predicate(scope):
    apmid = ManagedResource.metadata_json['apmid'].as_string()
    environment = ManagedResource.metadata_json['environment'].as_string()
    return and_(apmid == scope.apmid, environment == scope.environment)


def _vm_entity_predicate(scope):
    deployment_match = exists(
        select(Deployment.id).where(
            Deployment.id == ManagedVM.deployment_id,
            _deployment_entity_predicate(scope),
        )
    )
    resource_match = exists(
        select(ManagedResource.id).where(
            ManagedResource.deployment_id == ManagedVM.deployment_id,
            _resource_entity_predicate(scope),
        )
    )
    return or_(deployment_match, resource_match)


def _deployment_classification(row):
    workflow = dict(getattr(row, 'workflow', {}) or {})
    entity = dict(workflow.get('entity') or {})
    blueprint = dict(workflow.get('blueprint') or {})
    variables = dict(blueprint.get('variables') or {})
    return (
        str(entity.get('apmid') or variables.get('apmid') or '').strip().upper(),
        str(entity.get('environment') or variables.get('environment') or '').strip().lower(),
    )


def _resource_classification(row):
    metadata = dict(getattr(row, 'metadata_json', {}) or {})
    return (
        str(metadata.get('apmid') or '').strip().upper(),
        str(metadata.get('environment') or '').strip().lower(),
    )


def _matches_entity(scope, apmid, environment):
    if scope is None:
        return True
    return (
        str(apmid or '').strip().upper() == str(scope.apmid or '').strip().upper()
        and str(environment or '').strip().lower() == str(scope.environment or '').strip().lower()
    )


def _ensure_deployment_entity(request, deployment):
    scope = _entity_scope(request)
    if deployment is not None and not _matches_entity(scope, *_deployment_classification(deployment)):
        raise HTTPException(404, 'Resource not found')


def _ensure_resource_entity(request, row):
    scope = _entity_scope(request)
    if row is not None and not _matches_entity(scope, *_resource_classification(row)):
        raise HTTPException(404, 'Resource not found')


def _ensure_vm_entity(db, request, row):
    scope = _entity_scope(request)
    if scope is None or row is None:
        return
    resource = None
    deployment = None
    if row.deployment_id:
        resource = db.scalar(select(ManagedResource).where(
            ManagedResource.deployment_id == row.deployment_id
        ))
        deployment = db.get(Deployment, row.deployment_id)
    resource_match = resource is not None and _matches_entity(
        scope, *_resource_classification(resource)
    )
    deployment_match = deployment is not None and _matches_entity(
        scope, *_deployment_classification(deployment)
    )
    if not (resource_match or deployment_match):
        raise HTTPException(404, 'Resource not found')


def _ensure_job_entity(db, request, job):
    scope = _entity_scope(request)
    if scope is None or job is None:
        return
    if not job.deployment_id:
        raise HTTPException(404, 'Job not found')
    deployment = db.get(Deployment, job.deployment_id)
    if deployment is None or not _matches_entity(
        scope, *_deployment_classification(deployment)
    ):
        raise HTTPException(404, 'Job not found')


def ensure_request_scope(request, row):
    scope = getattr(request.state, 'resource_scope', None)
    if row is not None and scope is not None and (row.tenant_id, row.project_id) != (scope.tenant_id, scope.project_id):
        raise HTTPException(404, 'Resource not found')
    return row


def request_permissions(request):
    return set(getattr(request.state, 'permissions', set()))


def entity_deployment_predicate(request):
    scope = _entity_scope(request)
    return _deployment_entity_predicate(scope) if scope is not None else None


def entity_job_predicate(request):
    scope = _entity_scope(request)
    if scope is None:
        return None
    return exists(
        select(Deployment.id).where(
            Deployment.id == Job.deployment_id,
            _deployment_entity_predicate(scope),
        )
    )


def ensure_deployment_entity(request, deployment):
    _ensure_deployment_entity(request, deployment)
    return deployment


def ensure_job_entity(db, request, job):
    _ensure_job_entity(db, request, job)
    return job


def deployment_predicate(request, actor):
    predicates = []
    scope = _entity_scope(request)
    if scope is not None:
        predicates.append(_deployment_entity_predicate(scope))
    if 'deployments.read_all' not in request_permissions(request):
        predicates.append(Deployment.created_by == actor.user_id)
    if not predicates:
        return None
    return and_(*predicates)


def job_predicate(request, actor):
    predicates = []
    scope = _entity_scope(request)
    if scope is not None:
        predicates.append(exists(
            select(Deployment.id).where(
                Deployment.id == Job.deployment_id,
                _deployment_entity_predicate(scope),
            )
        ))
    if 'jobs.read_all' not in request_permissions(request):
        predicates.append(Job.created_by == actor.user_id)
    if not predicates:
        return None
    return and_(*predicates)


def inventory_vm_predicate(request, actor):
    predicates = []
    scope = _entity_scope(request)
    if scope is not None:
        predicates.append(_vm_entity_predicate(scope))
    if 'inventory.read_all' not in request_permissions(request):
        predicates.append(ManagedVM.created_by == actor.user_id)
    if not predicates:
        return None
    return and_(*predicates)


def inventory_resource_predicate(request, actor):
    predicates = []
    scope = _entity_scope(request)
    if scope is not None:
        predicates.append(_resource_entity_predicate(scope))
    if 'inventory.read_all' not in request_permissions(request):
        predicates.append(ManagedResource.created_by == actor.user_id)
    if not predicates:
        return None
    return and_(*predicates)


def ensure_deployment_access(request, actor, deployment):
    ensure_request_scope(request, deployment)
    _ensure_deployment_entity(request, deployment)
    if deployment is None:
        raise HTTPException(404, 'Deployment not found')
    if 'deployments.read_all' in request_permissions(request) or deployment.created_by == actor.user_id:
        return deployment
    raise HTTPException(404, 'Deployment not found')


def ensure_job_access(request, actor, job, db=None):
    ensure_request_scope(request, job)
    if db is not None:
        _ensure_job_entity(db, request, job)
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
    ensure_request_scope(request, row)
    _ensure_vm_entity(db, request, row)
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
    ensure_request_scope(request, row)
    _ensure_vm_entity(db, request, row)
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
    ensure_request_scope(request, row)
    _ensure_resource_entity(request, row)
    if row is None:
        raise HTTPException(404, 'Resource not found')
    if 'inventory.read_all' in request_permissions(request) or row.created_by == actor.user_id:
        return row
    deployment = db.get(Deployment, row.deployment_id)
    if deployment is not None and deployment.created_by == actor.user_id:
        return row
    raise HTTPException(404, 'Resource not found')
