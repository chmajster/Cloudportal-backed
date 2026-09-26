import json
from types import SimpleNamespace

from app.database import session
from app.jobs import proxmox_destroy
from app.models import Credential, Deployment, ManagedVM, Provider, User
from app.terraform.state import persist_state


class FakeContext:
    def __init__(self, deployment, credential):
        self.deployment = deployment
        self.credential = credential
        self.logs = []
        self.stages = []
        self.checks = 0

    def log(self, message):
        self.logs.append(message)

    def stage(self, value):
        self.stages.append(value)

    def check(self):
        self.checks += 1


def make_deployment(client, headers, *, with_inventory=True, vm_id=120):
    credential = client.post('/api/v1/credentials', headers=headers, json={
        'name': 'PVE destroy guard',
        'type': 'proxmox',
        'endpoint': 'https://pve.example.com:8006',
        'username': 'root@pam',
        'secrets': {
            'token_id': 'root@pam!terraform',
            'token_secret': 'destroy-guard-secret',
        },
    })
    assert credential.status_code == 201, credential.text
    provider = client.post('/api/v1/providers', headers=headers, json={
        'name': 'PVE destroy guard',
        'type': 'proxmox',
        'credentials_id': credential.json()['id'],
    })
    assert provider.status_code == 201, provider.text

    with session() as db:
        user_id = db.query(User.id).order_by(User.id).first()[0]
        row = Deployment(
            name='destroy-guard-vm',
            provider_id=provider.json()['id'],
            provider='proxmox',
            template='proxmox-vm',
            credentials_id=credential.json()['id'],
            variables={'node': 'pve01'},
            workflow={},
            status='successful',
            created_by=user_id,
        )
        db.add(row)
        db.flush()
        if with_inventory:
            db.add(ManagedVM(
                provider_id=provider.json()['id'],
                deployment_id=row.id,
                node='pve01',
                vm_id=vm_id,
                name='destroy-guard-vm',
                management_mode='terraform',
                lifecycle_status='active',
                created_by=user_id,
            ))
        db.commit()
        deployment_id = row.id

    with session() as db:
        deployment = db.get(Deployment, deployment_id)
        credential_row = db.get(Credential, credential.json()['id'])
        db.expunge(deployment)
        db.expunge(credential_row)
    return deployment, credential_row


def test_running_proxmox_vm_is_hard_stopped_before_destroy(client, headers, monkeypatch):
    deployment, credential = make_deployment(client, headers, with_inventory=True, vm_id=120)
    calls = []
    states = iter(['running', 'stopped'])

    class Provider:
        def vm_status(self, node, vm_id):
            calls.append(('status', node, vm_id))
            return {'status': next(states)}

        def vm_power(self, node, vm_id, action):
            calls.append(('power', node, vm_id, action))
            return 'UPID:hard-stop'

        def task_status(self, node, task):
            calls.append(('task', node, task))
            return {'status': 'stopped', 'exitstatus': 'OK'}

    monkeypatch.setattr(proxmox_destroy, 'provider_for', lambda _credential: Provider())
    monkeypatch.setattr(proxmox_destroy.time, 'sleep', lambda _seconds: None)
    context = FakeContext(deployment, credential)

    assert proxmox_destroy.force_stop_before_destroy(context) is True
    assert ('power', 'pve01', 120, 'stop') in calls
    assert context.stages == ['terraform.destroy.force_stop']
    assert any('QEMU Guest Agent bypassed' in message for message in context.logs)
    assert any('terraform.destroy.force_stop.completed' in message for message in context.logs)


def test_stopped_proxmox_vm_is_not_powered_off_again(client, headers, monkeypatch):
    deployment, credential = make_deployment(client, headers, with_inventory=True, vm_id=121)

    class Provider:
        def vm_status(self, node, vm_id):
            return {'status': 'stopped'}

        def vm_power(self, *_args):
            raise AssertionError('vm_power must not run for an already stopped VM')

    monkeypatch.setattr(proxmox_destroy, 'provider_for', lambda _credential: Provider())
    context = FakeContext(deployment, credential)

    assert proxmox_destroy.force_stop_before_destroy(context) is False
    assert context.stages == []
    assert any('already_stopped' in message for message in context.logs)


def test_destroy_guard_recovers_vmid_from_persisted_terraform_state(client, headers, monkeypatch, tmp_path):
    deployment, credential = make_deployment(client, headers, with_inventory=False)
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    (workspace / 'terraform.tfstate').write_text(json.dumps({
        'version': 4,
        'outputs': {'vm_id': {'value': 122, 'type': 'number'}},
    }))
    assert persist_state(deployment.id, workspace)

    calls = []

    class Provider:
        def vm_status(self, node, vm_id):
            calls.append(('status', node, vm_id))
            return {'status': 'stopped'}

        def vm_power(self, *_args):
            raise AssertionError('vm_power must not run for stopped VM')

    monkeypatch.setattr(proxmox_destroy, 'provider_for', lambda _credential: Provider())
    context = FakeContext(deployment, credential)

    assert proxmox_destroy.force_stop_before_destroy(context) is False
    assert calls == [('status', 'pve01', 122)]


def test_worker_calls_destroy_guard_for_regular_and_rollback_destroy():
    source = open('app/jobs/worker.py', encoding='utf-8').read()
    assert "if job.operation == 'terraform.destroy':\n                    force_stop_before_destroy(context)" in source
    assert "if rollback_type == 'terraform_destroy':\n                force_stop_before_destroy(context, timeout=rollback_timeout)" in source
