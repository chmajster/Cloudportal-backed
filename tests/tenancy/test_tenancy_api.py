"""Real authentication, HTTP, PostgreSQL-compatible migrations, audit and events."""
from uuid import uuid4

from sqlalchemy import func, select

from app.database import session
from app.models import Audit, EventRecord, Role, Token, User
from app.tenancy.models import TenantMembership, TenantRoleGrant
from app.tenancy.permissions import DEFAULT_TENANT_ID
from conftest import new_user


def tenant(client, headers, slug='engineering'):
    response = client.post('/api/v1/tenants', headers=headers, json={'name': slug.title(), 'slug': slug})
    assert response.status_code == 201, response.text
    return response.json()


def tenant_role(headers, client, name='Tenant Administrator'):
    result = client.get('/api/v1/roles?limit=200', headers=headers)
    assert result.status_code == 200, result.text
    return next(row['id'] for row in result.json()['items'] if row['name'] == name)


def add_member(client, headers, tenant_id, user_id, role_id):
    response = client.post(f'/api/v1/tenants/{tenant_id}/members', headers=headers,
                           json={'user_id': user_id, 'role_ids': [role_id]})
    assert response.status_code == 201, response.text
    return response.json()


def test_tenant_crud_scope_tokens_membership_and_events(system):
    client, headers, _ = system
    own, foreign = tenant(client, headers), tenant(client, headers, 'private')
    alice, alice_headers = new_user(client, headers, 'tenant-alice')
    bob, _ = new_user(client, headers, 'tenant-bob')
    role_id = tenant_role(headers, client)
    alice_member = add_member(client, headers, own['id'], alice['id'], role_id)
    add_member(client, headers, foreign['id'], bob['id'], role_id)

    listed = client.get('/api/v1/tenants?limit=1', headers=alice_headers)
    assert listed.status_code == 200, listed.text
    assert listed.json()['total'] == 1 and listed.json()['items'][0]['id'] == own['id']
    assert listed.json()['items'][0]['created_at'].endswith('Z')
    assert client.get('/api/v1/tenants?limit=201', headers=alice_headers).status_code == 422
    assert client.get('/api/v1/tenants?offset=-1', headers=alice_headers).status_code == 422
    for suffix in ('', '/members', '/permissions', '/assignable-roles', '/audit'):
        denied = client.get(f"/api/v1/tenants/{foreign['id']}{suffix}", headers=alice_headers)
        missing = client.get(f'/api/v1/tenants/{uuid4()}{suffix}', headers=alice_headers)
        assert denied.status_code == 404 and denied.json() == missing.json()
        assert 'private' not in denied.text
    assert client.get('/api/v1/providers', headers=alice_headers).status_code == 403
    assert client.post('/api/v1/tenants', headers=alice_headers,
                       json={'name': 'Escape', 'slug': 'escape'}).status_code == 403

    scoped = client.get(f"/api/v1/tenants/{own['id']}/permissions", headers=alice_headers).json()
    assert scoped['scope'] == 'TENANT' and not scoped['global_administration']
    assert 'tenants.members.manage' in scoped['permissions']
    assert 'tenants.admin' not in scoped['permissions']
    assert client.get('/api/v1/auth/me', headers=alice_headers).json()['permissions'] == []

    global_role = tenant_role(headers, client, 'Administrator')
    escalation = client.post(f"/api/v1/tenants/{own['id']}/members", headers=alice_headers,
                             json={'user_id': bob['id'], 'role_ids': [global_role]})
    assert escalation.status_code == 403
    with session() as db:
        assert db.get(TenantMembership, (own['id'], bob['id'])) is None

    update = {key: own[key] for key in ('name', 'slug', 'description', 'status', 'labels', 'metadata')}
    update |= {'name': 'Engineering updated', 'expected_version': own['version']}
    saved = client.put(f"/api/v1/tenants/{own['id']}", headers=alice_headers, json=update)
    assert saved.status_code == 200, saved.text
    assert client.put(f"/api/v1/tenants/{own['id']}", headers=alice_headers, json=update).status_code == 409
    removed = client.delete(f"/api/v1/tenants/{own['id']}/members/{alice['id']}?expected_version=1", headers=alice_headers)
    assert removed.status_code == 409 and removed.json()['detail']['code'] == 'TENANT_MANAGER_REQUIRED'
    assert client.get(f"/api/v1/tenants/{own['id']}", headers=alice_headers).status_code == 200

    history = client.get(f"/api/v1/tenants/{own['id']}/audit", headers=alice_headers)
    assert history.status_code == 200, history.text
    assert {'tenant.created', 'tenant.updated', 'tenant.member.added'} <= {row['action'] for row in history.json()['items']}
    assert foreign['id'] not in history.text
    assert all('ip' not in item and 'token_id' not in item for item in history.json()['items'])
    with session() as db:
        assert db.scalar(select(func.count()).select_from(EventRecord).where(EventRecord.type == 'tenant.created')) == 2
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action == 'tenant.updated')) == 1

    disabled = client.put(f"/api/v1/tenants/{own['id']}/members/{alice['id']}", headers=headers,
                          json={'status': 'disabled', 'expected_version': alice_member['version']})
    assert disabled.status_code == 200, disabled.text
    assert client.get('/api/v1/tenants', headers=alice_headers).json()['items'] == []
    assert client.get(f"/api/v1/tenants/{own['id']}", headers=alice_headers).status_code == 404


