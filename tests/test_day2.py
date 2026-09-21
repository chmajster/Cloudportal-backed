"""Day-2 integration: real API/database; provider calls replaced by deterministic fakes."""
from datetime import timedelta
from types import SimpleNamespace
import uuid
import pytest
from sqlalchemy import select, func
from conftest import new_user
from app.database import session
from app.day2 import service, worker
from app.day2.diff import configuration_diff, redact
from app.day2.errors import Day2Failure, failure
from app.day2.models import Day2ActionRequest, Day2ResourceLock
from app.day2.providers import ProxmoxDay2Adapter, Day2Target, _size_gib
from app.day2.registry import all_actions, get_action
from app.models import Credential, Provider, ManagedVM, Job, Token, User, Setting, now


def key(headers):
    return {**headers, 'Idempotency-Key': str(uuid.uuid4())}


class FakeAdapter:
    def __init__(self):
        self.calls = []
        self.fail_wait = False
    def capabilities(self, target):
        return {'actions': [row.id for row in all_actions()]}
    def snapshot_state(self, target):
        return {'power_state': 'stopped', 'name': target.name, 'node': target.node, 'cpu_cores': 2}
    def configuration(self, target):
        return {'cores': 2, 'sockets': 1, 'memory': 2048}
    def execute(self, target, action, params):
        self.calls.append((target.resource_id, action, params))
        return 'UPID:pve:task'
    def wait_task(self, target, task, check, timeout):
        check()
        if self.fail_wait:
            raise failure('TIMEOUT')
        return {'provider_task_id': task, 'provider_task_status': 'OK'}
    def cancel_task(self, target, task):
        return False


@pytest.fixture
def resource(system, monkeypatch):
    client, headers, _ = system
    fake = FakeAdapter()
    monkeypatch.setattr(service, 'day2_provider', lambda credential: fake)
    monkeypatch.setattr(worker, 'day2_provider', lambda credential: fake)
    with session() as db:
        owner = db.scalar(select(User).where(User.username == 'admin'))
        cred = Credential(name='day2-test', type='proxmox', encrypted_secret=b'not-used')
        db.add(cred); db.flush()
        provider = Provider(name='day2-test', type='proxmox', credentials_id=cred.id)
        db.add(provider); db.flush()
        vm = ManagedVM(provider_id=provider.id, node='pve', vm_id=700, name='test-vm', created_by=owner.id)
        db.add(vm); db.commit()
        vm_id = vm.id
    return client, headers, vm_id, fake


def submit(resource, headers=None, action='power_on', params=None):
    client, admin, vm, _ = resource
    return client.post(f'/api/v1/resources/{vm}/actions/{action}', headers=headers or key(admin),
                       json={'parameters': params or {}, 'reason': 'test operation'})


def test_create_execute_replay_and_release_lock(resource):
    client, headers, vm, fake = resource
    h = key(headers)
    created = submit(resource, h)
    assert created.status_code == 202, created.text
    data = created.json()
    assert submit(resource, h).json() == data
    worker.execute(data['job_id']); worker.execute(data['job_id'])
    assert len(fake.calls) == 1
    with session() as db:
        assert db.get(Job, data['job_id']).status == 'successful'
        row = db.get(Day2ActionRequest, data['action_request_id'])
        assert row.status == 'SUCCEEDED' and not row.result['reconciliation_required']
        assert db.get(Day2ResourceLock, vm) is None


def test_worker_revocation_fails_before_provider_and_releases_lock(resource):
    data = submit(resource).json()
    with session() as db:
        token = db.get(Token, db.get(Job, data['job_id']).token_id)
        token.revoked_at = now(); db.commit()
    worker.execute(data['job_id'])
    assert resource[3].calls == []
    with session() as db:
        row = db.get(Day2ActionRequest, data['action_request_id'])
        assert row.status == 'FAILED' and row.error_code == 'PERMISSION_DENIED'
        assert db.get(Day2ResourceLock, resource[2]) is None


def test_worker_reauthorizes_resource_ownership(resource):
    client, admin, vm, fake = resource
    user, h = new_user(client, admin, 'day2-owner', ['day2.view', 'day2.power'])
    with session() as db:
        db.get(ManagedVM, vm).created_by = user['id']; db.commit()
    created = submit(resource, key(h)); assert created.status_code == 202, created.text
    with session() as db:
        owner = db.scalar(select(User).where(User.username == 'admin'))
        db.get(ManagedVM, vm).created_by = owner.id; db.commit()
    worker.execute(created.json()['job_id'])
    assert fake.calls == []
    with session() as db:
        assert db.get(Day2ActionRequest, created.json()['action_request_id']).error_code == 'PERMISSION_DENIED'


