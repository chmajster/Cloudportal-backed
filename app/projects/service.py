"""Project administration with DB filtering, grant ceilings and one transaction."""
from collections import defaultdict
from datetime import timezone
from sqlalchemy import and_, delete, exists, func, or_, select, union_all
from app.models import Audit, Permission, Role, RolePermission, User, UserRole, now
from app.projects.authorization import (authorize, authorize_creation, effective_permissions, identity,
                                        project_grants, visible_projects)
from app.projects.models import Project, ProjectMembership, ProjectRoleAssignment, ProjectRoleGrant
from app.projects.permissions import PROJECT_DELEGABLE_PERMISSIONS
from app.tenancy.authorization import assert_version, fail
from app.tenancy.models import TenantMembership, TenantRoleAssignment, TenantRoleGrant
from app.tenancy.service import tenant_output


def project_output(project):
    return tenant_output(project) | {'tenant_id': project.tenant_id, 'default_environment': project.default_environment}


def page(items, total, limit, offset):
    return {'items': items, 'total': total, 'limit': limit, 'offset': offset}


def project_list(db, principal, *, tenant_id=None, status=None, limit=100, offset=0):
    predicate = visible_projects(identity(db, principal))
    if tenant_id is not None:
        predicate = and_(predicate, Project.tenant_id == str(tenant_id))
    if status is not None:
        predicate = and_(predicate, Project.status == status)
    total = db.scalar(select(func.count()).select_from(Project).where(predicate))
    rows = db.scalars(select(Project).where(predicate).order_by(Project.tenant_id, Project.slug, Project.id)
                      .limit(limit).offset(offset))
    return page([project_output(p) for p in rows], total, limit, offset)


def project_get(db, principal, project_id):
    return project_output(authorize(db, principal, project_id, 'projects.read').project)


def project_permissions(db, principal, project_id):
    access = authorize(db, principal, project_id, 'projects.read')
    return {'tenant_id': access.tenant.id, 'project_id': access.project.id, 'scope': 'PROJECT',
            'permissions': sorted(access.permissions), 'inherited_permissions': sorted(access.inherited_permissions),
            'global_administration': access.global_administration}


def project_create(db, principal, data):
    tenant, actor = authorize_creation(db, principal, data.tenant_id)
    if data.status == 'disabled':
        from app.projects.authorization import tenant_project_permissions
        if 'projects.admin' not in actor.global_permissions and 'projects.admin' not in tenant_project_permissions(db, actor, tenant.id):
            fail(403, 'TENANT_PERMISSION_REQUIRED', 'Creating a disabled project requires tenant-wide project administration')
    values = data.model_dump(exclude={'tenant_id'})
    values['metadata_json'] = values.pop('metadata')
    project = Project(tenant_id=tenant.id, created_by=actor.user_id, **values)
    db.add(project)
    db.flush()
    # Creation never manufactures new permissions. Tenant-wide authority is inherited;
    # an explicit, bounded assignment is needed for project-local administration.
    return project_output(project)


def project_update(db, principal, project_id, data):
    access = authorize(db, principal, project_id, 'projects.update', write=True, lock=True)
    row = access.project
    assert_version(row, data.expected_version)
    if row.is_system and (data.name != row.name or data.slug != row.slug or data.status != 'active'):
        fail(409, 'SYSTEM_PROJECT_PROTECTED', 'Default project must retain its identity and active status')
    if data.status == 'disabled' and not (access.global_administration or 'projects.admin' in access.inherited_permissions):
        fail(403, 'TENANT_PERMISSION_REQUIRED', 'Disabling a project requires tenant-wide project administration')
    values = data.model_dump(exclude={'expected_version'})
    values['metadata_json'] = values.pop('metadata')
    for key, value in values.items():
        setattr(row, key, value)
    row.version += 1
    db.flush()
    return project_output(row)


def project_delete(db, principal, project_id, expected_version):
    access = authorize(db, principal, project_id, 'projects.delete', write=True, lock=True)
    row = access.project
    assert_version(row, expected_version)
    if row.is_system:
        fail(409, 'SYSTEM_PROJECT_PROTECTED', 'Default project cannot be deleted')
    if db.scalar(select(exists().where(ProjectMembership.project_id == row.id))):
        fail(409, 'PROJECT_NOT_EMPTY', 'Remove project memberships before deleting the project')
    from app.resource_scope.service import ensure_project_empty
    ensure_project_empty(db, row.id)
    row.deleted_at = now()
    row.status = 'disabled'
    row.version += 1
    db.flush()
    return {'deleted': True}


