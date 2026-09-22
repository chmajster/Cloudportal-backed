import sys
import types


def test_provider_specific_credential_validation(client, headers):
    invalid = [
        {'name': 'VMware invalid', 'type': 'vmware', 'endpoint': 'https://vc.example.com', 'username': 'administrator@vsphere.local', 'secrets': {'secret': 'x'}},
        {'name': 'SSH invalid', 'type': 'ssh', 'username': 'clouduser', 'secrets': {'known_hosts': 'host ssh-ed25519 example'}},
        {'name': 'AWS invalid', 'type': 'aws', 'secrets': {'access_key_id': 'AKIATEST'}},
        {'name': 'Azure invalid', 'type': 'azure', 'secrets': {'tenant_id': 'tenant', 'client_id': 'client', 'client_secret': 'private'}},
        {'name': 'OpenStack invalid', 'type': 'openstack', 'endpoint': 'https://os.example.com:5000/v3', 'username': 'cloudportal', 'secrets': {'password': 'private'}},
        {'name': 'Other invalid', 'type': 'other', 'secrets': {'password': 'private'}},
    ]
    for payload in invalid:
        response = client.post('/api/v1/credentials', headers=headers, json=payload)
        assert response.status_code == 422, (payload['type'], response.text)

    valid = [
        {'name': 'Proxmox HTTP valid', 'type': 'proxmox', 'endpoint': 'http://pve.example.com:8006', 'username': 'root@pam', 'secrets': {'token_id': 'root@pam!portal', 'token_secret': 'private'}},
        {'name': 'VMware valid', 'type': 'vmware', 'endpoint': 'https://vc.example.com', 'username': 'administrator@vsphere.local', 'secrets': {'password': 'private'}},
        {'name': 'SSH valid', 'type': 'ssh', 'username': 'clouduser', 'secrets': {'private_key': 'private-key-data'}},
        {'name': 'AWS valid', 'type': 'aws', 'secrets': {'access_key_id': 'AKIATEST', 'secret_access_key': 'private'}},
        {'name': 'Azure valid', 'type': 'azure', 'secrets': {'tenant_id': 'tenant', 'client_id': 'client', 'client_secret': 'private', 'subscription_id': 'sub'}},
        {'name': 'OpenStack valid', 'type': 'openstack', 'endpoint': 'https://os.example.com:5000/v3', 'username': 'cloudportal', 'secrets': {'password': 'private', 'project_name': 'admin', 'domain_name': 'Default'}},
        {'name': 'Other valid', 'type': 'other', 'secrets': {'secret': 'private'}},
    ]
    for payload in valid:
        response = client.post('/api/v1/credentials', headers=headers, json=payload)
        assert response.status_code == 201, (payload['type'], response.text)
        assert response.json()['configured'] is True
        assert response.json()['secret'] == '********'



def test_ssh_credentials_do_not_require_known_hosts(client, headers):
    for name, secrets in [
        ('SSH password without known hosts', {'password': 'private-password'}),
        ('SSH key without known hosts', {'private_key': 'private-key-data'}),
    ]:
        response = client.post('/api/v1/credentials', headers=headers, json={
            'name': name,
            'type': 'ssh',
            'endpoint': 'ssh://server.example.com:22',
            'username': 'clouduser',
            'secrets': secrets,
        })
        assert response.status_code == 201, response.text
        assert response.json()['configured'] is True


def test_winrm_connection_test_honors_verify_ssl(client, headers, monkeypatch):
    observed = []

    class Result:
        status_code = 0

    class Session:
        def __init__(self, endpoint, auth, **kwargs):
            observed.append(kwargs['server_cert_validation'])
        def run_ps(self, command):
            return Result()

    monkeypatch.setitem(sys.modules, 'winrm', types.SimpleNamespace(Session=Session))

    for verify_ssl, expected, name in [(True, 'validate', 'verify'), (False, 'ignore', 'ignore')]:
        created = client.post('/api/v1/credentials', headers=headers, json={
            'name': 'WinRM ' + name,
            'type': 'winrm',
            'endpoint': 'https://windows.example.com:5986/wsman',
            'username': 'DOMAIN\\svc',
            'verify_ssl': verify_ssl,
            'secrets': {'password': 'private-password'},
        })
        assert created.status_code == 201, created.text
        response = client.post('/api/v1/credentials/' + str(created.json()['id']) + '/test', headers=headers)
        assert response.status_code == 200, response.text
        assert observed[-1] == expected


def test_ssh_credential_reports_cloud_init_key_capability(client, headers):
    password_only = client.post('/api/v1/credentials', headers=headers, json={
        'name': 'SSH password capability',
        'type': 'ssh',
        'endpoint': 'ssh://password.example.com:22',
        'username': 'clouduser',
        'secrets': {'password': 'private-password'},
    })
    assert password_only.status_code == 201, password_only.text
    assert password_only.json()['supports_cloud_init_ssh_key'] is False
    assert password_only.json()['supports_cloud_init_password'] is True

    key_based = client.post('/api/v1/credentials', headers=headers, json={
        'name': 'SSH key capability',
        'type': 'ssh',
        'endpoint': 'ssh://key.example.com:22',
        'username': 'clouduser',
        'secrets': {'private_key': 'private-key-data'},
    })
    assert key_based.status_code == 201, key_based.text
    assert key_based.json()['supports_cloud_init_ssh_key'] is True
    assert key_based.json()['supports_cloud_init_password'] is False

    listed = client.get('/api/v1/credentials?limit=200', headers=headers)
    assert listed.status_code == 200, listed.text
    by_id = {row['id']: row for row in listed.json()['items']}
    assert by_id[password_only.json()['id']]['supports_cloud_init_ssh_key'] is False
    assert by_id[password_only.json()['id']]['supports_cloud_init_password'] is True
    assert by_id[key_based.json()['id']]['supports_cloud_init_ssh_key'] is True
    assert by_id[key_based.json()['id']]['supports_cloud_init_password'] is False
