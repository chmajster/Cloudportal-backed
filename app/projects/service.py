"""Transactional project administration; authorization precedes every lookup/write.

Callers own the transaction and emit audit/events in that same transaction.
No provider operations or global grant mutation are performed by this domain.
"""
from collections import defaultdict
from datetime import timezone

from sqlalchemy import and_, delete, exists, func, or_, select

from app.models import Audit, Permission, Role, RolePermission, User, now
from app.projects.authorization import authorize, project_grants as live_grants, visible_projects
from app.tenancy.authorization import (assert_version, fail, identity, lock_authorization,
                                       authorize as authorize_tenant, visible_tenants)
from app.tenancy.models import TenantMembership, Tenant
from app.tenancy.service import tenant_output
from app.projects.models import (Project, ProjectMembership, ProjectRoleAssignment,
                                 ProjectRoleGrant, UserProjectContext)
from app.projects.permissions import PROJECT_DELEGABLE_PERMISSIONS


def project_output(project):
    return {**tenant_output(project), 'tenant_id': project.tenant_id,
            'default_environment': project.default_environment}


def project_list(db, principal, *, limit=100, offset=0, status=None, tenant_id=None):
    predicate = visible_projects(identity(db, principal))
    if tenant_id is not None:
        predicate = and_(predicate, Project.tenant_id == str(tenant_id))
    if status is not None:
        predicate = and_(predicate, Project.status == status)
    total = db.scalar(select(func.count()).select_from(Project).where(predicate))
    rows = db.scalars(select(Project).where(predicate).order_by(Project.slug, Project.id)
                      .offset(offset).limit(limit)).all()
    return {'items': [project_output(row) for row in rows], 'total': total, 'limit': limit, 'offset': offset}


def project_get(db, principal, project_id):
    return project_output(authorize(db, principal, project_id, 'projects.read').project)


def project_permissions(db, principal, project_id):
    access = authorize(db, principal, project_id, 'projects.read')
    return {'tenant_id': access.project.tenant_id, 'project_id': access.project.id, 'scope': 'PROJECT',
            'permissions': sorted(access.permissions & (PROJECT_DELEGABLE_PERMISSIONS | {
                'projects.create', 'projects.delete', 'projects.admin'})),
            'parent_administration': access.parent_admin}


def authorize_creation(db, principal, tenant_id):
    access = authorize_tenant(db, principal, tenant_id, 'projects.create', write=True, lock=True)
    if 'projects.read' not in access.permissions:
        fail(403, 'SCOPED_PERMISSION_REQUIRED', 'Project creation also requires projects.read')
    return access


def project_create(db, principal, data):
    access = authorize_creation(db, principal, data.tenant_id)
    values = data.model_dump(exclude={'tenant_id'})
    values['metadata_json'] = values.pop('metadata')
    project = Project(**values, tenant_id=access.tenant.id, created_by=access.identity.user_id)
    db.add(project)
    db.flush()
    if db.get(TenantMembership, (project.tenant_id, access.identity.user_id)) is not None:
        # Membership only; never manufacture a role or escalate the creator.
        db.add(ProjectMembership(tenant_id=project.tenant_id, project_id=project.id,
                                 user_id=access.identity.user_id, created_by=access.identity.user_id))
        db.flush()
    return project_output(project)


def project_update(db, principal, project_id, data):
    access = authorize(db, principal, project_id, 'projects.update', write=True, lock=True)
    project = access.project
    assert_version(project, data.expected_version)
    if project.is_system and (data.name != project.name or data.slug != project.slug or data.status != 'active'):
        fail(409, 'SYSTEM_PROJECT_PROTECTED', 'The Default project must retain its identity and active status')
    if not access.parent_admin and data.status != project.status:
        fail(403, 'GLOBAL_PERMISSION_REQUIRED', 'Changing project status requires parent administration')
    values = data.model_dump(exclude={'expected_version'})
    values['metadata_json'] = values.pop('metadata')
    for key, value in values.items():
        setattr(project, key, value)
    # Even a no-op PUT consumes its revision, so a stale client never silently wins.
    project.version += 1
    db.flush()
    return project_output(project)