def _member_values(member, username, roles):
    return {'tenant_id': member.tenant_id, 'project_id': member.project_id,
            'user_id': member.user_id, 'username': username, 'status': member.status,
            'role_ids': roles, 'version': member.version,
            'created_at': member.created_at.replace(tzinfo=timezone.utc),
            'updated_at': member.updated_at.replace(tzinfo=timezone.utc)}


def _member_output(db, member):
    roles = list(db.scalars(select(ProjectRoleAssignment.role_id).where(
        ProjectRoleAssignment.project_id == member.project_id, ProjectRoleAssignment.user_id == member.user_id)
        .order_by(ProjectRoleAssignment.role_id)))
    return _member_values(member, db.scalar(select(User.username).where(User.id == member.user_id)), roles)


def member_list(db, principal, project_id, *, limit=100, offset=0):
    access = authorize(db, principal, project_id, 'projects.members.read')
    predicate = ProjectMembership.project_id == access.project.id
    total = db.scalar(select(func.count()).select_from(ProjectMembership).where(predicate))
    rows = db.execute(select(ProjectMembership, User.username).join(User, User.id == ProjectMembership.user_id)
                      .where(predicate).order_by(ProjectMembership.user_id).limit(limit).offset(offset)).all()
    roles = defaultdict(list)
    if rows:
        for user_id, role_id in db.execute(select(ProjectRoleAssignment.user_id, ProjectRoleAssignment.role_id)
                .where(ProjectRoleAssignment.project_id == access.project.id,
                       ProjectRoleAssignment.user_id.in_([m.user_id for m, _ in rows]))
                .order_by(ProjectRoleAssignment.user_id, ProjectRoleAssignment.role_id)):
            roles[user_id].append(role_id)
    return page([_member_values(m, username, roles[m.user_id]) for m, username in rows], total, limit, offset)


def eligible_members(db, principal, project_id, *, limit=100, offset=0):
    access = authorize(db, principal, project_id, 'projects.members.manage')
    predicate = and_(TenantMembership.tenant_id == access.tenant.id, TenantMembership.status == 'active',
                     User.is_active.is_(True))
    query = select(TenantMembership.user_id, User.username).join(User, User.id == TenantMembership.user_id).where(predicate)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.execute(query.order_by(User.username, User.id).limit(limit).offset(offset))
    return page([{'user_id': u, 'username': n} for u, n in rows], total, limit, offset)


def _role_permissions(db, role_ids, permissions):
    allowed = permissions & PROJECT_DELEGABLE_PERMISSIONS
    if set(db.scalars(select(Role.id).where(Role.id.in_(role_ids)))) != set(role_ids):
        fail(422, 'ROLE_NOT_ASSIGNABLE', 'One or more roles cannot be assigned in this project')
    grouped = defaultdict(list)
    for role_id, permission_id, name in db.execute(select(RolePermission.role_id, Permission.id, Permission.name)
            .join(Permission, Permission.id == RolePermission.permission_id).where(RolePermission.role_id.in_(role_ids))):
        if name not in allowed:
            fail(403, 'GRANT_EXCEEDS_SCOPE', 'Cannot delegate permissions outside your effective project scope')
        grouped[role_id].append(permission_id)
    if any(not grouped[role_id] for role_id in role_ids):
        fail(422, 'ROLE_NOT_ASSIGNABLE', 'Roles must contain a delegable project permission')
    return grouped


def _replace_roles(db, access, member, role_ids):
    if 'projects.roles.assign' not in access.permissions:
        fail(403, 'SCOPED_PERMISSION_REQUIRED', 'Project role assignment permission is required')
    grouped = _role_permissions(db, role_ids, access.permissions)
    db.execute(delete(ProjectRoleAssignment).where(ProjectRoleAssignment.project_id == member.project_id,
                                                   ProjectRoleAssignment.user_id == member.user_id))
    for role_id in sorted(role_ids):
        assignment = ProjectRoleAssignment(tenant_id=member.tenant_id, project_id=member.project_id,
            user_id=member.user_id, role_id=role_id, created_by=access.identity.user_id)
        db.add(assignment)
        db.flush()
        db.add_all(ProjectRoleGrant(assignment_id=assignment.id, permission_id=p) for p in sorted(grouped[role_id]))
    db.flush()


