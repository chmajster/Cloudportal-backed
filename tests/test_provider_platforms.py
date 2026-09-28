from sqlalchemy import select

from app.database import session
from app.models import Provider, Setting


def _credential(client, headers, provider_type='vmware'):
    payload = {
        'name': provider_type.upper() + ' credentials',
        'type': provider_type,
        'endpoint': 'https://infra.example.com',
        'username': 'cloudportal',
        'secrets': {'password': 'test-secret-value'},
    }
    if provider_type == 'proxmox':
        payload.update({
            'endpoint': 'https://pve.example.com:8006',
            'username': 'root@pam',
            'secrets': {
                'token_id': 'root@pam!cloudportal',
                'token_secret': 'test-proxmox-token-secret',
            },
        })
    response = client.post('/api/v1/credentials', headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _set_platform(client, headers, provider_type, enabled):
    response = client.put(
        '/api/v1/settings/platforms/' + provider_type,
        headers=headers,
        json={'enabled': enabled},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_fresh_install_enables_only_proxmox(client, headers):
    response = client.get('/api/v1/settings/platforms', headers=headers)
    assert response.status_code == 200, response.text
    states = {item['name']: item for item in response.json()['items']}

    assert states['proxmox']['enabled'] is True
    assert states['proxmox']['configured'] is False
    for name in ('vmware', 'aws', 'azure', 'openstack'):
        assert states[name]['enabled'] is False
        assert states[name]['configured'] is False


def test_proxmox_provider_can_be_created_without_enabling_flag(client, headers):
    credential = _credential(client, headers, 'proxmox')
    response = client.post('/api/v1/providers', headers=headers, json={
        'name': 'PVE LAB',
        'type': 'proxmox',
        'credentials_id': credential['id'],
    })
    assert response.status_code == 201, response.text
    assert response.json()['enabled'] is True
    assert response.json()['configured'] is True


def test_disabled_platform_blocks_provider_creation_until_enabled(client, headers):
    credential = _credential(client, headers, 'vmware')
    payload = {
        'name': 'vCenter',
        'type': 'vmware',
        'credentials_id': credential['id'],
    }

    blocked = client.post('/api/v1/providers', headers=headers, json=payload)
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()['detail'] == 'Provider VMware is disabled'

    state = _set_platform(client, headers, 'vmware', True)
    assert state['enabled'] is True

    created = client.post('/api/v1/providers', headers=headers, json=payload)
    assert created.status_code == 201, created.text
    assert created.json()['enabled'] is True


def test_disable_preserves_provider_and_credentials_and_reenable_restores_use(client, headers):
    credential = _credential(client, headers, 'vmware')
    _set_platform(client, headers, 'vmware', True)
    created = client.post('/api/v1/providers', headers=headers, json={
        'name': 'vCenter',
        'type': 'vmware',
        'credentials_id': credential['id'],
    })
    assert created.status_code == 201, created.text
    provider_id = created.json()['id']

    disabled = _set_platform(client, headers, 'vmware', False)
    assert disabled['enabled'] is False
    assert disabled['configured'] is True
    assert disabled['connection_count'] == 1

    provider = client.get(f'/api/v1/providers/{provider_id}', headers=headers)
    assert provider.status_code == 200, provider.text
    assert provider.json()['enabled'] is False
    assert provider.json()['configured'] is True
    assert provider.json()['credentials_id'] == credential['id']

    credential_after = client.get(f"/api/v1/credentials/{credential['id']}", headers=headers)
    assert credential_after.status_code == 200, credential_after.text
    assert credential_after.json()['configured'] is True

    discovery = client.get(f'/api/v1/providers/{provider_id}/vms', headers=headers)
    assert discovery.status_code == 409, discovery.text
    assert discovery.json()['detail'] == 'Provider VMware is disabled'

    enabled = _set_platform(client, headers, 'vmware', True)
    assert enabled['enabled'] is True
    assert enabled['configured'] is True

    provider_after = client.get(f'/api/v1/providers/{provider_id}', headers=headers)
    assert provider_after.status_code == 200, provider_after.text
    assert provider_after.json()['enabled'] is True
    assert provider_after.json()['credentials_id'] == credential['id']


def test_legacy_configured_provider_is_enabled_until_admin_persists_switches(client, headers):
    credential = _credential(client, headers, 'vmware')

    with session() as db:
        assert db.get(Setting, 'provider_platforms') is None
        legacy = Provider(
            name='Legacy vCenter',
            type='vmware',
            credentials_id=credential['id'],
        )
        db.add(legacy)
        db.commit()
        provider_id = legacy.id

    response = client.get('/api/v1/settings/platforms', headers=headers)
    assert response.status_code == 200, response.text
    states = {item['name']: item for item in response.json()['items']}
    assert states['vmware']['enabled'] is True
    assert states['vmware']['configured'] is True
    assert states['vmware']['connection_count'] == 1

    provider = client.get(f'/api/v1/providers/{provider_id}', headers=headers)
    assert provider.status_code == 200, provider.text
    assert provider.json()['enabled'] is True

    with session() as db:
        assert db.get(Setting, 'provider_platforms') is None


def test_unknown_platform_setting_is_rejected(client, headers):
    response = client.put(
        '/api/v1/settings/platforms/not-a-provider',
        headers=headers,
        json={'enabled': True},
    )
    assert response.status_code == 404, response.text