def project_delete(db, principal, project_id, expected_version):
    access = authorize(db, principal, project_id, 'projects.delete', write=True, lock=True)
    project = access.project
    assert_version(project, expected_version)
    if project.is_system:
        fail(409, 'SYSTEM_PROJECT_PROTECTED', 'The Default project cannot be deleted')
    if db.scalar(select(exists().where(ProjectMembership.project_id == project.id))):
        fail(409, 'PROJECT_NOT_EMPTY', 'Remove project memberships before deleting the project')
    # A tombstone preserves history. Resource bindings must extend this guard
    # when resource scoping is integrated; no provider deletion occurs here.
    project.status = 'disabled'
    project.deleted_at = now()
    project.version += 1
    db.flush()
    return {'deleted': True}


def _member(db, project_id, user_id):
    member = db.scalar(select(ProjectMembership).where(
        ProjectMembership.project_id == project_id, ProjectMembership.user_id == user_id,
    ).with_for_update().execution_options(populate_existing=True))
    if member is None:
        fail(404, 'MEMBER_NOT_FOUND', 'Project member not found')
    return member


def _member_output(db, member):
    roles = db.scalars(select(ProjectRoleAssignment.role_id).where(
        ProjectRoleAssignment.project_id == member.project_id,
        ProjectRoleAssignment.user_id == member.user_id,
    ).order_by(ProjectRoleAssignment.role_id)).all()
    username = db.scalar(select(User.username).where(User.id == member.user_id))
    return _member_values(member, username, roles)


def _member_values(member, username, role_ids):
    return {'tenant_id': member.tenant_id, 'project_id': member.project_id, 'user_id': member.user_id, 'username': username,
            'status': member.status, 'role_ids': role_ids, 'version': member.version,
            'created_at': member.created_at.replace(tzinfo=timezone.utc),
            'updated_at': member.updated_at.replace(tzinfo=timezone.utc)}


def member_list(db, principal, project_id, *, limit=100, offset=0, status=None):
    access = authorize(db, principal, project_id, 'projects.members.read')
    predicate = ProjectMembership.project_id == access.project.id
    if status is not None:
        predicate = and_(predicate, ProjectMembership.status == status)
    total = db.scalar(select(func.count()).select_from(ProjectMembership).where(predicate))
    rows = db.execute(select(ProjectMembership, User.username).join(User, User.id == ProjectMembership.user_id)
        .where(predicate).order_by(ProjectMembership.user_id).offset(offset).limit(limit)).all()
    roles = defaultdict(list)
    if rows:
        for user_id, role_id in db.execute(select(ProjectRoleAssignment.user_id, ProjectRoleAssignment.role_id)
            .where(ProjectRoleAssignment.project_id == access.project.id,
                   ProjectRoleAssignment.user_id.in_([member.user_id for member, _ in rows]))
            .order_by(ProjectRoleAssignment.user_id, ProjectRoleAssignment.role_id)):
            roles[user_id].append(role_id)
    return {'items': [_member_values(member, username, roles[member.user_id]) for member, username in rows],
            'total': total, 'limit': limit, 'offset': offset}


def _role_permissions(db, role_ids, permissions):
    allowed = PROJECT_DELEGABLE_PERMISSIONS & permissions
    roles = db.scalars(select(Role.id).where(Role.id.in_(role_ids))).all()
    if set(roles) != set(role_ids):
        fail(422, 'ROLE_NOT_ASSIGNABLE', 'One or more roles cannot be assigned in this project')
    grouped = defaultdict(list)
    for role_id, permission_id, name in db.execute(select(
            RolePermission.role_id, Permission.id, Permission.name,
    ).join(Permission, Permission.id == RolePermission.permission_id)
        .where(RolePermission.role_id.in_(role_ids))):
        if name not in allowed:
            fail(403, 'GRANT_EXCEEDS_SCOPE', 'Cannot delegate permissions outside your effective project scope')
        grouped[role_id].append(permission_id)
    if any(not grouped[role_id] for role_id in role_ids):
        fail(422, 'ROLE_NOT_ASSIGNABLE', 'Roles must contain at least one delegable project permission')
    return grouped