def _member(db, access, user_id, expected_version):
    row = db.scalar(select(ProjectMembership).where(ProjectMembership.project_id == access.project.id,
        ProjectMembership.user_id == user_id).with_for_update().execution_options(populate_existing=True))
    if row is None:
        fail(404, 'MEMBER_NOT_FOUND', 'Project member not found')
    assert_version(row, expected_version)
    if not set(db.scalars(project_grants(access.project.id, user_id))) <= access.permissions:
        fail(403, 'GRANT_EXCEEDS_SCOPE', 'Cannot change a member with greater project permissions')
    return row


def _ensure_manager_remains(db, access):
    if access.global_administration:
        return
    required = {'projects.read', 'projects.members.manage', 'projects.roles.assign'}
    # Aggregate scoped grants, inherited tenant grants and global grants effective
    # only within an active project membership. No per-user permission query loop.
    project_rows = (select(ProjectRoleAssignment.user_id.label('user_id'), Permission.name.label('name'))
        .join(ProjectRoleGrant, ProjectRoleGrant.assignment_id == ProjectRoleAssignment.id)
        .join(RolePermission, and_(RolePermission.role_id == ProjectRoleAssignment.role_id,
                                   RolePermission.permission_id == ProjectRoleGrant.permission_id))
        .join(Permission, Permission.id == ProjectRoleGrant.permission_id)
        .join(ProjectMembership, and_(ProjectMembership.project_id == ProjectRoleAssignment.project_id,
                                      ProjectMembership.user_id == ProjectRoleAssignment.user_id))
        .where(ProjectMembership.project_id == access.project.id, ProjectMembership.status == 'active'))
    tenant_rows = (select(TenantRoleAssignment.user_id.label('user_id'), Permission.name.label('name'))
        .join(TenantRoleGrant, TenantRoleGrant.assignment_id == TenantRoleAssignment.id)
        .join(RolePermission, and_(RolePermission.role_id == TenantRoleAssignment.role_id,
                                   RolePermission.permission_id == TenantRoleGrant.permission_id))
        .join(Permission, Permission.id == TenantRoleGrant.permission_id)
        .where(TenantRoleAssignment.tenant_id == access.tenant.id))
    global_rows = (select(UserRole.user_id.label('user_id'), Permission.name.label('name'))
        .join(RolePermission, RolePermission.role_id == UserRole.role_id)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .join(ProjectMembership, ProjectMembership.user_id == UserRole.user_id)
        .where(ProjectMembership.project_id == access.project.id, ProjectMembership.status == 'active'))
    grants = union_all(project_rows, tenant_rows, global_rows).subquery()
    query = (select(grants.c.user_id).join(User, User.id == grants.c.user_id)
        .join(TenantMembership, and_(TenantMembership.user_id == grants.c.user_id,
                                     TenantMembership.tenant_id == access.tenant.id))
        .where(TenantMembership.status == 'active', User.is_active.is_(True), User.is_locked.is_(False),
               User.is_service_account.is_(False), or_(User.locked_until.is_(None), User.locked_until <= now()),
               grants.c.name.in_(required))
        .group_by(grants.c.user_id).having(func.count(func.distinct(grants.c.name)) == len(required)).limit(1))
    if db.scalar(query) is None:
        fail(409, 'PROJECT_MANAGER_REQUIRED', 'Keep an active human project manager or use global recovery')


def authorize_member_creation(db, principal, project_id, data):
    access = authorize(db, principal, project_id, 'projects.members.manage', write=True, lock=True)
    if data.role_ids:
        if 'projects.roles.assign' not in access.permissions:
            fail(403, 'SCOPED_PERMISSION_REQUIRED', 'Project role assignment permission is required')
        _role_permissions(db, data.role_ids, access.permissions)
    # This directory is restricted to this tenant, unlike global user-ID lookups.
    eligible = db.scalar(select(TenantMembership.user_id).join(User, User.id == TenantMembership.user_id).where(
        TenantMembership.tenant_id == access.tenant.id, TenantMembership.user_id == data.user_id,
        TenantMembership.status == 'active', User.is_active.is_(True)))
    if eligible is None:
        fail(404, 'MEMBER_NOT_FOUND', 'Eligible tenant member not found')
    return access


def member_create(db, principal, project_id, data):
    access = authorize_member_creation(db, principal, project_id, data)
    if db.get(ProjectMembership, (access.project.id, data.user_id)) is not None:
        fail(409, 'MEMBER_EXISTS', 'User already has a project membership')
    row = ProjectMembership(tenant_id=access.tenant.id, project_id=access.project.id,
                           user_id=data.user_id, status=data.status, created_by=access.identity.user_id)
    db.add(row)
    db.flush()
    if data.role_ids:
        _replace_roles(db, access, row, data.role_ids)
    return _member_output(db, row)