def test_idempotent_replay_rechecks_action_permission(resource):
    client, admin, vm, _ = resource
    user, h = new_user(client, admin, 'day2-replay', ['day2.view', 'day2.power'])
    with session() as db:
        db.get(ManagedVM, vm).created_by = user['id']; db.commit()
    h = key(h); response = submit(resource, h); assert response.status_code == 202, response.text
    with session() as db:
        userrow = db.get(User, user['id'])
        role = userrow.roles[0]; role.permissions = [p for p in role.permissions if p.name == 'day2.view']; db.commit()
    denied = submit(resource, h)
    assert denied.status_code == 403, denied.text


def test_uncertain_provider_timeout_retains_lock_even_after_expiry(resource):
    resource[3].fail_wait = True
    data = submit(resource).json(); worker.execute(data['job_id'])
    with session() as db:
        row = db.get(Day2ActionRequest, data['action_request_id'])
        assert row.status == 'FAILED' and row.result['reconciliation_required']
        lock = db.get(Day2ResourceLock, resource[2]); assert lock is not None
        lock.expires_at = now() - timedelta(hours=1); db.commit()
    assert submit(resource).status_code == 409


def test_active_expired_lock_cannot_be_stolen(resource):
    created = submit(resource); assert created.status_code == 202, created.text
    with session() as db:
        db.get(Day2ResourceLock, resource[2]).expires_at = now() - timedelta(hours=1); db.commit()
    assert submit(resource).status_code == 409
    assert resource[3].calls == []


def test_bulk_conflict_rolls_back_failed_child(resource):
    client, headers, vm, _ = resource
    created = submit(resource); assert created.status_code == 202, created.text
    bulk = client.post('/api/v1/day2-actions/bulk', headers=key(headers), json={'action': 'power_on', 'resource_ids': [vm]})
    assert bulk.status_code == 202, bulk.text
    assert len(bulk.json()['errors']) == 1 and bulk.json()['children'] == []
    with session() as db:
        assert db.scalar(select(func.count()).select_from(Day2ActionRequest)) == 1
        assert db.scalar(select(func.count()).select_from(Job).where(Job.operation.like('day2.%'))) == 1


def test_approval_and_cancel_state_machine(resource):
    client, admin, vm, fake = resource
    user, headers = new_user(client, admin, 'day2-approval', ['day2.view', 'day2.power', 'day2.cancel'])
    with session() as db:
        db.get(ManagedVM, vm).created_by = user['id']
        db.add(Setting(key='day2', value={'default_approval_policy': 'all'})); db.commit()
    response = submit(resource, key(headers)); assert response.status_code == 202, response.text
    row = response.json(); assert row['approval_required']
    worker.execute(row['job_id']); assert fake.calls == []
    approved = client.post('/api/v1/day2-actions/' + row['action_request_id'] + '/approve', headers=admin)
    assert approved.status_code == 200, approved.text
    assert client.post('/api/v1/day2-actions/' + row['action_request_id'] + '/approve', headers=admin).status_code == 409
    cancelled = client.post('/api/v1/day2-actions/' + row['action_request_id'] + '/cancel', headers=headers)
    assert cancelled.status_code == 200, cancelled.text
    worker.execute(row['job_id']); assert fake.calls == []
    with session() as db:
        assert db.get(Day2ResourceLock, vm) is None
        assert db.get(Job, row['job_id']).status == 'cancelled'


def test_dispatcher_terminal_outcome_updates_action(resource):
    from app.day2.reconciliation import reconcile_finished_jobs
    data = submit(resource).json()
    with session() as db:
        job = db.get(Job, data['job_id']); job.status = 'cancelled'; job.error = 'Cancelled'; db.flush()
        reconcile_finished_jobs(db); db.commit()
        assert db.get(Day2ActionRequest, data['action_request_id']).status == 'CANCELLED'
        assert db.get(Day2ResourceLock, resource[2]) is None


def test_foreign_resource_and_schema_rejected(resource):
    client, admin, vm, _ = resource
    _, h = new_user(client, admin, 'day2-foreign', ['day2.view', 'day2.power'])
    assert submit(resource, key(h)).status_code == 404
    assert submit(resource, params={'command': 'arbitrary shell'}).status_code == 422
    assert client.put('/api/v1/day2/settings', headers=admin, json={'default_approval_policy': 'invalid'}).status_code == 422