def _protect_member(db, access, user_id):
    target_permissions = set(db.scalars(live_grants(access.project.id, user_id)))
    if not target_permissions <= access.permissions:
        fail(403, 'GRANT_EXCEEDS_SCOPE', 'Cannot change a member with greater project permissions')


def _replace_roles(db, access, member, role_ids):
    if 'projects.roles.assign' not in access.permissions:
        fail(403, 'SCOPED_PERMISSION_REQUIRED', 'Project role assignment permission is required')
    grouped = _role_permissions(db, role_ids, access.permissions)
    # Ceiling rows cascade with assignments; no role/user/global assignment is changed.
    db.execute(delete(ProjectRoleAssignment).where(
        ProjectRoleAssignment.project_id == member.project_id,
        ProjectRoleAssignment.user_id == member.user_id,
    ))
    for role_id in sorted(role_ids):
        assignment = ProjectRoleAssignment(project_id=member.project_id, user_id=member.user_id,
                                          role_id=role_id, created_by=access.identity.user_id)
        db.add(assignment)
        db.flush()
        db.add_all(ProjectRoleGrant(assignment_id=assignment.id, permission_id=permission_id)
                   for permission_id in sorted(grouped[role_id]))
    db.flush()


def _ensure_manager_remains(db, access):
    if access.parent_admin:
        return
    required = {'projects.members.manage', 'projects.roles.assign', 'projects.read'}
    # One bounded aggregate query, not a scan with one permission query per user.
    managers = (select(ProjectMembership.user_id)
        .join(User, User.id == ProjectMembership.user_id)
        .join(TenantMembership, and_(TenantMembership.tenant_id == ProjectMembership.tenant_id,
                                    TenantMembership.user_id == ProjectMembership.user_id))
        .join(ProjectRoleAssignment, and_(ProjectRoleAssignment.project_id == ProjectMembership.project_id,
                                        ProjectRoleAssignment.user_id == ProjectMembership.user_id))
        .join(ProjectRoleGrant, ProjectRoleGrant.assignment_id == ProjectRoleAssignment.id)
        .join(RolePermission, and_(RolePermission.role_id == ProjectRoleAssignment.role_id,
                                   RolePermission.permission_id == ProjectRoleGrant.permission_id))
        .join(Permission, Permission.id == ProjectRoleGrant.permission_id)
        .where(ProjectMembership.project_id == access.project.id, ProjectMembership.status == 'active',
               TenantMembership.status == 'active',
               User.is_active.is_(True), User.is_locked.is_(False), User.is_service_account.is_(False),
               or_(User.locked_until.is_(None), User.locked_until <= now()),
               Permission.name.in_(required))
        .group_by(ProjectMembership.user_id)
        .having(func.count(func.distinct(Permission.name)) == len(required)).limit(1))
    if db.scalar(managers) is None:
        fail(409, 'PROJECT_MANAGER_REQUIRED', 'Keep an active human project manager or ask a global administrator')


def authorize_member_creation(db, principal, project_id, data):
    access = authorize(db, principal, project_id, 'projects.members.manage', write=True, lock=True)
    # Eligible identities come only from this tenant, never the global directory.
    # The same check runs on idempotency replay after membership revocation.
    user_exists = db.scalar(select(TenantMembership.user_id).join(User, User.id == TenantMembership.user_id)
        .where(TenantMembership.tenant_id == access.project.tenant_id,
               TenantMembership.user_id == data.user_id, TenantMembership.status == 'active',
               User.is_active.is_(True)))
    if user_exists is None:
        fail(422, 'MEMBER_NOT_ASSIGNABLE', 'The selected tenant member cannot be assigned')
    if data.role_ids:
        if 'projects.roles.assign' not in access.permissions:
            fail(403, 'SCOPED_PERMISSION_REQUIRED', 'Project role assignment permission is required')
        _role_permissions(db, data.role_ids, access.permissions)
    return access


