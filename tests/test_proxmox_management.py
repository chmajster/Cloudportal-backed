import uuid

from app.database import session
from app.models import ManagedVM
from app.projects.permissions import DEFAULT_PROJECT_ID
from app.quotas.service import set_project_limit
from app.resource_scope.authorization import Scope
from app.tenancy.permissions import DEFAULT_TENANT_ID
from app.providers.task_reconcile import reconcile_proxmox_tasks_once, track_proxmox_task

from conftest import new_user


def resources(client, headers):
    credential = client.post('/api/v1/credentials', headers=headers, json={
        'name': 'PVE management',
        'type': 'proxmox',
        'endpoint': 'https://pve.example.com:8006',
        'username': 'root@pam',
        'secrets': {'token_id': 'root@pam!portal', 'token_secret': 'private-proxmox-token'},
    })
    assert credential.status_code == 201, credential.text
    provider = client.post('/api/v1/providers', headers=headers, json={
        'name': 'PVE management',
        'type': 'proxmox',
        'credentials_id': credential.json()['id'],
    })
    assert provider.status_code == 201, provider.text
    return provider.json()


def idem(headers):
    return {**headers, 'Idempotency-Key': str(uuid.uuid4())}


def test_proxmox_vm_lifecycle_routes(client, headers, monkeypatch):
    from app.providers.proxmox import ProxmoxProvider

    provider = resources(client, headers)
    calls = []

    monkeypatch.setattr(ProxmoxProvider, 'vm_status', lambda self, node, vmid: {
        'vmid': vmid, 'name': 'vm01', 'status': 'running', 'secret': 'never-return'
    })
    monkeypatch.setattr(ProxmoxProvider, 'guest_addresses', lambda self, node, vmid: ['192.0.2.101'])
    monkeypatch.setattr(ProxmoxProvider, 'vm_power', lambda self, node, vmid, action: calls.append(('power', node, vmid, action)) or 'UPID:power')
    monkeypatch.setattr(ProxmoxProvider, 'snapshots', lambda self, node, vmid: [
        {'name': 'baseline', 'description': 'safe', 'snaptime': 1, 'secret': 'never-return'}
    ])
    monkeypatch.setattr(ProxmoxProvider, 'create_snapshot', lambda self, node, vmid, name, description='', include_ram=False: calls.append(('snapshot', name, include_ram)) or 'UPID:snapshot')
    monkeypatch.setattr(ProxmoxProvider, 'delete_snapshot', lambda self, node, vmid, name: calls.append(('snapshot-delete', name)) or 'UPID:snapshot-delete')
    monkeypatch.setattr(ProxmoxProvider, 'rollback_snapshot', lambda self, node, vmid, name: calls.append(('snapshot-rollback', name)) or 'UPID:snapshot-rollback')
    monkeypatch.setattr(ProxmoxProvider, 'convert_to_template', lambda self, node, vmid: calls.append(('template', vmid)) or 'UPID:template')
    monkeypatch.setattr(ProxmoxProvider, 'clone_vm', lambda self, node, vmid, **kwargs: calls.append(('clone', kwargs)) or 'UPID:clone')
    monkeypatch.setattr(ProxmoxProvider, 'resize_disk', lambda self, node, vmid, **kwargs: calls.append(('resize', kwargs)) or 'UPID:resize')
    monkeypatch.setattr(ProxmoxProvider, 'update_vm_config', lambda self, node, vmid, **kwargs: calls.append(('config', kwargs)) or None)
    monkeypatch.setattr(ProxmoxProvider, 'migrate_vm', lambda self, node, vmid, **kwargs: calls.append(('migrate', kwargs)) or 'UPID:migrate')
    monkeypatch.setattr(ProxmoxProvider, 'delete_vm', lambda self, node, vmid, **kwargs: calls.append(('delete', kwargs)) or 'UPID:delete')
    monkeypatch.setattr(ProxmoxProvider, 'task_status', lambda self, node, upid: {
        'status': 'stopped', 'exitstatus': 'OK', 'user': 'root@pam', 'secret': 'never-return'
    })

    base = f"/api/v1/providers/{provider['id']}/vms/pve01/101"

    status = client.get(base + '/status', headers=headers)
    assert status.status_code == 200 and status.json()['status'] == 'running'
    assert status.json()['primary_ip'] == '192.0.2.101'
    assert 'secret' not in status.json()

    power = client.post(base + '/power', headers=headers, json={'action': 'reboot'})
    assert power.status_code == 200 and power.json()['task'] == 'UPID:power'

    snapshots = client.get(base + '/snapshots', headers=headers)
    assert snapshots.status_code == 200 and snapshots.json()['items'][0]['name'] == 'baseline'
    assert 'secret' not in snapshots.text

    created = client.post(base + '/snapshots', headers=idem(headers), json={
        'snapname': 'before-update', 'description': 'Before update', 'include_ram': False,
    })
    assert created.status_code == 202 and created.json()['task'] == 'UPID:snapshot'

    assert client.delete(base + '/snapshots/before-update', headers=idem(headers)).status_code == 202
    assert client.post(base + '/snapshots/baseline/rollback', headers=idem(headers)).status_code == 202
    assert client.post(base + '/template', headers=idem(headers)).status_code == 202

    cloned = client.post(base + '/clone', headers=idem(headers), json={
        'new_vm_id': 202, 'name': 'vm02', 'target': 'pve02', 'full': True, 'storage': 'local-lvm',
    })
    assert cloned.status_code == 202 and cloned.json()['task'] == 'UPID:clone'

    resized = client.put(base + '/disk', headers=idem(headers), json={'disk': 'scsi0', 'grow_gib': 20})
    assert resized.status_code == 202 and resized.json()['task'] == 'UPID:resize'

    configured = client.put(base + '/config', headers=idem(headers), json={
        'cores': 4, 'memory': 8192, 'onboot': True,
    })
    assert configured.status_code == 202 and configured.json()['task'] is None

    migrated = client.post(base + '/migrate', headers=idem(headers), json={
        'target': 'pve02', 'online': True, 'with_local_disks': True,
    })
    assert migrated.status_code == 202 and migrated.json()['task'] == 'UPID:migrate'

    task = client.get(
        f"/api/v1/providers/{provider['id']}/tasks/pve01/UPID%3Apve01%3A123",
        headers=headers,
    )
    assert task.status_code == 200 and task.json()['exitstatus'] == 'OK'
    assert 'secret' not in task.text

    deleted = client.delete(
        base + '?purge=true&destroy_unreferenced_disks=true',
        headers=idem(headers),
    )
    assert deleted.status_code == 202 and deleted.json()['task'] == 'UPID:delete'

    assert ('power', 'pve01', 101, 'reboot') in calls
    assert any(call[0] == 'clone' and call[1]['new_vm_id'] == 202 for call in calls)
    assert any(call[0] == 'config' and call[1]['onboot'] == 1 for call in calls)


