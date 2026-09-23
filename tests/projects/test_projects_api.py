"""Real HTTP/authentication, DB persistence, audit and event transactions."""
from uuid import uuid4
from sqlalchemy import select, func
from conftest import new_user
from app.database import session
from app.models import Audit, EventRecord, Token
from app.projects.models import ProjectMembership


def create_tenant(client, headers, slug):
    response = client.post('/api/v1/tenants', headers=headers, json={'name': slug, 'slug': slug})
    assert response.status_code == 201, response.text
    return response.json()


def create_project(client, headers, tenant_id, slug):
    response = client.post('/api/v1/projects', headers=headers, json={'tenant_id': tenant_id, 'name': slug, 'slug': slug})
    assert response.status_code == 201, response.text
    return response.json()


def add_member(client, headers, tenant_id, project_id, user_id):
    roles = client.get('/api/v1/roles?limit=200', headers=headers).json()['items']
    role_id = next(r['id'] for r in roles if r['name'] == 'Project Administrator')
    response = client.post(f'/api/v1/tenants/{tenant_id}/members', headers=headers, json={'user_id': user_id})
    assert response.status_code == 201, response.text
    response = client.post(f'/api/v1/projects/{project_id}/members', headers=headers, json={'user_id': user_id, 'role_ids': [role_id]})
    assert response.status_code == 201, response.text
    return response.json()


def test_project_http_isolation_membership_and_context(system):
    client, headers, _ = system
    own = create_tenant(client, headers, 'engineering'); foreign = create_tenant(client, headers, 'foreign')
    p = create_project(client, headers, own['id'], 'production')
    q = create_project(client, headers, own['id'], 'sibling')
    f = create_project(client, headers, foreign['id'], 'private')
    alice, ah = new_user(client, headers, 'project-alice')
    bob, _ = new_user(client, headers, 'project-bob')
    add_member(client, headers, own['id'], p['id'], alice['id'])
    add_member(client, headers, foreign['id'], f['id'], bob['id'])
    listing = client.get('/api/v1/projects?limit=1', headers=ah)
    assert listing.status_code == 200, listing.text
    assert listing.json()['total'] == 1 and listing.json()['items'][0]['id'] == p['id']
    assert client.get('/api/v1/projects?limit=201', headers=ah).status_code == 422
    for target in (q['id'], f['id']):
        for suffix in ('', '/members', '/permissions', '/audit', '/eligible-members', '/assignable-roles'):
            result = client.get(f'/api/v1/projects/{target}{suffix}', headers=ah)
            missing = client.get(f'/api/v1/projects/{uuid4()}{suffix}', headers=ah)
            assert result.status_code == 404 and result.json() == missing.json()
    mismatch = client.post('/api/v1/project-context/resolve', headers=ah, json={'tenant_id': foreign['id'], 'project_id': p['id']})
    assert mismatch.status_code == 404
    valid = client.post('/api/v1/project-context/resolve', headers=ah, json={'tenant_id': own['id'], 'project_id': p['id']})
    assert valid.status_code == 200 and valid.json()['actor_id'] == alice['id'], valid.text
    assert client.get('/api/v1/auth/me', headers=ah).json()['permissions'] == []
    assert client.get('/api/v1/providers', headers=ah).status_code == 403
    eligible = client.get(f"/api/v1/projects/{p['id']}/eligible-members", headers=ah)
    assert eligible.status_code == 200 and 'project-bob' not in eligible.text
    for uid in (bob['id'], 987654321):
        response = client.post(f"/api/v1/projects/{p['id']}/members", headers=ah, json={'user_id': uid})
        assert response.status_code == 404 and response.json()['detail']['code'] == 'MEMBER_NOT_FOUND'
    with session() as db:
        assert db.get(ProjectMembership, (p['id'], bob['id'])) is None
    history = client.get(f"/api/v1/projects/{p['id']}/audit", headers=ah).json()
    assert {'project.created', 'project.member.added'} <= {row['action'] for row in history['items']}
    assert f['id'] not in str(history)
    assert all('ip' not in row and 'token_id' not in row for row in history['items'])
    with session() as db:
        assert db.scalar(select(func.count()).select_from(EventRecord).where(EventRecord.type == 'project.created')) == 3


