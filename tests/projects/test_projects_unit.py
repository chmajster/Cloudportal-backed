"""Semantic project tests, independently runnable without Redis or provider access."""
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from uuid import uuid4
from datetime import timedelta
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event, select, delete, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.database import Base
from app.models import Permission, Role, RolePermission, Setting, Token, User, UserRole, now
from app.tenancy import service as tenants
from app.tenancy.authorization import Principal
from app.tenancy.permissions import TENANCY_PERMISSIONS, DEFAULT_TENANT_ID
from app.tenancy.models import Tenant, TenantMembership
from app.tenancy.schemas import MemberCreate, MemberRoles, MemberUpdate, TenantCreate
from app.projects import service
from app.projects.authorization import authorize, resolve_context
from app.projects.models import Project, ProjectMembership, ProjectRoleGrant
from app.projects.permissions import PROJECT_DELEGABLE_PERMISSIONS, DEFAULT_PROJECT_ID
from app.projects.schemas import ProjectCreate, ProjectUpdate


def error(code, call, status=None):
    with pytest.raises(HTTPException) as exc:
        call()
    assert exc.value.detail['code'] == code
    if status:
        assert exc.value.status_code == status


@pytest.fixture
def domain():
    engine = create_engine('sqlite://')
    @event.listens_for(engine, 'connect')
    def foreign_keys(dbapi, _):
        dbapi.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        permissions = {name: Permission(name=name) for name in TENANCY_PERMISSIONS | {'users.read'}}
        admin_role = Role(name='arbitrary global label', permissions=list(permissions.values()))
        project_role = Role(name='arbitrary project label', permissions=[permissions[n] for n in PROJECT_DELEGABLE_PERMISSIONS])
        viewer_role = Role(name='reader', permissions=[permissions['projects.read']])
        inherited_role = Role(name='inherited', permissions=[permissions['projects.read'], permissions['projects.create']])
        db.add_all([admin_role, project_role, viewer_role, inherited_role, Setting(key='governance', value={})])
        users = [User(username=n, email=n+'@example.com', password_hash='unused', roles=[admin_role] if n == 'admin' else [])
                 for n in ('admin', 'alice', 'bob', 'eve')]
        db.add_all(users); db.flush()
        tokens = [Token(name=u.username, user_id=u.id, token_hash=uuid4().hex, token_prefix='test', kind='session', scopes=[]) for u in users]
        db.add_all(tokens)
        db.add(Tenant(id=DEFAULT_TENANT_ID, name='Default', slug='default', is_system=True)); db.flush()
        db.add(Project(id=DEFAULT_PROJECT_ID, tenant_id=DEFAULT_TENANT_ID, name='Default', slug='default', is_system=True))
        db.flush(); db.commit()
        d = SimpleNamespace(db=db, users=users, tokens=tokens, permissions=permissions,
            admin=Principal.from_token(tokens[0]), alice=Principal.from_token(tokens[1]), bob=Principal.from_token(tokens[2]),
            eve=Principal.from_token(tokens[3]), admin_role=admin_role, manager_role=project_role,
            viewer_role=viewer_role, inherited_role=inherited_role)
        yield d
    engine.dispose()


def setup(d, tenant_slug='engineering', project_slug='production'):
    t = tenants.tenant_create(d.db, d.admin, TenantCreate(name=tenant_slug, slug=tenant_slug))
    p = service.project_create(d.db, d.admin, ProjectCreate(tenant_id=t['id'], name=project_slug, slug=project_slug))
    return t, p


def tenant_member(d, t, principal, role=None):
    return tenants.member_create(d.db, d.admin, t['id'], MemberCreate(user_id=principal.user_id, role_ids=[role.id] if role else []))


def project_member(d, t, p, principal, role=None):
    if d.db.get(TenantMembership, (t['id'], principal.user_id)) is None:
        tenant_member(d, t, principal)
    return service.member_create(d.db, d.admin, p['id'], MemberCreate(user_id=principal.user_id, role_ids=[(role or d.manager_role).id]))


def test_sql_isolation_pagination_counts_and_parent_scope(domain):
    d = domain
    t, p = setup(d)
    _, foreign = setup(d, 'secret', 'aaa')
    own_other = service.project_create(d.db, d.admin, ProjectCreate(tenant_id=t['id'], name='AAA', slug='aaa'))
    project_member(d, t, p, d.alice, d.viewer_role)
    page = service.project_list(d.db, d.alice, limit=1)
    assert page['total'] == 1 and page['items'][0]['id'] == p['id']
    assert service.project_list(d.db, d.alice, offset=1)['items'] == []
    for target in (foreign['id'], own_other['id'], str(uuid4())):
        error('PROJECT_NOT_FOUND', lambda: service.project_get(d.db, d.alice, target), 404)
    assert service.project_list(d.db, d.alice, tenant_id=foreign['tenant_id'])['total'] == 0


