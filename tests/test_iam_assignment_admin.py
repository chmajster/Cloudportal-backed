from app.database import session
from app.iam.models import RoleAssignment


def _scope(client, headers, slug):
    tenant = client.post(
        '/api/v1/tenants',
        headers=headers,
        json={'name': slug.title(), 'slug': slug},
    )
    assert tenant.status_code == 201, tenant.text
    tenant = tenant.json()

    project = client.post(
        '/api/v1/projects',
        headers=headers,
        json={
            'tenant_id': tenant['id'],
            'name': slug.title() + ' Project',
            'slug': slug + '-project',
        },
    )
    assert project.status_code == 201, project.text
    return tenant, project.json()


def test_system_admin_can_assign_first_organization_binding_end_to_end(system):
    client, headers, _admin = system
    kynlab, kynlab_project = _scope(client, headers, 'kynlab')
    workspace, workspace_project = _scope(client, headers, 'workspace')

    user = client.post(
        '/api/v1/users',
        headers=headers,
        json={
            'username': 'adam',
            'email': 'adam@example.com',
            'password': 'strong-password-1234',
        },
    )
    assert user.status_code == 201, user.text
    user = user.json()

    before = client.get(
        '/api/v1/rbac/assignments',
        headers=headers,
        params={'subject_type': 'USER', 'subject_id': str(user['id'])},
    )
    assert before.status_code == 200, before.text
    assert before.json()['total'] == 0

    # Even if a caller accidentally carries a concrete workspace, IAM administration
    # must remain platform-wide for a system Administrator.
    workspace_headers = {
        **headers,
        'X-Tenant-ID': workspace['id'],
        'X-Project-ID': workspace_project['id'],
    }

    subjects = client.get(
        '/api/v1/iam/subjects',
        headers=workspace_headers,
        params={'type': 'USER', 'limit': 200},
    )
    assert subjects.status_code == 200, subjects.text
    subject_ids = {item['id'] for item in subjects.json()['items']}
    assert user['id'] in subject_ids

    organizations = client.get(
        '/api/v1/iam/organizations',
        headers=workspace_headers,
        params={'limit': 200},
    )
    assert organizations.status_code == 200, organizations.text
    organization_ids = {str(item['id']) for item in organizations.json()['items']}
    assert str(kynlab['id']) in organization_ids
    assert str(workspace['id']) in organization_ids

    organization_alias = client.get(
        '/api/v1/organizations',
        headers=workspace_headers,
        params={'limit': 200},
    )
    assert organization_alias.status_code == 200, organization_alias.text
    assert str(kynlab['id']) in {
        str(item['id']) for item in organization_alias.json()['items']
    }

    projects = client.get(
        '/api/v1/iam/projects',
        headers=workspace_headers,
        params={'organization_id': kynlab['id'], 'limit': 200},
    )
    assert projects.status_code == 200, projects.text
    assert str(kynlab_project['id']) in {
        str(item['id']) for item in projects.json()['items']
    }
    assert {
        str(item['organization_id']) for item in projects.json()['items']
    } == {str(kynlab['id'])}

    apmids = client.get(
        '/api/v1/iam/apmids',
        headers=workspace_headers,
        params={
            'organization_id': kynlab['id'],
            'project_id': kynlab_project['id'],
        },
    )
    assert apmids.status_code == 200, apmids.text
    assert 'LEO' in {item['id'] for item in apmids.json()['items']}

    environments = client.get(
        '/api/v1/iam/environments',
        headers=workspace_headers,
        params={
            'organization_id': kynlab['id'],
            'project_id': kynlab_project['id'],
            'apmid': 'LEO',
        },
    )
    assert environments.status_code == 200, environments.text
    assert {'test', 'dev', 'nonprod', 'prod'} <= {
        item['id'] for item in environments.json()['items']
    }

    roles = client.get('/api/v1/iam/roles', headers=workspace_headers, params={'limit': 200})
    assert roles.status_code == 200, roles.text
    operator = next(item for item in roles.json()['items'] if item['name'] == 'Operator')

    created = client.post(
        '/api/v1/rbac/assignments',
        headers=workspace_headers,
        json={
            'subject_type': 'USER',
            'subject_id': str(user['id']),
            'role_id': operator['id'],
            'effect': 'ALLOW',
            'scope_type': 'ORGANIZATION',
            'scope_id': str(kynlab['id']),
            'tenant_id': str(kynlab['id']),
            'conditions': {},
            'inherit': True,
            'approval_required': False,
            'enabled': True,
        },
    )
    assert created.status_code == 201, created.text
    binding = created.json()

    with session() as db:
        persisted = db.get(RoleAssignment, binding['id'])
        assert persisted is not None
        assert persisted.subject_type == 'USER'
        assert persisted.subject_id == str(user['id'])
        assert persisted.tenant_id == str(kynlab['id'])
        assert persisted.scope_type == 'ORGANIZATION'
        assert persisted.effect == 'ALLOW'

    effective = client.get(
        f"/api/v1/users/{user['id']}/effective-access",
        headers=headers,
        params={
            'scope_type': 'ORGANIZATION',
            'tenant_id': str(kynlab['id']),
        },
    )
    assert effective.status_code == 200, effective.text
    assert 'deployments.read' in effective.json()['permissions']

    listed = client.get(
        '/api/v1/rbac/assignments',
        headers=headers,
        params={'subject_type': 'USER', 'subject_id': str(user['id'])},
    )
    assert listed.status_code == 200, listed.text
    assert binding['id'] in {item['id'] for item in listed.json()['items']}

    updated = client.patch(
        f"/api/v1/rbac/assignments/{binding['id']}",
        headers=workspace_headers,
        json={
            'effect': 'DENY',
            'conditions': {},
            'inherit': True,
            'approval_required': False,
            'enabled': True,
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()['effect'] == 'DENY'

    denied = client.get(
        f"/api/v1/users/{user['id']}/effective-access",
        headers=headers,
        params={
            'scope_type': 'ORGANIZATION',
            'tenant_id': str(kynlab['id']),
        },
    )
    assert denied.status_code == 200, denied.text
    assert 'deployments.read' not in denied.json()['permissions']

    deleted = client.delete(
        f"/api/v1/rbac/assignments/{binding['id']}",
        headers=workspace_headers,
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()['deleted'] is True

    with session() as db:
        assert db.get(RoleAssignment, binding['id']) is None


def test_iam_web_assignment_form_uses_global_catalogs_and_explicit_errors():
    source = open('app/web/features/iam.js', encoding='utf-8').read()

    assert "return api(path, { ...options, scope: false }, canRefresh);" in source
    assert "async function iamAll(path)" in source
    assert "'/iam/subjects?type=USER'" in source
    assert "'/iam/organizations'" in source
    assert "'/iam/projects?organization_id='" in source
    assert "'/iam/apmids?organization_id='" in source
    assert "'/iam/environments?organization_id='" in source
    assert "searchableSelectField('Subject'" in source
    assert "searchableSelectField('Organization'" in source
    assert "Ładowanie użytkowników..." in source
    assert "Ładowanie organizacji..." in source
    assert "Brak użytkowników" in source
    assert "Brak organizacji" in source
    assert "Nie udało się pobrać " in source
    assert "Brak permission: " in source
    assert "Conditions JSON musi być obiektem JSON." in source
    assert "submit.disabled = !(" in source
    assert "method: 'PATCH'" in source
    assert "button('Edytuj'" in source
    assert "limit=500" not in source
