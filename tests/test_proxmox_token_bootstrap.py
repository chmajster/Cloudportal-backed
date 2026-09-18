from types import SimpleNamespace

import httpx

from app.database import session
from app.models import Credential
from app.security.core import decrypt_secret


def test_proxmox_bootstrap_creates_token_without_storing_password(client, headers, monkeypatch):
    calls = []

    class Response:
        def __init__(self, payload):
            self.payload = payload
        def raise_for_status(self):
            return None
        def json(self):
            return self.payload

    class FakeClient:
        def __init__(self, **kwargs):
            calls.append(('init', kwargs))
            self.cookies = SimpleNamespace(set=lambda *args: calls.append(('cookie', args)))
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def post(self, url, data=None, headers=None):
            calls.append(('post', url, data, headers))
            if url.endswith('/access/ticket'):
                return Response({'data': {'ticket': 'ticket', 'CSRFPreventionToken': 'csrf'}})
            assert '/access/users/root%40pam/token/cloudportal' in url
            assert data['privsep'] == 0
            return Response({'data': {'full-tokenid': 'root@pam!cloudportal', 'value': 'generated-token-secret'}})

    monkeypatch.setattr('app.providers.proxmox.httpx.Client', FakeClient)
    response = client.post('/api/v1/credentials/proxmox/bootstrap', headers=headers, json={
        'name': 'PVE generated token',
        'endpoint': 'https://pve.example.com:8006',
        'username': 'root@pam',
        'password': 'one-shot-password',
        'token_name': 'cloudportal',
        'verify_ssl': True,
    })
    assert response.status_code == 201, response.text
    body = response.json()
    assert body['type'] == 'proxmox'
    assert body['configured'] is True
    assert body['secret'] == '********'

    with session() as db:
        credential = db.get(Credential, body['id'])
        secret = decrypt_secret(credential)
        assert secret == {
            'token_id': 'root@pam!cloudportal',
            'token_secret': 'generated-token-secret',
        }
        assert 'one-shot-password' not in repr(secret)

    ticket_call = next(call for call in calls if call[0] == 'post' and call[1].endswith('/access/ticket'))
    assert ticket_call[2]['password'] == 'one-shot-password'


def test_proxmox_bootstrap_accepts_explicit_http_endpoint(client, headers, monkeypatch):
    calls = []

    class Response:
        def __init__(self, payload):
            self.payload = payload
        def raise_for_status(self):
            return None
        def json(self):
            return self.payload

    class FakeClient:
        def __init__(self, **kwargs):
            calls.append(('init', kwargs))
            self.cookies = SimpleNamespace(set=lambda *args: None)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def post(self, url, data=None, headers=None):
            calls.append(('post', url))
            if url.endswith('/access/ticket'):
                return Response({'data': {'ticket': 'ticket', 'CSRFPreventionToken': 'csrf'}})
            return Response({'data': {'full-tokenid': 'root@pam!cloudportal', 'value': 'generated-token-secret'}})

    monkeypatch.setattr('app.providers.proxmox.httpx.Client', FakeClient)
    response = client.post('/api/v1/credentials/proxmox/bootstrap', headers=headers, json={
        'name': 'PVE over HTTP',
        'endpoint': 'http://pve.example.com:8006',
        'username': 'root@pam',
        'password': 'password',
        'token_name': 'cloudportal',
    })
    assert response.status_code == 201, response.text
    assert response.json()['endpoint'] == 'http://pve.example.com:8006'
    assert any(call[0] == 'post' and call[1].startswith('http://pve.example.com:8006/') for call in calls)


def test_proxmox_bootstrap_detects_http_for_bare_ip(client, headers, monkeypatch):
    calls = []

    class Response:
        def __init__(self, payload=None):
            self.payload = payload or {}
        def raise_for_status(self):
            return None
        def json(self):
            return self.payload

    class FakeClient:
        def __init__(self, **kwargs):
            calls.append(('init', kwargs))
            self.cookies = SimpleNamespace(set=lambda *args: None)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def get(self, url):
            calls.append(('get', url))
            if url.startswith('https://'):
                raise httpx.ConnectError('wrong version number')
            return Response()
        def post(self, url, data=None, headers=None):
            calls.append(('post', url))
            if url.endswith('/access/ticket'):
                return Response({'data': {'ticket': 'ticket', 'CSRFPreventionToken': 'csrf'}})
            return Response({'data': {'full-tokenid': 'root@pam!cloudportal', 'value': 'generated-token-secret'}})

    monkeypatch.setattr('app.providers.proxmox.httpx.Client', FakeClient)
    response = client.post('/api/v1/credentials/proxmox/bootstrap', headers=headers, json={
        'name': 'PVE autodetect',
        'endpoint': '192.0.2.20',
        'username': 'root@pam',
        'password': 'password',
        'token_name': 'cloudportal',
    })
    assert response.status_code == 201, response.text
    assert response.json()['endpoint'] == 'http://192.0.2.20:8006'
    assert ('get', 'https://192.0.2.20:8006/api2/json/version') in calls
    assert ('get', 'http://192.0.2.20:8006/api2/json/version') in calls


def test_proxmox_bootstrap_can_disable_certificate_verification(client, headers, monkeypatch):
    client_options = []

    class Response:
        def __init__(self, payload=None):
            self.payload = payload or {}
        def raise_for_status(self):
            return None
        def json(self):
            return self.payload

    class FakeClient:
        def __init__(self, **kwargs):
            client_options.append(kwargs)
            self.cookies = SimpleNamespace(set=lambda *args: None)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def get(self, url):
            return Response()
        def post(self, url, data=None, headers=None):
            if url.endswith('/access/ticket'):
                return Response({'data': {'ticket': 'ticket', 'CSRFPreventionToken': 'csrf'}})
            return Response({'data': {'full-tokenid': 'root@pam!cloudportal', 'value': 'generated-token-secret'}})

    monkeypatch.setattr('app.providers.proxmox.httpx.Client', FakeClient)
    response = client.post('/api/v1/credentials/proxmox/bootstrap', headers=headers, json={
        'name': 'PVE self signed',
        'endpoint': '192.0.2.21',
        'username': 'root@pam',
        'password': 'password',
        'token_name': 'cloudportal',
        'verify_ssl': False,
    })
    assert response.status_code == 201, response.text
    assert response.json()['endpoint'] == 'https://192.0.2.21:8006'
    assert client_options
    assert all(options['verify'] is False for options in client_options)