def test_tenant_membership_is_not_a_project_grant(domain):
    d = domain; t, p = setup(d)
    tenant_member(d, t, d.alice)
    error('PROJECT_NOT_FOUND', lambda: service.project_get(d.db, d.alice, p['id']))
    d.db.add(UserRole(user_id=d.alice.user_id, role_id=d.viewer_role.id)); d.db.flush()
    error('PROJECT_NOT_FOUND', lambda: service.project_get(d.db, d.alice, p['id']))
    service.member_create(d.db, d.admin, p['id'], MemberCreate(user_id=d.alice.user_id))
    assert service.project_get(d.db, d.alice, p['id'])['id'] == p['id']


def test_tenant_scope_inheritance_and_creation_never_grants_global_permissions(domain):
    d = domain; t, p = setup(d)
    tenant_member(d, t, d.alice, d.inherited_role)
    assert service.project_permissions(d.db, d.alice, p['id'])['inherited_permissions'] == ['projects.create', 'projects.read']
    new = service.project_create(d.db, d.alice, ProjectCreate(tenant_id=t['id'], name='Dev', slug='dev'))
    assert service.project_get(d.db, d.alice, new['id'])['id'] == new['id']
    assert d.db.scalar(select(func.count()).select_from(UserRole).where(UserRole.user_id == d.alice.user_id)) == 0
    error('SCOPED_PERMISSION_REQUIRED', lambda: service.member_list(d.db, d.alice, new['id']))
    assert {row['id'] for row in service.project_list(d.db, d.alice)['items']} == {new['id'], p['id']}


@pytest.mark.parametrize('change', ['project_member', 'tenant_member', 'tenant_disabled', 'project_disabled', 'tenant_deleted', 'project_deleted'])
def test_parent_and_membership_revocation_applies_live(domain, change):
    d = domain; t, p = setup(d); project_member(d, t, p, d.alice)
    if change == 'project_member': d.db.get(ProjectMembership, (p['id'], d.alice.user_id)).status = 'disabled'
    if change == 'tenant_member': d.db.get(TenantMembership, (t['id'], d.alice.user_id)).status = 'disabled'
    if change == 'tenant_disabled': d.db.get(Tenant, t['id']).status = 'disabled'
    if change == 'project_disabled': d.db.get(Project, p['id']).status = 'disabled'
    if change == 'tenant_deleted': d.db.get(Tenant, t['id']).deleted_at = now()
    if change == 'project_deleted': d.db.get(Project, p['id']).deleted_at = now()
    d.db.flush()
    assert service.project_list(d.db, d.alice)['total'] == 0
    error('PROJECT_NOT_FOUND', lambda: service.project_get(d.db, d.alice, p['id']))


def test_suspended_parent_read_only_and_global_recovery(domain):
    d = domain; t, p = setup(d); project_member(d, t, p, d.alice)
    d.db.get(Tenant, t['id']).status = 'suspended'; d.db.flush()
    assert service.project_get(d.db, d.alice, p['id'])
    error('PROJECT_INACTIVE', lambda: authorize(d.db, d.alice, p['id'], 'projects.update', write=True))
    assert authorize(d.db, d.admin, p['id'], 'projects.update', write=True).global_administration


@pytest.mark.parametrize('kind', ['api_empty', 'api_read', 'revoke', 'expired', 'locked', 'inactive'])
def test_live_token_ceiling_and_account_status(domain, kind):
    d = domain; t, p = setup(d); project_member(d, t, p, d.alice)
    token = d.tokens[1]
    if kind.startswith('api_'):
        token.kind = 'api'; token.scopes = ['projects.read'] if kind == 'api_read' else []
    if kind == 'revoke': token.revoked_at = now()
    if kind == 'expired': token.expires_at = now() - timedelta(seconds=1)
    if kind == 'locked': d.users[1].is_locked = True
    if kind == 'inactive': d.users[1].is_active = False
    d.db.flush()
    if kind == 'api_read':
        assert service.project_permissions(d.db, d.alice, p['id'])['permissions'] == ['projects.read']
        error('SCOPED_PERMISSION_REQUIRED', lambda: service.member_list(d.db, d.alice, p['id']))
    elif kind == 'api_empty':
        assert service.project_list(d.db, d.alice)['total'] == 0
    else:
        error('AUTHENTICATION_REQUIRED', lambda: service.project_list(d.db, d.alice), 401)


