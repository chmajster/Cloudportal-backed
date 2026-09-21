"""Transactional tenant administration; authorization precedes every lookup/write.

Callers own the transaction and emit audit/events in that same transaction.
No provider operations or global grant mutation are performed by this domain.
"""
from collections import defaultdict
from datetime import timezone

from sqlalchemy import and_, delete, exists, func, or_, select

from app.projects.models import Project
from app.models import Audit, Permission, Role, RolePermission, User, now
from app.tenancy.authorization import (assert_version, authorize, fail, identity, live_grants,
                                       lock_authorization, require_global, visible_tenants)
from app.tenancy.models import Tenant, TenantMembership, TenantRoleAssignment, TenantRoleGrant
from app.tenancy.permissions import DELEGABLE_PERMISSIONS


def tenant_output(tenant):
    return {
        'id': tenant.id, 'name': tenant.name, 'slug': tenant.slug,
        'description': tenant.description, 'status': tenant.status,
        'labels': tenant.labels, 'metadata': tenant.metadata_json,
        'is_system': tenant.is_system, 'created_by': tenant.created_by,
        'created_at': tenant.created_at.replace(tzinfo=timezone.utc),
        'updated_at': tenant.updated_at.replace(tzinfo=timezone.utc),
        'version': tenant.version,
    }


def tenant_list(db, principal, *, limit=100, offset=0, status=None):
    predicate = visible_tenants(identity(db, principal))
    if status is not None:
        predicate = and_(predicate, Tenant.status == status)
    total = db.scalar(select(func.count()).select_from(Tenant).where(predicate))
    rows = db.scalars(select(Tenant).where(predicate).order_by(Tenant.slug, Tenant.id)
                      .offset(offset).limit(limit)).all()
    return {'items': [tenant_output(row) for row in rows], 'total': total, 'limit': limit, 'offset': offset}


def tenant_get(db, principal, tenant_id):
    return tenant_output(authorize(db, principal, tenant_id, 'tenants.read').tenant)


def tenant_permissions(db, principal, tenant_id):
    access = authorize(db, principal, tenant_id, 'tenants.read')
    return {'tenant_id': access.tenant.id, 'scope': 'TENANT',
            'permissions': sorted(access.permissions & (DELEGABLE_PERMISSIONS | {
                'tenants.create', 'tenants.delete', 'tenants.admin'})),
            'global_administration': access.identity.platform_admin}


def tenant_create(db, principal, data):
    lock_authorization(db)
    actor = require_global(db, principal, 'tenants.create')
    values = data.model_dump()
    values['metadata_json'] = values.pop('metadata')
    tenant = Tenant(**values, created_by=actor.user_id)
    db.add(tenant)
    db.flush()
    return tenant_output(tenant)


def tenant_update(db, principal, tenant_id, data):
    access = authorize(db, principal, tenant_id, 'tenants.update', write=True, lock=True)
    tenant = access.tenant
    assert_version(tenant, data.expected_version)
    if tenant.is_system and (data.name != tenant.name or data.slug != tenant.slug or data.status != 'active'):
        fail(409, 'SYSTEM_TENANT_PROTECTED', 'The Default tenant must retain its identity and active status')
    if not access.identity.platform_admin and data.status == 'disabled':
        fail(403, 'GLOBAL_PERMISSION_REQUIRED', 'Disabling a tenant requires global tenancy administration')
    values = data.model_dump(exclude={'expected_version'})
    values['metadata_json'] = values.pop('metadata')
    for key, value in values.items():
        setattr(tenant, key, value)
    # Even a no-op PUT consumes its revision, so a stale client never silently wins.
    tenant.version += 1
    db.flush()
    return tenant_output(tenant)


def tenant_delete(db, principal, tenant_id, expected_version):
    lock_authorization(db)
    require_global(db, principal, 'tenants.delete')
    access = authorize(db, principal, tenant_id, 'tenants.delete', write=True, lock=True)
    tenant = access.tenant
    assert_version(tenant, expected_version)
    if tenant.is_system:
        fail(409, 'SYSTEM_TENANT_PROTECTED', 'The Default tenant cannot be deleted')
    if db.scalar(select(exists().where(TenantMembership.tenant_id == tenant.id))):
        fail(409, 'TENANT_NOT_EMPTY', 'Remove tenant memberships before deleting the tenant')
    if db.scalar(select(exists().where(Project.tenant_id == tenant.id, Project.deleted_at.is_(None)))):
        fail(409, 'TENANT_NOT_EMPTY', 'Remove tenant projects before deleting the tenant')
    # Tombstone preserves identity and audit references.
    tenant.status = 'disabled'
    tenant.deleted_at = now()
    tenant.version += 1
    db.flush()
    return {'deleted': True}


def _member(db, tenant_id, user_id):
    member = db.scalar(select(TenantMembership).where(
        TenantMembership.tenant_id == tenant_id, TenantMembership.user_id == user_id,
    ).with_for_update().execution_options(populate_existing=True))
    if member is None:
        fail(404, 'MEMBER_NOT_FOUND', 'Tenant member not found')
    return member