def member_create(db, principal, project_id, data):
    access = authorize_member_creation(db, principal, project_id, data)
    user_exists = db.scalar(select(User.id).where(User.id == data.user_id, User.is_active.is_(True)))
    if user_exists is None:
        fail(422, 'MEMBER_NOT_ASSIGNABLE', 'The selected user cannot be assigned')
    if db.get(ProjectMembership, (access.project.id, data.user_id)) is not None:
        fail(409, 'MEMBER_EXISTS', 'This user already has a project membership')
    member = ProjectMembership(tenant_id=access.project.tenant_id, project_id=access.project.id, user_id=data.user_id,
                              status=data.status, created_by=access.identity.user_id)
    db.add(member)
    db.flush()
    if data.role_ids:
        _replace_roles(db, access, member, data.role_ids)
    return _member_output(db, member)


def member_update(db, principal, project_id, user_id, data):
    access = authorize(db, principal, project_id, 'projects.members.manage', write=True, lock=True)
    member = _member(db, access.project.id, user_id)
    assert_version(member, data.expected_version)
    _protect_member(db, access, user_id)
    member.status = data.status
    member.version += 1
    db.flush()
    _ensure_manager_remains(db, access)
    return _member_output(db, member)


def member_roles(db, principal, project_id, user_id, data):
    access = authorize(db, principal, project_id, 'projects.roles.assign', write=True, lock=True)
    member = _member(db, access.project.id, user_id)
    assert_version(member, data.expected_version)
    _protect_member(db, access, user_id)
    _replace_roles(db, access, member, data.role_ids)
    member.version += 1
    db.flush()
    _ensure_manager_remains(db, access)
    return _member_output(db, member)


def member_delete(db, principal, project_id, user_id, expected_version):
    access = authorize(db, principal, project_id, 'projects.members.manage', write=True, lock=True)
    member = _member(db, access.project.id, user_id)
    assert_version(member, expected_version)
    _protect_member(db, access, user_id)
    db.delete(member)
    db.flush()
    _ensure_manager_remains(db, access)
    return {'deleted': True}


def assignable_roles(db, principal, project_id, *, limit=100, offset=0):
    access = authorize(db, principal, project_id, 'projects.roles.assign')
    allowed = access.permissions & PROJECT_DELEGABLE_PERMISSIONS
    # Exclude roles containing even one non-delegable permission in SQL.
    present = exists(select(RolePermission.permission_id).where(RolePermission.role_id == Role.id))
    forbidden = exists(select(RolePermission.permission_id).join(Permission)
        .where(RolePermission.role_id == Role.id, Permission.name.not_in(allowed)))
    predicate = and_(present, ~forbidden)
    total = db.scalar(select(func.count()).select_from(Role).where(predicate))
    rows = db.execute(select(Role.id, Role.name).where(predicate).order_by(Role.name, Role.id)
                      .offset(offset).limit(limit)).all()
    permissions = defaultdict(list)
    if rows:
        for role_id, name in db.execute(select(RolePermission.role_id, Permission.name).join(Permission)
                .where(RolePermission.role_id.in_([row.id for row in rows])).order_by(Permission.name)):
            permissions[role_id].append(name)
    return {'items': [{'id': row.id, 'name': row.name, 'permissions': permissions[row.id]} for row in rows],
            'total': total, 'limit': limit, 'offset': offset}


