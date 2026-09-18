import uuid

from app.database import session
from app.executors.base import ExecutionFailed
from app.executors.terraform import TerraformExecutor
from app.jobs.worker import execute
from app.models import Deployment, Job
from conftest import new_user


def idem(headers):
    return {**headers, 'Idempotency-Key': str(uuid.uuid4())}


def infrastructure(client, headers):
    credential = client.post('/api/v1/credentials', headers=headers, json={
        'name': 'Governance PVE',
        'type': 'proxmox',
        'endpoint': 'https://pve.example.com:8006',
        'username': 'root@pam',
        'secrets': {'token_id': 'root@pam!governance', 'token_secret': 'private-governance-token'},
    }).json()
    provider = client.post('/api/v1/providers', headers=headers, json={
        'name': 'Governance PVE',
        'type': 'proxmox',
        'credentials_id': credential['id'],
    }).json()
    return credential, provider


def blueprint_payload(credential, provider, **policy):
    return {
        'slug': 'governed-vm',
        'name': 'Governed VM',
        **policy,
        'deployment': {
            'name': 'governed-vm',
            'provider_id': provider['id'],
            'credentials_id': credential['id'],
            'variables': {
                'name': 'governed-vm',
                'node': 'pve01',
                'template_id': 9000,
                'storage': 'local-lvm',
            },
        },
        'workflow': [
            {'id': 'clone', 'type': 'clone_vm'},
            {'id': 'apply', 'type': 'terraform_apply', 'depends_on': ['clone']},
        ],
    }


def test_blueprint_approval_permission_is_enforced(client, headers):
    credential, provider = infrastructure(client, headers)
    created = client.post('/api/v1/blueprints', headers=headers, json=blueprint_payload(
        credential, provider, requires_approval=True,
    ))
    assert created.status_code == 201, created.text
    blueprint = created.json()
    assert blueprint['requires_approval'] is True

    permissions = [
        'blueprints.read', 'blueprints.execute',
        'deployments.create', 'jobs.execute', 'terraform.execute',
    ]
    _, operator = new_user(client, headers, username='approval-operator', permissions=permissions)

    denied = client.post(
        f"/api/v1/blueprints/{blueprint['id']}/execute",
        headers=idem(operator),
        json={},
    )
    assert denied.status_code == 403
    assert 'blueprints.approve' in denied.text

    approved = client.post(
        f"/api/v1/blueprints/{blueprint['id']}/execute",
        headers=idem(headers),
        json={},
    )
    assert approved.status_code == 202, approved.text
    assert approved.json()['job']['status'] == 'queued'


def test_failed_blueprint_apply_queues_explicit_recovery_destroy(client, headers, monkeypatch, tmp_path):
    credential, provider = infrastructure(client, headers)
    created = client.post('/api/v1/blueprints', headers=headers, json=blueprint_payload(
        credential, provider, recovery_policy='destroy_on_failure',
    ))
    assert created.status_code == 201, created.text

    launched = client.post(
        f"/api/v1/blueprints/{created.json()['id']}/execute",
        headers=idem(headers),
        json={},
    )
    assert launched.status_code == 202, launched.text
    body = launched.json()
    original_id = body['job']['id']

    def fail_apply(*args):
        raise ExecutionFailed('controlled apply failure')

    monkeypatch.setattr(TerraformExecutor, 'execute', fail_apply)
    execute(original_id)

    with session() as db:
        original = db.get(Job, original_id)
        deployment = db.get(Deployment, body['id'])
        recovery = db.query(Job).filter(
            Job.deployment_id == deployment.id,
            Job.source == 'Recovery',
        ).one()
        assert original.status == 'failed'
        assert recovery.operation == 'terraform.destroy'
        assert recovery.payload['recovery_of'] == original.id
        assert deployment.active_job_id == recovery.id
        assert deployment.status == 'recovery_queued'
        recovery_id = recovery.id

    monkeypatch.setattr(TerraformExecutor, 'execute', lambda *args: tmp_path)
    execute(recovery_id)

    with session() as db:
        recovery = db.get(Job, recovery_id)
        deployment = db.get(Deployment, body['id'])
        assert recovery.status == 'successful'
        assert deployment.status == 'destroyed'
        assert deployment.destroyed_at is not None
