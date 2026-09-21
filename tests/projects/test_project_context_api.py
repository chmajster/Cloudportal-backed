"""Real HTTP selection checks, compatible with the existing explicit resolver."""
from conftest import new_user
from app.database import session
from app.projects.models import ProjectMembership
from test_projects_api import create_tenant, create_project, add_member


def test_context_http_revision_isolation_and_recovery(system):
    client, admin, _ = system
    t = create_tenant(client, admin, 'context'); p = create_project(client, admin, t['id'], 'context')
    u, h = new_user(client, admin, 'context-user'); add_member(client, admin, t['id'], p['id'], u['id'])
    v, vh = new_user(client, admin, 'context-other')
    payload = {'tenant_id': t['id'], 'project_id': p['id'], 'expected_version': 0}
    assert client.get('/api/v1/project-context', headers=h).json() == {'selected': None, 'version': 0}
    assert client.put('/api/v1/project-context', headers=vh, json=payload).status_code == 404
    chosen = client.put('/api/v1/project-context', headers=h, json=payload)
    assert chosen.status_code == 200 and chosen.json()['version'] == 1, chosen.text
    assert client.put('/api/v1/project-context', headers=h, json=payload).status_code == 409
    assert client.get('/api/v1/auth/me', headers=h).json()['permissions'] == []
    assert client.get('/api/v1/providers', headers=h).status_code == 403
    assert client.post('/api/v1/project-context/resolve', headers=h,
        json={'tenant_id': t['id'], 'project_id': p['id']}).json()['actor_id'] == u['id']
    with session() as db:
        db.get(ProjectMembership, (p['id'], u['id'])).status = 'disabled'; db.commit()
    assert client.get('/api/v1/project-context', headers=h).status_code == 404
    cleared = client.delete('/api/v1/project-context?expected_version=1', headers=h)
    assert cleared.status_code == 200 and cleared.json() == {'selected': None, 'version': 2}
    assert client.delete('/api/v1/project-context?expected_version=1', headers=h).status_code == 409
    assert client.get('/api/v1/project-context', headers=vh).json() == {'selected': None, 'version': 0}
    assert client.put('/api/v1/project-context', headers=admin, json=payload | {'expected_version': -1}).status_code == 422
    assert client.get('/api/v1/project-context').status_code == 401
