from sqlalchemy import select

from app.access.models import OrganizationAPMID
from app.database import session
from app.iam.models import Group, GroupMember, RoleAssignment
from app.models import Setting


def _organization(client, headers, slug):
    response = client.post('/api/v1/tenants', headers=headers, json={
        'name': slug.replace('-', ' ').title(),
        'slug': slug,
    })
    assert response.status_code == 201, response.text
    return response.json()


def _project(client, headers, organization, slug):
    response = client.post('/api/v1/projects', headers=headers, json={
        'tenant_id': organization['id'],
        'name': slug.replace('-', ' ').title(),
        'slug': slug,
    })
    assert response.status_code == 201, response.text
    return response.json()


def _user(client, headers, username):
    response = client.post('/api/v1/users', headers=headers, json={
        'username': username,
        'email': f'{username}@example.com',
        'password': 'strong-password-1234',
    })
    assert response.status_code == 201, response.text
    return response.json()


def test_organization_creation_creates_relational_leo_and_managed_groups(system):
    client, headers, _ = system
    organization = _organization(client, headers, 'unified-alpha')
    project = _project(client, headers, organization, 'platform')

    apmids = client.get(
        f"/api/v1/organizations/{organization['id']}/apmids",
        headers=headers,
    )
    assert apmids.status_code == 200, apmids.text
    assert [item['code'] for item in apmids.json()['items']] == ['LEO']
    assert apmids.json()['items'][0]['is_system'] is True

    groups = client.get(
        '/api/v1/access/groups',
        headers=headers,
        params={'organization_id': organization['id']},
    )
    assert groups.status_code == 200, groups.text
    keys = {item['system_key'] for item in groups.json()['items']}
    assert f"org:{organization['id']}:admins" in keys
    assert f"org:{organization['id']}:auditors" in keys
    assert f"project:{project['id']}:admins" in keys
    assert f"project:{project['id']}:operators" in keys
    assert f"project:{project['id']}:viewers" in keys
    assert f"apmid:{organization['id']}:LEO:operators" in keys
    assert f"apmid:{organization['id']}:LEO:viewers" in keys

    with session() as db:
        leo = db.scalar(select(OrganizationAPMID).where(
            OrganizationAPMID.organization_id == organization['id'],
            OrganizationAPMID.code == 'LEO',
        ))
        assert leo is not None
        assert leo.enabled is True
        assert leo.is_system is True
        managed = db.scalar(select(Group).where(
            Group.system_key == f"project:{project['id']}:operators"
        ))
        assert managed is not None
        binding = db.scalar(select(RoleAssignment).where(
            RoleAssignment.subject_type == 'GROUP',
            RoleAssignment.subject_id == managed.id,
            RoleAssignment.source_ref == f"managed-group:project:{project['id']}:operators",
        ))
        assert binding is not None
        assert binding.scope_type == 'PROJECT'
        assert binding.tenant_id == organization['id']
        assert binding.project_id == project['id']


