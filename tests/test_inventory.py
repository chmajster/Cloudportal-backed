import uuid


def key(headers):
    return {**headers, 'Idempotency-Key': str(uuid.uuid4())}


def provider(client, headers):
    credential = client.post('/api/v1/credentials', headers=headers, json={
        'name': 'Inventory PVE',
        'type': 'proxmox',
        'endpoint': 'https://pve.example.com:8006',
        'username': 'root@pam',
        'secrets': {'token_id': 'root@pam!inventory', 'token_secret': 'private-inventory-token'},
    })
    assert credential.status_code == 201, credential.text
    response = client.post('/api/v1/providers', headers=headers, json={
        'name': 'Inventory PVE',
        'type': 'proxmox',
        'credentials_id': credential.json()['id'],
    })
    assert response.status_code == 201, response.text
    return response.json()


def test_import_reconcile_and_unmanage_external_vm(client, headers, monkeypatch):
    from app.providers.proxmox import ProxmoxProvider

    p = provider(client, headers)
    state = {'present': True, 'name': 'legacy-vm', 'node': 'pve01'}

    def discover(self, resource, node=None):
        assert resource == 'vms'
        if not state['present']:
            return []
        return [{
            'vmid': 777,
            'name': state['name'],
            'node': state['node'],
            'status': 'running',
            'type': 'qemu',
            'template': 0,
            'password': 'never-return',
        }]

    monkeypatch.setattr(ProxmoxProvider, 'discover', discover)

    imported = client.post('/api/v1/inventory/vms/import', headers=key(headers), json={
        'provider_id': p['id'], 'vm_id': 777,
    })
    assert imported.status_code == 201, imported.text
    row = imported.json()
    assert row['management_mode'] == 'external'
    assert row['lifecycle_status'] == 'active'
    assert row['name'] == 'legacy-vm'

    duplicate = client.post('/api/v1/inventory/vms/import', headers=key(headers), json={
        'provider_id': p['id'], 'vm_id': 777,
    })
    assert duplicate.status_code == 409

    refreshed = client.get(f"/api/v1/inventory/vms/{row['id']}?refresh=true", headers=headers)
    assert refreshed.status_code == 200
    assert refreshed.json()['live']['status'] == 'running'
    assert 'password' not in refreshed.text

    state['name'] = 'renamed-legacy-vm'
    state['node'] = 'pve02'
    reconciled = client.post(f"/api/v1/inventory/vms/{row['id']}/reconcile", headers=headers)
    assert reconciled.status_code == 200
    assert reconciled.json()['name'] == 'renamed-legacy-vm'
    assert reconciled.json()['node'] == 'pve02'

    state['present'] = False
    missing = client.post(f"/api/v1/inventory/vms/{row['id']}/reconcile", headers=headers)
    assert missing.status_code == 200
    assert missing.json()['lifecycle_status'] == 'missing'

    removed = client.delete(f"/api/v1/inventory/vms/{row['id']}", headers=headers)
    assert removed.status_code == 200 and removed.json()['deleted']


def test_inventory_list_can_refresh_live_state(client, headers, monkeypatch):
    from app.providers.proxmox import ProxmoxProvider

    p = provider(client, headers)
    monkeypatch.setattr(ProxmoxProvider, 'discover', lambda self, resource, node=None: [{
        'vmid': 888, 'name': 'catalog-vm', 'node': 'pve01', 'status': 'stopped',
        'type': 'qemu', 'template': 0,
    }] if resource == 'vms' else [])

    imported = client.post('/api/v1/inventory/vms/import', headers=key(headers), json={
        'provider_id': p['id'], 'vm_id': 888,
    })
    assert imported.status_code == 201

    rows = client.get(f"/api/v1/inventory/vms?provider_id={p['id']}&refresh=true", headers=headers)
    assert rows.status_code == 200
    assert rows.json()['items'][0]['live']['status'] == 'stopped'