def _member_output(db, member):
    roles = db.scalars(select(TenantRoleAssignment.role_id).where(
        TenantRoleAssignment.tenant_id == member.tenant_id,
        TenantRoleAssignment.user_id == member.user_id,
    ).order_by(TenantRoleAssignment.role_id)).all()
    username = db.scalar(select(User.username).where(User.id == member.user_id))
    return _member_values(member, username, roles)


def _member_values(member, username, role_ids):
    return {'tenant_id': member.tenant_id, 'user_id': member.user_id, 'username': username,
            'status': member.status, 'role_ids': role_ids, 'version': member.version,
            'created_at': member.created_at.replace(tzinfo=timezone.utc),
            'updated_at': member.updated_at.replace(tzinfo=timezone.utc)}


def member_list(db, principal, tenant_id, *, limit=100, offset=0, status=None):
    access = authorize(db, principal, tenant_id, 'tenants.members.read')
    predicate = TenantMembership.tenant_id == access.tenant.id
    if status is not None:
        predicate = and_(predicate, TenantMembership.status == status)
    total = db.scalar(select(func.count()).select_from(TenantMembership).where(predicate))
    rows = db.execute(select(TenantMembership, User.username).join(User, User.id == TenantMembership.user_id)
        .where(predicate).order_by(TenantMembership.user_id).offset(offset).limit(limit)).all()
    roles = defaultdict(list)
    if rows:
        for user_id, role_id in db.execute(select(TenantRoleAssignment.user_id, TenantRoleAssignment.role_id)
            .where(TenantRoleAssignment.tenant_id == access.tenant.id,
                   TenantRoleAssignment.user_id.in_([member.user_id for member, _ in rows]))
            .order_by(TenantRoleAssignment.user_id, TenantRoleAssignment.role_id)):
            roles[user_id].append(role_id)
    return {'items': [_member_values(member, username, roles[member.user_id]) for member, username in rows],
            'total': total, 'limit': limit, 'offset': offset}


def _role_permissions(db, role_ids, permissions):
    allowed = DELEGABLE_PERMISSIONS & permissions
    roles = db.scalars(select(Role.id).where(Role.id.in_(role_ids))).all()
    if set(roles) != set(role_ids):
        fail(422, 'ROLE_NOT_ASSIGNABLE', 'One or more roles cannot be assigned in this tenant')
    grouped = defaultdict(list)
    for role_id, permission_id, name in db.execute(select(
            RolePermission.role_id, Permission.id, Permission.name,
    ).join(Permission, Permission.id == RolePermission.permission_id)
        .where(RolePermission.role_id.in_(role_ids))):
        if name not in allowed:
            fail(403, 'GRANT_EXCEEDS_SCOPE', 'Cannot delegate permissions outside your effective tenant scope')
        grouped[role_id].append(permission_id)
    if any(not grouped[role_id] for role_id in role_ids):
        fail(422, 'ROLE_NOT_ASSIGNABLE', 'Roles must contain at least one delegable tenant permission')
    return grouped


def _protect_member(db, access, user_id):
    target_permissions = set(db.scalars(live_grants(access.tenant.id, user_id)))
    if not target_permissions <= access.permissions:
        fail(403, 'GRANT_EXCEEDS_SCOPE', 'Cannot change a member with greater tenant permissions')


def _replace_roles(db, access, member, role_ids):
    if 'tenants.roles.assign' not in access.permissions:
        fail(403, 'SCOPED_PERMISSION_REQUIRED', 'Tenant role assignment permission is required')
    grouped = _role_permissions(db, role_ids, access.permissions)
    # Ceiling rows cascade with assignments; no role/user/global assignment is changed.
    db.execute(delete(TenantRoleAssignment).where(
        TenantRoleAssignment.tenant_id == member.tenant_id,
        TenantRoleAssignment.user_id == member.user_id,
    ))
    for role_id in sorted(role_ids):
        assignment = TenantRoleAssignment(tenant_id=member.tenant_id, user_id=member.user_id,
                                          role_id=role_id, created_by=access.identity.user_id)
        db.add(assignment)
        db.flush()
        db.add_all(TenantRoleGrant(assignment_id=assignment.id, permission_id=permission_id)
                   for permission_id in sorted(grouped[role_id]))
    db.flush()