def test_apmid_crud_is_relational_and_leo_is_protected(system):
    client, headers, _ = system
    organization = _organization(client, headers, 'unified-apmid')

    created = client.post(
        f"/api/v1/organizations/{organization['id']}/apmids",
        headers=headers,
        json={'code': 'iaasteam', 'name': 'IaaS Team', 'description': 'Infrastructure'},
    )
    assert created.status_code == 201, created.text
    assert created.json()['code'] == 'IAASTEAM'
    assert created.json()['organization_id'] == organization['id']

    legacy_key = f"vm_classification:{organization['id']}"
    with session() as db:
        assert db.get(Setting, legacy_key) is None
        assert db.scalar(select(OrganizationAPMID.id).where(
            OrganizationAPMID.organization_id == organization['id'],
            OrganizationAPMID.code == 'IAASTEAM',
        )) is not None

    protected = client.delete(
        f"/api/v1/organizations/{organization['id']}/apmids/LEO",
        headers=headers,
    )
    assert protected.status_code == 409

    deleted = client.delete(
        f"/api/v1/organizations/{organization['id']}/apmids/IAASTEAM",
        headers=headers,
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()['deleted'] is True


def test_apmid_group_access_is_organization_bounded_and_removed_immediately(system):
    client, headers, _ = system
    first = _organization(client, headers, 'scope-first')
    second = _organization(client, headers, 'scope-second')
    first_project = _project(client, headers, first, 'first-project')
    second_project = _project(client, headers, second, 'second-project')

    for organization in (first, second):
        response = client.post(
            f"/api/v1/organizations/{organization['id']}/apmids",
            headers=headers,
            json={'code': 'SHARED'},
        )
        assert response.status_code == 201, response.text

    user = _user(client, headers, 'apmid-operator')

    groups = client.get(
        '/api/v1/access/groups', headers=headers,
        params={'organization_id': first['id']},
    )
    assert groups.status_code == 200, groups.text
    operator_group = next(
        item for item in groups.json()['items']
        if item['system_key'] == f"apmid:{first['id']}:SHARED:operators"
    )

    add = client.post(
        f"/api/v1/rbac/groups/{operator_group['id']}/members",
        headers=headers,
        json={'user_id': user['id'], 'source': 'LOCAL'},
    )
    assert add.status_code == 201, add.text

    first_effective = client.get(
        f"/api/v1/users/{user['id']}/effective-access",
        headers=headers,
        params={
            'scope_type': 'APMID',
            'scope_id': 'SHARED',
            'tenant_id': first['id'],
            'project_id': first_project['id'],
            'apmid': 'SHARED',
        },
    )
    assert first_effective.status_code == 200, first_effective.text
    assert 'projects.read' in first_effective.json()['permissions']
    assert any(
        item.get('source_group') == operator_group['name']
        for item in first_effective.json()['assignments']
    )

    second_effective = client.get(
        f"/api/v1/users/{user['id']}/effective-access",
        headers=headers,
        params={
            'scope_type': 'APMID',
            'scope_id': 'SHARED',
            'tenant_id': second['id'],
            'project_id': second_project['id'],
            'apmid': 'SHARED',
        },
    )
    assert second_effective.status_code == 200, second_effective.text
    assert 'projects.read' not in second_effective.json()['permissions']

    removed = client.delete(
        f"/api/v1/rbac/groups/{operator_group['id']}/members/{user['id']}",
        headers=headers,
    )
    assert removed.status_code == 200, removed.text

    after = client.get(
        f"/api/v1/users/{user['id']}/effective-access",
        headers=headers,
        params={
            'scope_type': 'APMID',
            'scope_id': 'SHARED',
            'tenant_id': first['id'],
            'project_id': first_project['id'],
            'apmid': 'SHARED',
        },
    )
    assert after.status_code == 200, after.text
    assert 'projects.read' not in after.json()['permissions']


def test_access_binding_api_uses_organization_id_and_project_apmid_restriction(system):
    client, headers, _ = system
    organization = _organization(client, headers, 'binding-org')
    project = _project(client, headers, organization, 'binding-project')
    user = _user(client, headers, 'binding-user')

    created_apmid = client.post(
        f"/api/v1/organizations/{organization['id']}/apmids",
        headers=headers, json={'code': 'IAASTEAM'},
    )
    assert created_apmid.status_code == 201, created_apmid.text

    restriction = client.put(
        f"/api/v1/access/organizations/{organization['id']}/projects/{project['id']}/apmids",
        headers=headers, json={'apmids': ['LEO']},
    )
    assert restriction.status_code == 200, restriction.text
    assert restriction.json()['effective_apmids'] == ['LEO']

    roles = client.get(
        '/api/v1/access/roles', headers=headers,
        params={
            'assignable': True,
            'scope_type': 'PROJECT',
            'scope_id': project['id'],
            'organization_id': organization['id'],
            'project_id': project['id'],
        },
    )
    assert roles.status_code == 200, roles.text
    viewer = next(item for item in roles.json()['items'] if item['name'] == 'Project Viewer')

    binding = client.post(
        '/api/v1/access/bindings',
        headers=headers,
        json={
            'subject_type': 'USER',
            'subject_id': str(user['id']),
            'role_id': viewer['id'],
            'effect': 'ALLOW',
            'scope_type': 'PROJECT',
            'scope_id': project['id'],
            'organization_id': organization['id'],
            'project_id': project['id'],
            'conditions': {},
            'inherit': True,
            'approval_required': False,
            'enabled': True,
        },
    )
    assert binding.status_code == 201, binding.text
    body = binding.json()
    assert body['organization_id'] == organization['id']
    assert 'tenant_id' not in body

    blocked = client.post(
        '/api/v1/access/bindings',
        headers=headers,
        json={
            'subject_type': 'USER',
            'subject_id': str(user['id']),
            'role_id': viewer['id'],
            'effect': 'ALLOW',
            'scope_type': 'APMID',
            'scope_id': 'IAASTEAM',
            'organization_id': organization['id'],
            'project_id': project['id'],
            'apmid': 'IAASTEAM',
            'conditions': {},
            'inherit': True,
            'approval_required': False,
            'enabled': True,
        },
    )
    assert blocked.status_code == 422, blocked.text
    assert blocked.json()['detail']['error'] == 'apmid_not_allowed_in_project'
