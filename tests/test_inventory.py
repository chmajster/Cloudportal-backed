import uuid

from sqlalchemy import select

from app.database import session
from app.models import Deployment, ManagedResource, ManagedVM, Provider, User


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


def test_missing_terraform_vm_can_be_removed_only_after_provider_confirms_absence(client, headers, monkeypatch):
    from app.providers.proxmox import ProxmoxProvider

    p = provider(client, headers)
    with session() as db:
        provider_row = db.get(Provider, p['id'])
        user_id = db.scalar(select(User.id).order_by(User.id))
        deployment = Deployment(
            name='stale-terraform-vm',
            provider_id=p['id'],
            provider='proxmox',
            template='proxmox-vm',
            credentials_id=provider_row.credentials_id,
            variables={'node': 'pve01'},
            workflow={},
            status='successful',
            created_by=user_id,
        )
        db.add(deployment)
        db.flush()
        vm = ManagedVM(
            provider_id=p['id'],
            deployment_id=deployment.id,
            node='pve01',
            vm_id=901,
            name='stale-terraform-vm',
            management_mode='terraform',
            lifecycle_status='active',
            created_by=user_id,
        )
        resource = ManagedResource(
            deployment_id=deployment.id,
            provider_id=p['id'],
            provider='proxmox',
            resource_type='vm',
            external_id='901',
            name='stale-terraform-vm',
            lifecycle_status='active',
            metadata_json={'node': 'pve01', 'vm_id': 901},
            created_by=user_id,
        )
        db.add_all([vm, resource])
        db.commit()
        vm_id = vm.id
        deployment_id = deployment.id

    monkeypatch.setattr(
        ProxmoxProvider,
        'discover',
        lambda self, resource, node=None: [] if resource == 'vms' else [],
    )

    refreshed = client.get(f'/api/v1/inventory/vms/{vm_id}?refresh=true', headers=headers)
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()['live'] is None

    removed = client.delete(f'/api/v1/inventory/vms/{vm_id}/missing', headers=headers)
    assert removed.status_code == 200, removed.text
    assert removed.json()['deleted'] is True
    assert removed.json()['provider_absent'] is True

    with session() as db:
        vm = db.get(ManagedVM, vm_id)
        deployment = db.get(Deployment, deployment_id)
        resource = db.scalar(select(ManagedResource).where(
            ManagedResource.deployment_id == deployment_id
        ))
        assert vm is not None
        assert vm.lifecycle_status == 'destroyed'
        assert vm.destroyed_at is not None
        assert resource.lifecycle_status == 'destroyed'
        assert resource.destroyed_at is not None
        assert deployment.status == 'reconciliation_required'

    repaired = client.post('/api/v1/inventory/reconcile', headers=headers, json={})
    assert repaired.status_code == 200, repaired.text
    assert repaired.json()['repaired_count'] == 0


def test_missing_vm_cleanup_refuses_when_vm_still_exists(client, headers, monkeypatch):
    from app.providers.proxmox import ProxmoxProvider

    p = provider(client, headers)
    monkeypatch.setattr(ProxmoxProvider, 'discover', lambda self, resource, node=None: [{
        'vmid': 902,
        'name': 'still-there',
        'node': 'pve01',
        'status': 'running',
        'type': 'qemu',
        'template': 0,
    }] if resource == 'vms' else [])

    imported = client.post('/api/v1/inventory/vms/import', headers=key(headers), json={
        'provider_id': p['id'], 'vm_id': 902,
    })
    assert imported.status_code == 201, imported.text
    vm_id = imported.json()['id']

    refused = client.delete(f'/api/v1/inventory/vms/{vm_id}/missing', headers=headers)
    assert refused.status_code == 409, refused.text
    assert 'still exists on Proxmox' in refused.text

    with session() as db:
        assert db.get(ManagedVM, vm_id).lifecycle_status == 'active'