def member_update(db, principal, project_id, user_id, data):
    access = authorize(db, principal, project_id, 'projects.members.manage', write=True, lock=True)
    row = _member(db, access, user_id, data.expected_version)
    row.status = data.status
    row.version += 1
    db.flush()
    _ensure_manager_remains(db, access)
    return _member_output(db, row)


def member_roles(db, principal, project_id, user_id, data):
    access = authorize(db, principal, project_id, 'projects.roles.assign', write=True, lock=True)
    row = _member(db, access, user_id, data.expected_version)
    _replace_roles(db, access, row, data.role_ids)
    row.version += 1
    db.flush()
    _ensure_manager_remains(db, access)
    return _member_output(db, row)


def member_delete(db, principal, project_id, user_id, expected_version):
    access = authorize(db, principal, project_id, 'projects.members.manage', write=True, lock=True)
    row = _member(db, access, user_id, expected_version)
    db.delete(row)
    db.flush()
    _ensure_manager_remains(db, access)
    return {'deleted': True}


def assignable_roles(db, principal, project_id, *, limit=100, offset=0):
    access = authorize(db, principal, project_id, 'projects.roles.assign')
    allowed = access.permissions & PROJECT_DELEGABLE_PERMISSIONS
    present = exists(select(RolePermission.permission_id).where(RolePermission.role_id == Role.id))
    forbidden = exists(select(RolePermission.permission_id).join(Permission)
        .where(RolePermission.role_id == Role.id, Permission.name.not_in(allowed)))
    query = select(Role.id, Role.name).where(present, ~forbidden)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.execute(query.order_by(Role.name, Role.id).limit(limit).offset(offset)).all()
    permissions = defaultdict(list)
    if rows:
        for role_id, name in db.execute(select(RolePermission.role_id, Permission.name).join(Permission)
                .where(RolePermission.role_id.in_([row.id for row in rows])).order_by(Permission.name)):
            permissions[role_id].append(name)
    return page([{'id': row.id, 'name': row.name, 'permissions': permissions[row.id]} for row in rows], total, limit, offset)


def audit_list(db, principal, project_id, *, limit=100, offset=0):
    access = authorize(db, principal, project_id, 'projects.audit.read')
    predicate = or_(and_(Audit.resource == 'projects', Audit.resource_id == access.project.id),
        and_(Audit.resource == 'project_memberships', Audit.resource_id.like(access.project.id + ':%')))
    total = db.scalar(select(func.count()).select_from(Audit).where(predicate))
    rows = db.scalars(select(Audit).where(predicate).order_by(Audit.id.desc()).limit(limit).offset(offset))
    return page([{'id': row.id, 'timestamp': row.timestamp.replace(tzinfo=timezone.utc), 'user_id': row.user_id,
                  'action': row.action, 'resource': row.resource, 'resource_id': row.resource_id,
                  'result': row.result, 'request_id': row.request_id} for row in rows], total, limit, offset)


def creation_scopes(db, principal, *, limit=100, offset=0):
    from app.tenancy.authorization import live_grants
    from app.tenancy.models import Tenant
    actor = identity(db, principal)
    predicate = Tenant.deleted_at.is_(None)
    if not ('projects.admin' in actor.global_permissions and 'projects.create' in actor.global_permissions):
        permitted = (actor.token_ceiling is None or 'projects.create' in actor.token_ceiling)
        membership = exists(select(TenantMembership.user_id).where(TenantMembership.tenant_id == Tenant.id,
            TenantMembership.user_id == actor.user_id, TenantMembership.status == 'active').correlate(Tenant))
        grant = exists(live_grants(Tenant.id, actor.user_id).where(Permission.name == 'projects.create').correlate(Tenant))
        predicate = and_(predicate, Tenant.status == 'active', permitted, membership, grant)
    total = db.scalar(select(func.count()).select_from(Tenant).where(predicate))
    rows = db.execute(select(Tenant.id, Tenant.name, Tenant.slug).where(predicate)
                      .order_by(Tenant.slug, Tenant.id).limit(limit).offset(offset))
    return page([{'tenant_id': i, 'name': n, 'slug': s} for i, n, s in rows], total, limit, offset)
