import uuid

from sqlalchemy import select

from app.database import session
from app.day2.models import Day2ActionRequest
from app.models import Audit, Deployment, Job, JobLog, ManagedResource, ManagedVM, Provider, User
from app.terraform.state import persist_state


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


def test_inventory_exposes_active_day2_action_for_vm_status(client, headers, monkeypatch):
    from app.providers.proxmox import ProxmoxProvider

    p = provider(client, headers)
    monkeypatch.setattr(ProxmoxProvider, 'discover', lambda self, resource, node=None: [{
        'vmid': 887,
        'name': 'power-transition',
        'node': 'pve01',
        'status': 'running',
        'type': 'qemu',
        'template': 0,
    }] if resource == 'vms' else [])

    imported = client.post('/api/v1/inventory/vms/import', headers=key(headers), json={
        'provider_id': p['id'],
        'vm_id': 887,
    })
    assert imported.status_code == 201, imported.text
    vm_id = imported.json()['id']

    with session() as db:
        user_id = db.scalar(select(User.id).order_by(User.id))
        db.add(Day2ActionRequest(
            resource_id=vm_id,
            action='shutdown',
            parameters={},
            reason='test',
            requested_by=user_id,
            approval_state='not_required',
            status='RUNNING',
            request_id=str(uuid.uuid4()),
        ))
        db.commit()

    rows = client.get('/api/v1/inventory/vms?limit=200', headers=headers)
    assert rows.status_code == 200, rows.text
    vm = next(item for item in rows.json()['items'] if item['id'] == vm_id)
    assert vm['active_action']['action'] == 'shutdown'
    assert vm['active_action']['status'] == 'RUNNING'


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



def test_vm_history_includes_provisioning_job_stages(client, headers):
    p = provider(client, headers)

    with session() as db:
        provider_row = db.get(Provider, p['id'])
        user_id = db.scalar(select(User.id).order_by(User.id))
        deployment = Deployment(
            name='history-vm',
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
            vm_id=889,
            name='history-vm',
            management_mode='terraform',
            lifecycle_status='active',
            created_by=user_id,
        )
        db.add(vm)
        db.flush()

        job = Job(
            deployment_id=deployment.id,
            operation='terraform.apply',
            payload={'_current_stage': 'terraform_apply'},
            status='successful',
            created_by=user_id,
            request_id=str(uuid.uuid4()),
        )
        db.add(job)
        db.flush()
        db.add_all([
            JobLog(job_id=job.id, message='terraform.init'),
            JobLog(job_id=job.id, message='workflow.step.completed:apply:terraform_apply'),
            JobLog(job_id=job.id, message='provider raw secret output must not be exposed'),
            Audit(
                user_id=user_id,
                token_id=None,
                ip='127.0.0.1',
                source='API',
                action='snapshot.created',
                resource='vms',
                resource_id=f"{p['id']}:pve01:889:baseline",
                result='success',
                request_id=str(uuid.uuid4()),
            ),
            Audit(
                user_id=user_id,
                token_id=None,
                ip='127.0.0.1',
                source='API',
                action='vm.reboot.wrong-vm',
                resource='vms',
                resource_id=f"{p['id']}:pve01:8890",
                result='success',
                request_id=str(uuid.uuid4()),
            ),
        ])
        db.commit()
        vm_id = vm.id

    response = client.get(f'/api/v1/inventory/vms/{vm_id}/history?limit=500', headers=headers)
    assert response.status_code == 200, response.text
    rows = response.json()['items']
    titles = [row['title'] for row in rows]

    assert 'VM dodana do inventory' in titles
    assert 'Utworzono deployment' in titles
    assert 'terraform.apply' in titles
    assert 'terraform.init' in titles
    assert 'workflow.step.completed:apply:terraform_apply' in titles
    assert 'snapshot.created' in titles
    assert 'vm.reboot.wrong-vm' not in titles
    assert all('raw secret output' not in title for title in titles)



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


