"""Live project authorization, SQL visibility predicates and execution context.

Project membership is subordinate to active tenant membership. Only an explicit
parent projects.admin grant crosses project memberships inside that tenant.
Global tenants.admin never substitutes for the operation permission.
"""
from dataclasses import dataclass
from sqlalchemy import and_, exists, or_, select
from app.models import Permission, RolePermission
from app.projects.models import Project, ProjectMembership, ProjectRoleAssignment, ProjectRoleGrant, UserProjectContext
from app.projects.permissions import PROJECT_DELEGABLE_PERMISSIONS, DEFAULT_PROJECT_ID
from app.tenancy.authorization import Identity, Principal, fail, identity, live_grants, lock_authorization
from app.tenancy.models import Tenant, TenantMembership
from app.tenancy.permissions import DEFAULT_TENANT_ID


@dataclass(frozen=True, slots=True)
class ProjectAccess:
    project: Project
    tenant: Tenant
    identity: Identity
    permissions: frozenset[str]
    parent_admin: bool


@dataclass(frozen=True, slots=True)
class ProjectScope:
    tenant_id: str
    project_id: str
    user_id: int
    token_id: int
    permissions: frozenset[str]
    source: str


def project_grants(project_id, user_id):
    return (select(Permission.name).select_from(ProjectRoleAssignment)
        .join(ProjectRoleGrant, ProjectRoleGrant.assignment_id == ProjectRoleAssignment.id)
        .join(RolePermission, and_(RolePermission.role_id == ProjectRoleAssignment.role_id,
                                   RolePermission.permission_id == ProjectRoleGrant.permission_id))
        .join(Permission, Permission.id == ProjectRoleGrant.permission_id)
        .where(ProjectRoleAssignment.project_id == project_id,
               ProjectRoleAssignment.user_id == user_id,
               Permission.name.in_(PROJECT_DELEGABLE_PERMISSIONS)))


def _tenant_member(actor):
    return exists(select(TenantMembership.user_id).where(
        TenantMembership.tenant_id == Project.tenant_id,
        TenantMembership.user_id == actor.user_id, TenantMembership.status == 'active').correlate(Project))


def _project_member(actor):
    return exists(select(ProjectMembership.user_id).where(
        ProjectMembership.project_id == Project.id, ProjectMembership.user_id == actor.user_id,
        ProjectMembership.status == 'active').correlate(Project))


def _tenant_permission(actor, permission):
    return exists(live_grants(Project.tenant_id, actor.user_id)
        .where(Permission.name == permission).correlate(Project))


def visible_projects(actor, permission='projects.read'):
    """The SAME predicate powers collection counts and object visibility."""
    base = and_(Project.deleted_at.is_(None), exists(select(Tenant.id).where(
        Tenant.id == Project.tenant_id, Tenant.deleted_at.is_(None),
        True if actor.platform_admin else Tenant.status != 'disabled').correlate(Project)))
    if actor.platform_admin and permission in actor.global_permissions:
        return base
    if actor.token_ceiling is not None and permission not in actor.token_ceiling:
        return and_(base, False)
    parent = or_('projects.admin' in actor.global_permissions,
                 _tenant_permission(actor, 'projects.admin')
                 if actor.token_ceiling is None or 'projects.admin' in actor.token_ceiling else False)
    grants = or_(permission in actor.global_permissions, _tenant_permission(actor, permission),
                 and_(_project_member(actor), exists(project_grants(Project.id, actor.user_id)
                        .where(Permission.name == permission).correlate(Project))))
    return and_(base, _tenant_member(actor), or_(parent, _project_member(actor)),
                or_(Project.status != 'disabled', parent), grants)


def effective_project_permissions(db, actor, project):
    tenant_member = db.scalar(select(TenantMembership.user_id).where(
        TenantMembership.tenant_id == project.tenant_id,
        TenantMembership.user_id == actor.user_id, TenantMembership.status == 'active'))
    if tenant_member is None and not actor.platform_admin:
        return frozenset(), False
    inherited = set(db.scalars(live_grants(project.tenant_id, actor.user_id))) if tenant_member is not None else set()
    parent_permissions = actor.restrict(actor.global_permissions | inherited)
    parent_admin = actor.platform_admin or 'projects.admin' in parent_permissions
    member = db.scalar(select(ProjectMembership.user_id).where(
        ProjectMembership.project_id == project.id,
        ProjectMembership.user_id == actor.user_id, ProjectMembership.status == 'active'))
    if member is None and not parent_admin:
        return frozenset(), False
    delegated = set(db.scalars(project_grants(project.id, actor.user_id))) if member is not None else set()
    return actor.restrict(parent_permissions | delegated), parent_admin


def authorize(db, principal, project_id, permission, *, tenant_id=None, write=False, lock=False):
    if lock:
        lock_authorization(db)
    actor = identity(db, principal)
    # Fetch only an authorized row. Never reveal the containing tenant on IDOR.
    predicate = and_(Project.id == str(project_id), visible_projects(actor))
    if tenant_id is not None:
        predicate = and_(predicate, Project.tenant_id == str(tenant_id))
    project = db.scalar(select(Project).where(predicate).execution_options(populate_existing=True))
    if project is None:
        fail(404, 'PROJECT_NOT_FOUND', 'Project not found')
    tenant_query = select(Tenant).where(Tenant.id == project.tenant_id)
    if lock:
        tenant_query = tenant_query.with_for_update()
    tenant = db.scalar(tenant_query.execution_options(populate_existing=True))
    if lock:
        project = db.scalar(select(Project).where(Project.id == project.id)
            .with_for_update().execution_options(populate_existing=True))
    permissions, parent_admin = effective_project_permissions(db, actor, project)
    if permission not in permissions:
        fail(403, 'SCOPED_PERMISSION_REQUIRED', 'The operation is not permitted in this project')
    if write and tenant.status != 'active' and not actor.platform_admin:
        fail(409, 'TENANT_INACTIVE', 'This tenant is not accepting changes')
    if write and project.status != 'active' and not parent_admin:
        fail(409, 'PROJECT_INACTIVE', 'This project is not accepting changes')
    return ProjectAccess(project, tenant, actor, permissions, parent_admin)


def resolve_scope(db, principal, *, tenant_id=None, project_id=None,
                  permission='projects.read', write=False, use_preference=True):
    """Explicit IDs > validated server preference > Default/Default.

    No fallback from an inaccessible explicit/preferred scope is permitted.
    Nothing in this function grants access to an existing unscoped API.
    """
    source = 'explicit'
    if tenant_id is not None and project_id is None:
        fail(422, 'PROJECT_REQUIRED', 'A project is required with an explicit tenant')
    if project_id is None:
        preference = db.get(UserProjectContext, principal.user_id) if use_preference else None
        if preference is not None and preference.project_id is not None:
            tenant_id, project_id, source = preference.tenant_id, preference.project_id, 'preference'
        else:
            tenant_id, project_id, source = DEFAULT_TENANT_ID, DEFAULT_PROJECT_ID, 'default'
    access = authorize(db, principal, project_id, permission, tenant_id=tenant_id, write=write)
    return ProjectScope(access.project.tenant_id, access.project.id, access.identity.user_id,
                        access.identity.token_id, access.permissions, source)
