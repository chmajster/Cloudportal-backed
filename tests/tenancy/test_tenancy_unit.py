"""Service tests also runnable without Redis using --confcutdir=tests/tenancy.

SQLite verifies semantics; the separate PostgreSQL test verifies the lock race.
"""
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, delete, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Audit, Permission, Role, RolePermission, Setting, Token, User, UserRole, now
from app.tenancy import service
from app.tenancy.authorization import Principal, authorize, identity
from app.tenancy.models import Tenant, TenantMembership, TenantRoleAssignment, TenantRoleGrant
from app.tenancy.permissions import DEFAULT_TENANT_ID, DELEGABLE_PERMISSIONS, TENANCY_PERMISSIONS
from app.tenancy.schemas import MemberCreate, MemberRoles, MemberUpdate, TenantCreate, TenantUpdate


def assert_error(code, callback, status=None):
    with pytest.raises(HTTPException) as error:
        callback()
    assert error.value.detail['code'] == code
    if status is not None:
        assert error.value.status_code == status


@pytest.fixture
def domain():
    engine = create_engine('sqlite://')
    @event.listens_for(engine, 'connect')
    def foreign_keys(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        perms = {name: Permission(name=name) for name in TENANCY_PERMISSIONS | {'users.delete', 'users.read'}}
        db.add_all(perms.values())
        db.add(Setting(key='governance', value={}))
        global_role = Role(name='Not a magic admin name', permissions=list(perms.values()))
        tenant_role = Role(name='Delegated manager', permissions=[perms[name] for name in DELEGABLE_PERMISSIONS])
        viewer_role = Role(name='Scoped reader', permissions=[perms['tenants.read']])
        db.add_all([global_role, tenant_role, viewer_role])
        users = [User(username=name, email=f'{name}@example.com', password_hash='unused',
                      roles=[global_role] if name == 'platform' else [])
                 for name in ('platform', 'alice', 'bob', 'eve')]
        db.add_all(users)
        db.flush()
        tokens = [Token(name=user.username, user_id=user.id, token_hash=uuid4().hex,
                        token_prefix='unit', kind='session', scopes=[])
                  for user in users]
        db.add_all(tokens)
        db.add(Tenant(id=DEFAULT_TENANT_ID, name='Default', slug='default', is_system=True))
        db.flush()
        db.commit()
        fixture = SimpleNamespace(db=db, perms=perms, users=users, tokens=tokens,
                                  admin=Principal.from_token(tokens[0]), alice=Principal.from_token(tokens[1]),
                                  bob=Principal.from_token(tokens[2]), eve=Principal.from_token(tokens[3]),
                                  manager_role=tenant_role, viewer_role=viewer_role, global_role=global_role)
        yield fixture
    engine.dispose()


def create_tenant(d, slug='engineering'):
    return service.tenant_create(d.db, d.admin, TenantCreate(name=slug.title(), slug=slug))


def member(d, tenant, principal, role=None):
    return service.member_create(d.db, d.admin, tenant['id'], MemberCreate(
        user_id=principal.user_id, role_ids=[(role or d.manager_role).id]))


def test_tenant_access_requires_explicit_global_permission_or_membership(domain):
    d = domain
    tenant = create_tenant(d)
    assert service.tenant_get(d.db, d.admin, tenant['id'])['id'] == tenant['id']
    assert_error('TENANT_NOT_FOUND', lambda: service.tenant_get(d.db, d.alice, tenant['id']), 404)
    assert_error('TENANT_NOT_FOUND', lambda: service.tenant_get(d.db, d.alice, str(uuid4())), 404)
    member(d, tenant, d.alice)
    assert service.tenant_get(d.db, d.alice, tenant['id'])['id'] == tenant['id']
    assert identity(d.db, d.alice).global_permissions == frozenset()
    assert d.db.scalar(select(func.count()).select_from(UserRole).where(UserRole.user_id == d.alice.user_id)) == 0


def test_filtering_is_before_pagination_and_count(domain):
    d = domain
    foreign = create_tenant(d, 'aaa-private')
    own = create_tenant(d, 'zzz-own')
    member(d, own, d.alice, d.viewer_role)
    result = service.tenant_list(d.db, d.alice, limit=1)
    assert result['total'] == 1
    assert [row['id'] for row in result['items']] == [own['id']]
    assert service.tenant_list(d.db, d.alice, limit=1, offset=1)['items'] == []
    assert foreign['id'] not in str(result)


def test_global_read_without_global_admin_is_still_membership_bounded(domain):
    d = domain
    tenant = create_tenant(d)
    d.db.add(UserRole(user_id=d.alice.user_id, role_id=d.viewer_role.id))
    d.db.flush()
    assert service.tenant_list(d.db, d.alice)['items'] == []
    assert_error('TENANT_NOT_FOUND', lambda: service.tenant_get(d.db, d.alice, tenant['id']))
    service.member_create(d.db, d.admin, tenant['id'], MemberCreate(user_id=d.alice.user_id))
    assert service.tenant_list(d.db, d.alice)['total'] == 1


def test_api_token_ceiling_applies_to_scoped_grants(domain):
    d = domain
    tenant = create_tenant(d)
    member(d, tenant, d.alice)
    d.tokens[1].kind = 'api'
    d.tokens[1].scopes = ['tenants.read']
    d.db.flush()
    assert service.tenant_permissions(d.db, d.alice, tenant['id'])['permissions'] == ['tenants.read']
    assert_error('SCOPED_PERMISSION_REQUIRED', lambda: service.member_list(d.db, d.alice, tenant['id']))
    d.tokens[1].scopes = []
    d.db.flush()
    assert service.tenant_list(d.db, d.alice)['items'] == []
    assert_error('TENANT_NOT_FOUND', lambda: service.tenant_get(d.db, d.alice, tenant['id']))


@pytest.mark.parametrize('change', ['revoke', 'expire', 'disable_user', 'lock_user', 'locked_until', 'refresh'])
def test_live_identity_revalidation(domain, change):
    d = domain
    tenant = create_tenant(d)
    member(d, tenant, d.alice)
    if change == 'revoke': d.tokens[1].revoked_at = now()
    elif change == 'expire': d.tokens[1].expires_at = now() - timedelta(seconds=1)
    elif change == 'disable_user': d.users[1].is_active = False
    elif change == 'lock_user': d.users[1].is_locked = True
    elif change == 'locked_until': d.users[1].locked_until = now() + timedelta(minutes=1)
    elif change == 'refresh': d.tokens[1].kind = 'refresh'
    d.db.flush()
    assert_error('AUTHENTICATION_REQUIRED', lambda: service.tenant_get(d.db, d.alice, tenant['id']), 401)


def test_revoked_membership_revokes_all_scoped_access(domain):
    d = domain
    tenant = create_tenant(d)
    row = member(d, tenant, d.alice)
    service.member_update(d.db, d.admin, tenant['id'], d.alice.user_id,
                          MemberUpdate(status='disabled', expected_version=row['version']))
    assert service.tenant_list(d.db, d.alice)['total'] == 0
    assert_error('TENANT_NOT_FOUND', lambda: service.tenant_get(d.db, d.alice, tenant['id']))


def test_scoped_admin_cannot_create_other_tenants_or_assign_global_role(domain):
    d = domain
    tenant = create_tenant(d)
    member(d, tenant, d.alice)
    assert_error('GLOBAL_PERMISSION_REQUIRED', lambda: service.tenant_create(
        d.db, d.alice, TenantCreate(name='Escape', slug='escape')))
    assert_error('DIRECTORY_PERMISSION_REQUIRED', lambda: service.member_create(
        d.db, d.alice, tenant['id'], MemberCreate(user_id=d.bob.user_id, role_ids=[d.global_role.id])))
    assert d.db.get(TenantMembership, (tenant['id'], d.bob.user_id)) is None
    existing = member(d, tenant, d.bob, d.viewer_role)
    assert_error('GRANT_EXCEEDS_SCOPE', lambda: service.member_roles(d.db, d.alice, tenant['id'], d.bob.user_id,
        MemberRoles(role_ids=[d.global_role.id], expected_version=existing['version'])))
    roles = service.assignable_roles(d.db, d.alice, tenant['id'])
    assert {r['id'] for r in roles['items']} == {d.manager_role.id, d.viewer_role.id}


def test_later_role_addition_does_not_expand_delegated_ceiling(domain):
    d = domain
    tenant = create_tenant(d)
    member(d, tenant, d.alice, d.viewer_role)
    d.viewer_role.permissions.append(d.perms['tenants.update'])
    d.db.flush()
    assert service.tenant_permissions(d.db, d.alice, tenant['id'])['permissions'] == ['tenants.read']
    row = d.db.get(TenantMembership, (tenant['id'], d.alice.user_id))
    service.member_roles(d.db, d.admin, tenant['id'], d.alice.user_id,
                         MemberRoles(role_ids=[d.viewer_role.id], expected_version=row.version))
    assert 'tenants.update' in service.tenant_permissions(d.db, d.alice, tenant['id'])['permissions']


def test_later_role_removal_immediately_revokes_scoped_permission(domain):
    d = domain
    tenant = create_tenant(d)
    member(d, tenant, d.alice, d.viewer_role)
    d.db.execute(delete(RolePermission).where(RolePermission.role_id == d.viewer_role.id))
    assert service.tenant_list(d.db, d.alice)['items'] == []
    assert_error('TENANT_NOT_FOUND', lambda: service.tenant_get(d.db, d.alice, tenant['id']))


def test_member_assignment_isolated_between_tenants(domain):
    d = domain
    first, second = create_tenant(d, 'first'), create_tenant(d, 'second')
    member(d, first, d.alice)
    member(d, second, d.bob)
    assert_error('TENANT_NOT_FOUND', lambda: service.member_list(d.db, d.alice, second['id']))
    assert_error('TENANT_NOT_FOUND', lambda: service.assignable_roles(d.db, d.alice, second['id']))
    assert_error('TENANT_NOT_FOUND', lambda: service.member_roles(d.db, d.alice, second['id'], d.bob.user_id,
                                                               MemberRoles(role_ids=[], expected_version=1)))


def test_suspended_tenant_read_only_for_members_and_recoverable_globally(domain):
    d = domain
    tenant = create_tenant(d)
    member(d, tenant, d.alice)
    data = TenantUpdate(name=tenant['name'], slug=tenant['slug'], status='suspended', expected_version=1)
    service.tenant_update(d.db, d.admin, tenant['id'], data)
    assert service.tenant_get(d.db, d.alice, tenant['id'])['status'] == 'suspended'
    assert_error('TENANT_INACTIVE', lambda: service.member_create(
        d.db, d.alice, tenant['id'], MemberCreate(user_id=d.bob.user_id)))
    service.tenant_update(d.db, d.admin, tenant['id'], data.model_copy(update={'status': 'active', 'expected_version': 2}))
    assert service.tenant_get(d.db, d.alice, tenant['id'])['status'] == 'active'


def test_disabled_tenant_is_not_disclosed_to_members(domain):
    d = domain
    tenant = create_tenant(d)
    member(d, tenant, d.alice)
    service.tenant_update(d.db, d.admin, tenant['id'], TenantUpdate(
        name=tenant['name'], slug=tenant['slug'], status='disabled', expected_version=1))
    assert_error('TENANT_NOT_FOUND', lambda: service.tenant_get(d.db, d.alice, tenant['id']))
    assert service.tenant_list(d.db, d.alice)['total'] == 0
    assert service.tenant_get(d.db, d.admin, tenant['id'])['status'] == 'disabled'


def test_system_default_is_immutable_and_cannot_be_deleted(domain):
    d = domain
    assert_error('SYSTEM_TENANT_PROTECTED', lambda: service.tenant_delete(d.db, d.admin, DEFAULT_TENANT_ID, 1))
    assert_error('SYSTEM_TENANT_PROTECTED', lambda: service.tenant_update(d.db, d.admin, DEFAULT_TENANT_ID,
        TenantUpdate(name='Default', slug='moved', expected_version=1)))
    result = service.tenant_update(d.db, d.admin, DEFAULT_TENANT_ID,
        TenantUpdate(name='Default', slug='default', description='Description is editable', expected_version=1))
    assert result['version'] == 2


def test_versions_prevent_lost_updates(domain):
    d = domain
    tenant = create_tenant(d)
    update = TenantUpdate(name='Updated', slug=tenant['slug'], expected_version=1)
    assert service.tenant_update(d.db, d.admin, tenant['id'], update)['version'] == 2
    assert_error('VERSION_CONFLICT', lambda: service.tenant_update(d.db, d.admin, tenant['id'], update))
    row = member(d, tenant, d.alice)
    changed = service.member_roles(d.db, d.admin, tenant['id'], d.alice.user_id,
                                  MemberRoles(role_ids=[d.viewer_role.id], expected_version=row['version']))
    assert changed['version'] == row['version'] + 1
    assert_error('VERSION_CONFLICT', lambda: service.member_update(d.db, d.admin, tenant['id'], d.alice.user_id,
                                                                 MemberUpdate(status='disabled', expected_version=1)))


def test_last_scoped_manager_protected_transaction_rolls_back(domain):
    d = domain
    tenant = create_tenant(d)
    member(d, tenant, d.alice)
    d.db.commit()
    with pytest.raises(HTTPException) as error:
        with d.db.begin():
            service.member_delete(d.db, d.alice, tenant['id'], d.alice.user_id, 1)
    assert error.value.detail['code'] == 'TENANT_MANAGER_REQUIRED'
    assert service.tenant_get(d.db, d.alice, tenant['id'])['id'] == tenant['id']
    assert d.db.scalar(select(func.count()).select_from(TenantRoleGrant)) > 0


def test_membership_removal_cascades_grants_and_tombstone_preserves_identity(domain):
    d = domain
    tenant = create_tenant(d)
    member(d, tenant, d.alice)
    assert_error('TENANT_NOT_EMPTY', lambda: service.tenant_delete(d.db, d.admin, tenant['id'], 1))
    service.member_delete(d.db, d.admin, tenant['id'], d.alice.user_id, 1)
    assert d.db.scalar(select(func.count()).select_from(TenantRoleGrant)) == 0
    assert service.tenant_delete(d.db, d.admin, tenant['id'], 1) == {'deleted': True}
    assert d.db.get(Tenant, tenant['id']).deleted_at is not None
    assert_error('TENANT_NOT_FOUND', lambda: service.tenant_get(d.db, d.admin, tenant['id']))


def test_foreign_scope_audit_is_not_leaked(domain):
    d = domain
    own, other = create_tenant(d, 'own'), create_tenant(d, 'other')
    member(d, own, d.alice)
    for resource, resource_id in [('tenants', own['id']), ('tenant_memberships', own['id'] + ':123'),
                                 ('tenants', other['id']), ('tenant_memberships', other['id'] + ':123')]:
        d.db.add(Audit(action='tenant.changed', resource=resource, resource_id=resource_id,
                       user_id=d.admin.user_id, ip='sensitive-address', request_id=str(uuid4())))
    d.db.flush()
    history = service.audit_list(d.db, d.alice, own['id'])
    assert history['total'] == 2
    assert 'sensitive-address' not in str(history)
    assert other['id'] not in str(history)
    assert_error('TENANT_NOT_FOUND', lambda: service.audit_list(d.db, d.alice, other['id']))


def test_database_rejects_orphaned_assignment(domain):
    d = domain
    tenant = create_tenant(d)
    with pytest.raises(IntegrityError):
        with d.db.begin_nested():
            d.db.add(TenantRoleAssignment(tenant_id=tenant['id'], user_id=d.alice.user_id,
                                          role_id=d.viewer_role.id))
            d.db.flush()


@pytest.mark.parametrize('values', [
    {'name': ' '}, {'slug': '../escape'}, {'unknown': 'field'},
    {'metadata': {'__proto__': {}}}, {'metadata': {'key': 'x' * 20000}},
    {'metadata': {'key': float('nan')}}, {'labels': {'constructor': 'x'}},
])
def test_input_bounds(values):
    with pytest.raises(ValidationError):
        TenantCreate(**({'name': 'Name', 'slug': 'name'} | values))


@pytest.mark.parametrize('role_ids', [[True], [-1], [1, 1], ['1'], list(range(1, 35))])
def test_role_ids_are_strict_and_bounded(role_ids):
    with pytest.raises(ValidationError):
        MemberCreate(user_id=1, role_ids=role_ids)
    with pytest.raises(ValidationError):
        MemberRoles(role_ids=role_ids, expected_version=1)
