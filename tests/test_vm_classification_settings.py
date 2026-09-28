from app.database import session
from app.models import Setting


def test_vm_classification_defaults_and_update(client, headers):
    initial = client.get('/api/v1/settings/vm-classification', headers=headers)
    assert initial.status_code == 200, initial.text
    assert initial.json() == {
        'environments': {
            'test': True,
            'dev': True,
            'nonprod': True,
            'prod': True,
        },
        'apmids': ['LEO'],
        'hostname_defaults': {'location': 'wro', 'role': 'server'},
    }

    payload = {
        'environments': {
            'test': False,
            'dev': True,
            'nonprod': True,
            'prod': False,
        },
        'apmids': ['iaasteam', 'CRM', 'iaasteam'],
    }
    saved = client.put('/api/v1/settings/vm-classification', headers=headers, json=payload)
    assert saved.status_code == 200, saved.text
    assert saved.json()['environments'] == payload['environments']
    assert saved.json()['apmids'] == ['LEO', 'IAASTEAM', 'CRM']
    assert saved.json()['hostname_defaults'] == {'location': 'wro', 'role': 'server'}

    hostname_defaults = client.put('/api/v1/settings/vm-classification', headers=headers, json={
        **payload,
        'hostname_defaults': {'location': 'dc1', 'role': 'web'},
    })
    assert hostname_defaults.status_code == 200, hostname_defaults.text
    assert hostname_defaults.json()['hostname_defaults'] == {'location': 'dc1', 'role': 'web'}

    with session() as db:
        row = db.get(Setting, 'vm_classification')
        assert row is not None
        assert row.value['environments']['dev'] is True
        assert row.value['environments']['prod'] is False
        assert row.value['apmids'] == ['LEO', 'IAASTEAM', 'CRM']
        assert row.value['hostname_defaults'] == {'location': 'dc1', 'role': 'web'}


def test_leo_apmid_is_immutable_system_default(client, headers):
    removed = client.put('/api/v1/settings/vm-classification', headers=headers, json={
        'environments': {'test': True, 'dev': True, 'nonprod': True, 'prod': True},
        'apmids': [],
    })
    assert removed.status_code == 200, removed.text
    assert removed.json()['apmids'] == ['LEO']

    renamed = client.put('/api/v1/settings/vm-classification', headers=headers, json={
        'environments': {'test': True, 'dev': True, 'nonprod': True, 'prod': True},
        'apmids': ['LEO2'],
    })
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()['apmids'] == ['LEO', 'LEO2']

    with session() as db:
        row = db.get(Setting, 'vm_classification')
        assert row.value['apmids'] == ['LEO', 'LEO2']


def test_vm_classification_requires_settings_permissions(client, headers):
    from conftest import new_user

    _user, auth = new_user(client, headers, permissions=['blueprints.read'])
    assert client.get('/api/v1/settings/vm-classification', headers=auth).status_code == 403
    assert client.put('/api/v1/settings/vm-classification', headers=auth, json={
        'environments': {'test': True, 'dev': True, 'nonprod': True, 'prod': True},
        'apmids': ['IAASTEAM'],
    }).status_code == 403


def test_blueprint_execution_can_read_vm_classification_without_settings_permission(client, headers):
    from conftest import new_user

    client.put('/api/v1/settings/vm-classification', headers=headers, json={
        'environments': {'test': True, 'dev': True, 'nonprod': True, 'prod': True},
        'apmids': ['IAASTEAM', 'CRM'],
    })

    _user, executor = new_user(
        client,
        headers,
        username='blueprint-executor',
        permissions=['blueprints.execute'],
    )
    response = client.get('/api/v1/vm-classification/options', headers=executor)
    assert response.status_code == 200, response.text
    assert response.json()['apmids'] == ['LEO', 'IAASTEAM', 'CRM']

    _reader, reader = new_user(
        client,
        headers,
        username='blueprint-reader',
        permissions=['blueprints.read'],
    )
    assert client.get('/api/v1/vm-classification/options', headers=reader).status_code == 403