def test_missing_terraform_vm_can_be_removed_only_after_provider_confirms_absence(
    client, headers, monkeypatch, tmp_path
):
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

    workspace = tmp_path / 'stale-terraform-state'
    workspace.mkdir()
    (workspace / 'terraform.tfstate').write_text(
        '{"version":4,"outputs":{"vm_id":{"value":901},"primary_ip":{"value":null}}}'
    )
    assert persist_state(deployment_id, workspace) is True

    monkeypatch.setattr(
        ProxmoxProvider,
        'discover',
        lambda self, resource, node=None: [] if resource == 'vms' else [],
    )

    refreshed = client.get(f'/api/v1/inventory/vms/{vm_id}?refresh=true', headers=headers)
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()['live'] is None
    assert refreshed.json()['lifecycle_status'] == 'missing'

    reconciled = client.post('/api/v1/inventory/reconcile', headers=headers, json={})
    assert reconciled.status_code == 200, reconciled.text
    assert reconciled.json()['missing_count'] == 1
    with session() as db:
        assert db.get(ManagedVM, vm_id).lifecycle_status == 'missing'

    removed = client.delete(
        f'/api/v1/inventory/vms/{vm_id}/missing?purge=true',
        headers=headers,
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()['deleted'] is True
    assert removed.json()['purged'] is True
    assert removed.json()['provider_absent'] is True

    with session() as db:
        vm = db.get(ManagedVM, vm_id)
        deployment = db.get(Deployment, deployment_id)
        resource = db.scalar(select(ManagedResource).where(
            ManagedResource.deployment_id == deployment_id
        ))
        assert vm is None
        assert resource is None
        assert deployment.status == 'destroyed'
        assert deployment.destroyed_at is not None
        assert deployment.active_job_id is None

    repaired = client.post('/api/v1/inventory/reconcile', headers=headers, json={})
    assert repaired.status_code == 200, repaired.text
    assert repaired.json()['repaired_count'] == 0
    with session() as db:
        assert db.get(ManagedVM, vm_id) is None
        assert db.get(Deployment, deployment_id).status == 'destroyed'



def test_inventory_reconcile_restores_missing_vm_when_it_reappears(client, headers, monkeypatch):
    from app.providers.proxmox import ProxmoxProvider

    p = provider(client, headers)
    monkeypatch.setattr(
        ProxmoxProvider,
        'discover',
        lambda self, resource, node=None: [{
            'vmid': 903,
            'name': 'returns-later',
            'node': 'pve01',
            'status': 'stopped',
            'type': 'qemu',
            'template': 0,
        }] if resource == 'vms' else [],
    )
    imported = client.post('/api/v1/inventory/vms/import', headers=key(headers), json={
        'provider_id': p['id'],
        'vm_id': 903,
    })
    assert imported.status_code == 201, imported.text
    vm_id = imported.json()['id']

    monkeypatch.setattr(
        ProxmoxProvider,
        'discover',
        lambda self, resource, node=None: [] if resource == 'vms' else [],
    )
    missing = client.post('/api/v1/inventory/reconcile', headers=headers, json={})
    assert missing.status_code == 200, missing.text
    assert missing.json()['missing_count'] == 1

    monkeypatch.setattr(
        ProxmoxProvider,
        'discover',
        lambda self, resource, node=None: [{
            'vmid': 903,
            'name': 'returns-later',
            'node': 'pve02',
            'status': 'running',
            'type': 'qemu',
            'template': 0,
        }] if resource == 'vms' else [],
    )
    restored = client.post('/api/v1/inventory/reconcile', headers=headers, json={})
    assert restored.status_code == 200, restored.text
    assert restored.json()['restored_count'] == 1

    with session() as db:
        vm = db.get(ManagedVM, vm_id)
        assert vm.lifecycle_status == 'active'
        assert vm.node == 'pve02'


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
