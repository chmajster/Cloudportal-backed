"""Shared infrastructure assignments, replay validation and provider result filtering."""
from copy import deepcopy
from sqlalchemy import delete, exists, select, func
from app import models as m
from app.projects.models import Project
from app.resource_scope.authorization import Scope, DEFAULT_SCOPE, permissions_for_identity
from app.resource_scope.database import OWNED_MODELS, reference_visible
from app.resource_scope.models import ProjectCredentialAccess, ProjectProviderAccess
from app.tenancy.authorization import fail, identity, assert_version, lock_authorization


def access_view(db, principal, project_id, *, limit=100, offset=0):
    actor = identity(db, principal)
    project = db.get(Project, str(project_id))
    if project is None or project.deleted_at is not None:
        fail(404, 'PROJECT_NOT_FOUND', 'Project not found')
    scope = Scope(project.tenant_id, project.id)
    permissions = permissions_for_identity(db, actor, scope)
    if not {'providers.read', 'credentials.read'} <= permissions and 'governance.access.read' not in actor.global_permissions:
        fail(403, 'SCOPED_PERMISSION_REQUIRED', 'Infrastructure access is not visible')
    return {
        'tenant_id': project.tenant_id, 'project_id': project.id, 'version': project.version,
        'limit': limit, 'offset': offset,
        'provider_total': db.scalar(select(func.count()).select_from(ProjectProviderAccess).where(ProjectProviderAccess.project_id == project.id)),
        'credential_total': db.scalar(select(func.count()).select_from(ProjectCredentialAccess).where(ProjectCredentialAccess.project_id == project.id)),
        'provider_ids': list(db.scalars(select(ProjectProviderAccess.provider_id).where(
            ProjectProviderAccess.project_id == project.id).order_by(ProjectProviderAccess.provider_id).limit(limit).offset(offset))),
        'credential_ids': list(db.scalars(select(ProjectCredentialAccess.credential_id).where(
            ProjectCredentialAccess.project_id == project.id).order_by(ProjectCredentialAccess.credential_id).limit(limit).offset(offset))),
    }


def set_access(db, principal, project_id, data):
    lock_authorization(db)
    actor = identity(db, principal)
    if not {'governance.admin', 'governance.access.manage'} <= actor.global_permissions:
        fail(403, 'GLOBAL_PERMISSION_REQUIRED', 'Global infrastructure assignment permission is required')
    project = db.scalar(select(Project).where(Project.id == str(project_id), Project.deleted_at.is_(None))
                        .with_for_update().execution_options(populate_existing=True))
    if project is None:
        fail(404, 'PROJECT_NOT_FOUND', 'Project not found')
    assert_version(project, data.expected_version)
    providers = set(data.provider_ids)
    credentials = set(data.credential_ids)
    rows = list(db.execute(select(m.Provider.id, m.Provider.credentials_id).where(m.Provider.id.in_(providers))))
    if {row.id for row in rows} != providers or set(db.scalars(select(m.Credential.id).where(
            m.Credential.id.in_(credentials)))) != credentials:
        fail(404, 'RESOURCE_NOT_FOUND', 'Infrastructure reference not found')
    if not {row.credentials_id for row in rows} <= credentials:
        fail(422, 'PROVIDER_CREDENTIAL_REQUIRED', 'Every provider requires an explicit credential assignment')
    db.execute(delete(ProjectProviderAccess).where(ProjectProviderAccess.project_id == project.id))
    db.execute(delete(ProjectCredentialAccess).where(ProjectCredentialAccess.project_id == project.id))
    db.add_all(ProjectProviderAccess(tenant_id=project.tenant_id, project_id=project.id, provider_id=key)
               for key in sorted(providers))
    db.add_all(ProjectCredentialAccess(tenant_id=project.tenant_id, project_id=project.id, credential_id=key)
               for key in sorted(credentials))
    project.version += 1
    db.flush()
    return access_view(db, principal, project.id, limit=200)


def ensure_project_empty(db, project_id):
    for model in (*OWNED_MODELS, ProjectProviderAccess, ProjectCredentialAccess):
        if db.scalar(select(exists().where(model.project_id == project_id))):
            fail(409, 'PROJECT_NOT_EMPTY', 'Project still owns infrastructure, history or access assignments')