def _ensure_manager_remains(db, access):
    if access.identity.platform_admin:
        return
    required = {'tenants.members.manage', 'tenants.roles.assign', 'tenants.read'}
    # One bounded aggregate query, not a scan with one permission query per user.
    managers = (select(TenantMembership.user_id)
        .join(User, User.id == TenantMembership.user_id)
        .join(TenantRoleAssignment, and_(TenantRoleAssignment.tenant_id == TenantMembership.tenant_id,
                                        TenantRoleAssignment.user_id == TenantMembership.user_id))
        .join(TenantRoleGrant, TenantRoleGrant.assignment_id == TenantRoleAssignment.id)
        .join(RolePermission, and_(RolePermission.role_id == TenantRoleAssignment.role_id,
                                   RolePermission.permission_id == TenantRoleGrant.permission_id))
        .join(Permission, Permission.id == TenantRoleGrant.permission_id)
        .where(TenantMembership.tenant_id == access.tenant.id, TenantMembership.status == 'active',
               User.is_active.is_(True), User.is_locked.is_(False), User.is_service_account.is_(False),
               or_(User.locked_until.is_(None), User.locked_until <= now()),
               Permission.name.in_(required))
        .group_by(TenantMembership.user_id)
        .having(func.count(func.distinct(Permission.name)) == len(required)).limit(1))
    if db.scalar(managers) is None:
        fail(409, 'TENANT_MANAGER_REQUIRED', 'Keep an active human tenant manager or ask a global administrator')


def authorize_member_creation(db, principal, tenant_id, data):
    access = authorize(db, principal, tenant_id, 'tenants.members.manage', write=True, lock=True)
    # User identities are global in the current platform. A tenant-only manager
    # must not use member creation to probe the global directory by guessing IDs.
    if 'users.read' not in access.identity.global_permissions:
        fail(403, 'DIRECTORY_PERMISSION_REQUIRED', 'Adding an existing account requires global user-directory read permission')
    if data.role_ids:
        if 'tenants.roles.assign' not in access.permissions:
            fail(403, 'SCOPED_PERMISSION_REQUIRED', 'Tenant role assignment permission is required')
        _role_permissions(db, data.role_ids, access.permissions)
    return access


def member_create(db, principal, tenant_id, data):
    access = authorize_member_creation(db, principal, tenant_id, data)
    user_exists = db.scalar(select(User.id).where(User.id == data.user_id, User.is_active.is_(True)))
    if user_exists is None:
        fail(422, 'MEMBER_NOT_ASSIGNABLE', 'The selected user cannot be assigned')
    if db.get(TenantMembership, (access.tenant.id, data.user_id)) is not None:
        fail(409, 'MEMBER_EXISTS', 'This user already has a tenant membership')
    member = TenantMembership(tenant_id=access.tenant.id, user_id=data.user_id,
                              status=data.status, created_by=access.identity.user_id)
    db.add(member)
    db.flush()
    if data.role_ids:
        _replace_roles(db, access, member, data.role_ids)
    return _member_output(db, member)


def member_update(db, principal, tenant_id, user_id, data):
    access = authorize(db, principal, tenant_id, 'tenants.members.manage', write=True, lock=True)
    member = _member(db, access.tenant.id, user_id)
    assert_version(member, data.expected_version)
    _protect_member(db, access, user_id)
    member.status = data.status
    member.version += 1
    db.flush()
    _ensure_manager_remains(db, access)
    return _member_output(db, member)


def member_roles(db, principal, tenant_id, user_id, data):
    access = authorize(db, principal, tenant_id, 'tenants.roles.assign', write=True, lock=True)
    member = _member(db, access.tenant.id, user_id)
    assert_version(member, data.expected_version)
    _protect_member(db, access, user_id)
    _replace_roles(db, access, member, data.role_ids)
    member.version += 1
    db.flush()
    _ensure_manager_remains(db, access)
    return _member_output(db, member)


def member_delete(db, principal, tenant_id, user_id, expected_version):
    access = authorize(db, principal, tenant_id, 'tenants.members.manage', write=True, lock=True)
    member = _member(db, access.tenant.id, user_id)
    assert_version(member, expected_version)
    _protect_member(db, access, user_id)
    db.delete(member)
    db.flush()
    _ensure_manager_remains(db, access)
    return {'deleted': True}


def assignable_roles(db, principal, tenant_id, *, limit=100, offset=0):
    access = authorize(db, principal, tenant_id, 'tenants.roles.assign')
    allowed = access.permissions & DELEGABLE_PERMISSIONS
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


def audit_list(db, principal, tenant_id, *, limit=100, offset=0):
    access = authorize(db, principal, tenant_id, 'tenants.audit.read')
    predicate = or_(
        and_(Audit.resource == 'tenants', Audit.resource_id == access.tenant.id),
        and_(Audit.resource == 'tenant_memberships', Audit.resource_id.like(access.tenant.id + ':%')),
    )
    total = db.scalar(select(func.count()).select_from(Audit).where(predicate))
    rows = db.scalars(select(Audit).where(predicate).order_by(Audit.id.desc()).offset(offset).limit(limit))
    # Do not expose IP addresses/token IDs or unrelated global audit entries to scoped members.
    return {'items': [{'id': row.id, 'timestamp': row.timestamp.replace(tzinfo=timezone.utc), 'user_id': row.user_id,
                       'action': row.action, 'resource': row.resource, 'resource_id': row.resource_id,
                       'result': row.result, 'request_id': row.request_id} for row in rows],
            'total': total, 'limit': limit, 'offset': offset}
