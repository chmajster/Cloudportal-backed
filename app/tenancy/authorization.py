"""Database-backed scoped permission evaluation with API token ceilings."""
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import and_, exists, select

from app.models import Permission, RolePermission, Token, User, UserRole, now
from app.rbac.locking import governance_lock
from app.tenancy.models import Tenant, TenantMembership, TenantRoleAssignment, TenantRoleGrant
from app.tenancy.permissions import DELEGABLE_PERMISSIONS


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: int
    token_id: int

    @classmethod
    def from_token(cls, token):
        return cls(user_id=token.user_id, token_id=token.id)


@dataclass(frozen=True, slots=True)
class Identity:
    user_id: int
    token_id: int
    global_permissions: frozenset[str]
    token_ceiling: frozenset[str] | None

    @property
    def platform_admin(self):
        return 'tenants.admin' in self.global_permissions

    def restrict(self, permissions):
        permissions = frozenset(permissions)
        return permissions if self.token_ceiling is None else permissions & self.token_ceiling


@dataclass(frozen=True, slots=True)
class TenantAccess:
    tenant: Tenant
    identity: Identity
    permissions: frozenset[str]


def fail(status, code, message):
    raise HTTPException(status, {'code': code, 'message': message})


def identity(db, principal: Principal) -> Identity:
    # Read current rows rather than trusting a role name, the HTTP body, a
    # previously loaded ORM relationship, or permissions cached before a lock.
    row = db.execute(select(
        Token.kind, Token.scopes, Token.expires_at, Token.revoked_at,
        User.is_active, User.is_locked, User.locked_until, User.must_change_password,
    ).join(User, User.id == Token.user_id).where(
        Token.id == principal.token_id, Token.user_id == principal.user_id,
    )).one_or_none()
    instant = now()
    if (row is None or row.kind not in {'api', 'session'} or row.revoked_at is not None
            or (row.expires_at is not None and row.expires_at <= instant)
            or not row.is_active or row.is_locked
            or (row.locked_until is not None and row.locked_until > instant)):
        fail(401, 'AUTHENTICATION_REQUIRED', 'Authentication is no longer valid')
    if row.kind == 'session' and row.must_change_password:
        fail(403, 'PASSWORD_CHANGE_REQUIRED', 'Password change required')
    ceiling = frozenset(row.scopes or []) if row.kind == 'api' else None
    permissions = frozenset(db.scalars(select(Permission.name)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(UserRole, UserRole.role_id == RolePermission.role_id)
        .where(UserRole.user_id == principal.user_id)))
    if ceiling is not None:
        permissions &= ceiling
    return Identity(principal.user_id, principal.token_id, permissions, ceiling)


def lock_authorization(db):
    # Same lock and lock order as existing global role/user mutations. Obtain
    # it BEFORE re-evaluating authorization and before locking a tenant/member.
    if governance_lock(db) is None:
        fail(503, 'GOVERNANCE_NOT_INITIALIZED', 'Initialize the platform before changing tenancy')


def require_global(db, principal, permission):
    actor = identity(db, principal)
    if not actor.platform_admin or permission not in actor.global_permissions:
        fail(403, 'GLOBAL_PERMISSION_REQUIRED', 'A global tenancy permission is required')
    return actor


def live_grants(tenant_id, user_id):
    return (select(Permission.name)
        .select_from(TenantRoleAssignment)
        .join(TenantRoleGrant, TenantRoleGrant.assignment_id == TenantRoleAssignment.id)
        .join(RolePermission, and_(RolePermission.role_id == TenantRoleAssignment.role_id,
                                   RolePermission.permission_id == TenantRoleGrant.permission_id))
        .join(Permission, Permission.id == TenantRoleGrant.permission_id)
        .where(TenantRoleAssignment.tenant_id == tenant_id,
               TenantRoleAssignment.user_id == user_id,
               Permission.name.in_(DELEGABLE_PERMISSIONS)))


def effective_tenant_permissions(db, actor: Identity, tenant_id):
    member = db.scalar(select(TenantMembership.user_id).where(
        TenantMembership.tenant_id == tenant_id,
        TenantMembership.user_id == actor.user_id,
        TenantMembership.status == 'active',
    ))
    if member is None and not actor.platform_admin:
        return frozenset()
    scoped = frozenset(db.scalars(live_grants(tenant_id, actor.user_id))) if member is not None else frozenset()
    return actor.restrict(actor.global_permissions | scoped)


def authorize(db, principal, tenant_id, permission, *, write=False, lock=False):
    if lock:
        lock_authorization(db)
    actor = identity(db, principal)
    query = select(Tenant).where(Tenant.id == str(tenant_id), Tenant.deleted_at.is_(None))
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    tenant = db.scalar(query)
    if tenant is None or (tenant.status == 'disabled' and not actor.platform_admin):
        fail(404, 'TENANT_NOT_FOUND', 'Tenant not found')
    permissions = effective_tenant_permissions(db, actor, tenant.id)
    # A foreign UUID yields the same response as an absent one. An authenticated
    # member with a known tenant but insufficient powers gets a scoped 403.
    visible = actor.platform_admin or bool(permissions)
    if not visible:
        fail(404, 'TENANT_NOT_FOUND', 'Tenant not found')
    if permission not in permissions:
        fail(403, 'SCOPED_PERMISSION_REQUIRED', 'The operation is not permitted in this tenant')
    if write and tenant.status != 'active' and not actor.platform_admin:
        fail(409, 'TENANT_INACTIVE', 'This tenant is not accepting changes')
    return TenantAccess(tenant, actor, permissions)


def visible_tenants(actor: Identity, permission='tenants.read'):
    base = Tenant.deleted_at.is_(None)
    if actor.platform_admin and permission in actor.global_permissions:
        return base
    if actor.token_ceiling is not None and permission not in actor.token_ceiling:
        # SQL false, not a Python post-pagination filter.
        return and_(base, False)
    membership = select(TenantMembership.user_id).where(
        TenantMembership.tenant_id == Tenant.id,
        TenantMembership.user_id == actor.user_id,
        TenantMembership.status == 'active',
    )
    if permission not in actor.global_permissions:
        permission_exists = live_grants(Tenant.id, actor.user_id).where(Permission.name == permission)
        membership = membership.where(exists(permission_exists.correlate(Tenant)))
    return and_(base, Tenant.status != 'disabled', exists(membership.correlate(Tenant)))


def assert_version(row, expected_version):
    if row.version != expected_version:
        fail(409, 'VERSION_CONFLICT', 'The object changed; reload it before retrying')
