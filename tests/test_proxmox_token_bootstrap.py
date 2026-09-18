from types import SimpleNamespace

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


def test_proxmox_bootstrap_rejects_non_https_endpoint(client, headers):
    response = client.post('/api/v1/credentials/proxmox/bootstrap', headers=headers, json={
        'name': 'bad', 'endpoint': 'http://pve.example.com:8006', 'username': 'root@pam',
        'password': 'password', 'token_name': 'cloudportal',
    })
    assert response.status_code == 422
