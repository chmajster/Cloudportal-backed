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


def test_proxmox_draft_connection_test_is_detailed_and_does_not_persist_secret(client, headers, monkeypatch):
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
            assert url.endswith('/access/ticket')
            return Response({'data': {'ticket': 'ticket', 'CSRFPreventionToken': 'csrf'}})
        def get(self, url, headers=None):
            calls.append(('get', url, headers))
            assert url.endswith('/version')
            return Response({'data': {'version': '9.0.3'}})

    monkeypatch.setattr('app.providers.proxmox.httpx.Client', FakeClient)
    with session() as db:
        before = len(db.query(Credential).all())

    response = client.post('/api/v1/credentials/proxmox/test', headers=headers, json={
        'name': 'PVE test',
        'type': 'proxmox',
        'endpoint': 'https://pve.example.com:8006',
        'username': 'root@pam',
        'verify_ssl': True,
        'secrets': {'password': 'one-shot-password'},
    })

    assert response.status_code == 200, response.text
    body = response.json()
    assert body['ok'] is True
    assert body['provider'] == 'proxmox'
    assert body['version'] == '9.0.3'
    assert body['endpoint'] == 'https://pve.example.com:8006'
    assert body['username'] == 'root@pam'
    assert body['auth_mode'] == 'password'
    assert body['verify_ssl'] is True
    assert isinstance(body['latency_ms'], int)
    assert body['message'] == 'Połączenie z Proxmox VE działa poprawnie.'
    assert 'one-shot-password' not in response.text
    with session() as db:
        assert len(db.query(Credential).all()) == before


def test_proxmox_bootstrap_verifies_duplicate_after_http_400_and_suggests_new_name(client, headers, monkeypatch):
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
            self.cookies = SimpleNamespace(set=lambda *args: None)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def post(self, url, data=None, headers=None):
            calls.append(('post', url))
            if url.endswith('/access/ticket'):
                return Response({'data': {'ticket': 'ticket', 'CSRFPreventionToken': 'csrf'}})
            request = httpx.Request('POST', url)
            return httpx.Response(400, request=request, json={'errors': {'tokenid': 'invalid value'}})
        def get(self, url, headers=None):
            calls.append(('get', url))
            assert url.endswith('/access/users/root%40pam/token')
            return Response({'data': [
                {'tokenid': 'cloudportal', 'comment': 'existing'},
                {'tokenid': 'backup'},
            ]})

    monkeypatch.setattr('app.providers.proxmox.httpx.Client', FakeClient)
    with session() as db:
        before = len(db.query(Credential).all())

    response = client.post('/api/v1/credentials/proxmox/bootstrap', headers=headers, json={
        'name': 'PVE duplicate token',
        'endpoint': 'https://pve.example.com:8006',
        'username': 'root@pam',
        'password': 'one-shot-password',
        'token_name': 'cloudportal',
        'verify_ssl': True,
    })

    assert response.status_code == 409, response.text
    detail = response.json()['detail']
    assert detail['code'] == 'proxmox_token_duplicate'
    assert detail['token_name'] == 'cloudportal'
    assert detail['suggested_token_name'] == 'cloudportal-2'
    assert 'root@pam!cloudportal' in detail['message']
    assert 'już istnieje' in detail['message']
    assert ('get', 'https://pve.example.com:8006/api2/json/access/users/root%40pam/token') in calls
    assert 'one-shot-password' not in response.text
    with session() as db:
        assert len(db.query(Credential).all()) == before


def test_proxmox_bootstrap_http_400_without_duplicate_reports_parameter_error(client, headers, monkeypatch):
    class Response:
        def __init__(self, payload):
            self.payload = payload
        def raise_for_status(self):
            return None
        def json(self):
            return self.payload

    class FakeClient:
        def __init__(self, **kwargs):
            self.cookies = SimpleNamespace(set=lambda *args: None)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def post(self, url, data=None, headers=None):
            if url.endswith('/access/ticket'):
                return Response({'data': {'ticket': 'ticket', 'CSRFPreventionToken': 'csrf'}})
            request = httpx.Request('POST', url)
            return httpx.Response(400, request=request, json={'errors': {'privsep': 'invalid parameter'}})
        def get(self, url, headers=None):
            return Response({'data': [{'tokenid': 'different-token'}]})

    monkeypatch.setattr('app.providers.proxmox.httpx.Client', FakeClient)
    response = client.post('/api/v1/credentials/proxmox/bootstrap', headers=headers, json={
        'name': 'PVE invalid params',
        'endpoint': 'https://pve.example.com:8006',
        'username': 'root@pam',
        'password': 'one-shot-password',
        'token_name': 'cloudportal',
        'verify_ssl': True,
    })

    assert response.status_code == 502, response.text
    detail = response.json()['detail']
    assert detail['code'] == 'proxmox_token_create_rejected'
    assert detail['proxmox_status'] == 400
    assert detail['duplicate_checked'] is True
    assert detail['duplicate'] is False
    assert 'nie jest duplikatem' in detail['message']


def test_proxmox_bootstrap_failure_is_reported_in_polish_with_phase_and_http_status(client, headers, monkeypatch):
    class FakeClient:
        def __init__(self, **kwargs):
            self.cookies = SimpleNamespace(set=lambda *args: None)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def post(self, url, data=None, headers=None):
            request = httpx.Request('POST', url)
            return httpx.Response(401, request=request)

    monkeypatch.setattr('app.providers.proxmox.httpx.Client', FakeClient)
    response = client.post('/api/v1/credentials/proxmox/bootstrap', headers=headers, json={
        'name': 'PVE invalid login',
        'endpoint': 'https://pve.example.com:8006',
        'username': 'root@pam',
        'password': 'wrong-password',
        'token_name': 'cloudportal',
        'verify_ssl': True,
    })

    assert response.status_code == 502
    detail = response.json()['detail']
    assert 'Logowanie do Proxmox VE' in detail
    assert 'Logowanie zostało odrzucone' in detail
    assert 'HTTP 401' in detail
    assert 'token creation failed' not in detail.lower()
