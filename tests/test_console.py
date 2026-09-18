def test_console_session_is_ephemeral_and_rbac_protected(client, headers, monkeypatch):
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
        'user': 'root@pam',
        'cert': 'temporary-cert',
        'websocket_path': f'/api2/json/nodes/{node}/qemu/{vmid}/vncwebsocket',
        'origin': 'https://pve.example.com:8006',
    })

    base = f"/api/v1/providers/{provider['id']}/vms/pve01/101/console"
    response = client.post(base, headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['ticket'].startswith('PVEVNC:')
    assert response.headers['Cache-Control'] == 'no-store'
    assert credential['secret'] == '********'

    _, reader = new_user(client, headers, username='console-reader', permissions=['vms.read'])
    denied = client.post(base, headers=reader)
    assert denied.status_code == 403
