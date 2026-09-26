from __future__ import annotations

import uuid


ICO_DATA_URI = (
    'data:image/x-icon;base64,'
    'AAABAAEAAQEAAAEAIABEAAAAFgAAAIlQTkcNChoKAAAADUlIRFIAAAABAAAAAQgE'
    'AAAAtRwMAgAAAAtJREFUeNpjZPgPAAEFAQEnGONmAAAAAElFTkSuQmCC'
)


def idem(headers):
    return {**headers, 'Idempotency-Key': str(uuid.uuid4())}


def infrastructure(client, headers):
    credential = client.post('/api/v1/credentials', headers=headers, json={
        'name': 'Avatar PVE',
        'type': 'proxmox',
        'endpoint': 'https://pve.example.com:8006',
        'username': 'root@pam',
        'secrets': {
            'token_id': 'root@pam!avatar',
            'token_secret': 'avatar-secret',
        },
    })
    assert credential.status_code == 201, credential.text
    provider = client.post('/api/v1/providers', headers=headers, json={
        'name': 'Avatar PVE',
        'type': 'proxmox',
        'credentials_id': credential.json()['id'],
    })
    assert provider.status_code == 201, provider.text
    return credential.json(), provider.json()


def blueprint_payload(credential, provider, avatar_id=None):
    return {
        'slug': 'avatar-vm',
        'name': 'Avatar VM',
        'description': 'Blueprint z awatarem',
        'avatar_id': avatar_id,
        'deployment': {
            'name': 'avatar-vm',
            'provider_id': provider['id'],
            'credentials_id': credential['id'],
            'variables': {
                'name': 'avatar-vm',
                'node': 'pve01',
                'template_id': 9000,
                'storage': 'local-lvm',
            },
        },
        'workflow': [
            {'id': 'apply', 'type': 'terraform_apply'},
        ],
    }


def test_blueprint_avatar_catalog_crud_and_public_read(client, headers):
    created = client.post('/api/v1/settings/blueprint-avatars', headers=headers, json={
        'id': 'ubuntu',
        'name': 'Ubuntu',
        'data_uri': ICO_DATA_URI,
    })
    assert created.status_code == 201, created.text
    assert created.json()['id'] == 'ubuntu'
    assert created.json()['data_uri'].startswith('data:image/x-icon;base64,')

    public = client.get('/api/v1/blueprint-avatars', headers=headers)
    assert public.status_code == 200, public.text
    assert public.json()['items'] == [created.json()]

    updated = client.put('/api/v1/settings/blueprint-avatars/ubuntu', headers=headers, json={
        'id': 'ubuntu',
        'name': 'Ubuntu Server',
        'data_uri': ICO_DATA_URI.removeprefix('data:'),
    })
    assert updated.status_code == 200, updated.text
    assert updated.json()['name'] == 'Ubuntu Server'
    assert updated.json()['data_uri'].startswith('data:image/x-icon;base64,')

    deleted = client.delete('/api/v1/settings/blueprint-avatars/ubuntu', headers=headers)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {'deleted': True}


def test_blueprint_avatar_rejects_invalid_icon_payload(client, headers):
    wrong_type = client.post('/api/v1/settings/blueprint-avatars', headers=headers, json={
        'id': 'bad-png',
        'name': 'Bad PNG',
        'data_uri': 'data:image/png;base64,iVBORw0KGgo=',
    })
    assert wrong_type.status_code == 422, wrong_type.text

    truncated = client.post('/api/v1/settings/blueprint-avatars', headers=headers, json={
        'id': 'bad-ico',
        'name': 'Bad ICO',
        'data_uri': 'data:image/x-icon;base64,AAABAAE=',
    })
    assert truncated.status_code == 422, truncated.text


def test_blueprint_can_reference_avatar_and_blocks_deletion_while_used(client, headers):
    avatar = client.post('/api/v1/settings/blueprint-avatars', headers=headers, json={
        'id': 'linux',
        'name': 'Linux',
        'data_uri': ICO_DATA_URI,
    })
    assert avatar.status_code == 201, avatar.text

    credential, provider = infrastructure(client, headers)
    payload = blueprint_payload(credential, provider, avatar_id='linux')
    created = client.post('/api/v1/blueprints', headers=idem(headers), json=payload)
    assert created.status_code == 201, created.text
    assert created.json()['avatar_id'] == 'linux'

    blocked = client.delete('/api/v1/settings/blueprint-avatars/linux', headers=headers)
    assert blocked.status_code == 409, blocked.text

    payload['avatar_id'] = None
    updated = client.put(
        '/api/v1/blueprints/' + str(created.json()['id']),
        headers=headers,
        json=payload,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()['avatar_id'] is None

    deleted = client.delete('/api/v1/settings/blueprint-avatars/linux', headers=headers)
    assert deleted.status_code == 200, deleted.text


def test_blueprint_rejects_unknown_avatar(client, headers):
    credential, provider = infrastructure(client, headers)
    payload = blueprint_payload(credential, provider, avatar_id='does-not-exist')
    created = client.post('/api/v1/blueprints', headers=idem(headers), json=payload)
    assert created.status_code == 404, created.text
    assert 'avatar' in created.text.lower()
