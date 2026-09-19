import io

import paramiko

from app.credentials.ssh import generate_ed25519_key_pair
from app.database import session
from app.models import Credential
from app.security.core import decrypt_secret


def test_generated_ed25519_key_is_usable_by_paramiko():
    private_key, public_key = generate_ed25519_key_pair()

    assert private_key.startswith('-----BEGIN OPENSSH PRIVATE KEY-----')
    assert public_key.startswith('ssh-ed25519 ')
    parsed = paramiko.Ed25519Key.from_private_key(io.StringIO(private_key))
    assert parsed.get_name() == 'ssh-ed25519'


def test_ssh_host_key_scan_endpoint(client, headers, monkeypatch):
    monkeypatch.setattr(
        'app.api.infrastructure.scan_ssh_host_key',
        lambda endpoint: {
            'host': 'server.example.com',
            'port': 22,
            'key_type': 'ssh-ed25519',
            'fingerprint': 'SHA256:hostfingerprint',
            'known_hosts': 'server.example.com ssh-ed25519 AAAATEST',
        },
    )

    response = client.post(
        '/api/v1/credentials/ssh/host-key',
        headers=headers,
        json={'endpoint': 'ssh://server.example.com:22'},
    )

    assert response.status_code == 200, response.text
    assert response.json()['fingerprint'] == 'SHA256:hostfingerprint'
    assert response.json()['known_hosts'].startswith('server.example.com ssh-ed25519 ')


def test_ssh_bootstrap_installs_key_and_never_stores_password(client, headers, monkeypatch):
    installed = {}

    def fake_install(endpoint, username, password, known_hosts):
        installed.update(
            endpoint=endpoint,
            username=username,
            password=password,
            known_hosts=known_hosts,
        )
        return {
            'secret': {
                'private_key': '-----BEGIN OPENSSH PRIVATE KEY-----\nprivate-test\n-----END OPENSSH PRIVATE KEY-----',
                'known_hosts': known_hosts,
            },
            'public_key': 'ssh-ed25519 AAAATEST cloudportal',
            'fingerprint': 'SHA256:keyfingerprint',
        }

    monkeypatch.setattr('app.api.infrastructure.install_generated_key', fake_install)

    response = client.post(
        '/api/v1/credentials/ssh/bootstrap',
        headers=headers,
        json={
            'name': 'Linux managed key',
            'endpoint': 'ssh://server.example.com:22',
            'username': 'clouduser',
            'password': 'temporary-password',
            'known_hosts': 'server.example.com ssh-ed25519 AAAAHOST',
        },
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    credential = payload['credential']
    assert credential['type'] == 'ssh'
    assert credential['endpoint'] == 'ssh://server.example.com:22'
    assert payload['public_key'] == 'ssh-ed25519 AAAATEST cloudportal'
    assert payload['fingerprint'] == 'SHA256:keyfingerprint'
    assert installed['password'] == 'temporary-password'

    with session() as db:
        row = db.get(Credential, credential['id'])
        secret = decrypt_secret(row)
        assert 'password' not in secret
        assert secret['private_key'].startswith('-----BEGIN OPENSSH PRIVATE KEY-----')
        assert secret['known_hosts'] == 'server.example.com ssh-ed25519 AAAAHOST'
        assert b'temporary-password' not in row.encrypted_secret
