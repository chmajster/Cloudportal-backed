"""Live project RBAC. Scoped permissions never become global identity grants."""
from dataclasses import dataclass
from sqlalchemy import and_, exists, or_, select
from app.models import Permission, RolePermission
from app.projects.models import Project, ProjectMembership, ProjectRoleAssignment, ProjectRoleGrant
from app.projects.permissions import PROJECT_DELEGABLE_PERMISSIONS, PROJECT_PERMISSIONS
from app.tenancy.authorization import Identity, Principal, fail, identity, live_grants, lock_authorization
from app.tenancy.models import Tenant, TenantMembership


@dataclass(frozen=True, slots=True)
class ProjectAccess:
    project: Project
    tenant: Tenant
    identity: Identity
    permissions: frozenset[str]
    inherited_permissions: frozenset[str]
    global_administration: bool


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    tenant_id: str
    project_id: str
    actor_id: int
    token_id: int
    permissions: frozenset[str]


def project_grants(project_id, user_id):
    return (select(Permission.name).select_from(ProjectRoleAssignment)
        .join(ProjectRoleGrant, ProjectRoleGrant.assignment_id == ProjectRoleAssignment.id)
        .join(RolePermission, and_(RolePermission.role_id == ProjectRoleAssignment.role_id,
                                   RolePermission.permission_id == ProjectRoleGrant.permission_id))
        .join(Permission, Permission.id == ProjectRoleGrant.permission_id)
        .where(ProjectRoleAssignment.project_id == project_id, ProjectRoleAssignment.user_id == user_id,
               Permission.name.in_(PROJECT_DELEGABLE_PERMISSIONS)))


def tenant_project_permissions(db, actor, tenant_id):
    active = db.scalar(select(TenantMembership.user_id).where(
        TenantMembership.tenant_id == tenant_id, TenantMembership.user_id == actor.user_id,
        TenantMembership.status == 'active'))
    if active is None:
        return frozenset()
    return actor.restrict(db.scalars(live_grants(tenant_id, actor.user_id))) & (PROJECT_PERMISSIONS | PROJECT_DELEGABLE_PERMISSIONS)


def effective_permissions(db, actor, project):
    global_admin = 'projects.admin' in actor.global_permissions
    inherited = tenant_project_permissions(db, actor, project.tenant_id)
    tenant_member = exists(select(TenantMembership.user_id).where(
        TenantMembership.tenant_id == project.tenant_id, TenantMembership.user_id == actor.user_id,
        TenantMembership.status == 'active'))
    member = db.scalar(select(ProjectMembership.user_id).where(
        ProjectMembership.project_id == project.id, ProjectMembership.user_id == actor.user_id,
        ProjectMembership.status == 'active', tenant_member))
    grants = frozenset(db.scalars(project_grants(project.id, actor.user_id))) if member is not None else frozenset()
    global_here = actor.global_permissions & (PROJECT_PERMISSIONS | PROJECT_DELEGABLE_PERMISSIONS) if member is not None or global_admin else frozenset()
    return actor.restrict(global_here | inherited | grants), inherited, global_admin


def authorize(db, principal, project_id, permission, *, tenant_id=None, write=False, lock=False):
    if lock:
        lock_authorization(db)
    actor = identity(db, principal)
    # Joining the parent makes tombstones/disabled state part of every direct-ID lookup.
    statement = select(Project, Tenant).join(Tenant, Tenant.id == Project.tenant_id).where(
        Project.id == str(project_id), Project.deleted_at.is_(None), Tenant.deleted_at.is_(None))
    if tenant_id is not None:
        statement = statement.where(Project.tenant_id == str(tenant_id))
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    row = db.execute(statement).one_or_none()
    if row is None:
        fail(404, 'PROJECT_NOT_FOUND', 'Project not found')
    project, tenant = row
    permissions, inherited, global_admin = effective_permissions(db, actor, project)
    if not permissions or ((project.status == 'disabled' or tenant.status == 'disabled') and not global_admin):
        fail(404, 'PROJECT_NOT_FOUND', 'Project not found')
    if permission not in permissions:
        fail(403, 'SCOPED_PERMISSION_REQUIRED', 'The operation is not permitted in this project')
    if write and (project.status != 'active' or tenant.status != 'active') and not global_admin:
        fail(409, 'PROJECT_INACTIVE', 'Project or tenant is not accepting changes')
    return ProjectAccess(project, tenant, actor, permissions, inherited, global_admin)


def visible_projects(actor, permission='projects.read'):
    parent_exists = exists(
        select(Tenant.id)
        .where(Tenant.id == Project.tenant_id, Tenant.deleted_at.is_(None))
        .correlate(Project)
    )
    base = and_(Project.deleted_at.is_(None), parent_exists)
    if 'projects.admin' in actor.global_permissions and permission in actor.global_permissions:
        return base
    if actor.token_ceiling is not None and permission not in actor.token_ceiling:
        return and_(base, False)
    active_parent = exists(
        select(Tenant.id)
        .where(
            Tenant.id == Project.tenant_id,
            Tenant.deleted_at.is_(None),
            Tenant.status != 'disabled',
        )
        .correlate(Project)
    )
    tenant_member = exists(select(TenantMembership.user_id).where(
        TenantMembership.tenant_id == Project.tenant_id,
        TenantMembership.user_id == actor.user_id,
        TenantMembership.status == 'active').correlate(Project))
    inherited = exists(live_grants(Project.tenant_id, actor.user_id).where(Permission.name == permission).correlate(Project))
    project_member = select(ProjectMembership.user_id).where(
        ProjectMembership.project_id == Project.id, ProjectMembership.user_id == actor.user_id,
        ProjectMembership.status == 'active')
    if permission not in actor.global_permissions:
        own_grant = exists(project_grants(Project.id, actor.user_id).where(Permission.name == permission).correlate(Project))
        project_member = project_member.where(own_grant)
    return and_(base, Project.status != 'disabled', active_parent, tenant_member,
                or_(inherited, exists(project_member.correlate(Project))))


def authorize_creation(db, principal, tenant_id, *, lock=True):
    if lock:
        lock_authorization(db)
    actor = identity(db, principal)
    tenant = db.scalar(select(Tenant).where(Tenant.id == str(tenant_id), Tenant.deleted_at.is_(None)))
    global_admin = 'projects.admin' in actor.global_permissions
    inherited = tenant_project_permissions(db, actor, str(tenant_id))
    if tenant is None or (not global_admin and (tenant.status == 'disabled' or not inherited)):
        fail(404, 'TENANT_NOT_FOUND', 'Tenant not found')
    permissions = actor.global_permissions if global_admin else inherited
    if 'projects.create' not in permissions:
        fail(403, 'SCOPED_PERMISSION_REQUIRED', 'Project creation is not permitted in this tenant')
    if tenant.status != 'active' and not global_admin:
        fail(409, 'TENANT_INACTIVE', 'Tenant is not accepting changes')
    return tenant, actor


def resolve_context(db, principal, project_id, *, tenant_id=None, permission='projects.use', write=False, lock=False):
    access = authorize(db, principal, project_id, permission, tenant_id=tenant_id, write=write, lock=lock)
    return ExecutionContext(access.tenant.id, access.project.id, access.identity.user_id,
                            access.identity.token_id, access.permissions)
