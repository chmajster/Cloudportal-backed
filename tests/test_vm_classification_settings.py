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
        'apmids': [],
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
    assert saved.json()['apmids'] == ['IAASTEAM', 'CRM']
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
        assert row.value['apmids'] == ['IAASTEAM', 'CRM']
        assert row.value['hostname_defaults'] == {'location': 'dc1', 'role': 'web'}


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
    assert response.json()['apmids'] == ['IAASTEAM', 'CRM']

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