def validate_replay(db, path, result):
    if not isinstance(result, dict):
        fail(409, 'INVALID_REPLAY', 'Stored response cannot be replayed')
    result = deepcopy(result)
    parts = path.strip('/').split('/')
    area = parts[2] if len(parts) > 2 else ''
    model = {'deployments': m.Deployment, 'jobs': m.Job, 'credentials': m.Credential,
             'providers': m.Provider, 'blueprints': m.Blueprint}.get(area)
    # Executing a Blueprint or adopting a VM returns a Deployment, whereas
    # destroying a Deployment returns a Job. Do not confuse URL and result IDs.
    if 'workspace' in result and 'credentials_id' in result:
        model = m.Deployment
    elif 'operation' in result:
        model = m.Job
    elif 'vm_id' in result and area == 'inventory':
        model = m.ManagedVM
    elif 'scheme_id' in result:
        model = m.HostnameReservation
    elif 'pool_id' in result:
        model = m.IPAllocation
    elif area == 'schedules':
        model = m.ScheduledOperation
    if model is not None and result.get('id') is not None:
        row = db.get(model, result['id'])
        if row is None:
            fail(404, 'RESOURCE_NOT_FOUND', 'Resource is no longer accessible')
        if isinstance(row, m.Deployment):
            if db.get(m.Provider, row.provider_id) is None or db.get(m.Credential, row.credentials_id) is None:
                fail(404, 'RESOURCE_NOT_FOUND', 'Infrastructure access was revoked')
            nested = result.get('job')
            if nested is not None:
                job = db.get(m.Job, nested.get('id')) if isinstance(nested, dict) else None
                if job is None or job.deployment_id != row.id:
                    fail(404, 'RESOURCE_NOT_FOUND', 'Job is no longer accessible')
                nested.update(tenant_id=job.tenant_id, project_id=job.project_id)
        if isinstance(row, (m.Deployment, m.Job)):
            # Upgrade legacy Default responses without changing their operation,
            # timestamps or saved status. The old Default fingerprint stays valid.
            result.update(tenant_id=row.tenant_id, project_id=row.project_id)
    return result


def filter_provider_vms(db, provider_id, rows):
    scope = db.info.get('resource_scope')
    if scope is None:
        fail(403, 'SCOPE_REQUIRED', 'Provider VM discovery requires a validated project')
    # Query ownership only for identities actually returned by this provider,
    # in bounded batches; never scan every project's inventory into Python.
    def identity(row):
        for field in ('vmid', 'id', 'instance_id', 'InstanceId', 'uuid', 'resource_id'):
            value = row.get(field)
            if value is not None:
                return str(value)
        return None
    keys = sorted({identity(row) for row in rows if identity(row) is not None})
    ownership = {}
    for start in range(0, len(keys), 200):
        batch = keys[start:start + 200]
        for model, key, values in ((m.ManagedVM, m.ManagedVM.__table__.c.vm_id,
                                    [int(value) for value in batch if value.isdigit()]),
                                   (m.ManagedResource, m.ManagedResource.__table__.c.external_id, batch)):
            if not values:
                continue
            table = model.__table__
            for item in db.connection().execute(select(key, table.c.tenant_id, table.c.project_id).where(
                    table.c.provider_id == provider_id, key.in_(values))):
                ownership.setdefault(str(item[0]), set()).add((item.tenant_id, item.project_id))
    expected = {(scope.tenant_id, scope.project_id)}
    allow_untracked = scope == DEFAULT_SCOPE and not provider_has_foreign_resources(db, provider_id, scope)
    return [row for row in rows if ownership.get(identity(row)) == expected or (
        identity(row) not in ownership and allow_untracked)]


def guard_vm_identity(db, provider_id, vmid, scope):
    try:
        vmid = int(vmid)
    except (TypeError, ValueError):
        fail(422, 'INVALID_VM_ID', 'VM identifier must be an integer')
    table = m.ManagedVM.__table__
    owned = db.connection().execute(select(table.c.tenant_id, table.c.project_id).where(
        table.c.provider_id == provider_id, table.c.vm_id == vmid)).one_or_none()
    if owned is not None and tuple(owned) != (scope.tenant_id, scope.project_id):
        fail(404, 'RESOURCE_NOT_FOUND', 'Resource not found')


def provider_has_foreign_resources(db, provider_id, scope):
    from sqlalchemy import or_
    for model in (m.Deployment, m.ManagedVM, m.ManagedResource):
        table = model.__table__
        foreign = select(exists().where(table.c.provider_id == provider_id, or_(
            table.c.tenant_id != scope.tenant_id, table.c.project_id != scope.project_id)))
        if db.connection().scalar(foreign):
            return True
    return False


def guard_raw_provider(db, provider_id, scope):
    if provider_has_foreign_resources(db, provider_id, scope):
        fail(409, 'GOVERNED_DAY2_REQUIRED', 'Shared provider requires the deployment job workflow')