@pytest.mark.parametrize(('action','params'), [
    ('add_disk', {'device':'scsi0','size_gib':20,'storage':'local-lvm'}),
    ('resize_disk', {'device':'virtio10','new_size_gib':30}),
    ('detach_disk', {'device':'sata1'}),
    ('delete_disk', {'device':'unused0','confirmation':'vm'}),
    ('add_nic', {'device':'net0','bridge':'vmbr0'}),
    ('edit_nic', {'device':'net1','bridge':'vmbr0'}),
    ('move_storage', {'device':'scsi0','target_storage':'local-lvm'}),
])
def test_real_proxmox_device_names_validate(action, params):
    service._validate_schema(get_action(action).schema, params)


def test_partial_diff_and_recursive_redaction():
    assert configuration_diff({'cpu': 2, 'memory': 4096}, {'cpu': 4}) == {'cpu': {'before': 2, 'after': 4}}
    assert redact({'nested': [{'password': 'secret'}]}) == {'nested': [{'password': '[REDACTED]'}]}


def test_adapter_disks_nics_cloudinit_and_preserved_network_fields():
    adapter = object.__new__(ProxmoxDay2Adapter)
    config = {'scsi0': 'local:vm-1-disk-0,size=1.5T', 'net0': 'e1000=AA:BB:CC:DD:EE:FF,bridge=vmbr0,tag=20,firewall=1,link_down=1'}
    adapter.configuration = lambda target: config
    adapter.guest_addresses = lambda target: ['192.0.2.1']
    assert _size_gib(config['scsi0']) == 1536
    assert adapter.disks(None)[0]['bus'] == 'scsi'
    assert adapter.nics(None)[0]['mac'] == 'AA:BB:CC:DD:EE:FF'
    value = adapter._network_value({'bridge':'vmbr2'}, config['net0'])
    assert value == 'e1000=AA:BB:CC:DD:EE:FF,bridge=vmbr2,tag=20,firewall=1,link_down=1'
    assert adapter._cloud_init_values({'ssh_public_keys':['a','b']})['sshkeys'] == 'a\nb'


def test_adapter_unknown_task_result_is_not_success():
    adapter = object.__new__(ProxmoxDay2Adapter)
    adapter.provider = SimpleNamespace(task_status=lambda *args: {'status':'stopped'})
    target = Day2Target('id',1,'proxmox','vm','vm',None,'EXTERNAL',node='pve',vm_id=1)
    with pytest.raises(Day2Failure):
        adapter.wait_task(target, 'UPID:task', lambda: None)


def test_existing_disk_slot_not_overwritten():
    adapter = object.__new__(ProxmoxDay2Adapter)
    adapter.configuration = lambda target: {'scsi0':'existing'}
    target = Day2Target('id',1,'proxmox','vm','vm',None,'EXTERNAL',node='pve',vm_id=1)
    with pytest.raises(Day2Failure):
        adapter.execute(target, 'add_disk', {'device':'scsi0','size_gib':10,'storage':'local'})


def test_worker_rechecks_approval_policy_changes(resource):
    client, admin, vm, fake = resource
    user, headers = new_user(client, admin, 'day2-policy', ['day2.view', 'day2.power'])
    with session() as db:
        db.get(ManagedVM, vm).created_by = user['id']; db.commit()
    response = submit(resource, key(headers))
    assert response.status_code == 202, response.text
    data = response.json(); assert not data['approval_required']
    with session() as db:
        db.add(Setting(key='day2', value={'default_approval_policy': 'all'})); db.commit()
    worker.execute(data['job_id'])
    assert fake.calls == []
    with session() as db:
        assert db.get(Day2ActionRequest, data['action_request_id']).error_code == 'APPROVAL_REQUIRED'
        assert db.get(Day2ResourceLock, vm) is None


def test_postgres_simultaneous_resource_claim_has_one_winner(resource):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from app.database import engine
    if engine().dialect.name != 'postgresql':
        pytest.skip('PostgreSQL resource lock contention test')
    vm = resource[2]
    with session() as db:
        owner = db.get(ManagedVM, vm).created_by
    barrier = Barrier(2)
    def claim(_):
        with session() as db:
            row = Day2ActionRequest(resource_id=vm, action='power_on', requested_by=owner,
                                    status='QUEUED', request_id=str(uuid.uuid4()))
            db.add(row); db.flush()
            barrier.wait(timeout=10)
            try:
                service.acquire_resource_lock(db, vm, row.id, 600)
                db.commit(); return 'claimed'
            except Day2Failure as exc:
                db.rollback(); return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(claim, range(2))) == ['RESOURCE_LOCKED', 'claimed']
    with session() as db:
        assert db.scalar(select(func.count()).select_from(Day2ActionRequest)) == 1
        assert db.scalar(select(func.count()).select_from(Day2ResourceLock)) == 1
