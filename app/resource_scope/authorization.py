"""Live resource grants. A selected scope is an input, never an authorization ticket."""
from dataclasses import dataclass
from uuid import UUID
from sqlalchemy import select
from app.projects.models import Project, ProjectMembership
from app.projects.authorization import project_grants
from app.tenancy.models import Tenant, TenantMembership
from app.tenancy.authorization import fail, identity, live_grants, Principal, lock_authorization
from app.resource_scope.columns import DEFAULT_TENANT_ID, DEFAULT_PROJECT_ID
from app.resource_scope.permissions import RESOURCE_PERMISSIONS


@dataclass(frozen=True, slots=True, eq=False)
class Scope:
    tenant_id: str
    project_id: str
    entity_key: str | None = None
    apmid: str | None = None
    environment: str | None = None
    entity_role: str | None = None

    def __eq__(self, other):
        if not isinstance(other, Scope):
            return NotImplemented
        return (
            str(self.tenant_id), str(self.project_id)
        ) == (
            str(other.tenant_id), str(other.project_id)
        )

    def __hash__(self):
        return hash((str(self.tenant_id), str(self.project_id)))


DEFAULT_SCOPE = Scope(DEFAULT_TENANT_ID, DEFAULT_PROJECT_ID)


def requested_scope(request):
    """Headers/query must agree. Entity is a role ceiling inside the selected project."""
    values = []
    for field in ('tenant_id', 'project_id'):
        header_name = 'X-' + field.replace('_', '-')
        if len(request.headers.getlist(header_name)) > 1 or len(request.query_params.getlist(field)) > 1:
            fail(422, 'SCOPE_CONFLICT', 'Repeated project context fields are not allowed')
        header = request.headers.get(header_name)
        query = request.query_params.get(field)
        if header and query and header != query:
            fail(422, 'SCOPE_CONFLICT', 'Scope header and query parameter disagree')
        values.append(header if header is not None else query)

    if len(request.headers.getlist('X-Entity')) > 1 or len(request.query_params.getlist('entity')) > 1:
        fail(422, 'ENTITY_CONFLICT', 'Repeated Entity context fields are not allowed')
    entity_header = request.headers.get('X-Entity')
    entity_query = request.query_params.get('entity')
    if entity_header and entity_query and entity_header != entity_query:
        fail(422, 'ENTITY_CONFLICT', 'Entity header and query parameter disagree')
    raw_entity = entity_header if entity_header is not None else entity_query

    if values == [None, None]:
        if raw_entity:
            fail(422, 'SCOPE_PAIR_REQUIRED', 'Entity requires tenant_id and project_id')
        return DEFAULT_SCOPE
    if any(value is None for value in values):
        fail(422, 'SCOPE_PAIR_REQUIRED', 'Supply both tenant_id and project_id')

    try:
        tenant_id, project_id = (str(UUID(value)) for value in values)
    except (ValueError, TypeError, AttributeError):
        fail(422, 'INVALID_SCOPE', 'Tenant and project must be valid UUIDs')

    if not raw_entity:
        return Scope(tenant_id, project_id)

    from app.policy_engine.entities import normalize_entity_key, parse_entity_key
    try:
        entity_key = normalize_entity_key(raw_entity)
        parts = parse_entity_key(entity_key)
    except ValueError as exc:
        fail(422, 'INVALID_ENTITY', str(exc))
    return Scope(
        tenant_id,
        project_id,
        entity_key=entity_key,
        apmid=parts['apmid'],
        environment=parts['environment'],
        entity_role=parts['role'],
    )


def permissions_for_identity(db, actor, scope, *, write=False):
    row = db.execute(select(Project, Tenant).join(Tenant, Tenant.id == Project.tenant_id).where(
        Project.id == scope.project_id, Project.tenant_id == scope.tenant_id,
        Project.deleted_at.is_(None), Tenant.deleted_at.is_(None)).execution_options(populate_existing=True)).one_or_none()
    if row is None:
        fail(404, 'PROJECT_NOT_FOUND', 'Project not found')
    project, tenant = row
    admin = 'governance.admin' in actor.global_permissions
    tenant_member = db.scalar(select(TenantMembership.user_id).where(
        TenantMembership.tenant_id == scope.tenant_id, TenantMembership.user_id == actor.user_id,
        TenantMembership.status == 'active')) is not None
    project_member = tenant_member and db.scalar(select(ProjectMembership.user_id).where(
        ProjectMembership.project_id == scope.project_id, ProjectMembership.user_id == actor.user_id,
        ProjectMembership.status == 'active')) is not None
    inherited = frozenset(db.scalars(live_grants(scope.tenant_id, actor.user_id))) if tenant_member else frozenset()
    inherited = actor.restrict(inherited) & RESOURCE_PERMISSIONS
    grants = frozenset(db.scalars(project_grants(scope.project_id, actor.user_id))) if project_member else frozenset()
    if not admin and not project_member and not inherited:
        fail(404, 'PROJECT_NOT_FOUND', 'Project not found')
    # Even a global administrator may not provision into a disabled project.
    if project.status == 'disabled' or tenant.status == 'disabled':
        fail(404, 'PROJECT_NOT_FOUND', 'Project not found')
    if write and (project.status != 'active' or tenant.status != 'active'):
        fail(409, 'PROJECT_INACTIVE', 'Project or tenant is not accepting resource changes')
    global_here = actor.global_permissions if project_member or admin else frozenset()
    return actor.restrict(global_here | inherited | grants) & RESOURCE_PERMISSIONS


def authorize(db, principal, scope, permission, *, write=False):
    if write:
        lock_authorization(db)
    actor = identity(db, principal)
    permissions = permissions_for_identity(db, actor, scope, write=write)
    allowed = permissions if permission in RESOURCE_PERMISSIONS else actor.global_permissions
    if permission not in allowed:
        fail(403, 'SCOPED_PERMISSION_REQUIRED', 'Operation is not permitted in this project')
    return actor, permissions


def ensure_execution_ready(db, actor, scope):
    """Fail closed for delegated execution until provider-target governance lands.

    Platform administrators can exercise the integrated job path. Existing
    single-project installations retain their prior execution permissions.
    This is an explicit rollout boundary, not an undocumented RBAC bypass.
    """
    if 'governance.admin' in actor.global_permissions:
        return
    from sqlalchemy import exists
    from app.models import Deployment, ManagedVM, ManagedResource
    nondefault_resources = scope != DEFAULT_SCOPE
    if not nondefault_resources:
        for model in (Deployment, ManagedVM, ManagedResource):
            table = model.__table__
            if db.connection().scalar(select(exists().where(table.c.project_id != DEFAULT_PROJECT_ID))):
                nondefault_resources = True
                break
    if nondefault_resources:
        fail(409, 'PROJECT_EXECUTION_REQUIRES_PLATFORM_ADMIN',
             'Delegated multi-project execution is not enabled: provider-target governance is required')
