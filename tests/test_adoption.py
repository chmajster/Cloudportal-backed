import uuid
from types import SimpleNamespace

from app.database import session
from app.executors.terraform import TerraformExecutor
from app.jobs.worker import execute
from app.models import Credential, Deployment, Job, ManagedVM


def idem(headers):
    return {**headers, 'Idempotency-Key': str(uuid.uuid4())}


def test_proxmox_vm_adoption_is_import_and_plan_only(client, headers, monkeypatch):
    from app.providers.proxmox import ProxmoxProvider

    credential = client.post('/api/v1/credentials', headers=headers, json={
        'name': 'Adoption PVE',
        'type': 'proxmox',
        'endpoint': 'https://pve.example.com:8006',
        'username': 'root@pam',
        'secrets': {'token_id': 'root@pam!adopt', 'token_secret': 'private-adoption-token'},
    }).json()
    provider = client.post('/api/v1/providers', headers=headers, json={
        'name': 'Adoption PVE',
        'type': 'proxmox',
        'credentials_id': credential['id'],
    }).json()

    monkeypatch.setattr(ProxmoxProvider, 'discover', lambda self, resource, node=None: [{
        'vmid': 777,
        'name': 'legacy-vm',
        'node': 'pve01',
        'status': 'running',
        'type': 'qemu',
        'template': 0,
    }] if resource == 'vms' else [])
    monkeypatch.setattr(ProxmoxProvider, 'vm_config', lambda self, node, vmid: {
        'name': 'legacy-vm',
        'cores': 4,
        'memory': 8192,
        'scsi0': 'local-lvm:vm-777-disk-0,size=64G',
        'net0': 'virtio=00:11:22:33:44:55,bridge=vmbr0,tag=20',
        'onboot': 1,
        'agent': '1',
    })

    imported = client.post('/api/v1/inventory/vms/import', headers=idem(headers), json={
        'provider_id': provider['id'],
        'vm_id': 777,
    })
    assert imported.status_code == 201, imported.text
    inventory = imported.json()
    assert inventory['management_mode'] == 'external'

    preview = client.get(
        f"/api/v1/inventory/vms/{inventory['id']}/adoption-preview",
        headers=headers,
    )
    assert preview.status_code == 200, preview.text
    suggestion = preview.json()['suggested_variables']
    assert suggestion['node'] == 'pve01'
    assert suggestion['cpu'] == 4
    assert suggestion['memory'] == 8192
    assert suggestion['disk'] == 64
    assert suggestion['storage'] == 'local-lvm'
    assert suggestion['network'] == 'vmbr0'
    assert suggestion['vlan_id'] == 20
    assert preview.json()['plan_only'] is True
    assert preview.json()['template']['importable'] is True

    adopted = client.post(
        f"/api/v1/inventory/vms/{inventory['id']}/adopt",
        headers=idem(headers),
        json={'template': 'proxmox-vm', 'variables': suggestion, 'executor': 'terraform'},
    )
    assert adopted.status_code == 202, adopted.text
    deployment = adopted.json()
    assert deployment['job']['operation'] == 'terraform.import'

    with session() as db:
        row = db.get(ManagedVM, inventory['id'])
        assert row.management_mode == 'external'
        assert row.deployment_id is None
        dep = db.get(Deployment, deployment['id'])
        cred = db.get(Credential, dep.credentials_id)
        job = db.get(Job, deployment['job']['id'])

    commands = []
    stages = []

    def fake_process(argv, cwd, env, context, secrets=()):
        commands.append(list(argv))
        if len(argv) > 1 and argv[1] == 'import':
            (cwd / 'terraform.tfstate').write_text('{"version":4,"resources":[]}')

    monkeypatch.setattr('app.executors.terraform.run_process', fake_process)
    context = SimpleNamespace(
        deployment=dep,
        credential=cred,
        job=job,
        stage=lambda value: stages.append(value),
        check=lambda: None,
        log=lambda value: None,
    )
    workspace = TerraformExecutor().execute('terraform.import', context)
    verbs = [argv[1] for argv in commands if len(argv) > 1]
    assert verbs == ['init', 'import', 'plan']
    assert 'apply' not in verbs
    assert 'terraform.import' in stages
    assert (workspace / 'terraform.tfstate').exists()

    monkeypatch.setattr(TerraformExecutor, 'execute', lambda *args: workspace)
    execute(deployment['job']['id'])

    with session() as db:
        row = db.get(ManagedVM, inventory['id'])
        dep = db.get(Deployment, deployment['id'])
        job = db.get(Job, deployment['job']['id'])
        assert job.status == 'successful'
        assert dep.status == 'imported'
        assert row.management_mode == 'terraform'
        assert row.deployment_id == dep.id

    blocked = client.delete(f"/api/v1/inventory/vms/{inventory['id']}", headers=headers)
    assert blocked.status_code == 409


    apply = client.post('/api/v1/jobs', headers=idem(headers), json={
        'operation': 'terraform.apply',
        'deployment_id': deployment['id'],
    })
    assert apply.status_code == 409
    assert 'plan-only' in apply.text

    schedule = client.post('/api/v1/schedules', headers=headers, json={
        'name': 'invalid adopted apply',
        'deployment_id': deployment['id'],
        'operation': 'terraform.apply',
        'next_run_at': '2099-01-01T00:00:00Z',
    })
    assert schedule.status_code == 409
    assert 'plan-only' in schedule.text
