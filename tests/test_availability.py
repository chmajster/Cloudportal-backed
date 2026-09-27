from uuid import uuid4

from app.api.schemas import BlueprintExecuteInput
from app.providers.proxmox import ProxmoxProvider


def _project(client, headers, tenant_id, slug):
    response = client.post('/api/v1/projects', headers=headers, json={
        'tenant_id': tenant_id,
        'name': slug,
        'slug': slug,
    })
    assert response.status_code == 201, response.text
    return response.json()


def _scope(headers, project):
    return headers | {
        'X-Tenant-ID': project['tenant_id'],
        'X-Project-ID': project['id'],
    }


def _plan(name='AV-PROD'):
    return {
        'name': name,
        'description': 'HA for production VM',
        'state': 'started',
        'group': None,
        'max_restart': 2,
        'max_relocate': 3,
        'is_active': True,
    }


def test_availability_plan_name_is_unique_only_inside_organization_and_project(client, headers):
    tenant_a = client.post('/api/v1/tenants', headers=headers, json={
        'name': 'AV tenant A', 'slug': 'av-tenant-a',
    })
    assert tenant_a.status_code == 201, tenant_a.text
    tenant_b = client.post('/api/v1/tenants', headers=headers, json={
        'name': 'AV tenant B', 'slug': 'av-tenant-b',
    })
    assert tenant_b.status_code == 201, tenant_b.text

    project_a1 = _project(client, headers, tenant_a.json()['id'], 'av-project-a1')
    project_a2 = _project(client, headers, tenant_a.json()['id'], 'av-project-a2')
    project_b1 = _project(client, headers, tenant_b.json()['id'], 'av-project-b1')

    first = client.post('/api/v1/availability-plans', headers=_scope(headers, project_a1), json=_plan())
    assert first.status_code == 201, first.text

    # Case-insensitive duplicate in the same Organization + Project is forbidden.
    duplicate = client.post(
        '/api/v1/availability-plans',
        headers=_scope(headers, project_a1),
        json=_plan('av-prod'),
    )
    assert duplicate.status_code == 409, duplicate.text

    # The same display name is valid in another project of the same organization.
    second_project = client.post(
        '/api/v1/availability-plans',
        headers=_scope(headers, project_a2),
        json=_plan(),
    )
    assert second_project.status_code == 201, second_project.text

    # It is also valid in another organization/project.
    other_tenant = client.post(
        '/api/v1/availability-plans',
        headers=_scope(headers, project_b1),
        json=_plan(),
    )
    assert other_tenant.status_code == 201, other_tenant.text

    listing = client.get('/api/v1/availability-plans', headers=_scope(headers, project_a1))
    assert listing.status_code == 200, listing.text
    assert [row['name'] for row in listing.json()['items']] == ['AV-PROD']


def test_blueprint_execute_contract_accepts_optional_availability_plan():
    plan_id = str(uuid4())
    data = BlueprintExecuteInput(availability_plan_id=plan_id)
    assert data.availability_plan_id == plan_id

    empty = BlueprintExecuteInput()
    assert empty.availability_plan_id is None


def test_proxmox_provider_creates_and_reads_back_ha_resource(monkeypatch):
    provider = object.__new__(ProxmoxProvider)
    reads = iter([
        None,
        {
            'sid': 'vm:123',
            'state': 'started',
            'max_restart': 2,
            'max_relocate': 3,
        },
    ])
    calls = []

    monkeypatch.setattr(provider, 'vm_ha_resource', lambda vm_id: next(reads))
    monkeypatch.setattr(provider, '_post', lambda path, data=None: calls.append(('POST', path, data)))
    monkeypatch.setattr(provider, '_put', lambda path, data=None: calls.append(('PUT', path, data)))

    result = provider.set_vm_ha(
        123,
        state='started',
        max_restart=2,
        max_relocate=3,
        comment='CloudPortal AV',
    )

    assert result['sid'] == 'vm:123'
    assert calls == [(
        'POST',
        '/cluster/ha/resources',
        {
            'sid': 'vm:123',
            'state': 'started',
            'max_restart': 2,
            'max_relocate': 3,
            'comment': 'CloudPortal AV',
        },
    )]


def test_proxmox_provider_updates_ha_and_removes_legacy_group(monkeypatch):
    provider = object.__new__(ProxmoxProvider)
    reads = iter([
        {
            'sid': 'vm:124',
            'state': 'started',
            'group': 'legacy',
            'max_restart': 1,
            'max_relocate': 1,
        },
        {
            'sid': 'vm:124',
            'state': 'stopped',
            'max_restart': 0,
            'max_relocate': 2,
        },
    ])
    calls = []

    monkeypatch.setattr(provider, 'vm_ha_resource', lambda vm_id: next(reads))
    monkeypatch.setattr(provider, '_post', lambda path, data=None: calls.append(('POST', path, data)))
    monkeypatch.setattr(provider, '_put', lambda path, data=None: calls.append(('PUT', path, data)))

    provider.set_vm_ha(124, state='stopped', max_restart=0, max_relocate=2)

    assert calls == [(
        'PUT',
        '/cluster/ha/resources/vm:124',
        {
            'state': 'stopped',
            'max_restart': 0,
            'max_relocate': 2,
            'delete': 'group',
        },
    )]


def test_availability_ui_contract_is_wired():
    source = open('app/web/features/availability.js', encoding='utf-8').read()
    blueprints = open('app/web/features/blueprints.js', encoding='utf-8').read()
    inventory = open('app/web/features/inventory.js', encoding='utf-8').read()
    tools = open('app/web/features/tools.js', encoding='utf-8').read()

    assert "id: 'availability-plans'" in source
    assert "navigationParent: 'tools'" in source
    assert "'/availability-plans/resources/'" in source
    assert "availability_plan_id" in blueprints
    assert "availabilityVisible ? ['availability', 'Dostępność'] : null" in inventory
    assert "canReadResource" in source
    assert "permission: null" in source[source.rfind("registerView({"):]
    assert "window.AvailabilityPlans.card()" in tools
