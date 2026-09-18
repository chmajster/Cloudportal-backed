def test_console_session_is_ephemeral_proxied_and_rbac_protected(client, headers, monkeypatch):
    from app.providers.proxmox import ProxmoxProvider
    from conftest import new_user

    credential = client.post('/api/v1/credentials', headers=headers, json={
        'name': 'Console PVE',
        'type': 'proxmox',
        'endpoint': 'https://pve.example.com:8006',
        'username': 'root@pam',
        'secrets': {'token_id': 'root@pam!console', 'token_secret': 'private-console-token'},
    }).json()
    provider = client.post('/api/v1/providers', headers=headers, json={
        'name': 'Console PVE',
        'type': 'proxmox',
        'credentials_id': credential['id'],
    }).json()

    monkeypatch.setattr(ProxmoxProvider, 'console_session', lambda self, node, vmid: {
        'ticket': 'PVEVNC:ephemeral-ticket',
        'port': 5900,
        'password': 'ephemeral-rfb-password',
    })
    monkeypatch.setattr(
        ProxmoxProvider,
        'novnc_asset',
        lambda self, asset: (b'export default class RFB {}', 'text/javascript; charset=utf-8'),
    )

    base = f"/api/v1/providers/{provider['id']}/vms/pve01/101/console"
    response = client.post(base, headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['mode'] == 'novnc'
    assert body['rfb_module'].startswith('/api/v1/console-sessions/')
    assert body['rfb_module'].endswith('/novnc/core/rfb.js')
    assert body['ws_path'].startswith('/api/v1/console-sessions/')
    assert body['ws_path'].endswith('/websocket')
    assert body['password'] == 'ephemeral-rfb-password'
    assert body['expires_in'] > 0
    assert 'ticket' not in body
    assert 'port' not in body
    assert 'pve.example.com' not in str(body)
    assert response.headers['Cache-Control'] == 'no-store'
    assert credential['secret'] == '********'

    asset = client.get(body['rfb_module'])
    assert asset.status_code == 200, asset.text
    assert asset.content == b'export default class RFB {}'
    assert asset.headers['content-type'].startswith('text/javascript')
    assert asset.headers['Cache-Control'] == 'no-store'

    _, reader = new_user(client, headers, username='console-reader', permissions=['vms.read'])
    denied = client.post(base, headers=reader)
    assert denied.status_code == 403


def test_novnc_asset_validation_blocks_traversal(system):
    from app.providers.proxmox import ProxmoxProvider
    from fastapi import HTTPException

    provider = object.__new__(ProxmoxProvider)
    provider.endpoint = 'https://pve.example.com:8006/api2/json'
    provider.verify_ssl = True

    try:
        provider.novnc_asset('../etc/passwd')
    except HTTPException as error:
        assert error.status_code == 404
    else:
        raise AssertionError('Traversal asset path was accepted')

def test_console_websocket_url_supports_http(system):
    from app.providers.proxmox import ProxmoxProvider

    provider = object.__new__(ProxmoxProvider)
    provider.endpoint = 'http://pve.example.com:8006/api2/json'
    provider.verify_ssl = True

    url = provider.console_websocket_url('pve01', 101, 5900, 'ticket')
    assert url.startswith('ws://pve.example.com:8006/api2/json/nodes/pve01/qemu/101/vncwebsocket?')