def audit_list(db, principal, project_id, *, limit=100, offset=0):
    access = authorize(db, principal, project_id, 'projects.audit.read')
    predicate = or_(
        and_(Audit.resource == 'projects', Audit.resource_id == access.project.id),
        and_(Audit.resource == 'project_memberships', Audit.resource_id.like(access.project.id + ':%')),
    )
    total = db.scalar(select(func.count()).select_from(Audit).where(predicate))
    rows = db.scalars(select(Audit).where(predicate).order_by(Audit.id.desc()).offset(offset).limit(limit))
    # Do not expose IP addresses/token IDs or unrelated global audit entries to scoped members.
    return {'items': [{'id': row.id, 'timestamp': row.timestamp.replace(tzinfo=timezone.utc), 'user_id': row.user_id,
                       'action': row.action, 'resource': row.resource, 'resource_id': row.resource_id,
                       'result': row.result, 'request_id': row.request_id} for row in rows],
            'total': total, 'limit': limit, 'offset': offset}


def eligible_members(db, principal, project_id, *, limit=100, offset=0):
    access = authorize(db, principal, project_id, 'projects.members.manage')
    predicate = and_(TenantMembership.tenant_id == access.project.tenant_id,
                     TenantMembership.status == 'active', User.is_active.is_(True),
                     ~exists(select(ProjectMembership.user_id).where(
                         ProjectMembership.project_id == access.project.id,
                         ProjectMembership.user_id == TenantMembership.user_id).correlate(TenantMembership)))
    query = select(TenantMembership.user_id, User.username).join(User, User.id == TenantMembership.user_id).where(predicate)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.execute(query.order_by(User.username, User.id).offset(offset).limit(limit))
    return {'items': [dict(row._mapping) for row in rows], 'total': total, 'limit': limit, 'offset': offset}


def context_get(db, principal):
    actor = identity(db, principal)
    row = db.get(UserProjectContext, actor.user_id)
    if row is None or row.project_id is None:
        return {'selected': None, 'version': row.version if row else 0}
    access = authorize(db, principal, row.project_id, 'projects.read', tenant_id=row.tenant_id)
    return {'selected': project_output(access.project), 'version': row.version}


def context_set(db, principal, data):
    access = authorize(db, principal, data.project_id, 'projects.select', tenant_id=data.tenant_id, lock=True)
    row = db.get(UserProjectContext, access.identity.user_id)
    actual_version = row.version if row else 0
    if data.expected_version != actual_version:
        fail(409, 'VERSION_CONFLICT', 'Project selection changed; reload it before retrying')
    if row is None:
        row = UserProjectContext(user_id=access.identity.user_id, tenant_id=access.project.tenant_id,
                                 project_id=access.project.id)
        db.add(row)
    else:
        row.tenant_id, row.project_id = access.project.tenant_id, access.project.id
        row.version += 1
    db.flush()
    return {'selected': project_output(access.project), 'version': row.version}


def context_clear(db, principal, expected_version):
    lock_authorization(db)
    actor = identity(db, principal)
    row = db.get(UserProjectContext, actor.user_id)
    if row is None:
        if expected_version is not None:
            fail(409, 'VERSION_CONFLICT', 'Project selection changed; reload it before retrying')
        return {'selected': None, 'version': 0}
    if expected_version is not None:
        assert_version(row, expected_version)
    # Retain a monotonic revision (including clears) to prevent the ABA race.
    # Clearing a revoked selection must not require access to the old project.
    row.tenant_id = row.project_id = None
    row.version += 1
    db.flush()
    return {'selected': None, 'version': row.version}


def creation_tenants(db, principal, *, limit=100, offset=0):
    actor = identity(db, principal)
    predicate = and_(visible_tenants(actor, 'projects.create'), visible_tenants(actor, 'projects.read'),
                     True if actor.platform_admin else Tenant.status == 'active')
    total = db.scalar(select(func.count()).select_from(Tenant).where(predicate))
    rows = db.scalars(select(Tenant).where(predicate).order_by(Tenant.slug, Tenant.id).offset(offset).limit(limit))
    return {'items': [tenant_output(row) for row in rows], 'total': total, 'limit': limit, 'offset': offset}