def test_destructive_vm_operations_require_idempotency_key(client, headers, monkeypatch):
    from app.providers.proxmox import ProxmoxProvider

    provider = resources(client, headers)
    monkeypatch.setattr(ProxmoxProvider, 'create_snapshot', lambda *args, **kwargs: 'UPID:test')
    base = f"/api/v1/providers/{provider['id']}/vms/pve01/101"

    response = client.post(base + '/snapshots', headers=headers, json={'snapname': 'baseline'})
    assert response.status_code == 400
    assert 'Idempotency-Key' in response.text


def test_vm_rbac_separates_read_from_power(client, headers, monkeypatch):
    from app.providers.proxmox import ProxmoxProvider

    provider = resources(client, headers)
    _, viewer = new_user(client, headers, username='vm-reader', permissions=['vms.read'])
    monkeypatch.setattr(ProxmoxProvider, 'vm_status', lambda self, node, vmid: {'vmid': vmid, 'status': 'stopped'})
    monkeypatch.setattr(ProxmoxProvider, 'vm_power', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('must not execute')))

    base = f"/api/v1/providers/{provider['id']}/vms/pve01/101"
    assert client.get(base + '/status', headers=viewer).status_code == 200
    assert client.post(base + '/power', headers=viewer, json={'action': 'start'}).status_code == 403


def test_guest_addresses_prefer_primary_proxmox_nic(monkeypatch):
    from app.providers.proxmox import ProxmoxProvider

    provider = object.__new__(ProxmoxProvider)

    def fake_get(path):
        if path.endswith('/config'):
            return {
                'net0': 'virtio=AA:BB:CC:DD:EE:01,bridge=vmbr20',
                'net1': 'virtio=AA:BB:CC:DD:EE:02,bridge=vmbr30',
            }
        if path.endswith('/agent/network-get-interfaces'):
            return {
                'result': [
                    {
                        'name': 'docker0',
                        'hardware-address': '02:42:ac:11:00:01',
                        'ip-addresses': [{'ip-address': '172.17.0.1'}],
                    },
                    {
                        'name': 'eth1',
                        'hardware-address': 'aa:bb:cc:dd:ee:02',
                        'ip-addresses': [{'ip-address': '10.30.0.25'}],
                    },
                    {
                        'name': 'eth0',
                        'hardware-address': 'aa:bb:cc:dd:ee:01',
                        'ip-addresses': [
                            {'ip-address': 'fe80::1'},
                            {'ip-address': '192.0.2.25'},
                        ],
                    },
                ],
            }
        raise AssertionError('unexpected Proxmox path: ' + path)

    monkeypatch.setattr(provider, '_get', fake_get)
    assert provider.guest_addresses('pve01', 101) == ['192.0.2.25']