def test_role_expansion_cannot_expand_assignment_but_removal_revokes(domain):
    d = domain; t, p = setup(d); project_member(d, t, p, d.alice, d.viewer_role)
    d.db.add(RolePermission(role_id=d.viewer_role.id, permission_id=d.permissions['projects.update'].id)); d.db.flush()
    assert service.project_permissions(d.db, d.alice, p['id'])['permissions'] == ['projects.read']
    d.db.execute(delete(RolePermission).where(RolePermission.role_id == d.viewer_role.id)); d.db.flush()
    assert service.project_list(d.db, d.alice)['total'] == 0


def test_delegation_cannot_grant_tenant_or_global_admin_and_directory_is_scoped(domain):
    d = domain; t, p = setup(d); project_member(d, t, p, d.alice)
    tenant_member(d, t, d.bob)
    for role in (d.admin_role, d.inherited_role):
        error('GRANT_EXCEEDS_SCOPE', lambda: service.member_create(d.db, d.alice, p['id'], MemberCreate(user_id=d.bob.user_id, role_ids=[role.id])))
    error('MEMBER_NOT_FOUND', lambda: service.member_create(d.db, d.alice, p['id'], MemberCreate(user_id=d.eve.user_id)), 404)
    eligible = service.eligible_members(d.db, d.alice, p['id'])
    assert {row['user_id'] for row in eligible['items']} == {d.alice.user_id, d.bob.user_id}
    assert 'eve' not in str(eligible)
    service.member_create(d.db, d.alice, p['id'], MemberCreate(user_id=d.bob.user_id, role_ids=[d.viewer_role.id]))
    assert service.project_get(d.db, d.bob, p['id'])


def test_last_human_manager_protection_and_cascade(domain):
    d = domain; t, p = setup(d); m = project_member(d, t, p, d.alice); d.db.commit()
    error('PROJECT_MANAGER_REQUIRED', lambda: service.member_delete(d.db, d.alice, p['id'], d.alice.user_id, m['version']))
    d.db.rollback()
    assert service.project_get(d.db, d.alice, p['id'])
    service.member_delete(d.db, d.admin, p['id'], d.alice.user_id, m['version'])
    assert d.db.scalar(select(func.count()).select_from(ProjectRoleGrant)) == 0


def test_optimistic_revision_and_default_protection(domain):
    d = domain; t, p = setup(d); project_member(d, t, p, d.alice)
    values = {k: p[k] for k in ('name', 'slug', 'description', 'status', 'metadata', 'labels', 'default_environment')}
    update = ProjectUpdate(**values, expected_version=p['version'])
    assert service.project_update(d.db, d.alice, p['id'], update)['version'] == 2
    error('VERSION_CONFLICT', lambda: service.project_update(d.db, d.alice, p['id'], update))
    error('PROJECT_NOT_EMPTY', lambda: service.project_delete(d.db, d.admin, p['id'], 2))
    error('SYSTEM_PROJECT_PROTECTED', lambda: service.project_delete(d.db, d.admin, DEFAULT_PROJECT_ID, 1))
    error('SYSTEM_PROJECT_PROTECTED', lambda: service.project_update(d.db, d.admin, DEFAULT_PROJECT_ID,
        ProjectUpdate(name='renamed', slug='default', expected_version=1)))


def test_context_is_immutable_mismatch_rejected_no_grant_from_uuid(domain):
    d = domain; t, p = setup(d); project_member(d, t, p, d.alice)
    ctx = resolve_context(d.db, d.alice, p['id'], tenant_id=t['id'])
    assert ctx.project_id == p['id'] and ctx.actor_id == d.alice.user_id
    with pytest.raises(FrozenInstanceError): ctx.project_id = 'another'
    error('PROJECT_NOT_FOUND', lambda: resolve_context(d.db, d.alice, p['id'], tenant_id=str(uuid4())))
    error('PROJECT_NOT_FOUND', lambda: resolve_context(d.db, d.eve, p['id'], tenant_id=t['id']))


def test_foreign_tenant_membership_rejected_by_database(domain):
    d = domain; t, p = setup(d); other, _ = setup(d, 'other')
    tenant_member(d, other, d.eve); d.db.commit()
    d.db.add(ProjectMembership(project_id=p['id'], tenant_id=other['id'], user_id=d.eve.user_id))
    with pytest.raises(IntegrityError): d.db.flush()


def test_parent_delete_checks_nonempty_projects(domain):
    d = domain; t, p = setup(d)
    error('TENANT_NOT_EMPTY', lambda: tenants.tenant_delete(d.db, d.admin, t['id'], t['version']))
    service.project_delete(d.db, d.admin, p['id'], p['version'])
    assert tenants.tenant_delete(d.db, d.admin, t['id'], t['version']) == {'deleted': True}