def test_proxmox_vm_tags_accept_apmid_environment_code():
    from app.api.schemas import VMVariables

    values = VMVariables.model_validate({
        'name': 'vm01',
        'node': 'pve01',
        'template_id': 9000,
        'storage': 'local-lvm',
        'tags': ['IAASTEAM.DEV', 'env-dev', 'apmid-iaasteam'],
    })
    assert values.tags == ['apmid-iaasteam', 'env-dev', 'iaasteam.dev']

def test_apmids_are_scoped_per_organization_and_expand_enabled_environments(client, headers):
    first = client.post('/api/v1/tenants', headers=headers, json={
        'name': 'Organization Alpha',
        'slug': 'organization-alpha',
    })
    second = client.post('/api/v1/tenants', headers=headers, json={
        'name': 'Organization Beta',
        'slug': 'organization-beta',
    })
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    alpha, beta = first.json(), second.json()

    environments = {
        'test': False,
        'dev': True,
        'nonprod': True,
        'prod': True,
    }
    saved_global = client.put('/api/v1/settings/vm-classification', headers=headers, json={
        'environments': environments,
        'apmids': ['LEO'],
    })
    assert saved_global.status_code == 200, saved_global.text

    alpha_saved = client.put(
        f"/api/v1/tenants/{alpha['id']}/vm-classification",
        headers=headers,
        json={'apmids': ['xd']},
    )
    assert alpha_saved.status_code == 200, alpha_saved.text
    assert alpha_saved.json()['apmids'] == ['XD']
    assert alpha_saved.json()['apmid_environments'] == {
        'XD': ['dev', 'nonprod', 'prod'],
    }
    assert alpha_saved.json()['classifications'] == [
        'XD.DEV',
        'XD.NONPROD',
        'XD.PROD',
    ]

    beta_before = client.get(
        f"/api/v1/tenants/{beta['id']}/vm-classification",
        headers=headers,
    )
    assert beta_before.status_code == 200, beta_before.text
    assert beta_before.json()['apmids'] == ['LEO']
    assert beta_before.json()['classifications'] == [
        'LEO.DEV',
        'LEO.NONPROD',
        'LEO.PROD',
    ]

    beta_saved = client.put(
        f"/api/v1/tenants/{beta['id']}/vm-classification",
        headers=headers,
        json={'apmids': ['crm', 'billing']},
    )
    assert beta_saved.status_code == 200, beta_saved.text
    assert beta_saved.json()['apmids'] == ['CRM', 'BILLING']
    assert beta_saved.json()['classifications'] == [
        'CRM.DEV',
        'CRM.NONPROD',
        'CRM.PROD',
        'BILLING.DEV',
        'BILLING.NONPROD',
        'BILLING.PROD',
    ]

    alpha_again = client.get(
        f"/api/v1/tenants/{alpha['id']}/vm-classification",
        headers=headers,
    )
    assert alpha_again.status_code == 200, alpha_again.text
    assert alpha_again.json()['apmids'] == ['XD']


def test_runtime_classification_uses_selected_organization_scope(client, headers):
    tenant = client.post('/api/v1/tenants', headers=headers, json={
        'name': 'Scoped APMID Org',
        'slug': 'scoped-apmid-org',
    })
    assert tenant.status_code == 201, tenant.text
    tenant = tenant.json()

    project = client.post('/api/v1/projects', headers=headers, json={
        'tenant_id': tenant['id'],
        'name': 'Scoped APMID Project',
        'slug': 'scoped-apmid-project',
    })
    assert project.status_code == 201, project.text
    project = project.json()

    configured = client.put(
        f"/api/v1/tenants/{tenant['id']}/vm-classification",
        headers=headers,
        json={'apmids': ['xd']},
    )
    assert configured.status_code == 200, configured.text

    scoped_headers = headers | {
        'X-Tenant-ID': tenant['id'],
        'X-Project-ID': project['id'],
    }
    options = client.get('/api/v1/vm-classification/options', headers=scoped_headers)
    assert options.status_code == 200, options.text
    assert options.json()['apmids'] == ['XD']