def test_idempotency_replay_reauthorizes_and_emits_one_event(system):
    client, headers, _ = system
    key = str(uuid4())
    values = {'name': 'Idempotent', 'slug': 'idempotent'}
    first = client.post('/api/v1/tenants', headers=headers | {'Idempotency-Key': key}, json=values)
    again = client.post('/api/v1/tenants', headers=headers | {'Idempotency-Key': key}, json=values)
    assert first.status_code == again.status_code == 201
    assert first.json() == again.json()
    with session() as db:
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action == 'tenant.created')) == 1
        # Preserve a live token, but withdraw the explicit global crossing permission.
        token = db.scalar(select(Token).where(Token.kind == 'api'))
        token.scopes = [scope for scope in token.scopes if scope != 'tenants.admin']
        db.commit()
    denied = client.post('/api/v1/tenants', headers=headers | {'Idempotency-Key': key}, json=values)
    assert denied.status_code == 403


def test_scoped_api_token_ceiling_and_live_role_ceiling(system):
    client, headers, _ = system
    own = tenant(client, headers)
    alice, alice_headers = new_user(client, headers, 'scoped-token-user')
    viewer_id = tenant_role(headers, client, 'Tenant Viewer')
    membership = add_member(client, headers, own['id'], alice['id'], viewer_id)
    from app.security.core import issue_token
    with session() as db:
        _, plain = issue_token(db, db.get(User, alice['id']), 'Tenant read only', ['tenants.read'])
        db.commit()
    limited = {'Authorization': 'Bearer ' + plain}
    assert client.get(f"/api/v1/tenants/{own['id']}", headers=limited).status_code == 200
    assert client.get(f"/api/v1/tenants/{own['id']}/members", headers=limited).status_code == 403
    role = client.get(f'/api/v1/roles/{viewer_id}', headers=headers).json()
    role['permissions'].append('tenants.update')
    changed = client.put(f'/api/v1/roles/{viewer_id}', headers=headers,
                         json={'name': role['name'], 'permissions': role['permissions']})
    assert changed.status_code == 200, changed.text
    perms = client.get(f"/api/v1/tenants/{own['id']}/permissions", headers=alice_headers).json()['permissions']
    assert 'tenants.update' not in perms
    updated = client.put(f"/api/v1/tenants/{own['id']}/members/{alice['id']}/roles", headers=headers,
                         json={'role_ids': [viewer_id], 'expected_version': membership['version']})
    assert updated.status_code == 200, updated.text
    assert 'tenants.update' in client.get(f"/api/v1/tenants/{own['id']}/permissions", headers=alice_headers).json()['permissions']
    assert client.get(f"/api/v1/tenants/{own['id']}/permissions", headers=limited).json()['permissions'] == ['tenants.read']


def test_default_and_openapi_contracts(system):
    client, headers, _ = system
    result = client.get(f'/api/v1/tenants/{DEFAULT_TENANT_ID}', headers=headers)
    assert result.status_code == 200 and result.json()['is_system']
    assert client.delete(f'/api/v1/tenants/{DEFAULT_TENANT_ID}?expected_version=1', headers=headers).status_code == 409
    assert client.get('/api/v1/tenants').status_code == 401
    schema = client.get('/openapi.json').json()
    assert '/api/v1/tenants/{tenant_id}/members/{user_id}/roles' in schema['paths']
    assert schema['components']['schemas']['TenantCreate']['additionalProperties'] is False
    with session() as db:
        infrastructure = db.scalar(select(Role).where(Role.name == 'Infrastructure Administrator'))
        assert not any(permission.name.startswith('tenants.') for permission in infrastructure.permissions)