def test_project_idempotency_replay_reauthorizes_and_never_duplicates_events(system):
    client, headers, _ = system
    t = create_tenant(client, headers, 'idempotent')
    values = {'tenant_id': t['id'], 'name': 'repeat', 'slug': 'repeat'}
    replay_headers = headers | {'Idempotency-Key': str(uuid4())}
    first = client.post('/api/v1/projects', headers=replay_headers, json=values)
    again = client.post('/api/v1/projects', headers=replay_headers, json=values)
    assert first.status_code == again.status_code == 201 and first.json() == again.json()
    with session() as db:
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action == 'project.created')) == 1
        token = db.scalar(select(Token).where(Token.kind == 'api'))
        token.scopes = [p for p in token.scopes if p != 'projects.admin']; db.commit()
    response = client.post('/api/v1/projects', headers=replay_headers, json=values)
    assert response.status_code in (403, 404)


def test_project_revisions_last_manager_openapi_and_parent_guard(system):
    client, headers, _ = system
    t = create_tenant(client, headers, 'revision'); p = create_project(client, headers, t['id'], 'revision')
    alice, ah = new_user(client, headers, 'revision-alice'); m = add_member(client, headers, t['id'], p['id'], alice['id'])
    values = {k: p[k] for k in ('name', 'slug', 'status', 'description', 'labels', 'metadata', 'default_environment')}
    values |= {'expected_version': p['version'], 'default_environment': 'prod'}
    response = client.put(f"/api/v1/projects/{p['id']}", headers=ah, json=values)
    assert response.status_code == 200 and response.json()['version'] == 2, response.text
    assert client.put(f"/api/v1/projects/{p['id']}", headers=ah, json=values).status_code == 409
    removal = client.delete(f"/api/v1/projects/{p['id']}/members/{alice['id']}?expected_version={m['version']}", headers=ah)
    assert removal.status_code == 409 and removal.json()['detail']['code'] == 'PROJECT_MANAGER_REQUIRED'
    assert client.get(f"/api/v1/projects/{p['id']}", headers=ah).status_code == 200
    assert client.delete(f"/api/v1/tenants/{t['id']}?expected_version=1", headers=headers).status_code == 409
    paths = client.get('/openapi.json').json()['paths']
    assert '/api/v1/projects' in paths and '/api/v1/project-context/resolve' in paths


def test_project_blueprint_approval_policy_round_trip(system):
    client, headers, _ = system
    tenant = create_tenant(client, headers, 'approval-policy')
    created = client.post('/api/v1/projects', headers=headers, json={
        'tenant_id': tenant['id'],
        'name': 'approval-policy',
        'slug': 'approval-policy',
        'blueprint_auto_approve_for_executors': False,
        'blueprint_approval_timeout_hours': 9,
    })
    assert created.status_code == 201, created.text
    body = created.json()
    assert body['blueprint_auto_approve_for_executors'] is False
    assert body['blueprint_approval_timeout_hours'] == 9

    values = {k: body[k] for k in (
        'name', 'slug', 'status', 'description', 'labels', 'metadata', 'default_environment'
    )}
    values |= {
        'expected_version': body['version'],
        'blueprint_auto_approve_for_executors': None,
        'blueprint_approval_timeout_hours': None,
    }
    updated = client.put(f"/api/v1/projects/{body['id']}", headers=headers, json=values)
    assert updated.status_code == 200, updated.text
    assert updated.json()['blueprint_auto_approve_for_executors'] is None
    assert updated.json()['blueprint_approval_timeout_hours'] is None