def test_terraform_managed_vm_blocks_raw_mutations(client, headers, monkeypatch):
    from app.providers.proxmox import ProxmoxProvider

    provider = resources(client, headers)
    with session() as db:
        db.add(ManagedVM(
            provider_id=provider['id'],
            node='pve01',
            vm_id=303,
            name='terraform-vm',
            management_mode='terraform',
            lifecycle_status='active',
            created_by=1,
        ))
        db.commit()

    monkeypatch.setattr(
        ProxmoxProvider,
        'delete_vm',
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('raw delete must not run')),
    )
    monkeypatch.setattr(
        ProxmoxProvider,
        'migrate_vm',
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('raw migrate must not run')),
    )

    base = f"/api/v1/providers/{provider['id']}/vms/pve01/303"
    deleted = client.delete(base, headers=idem(headers))
    assert deleted.status_code == 409
    migrated = client.post(base + '/migrate', headers=idem(headers), json={
        'target': 'pve02', 'online': True, 'with_local_disks': True,
    })
    assert migrated.status_code == 409


def test_proxmox_task_reconciliation_registers_clone(client, headers, monkeypatch):
    from app.providers.proxmox import ProxmoxProvider

    provider = resources(client, headers)
    monkeypatch.setattr(
        ProxmoxProvider,
        'task_status',
        lambda self, node, upid: {'status': 'stopped', 'exitstatus': 'OK'},
    )
    monkeypatch.setattr(
        ProxmoxProvider,
        'vm_status',
        lambda self, node, vmid: {'vmid': vmid, 'node': node, 'name': 'clone-vm'},
    )

    track_proxmox_task(
        provider_id=provider['id'],
        node='pve01',
        upid='UPID:clone-reconcile',
        action='clone',
        created_by=1,
        vm_id=101,
        target_node='pve02',
        target_vm_id=404,
        name='clone-vm',
    )
    result = reconcile_proxmox_tasks_once()
    assert result['completed'] >= 1

    with session() as db:
        row = db.query(ManagedVM).filter(
            ManagedVM.provider_id == provider['id'],
            ManagedVM.vm_id == 404,
        ).one()
        assert row.node == 'pve02'
        assert row.name == 'clone-vm'
        assert row.management_mode == 'external'



def test_active_quota_blocks_legacy_capacity_mutations(client, headers, monkeypatch):
    from app.providers.proxmox import ProxmoxProvider

    provider = resources(client, headers)
    with session() as db:
        scope = Scope(DEFAULT_TENANT_ID, DEFAULT_PROJECT_ID)
        set_project_limit(db, scope, 'vm_count', 10)
        set_project_limit(db, scope, 'disk_gib', 1000)
        set_project_limit(db, scope, 'memory_mb', 65536)
        db.commit()

    def forbidden(*args, **kwargs):
        raise AssertionError('legacy provider mutation must be blocked before provider execution')

    monkeypatch.setattr(ProxmoxProvider, 'clone_vm', forbidden)
    monkeypatch.setattr(ProxmoxProvider, 'resize_disk', forbidden)
    monkeypatch.setattr(ProxmoxProvider, 'update_vm_config', forbidden)
    monkeypatch.setattr(ProxmoxProvider, 'delete_vm', forbidden)

    base = f"/api/v1/providers/{provider['id']}/vms/pve01/101"

    clone = client.post(base + '/clone', headers=idem(headers), json={
        'new_vm_id': 202, 'name': 'vm02', 'full': True,
    })
    assert clone.status_code == 409
    assert clone.json()['detail']['code'] == 'QUOTA_GOVERNED_ACTION_REQUIRED'

    disk = client.put(base + '/disk', headers=idem(headers), json={'disk': 'scsi0', 'grow_gib': 20})
    assert disk.status_code == 409
    assert disk.json()['detail']['code'] == 'QUOTA_GOVERNED_ACTION_REQUIRED'

    compute = client.put(base + '/config', headers=idem(headers), json={'memory': 8192})
    assert compute.status_code == 409
    assert compute.json()['detail']['code'] == 'QUOTA_GOVERNED_ACTION_REQUIRED'

    deleted = client.delete(base, headers=idem(headers))
    assert deleted.status_code == 409
    assert deleted.json()['detail']['code'] == 'QUOTA_GOVERNED_ACTION_REQUIRED'



def test_guest_agent_ping_uses_http_success_not_data_payload(monkeypatch):
    from app.providers.proxmox import ProxmoxProvider

    provider = object.__new__(ProxmoxProvider)
    observed = []

    def request(method, path, **kwargs):
        observed.append((method, path, kwargs))
        return type('Response', (), {'status_code': 200})()

    monkeypatch.setattr(provider, '_request', request)

    assert provider.guest_agent_ready('pve01', 114) is True
    assert observed == [(
        'POST',
        '/nodes/pve01/qemu/114/agent/ping',
        {'return_response': True, 'accepted_statuses': {500}},
    )]


def test_guest_agent_ping_treats_proxmox_500_as_not_ready(monkeypatch):
    from app.providers.proxmox import ProxmoxProvider

    provider = object.__new__(ProxmoxProvider)
    monkeypatch.setattr(
        provider,
        '_request',
        lambda *_args, **_kwargs: type('Response', (), {'status_code': 500})(),
    )

    assert provider.guest_agent_ready('pve01', 114) is False
