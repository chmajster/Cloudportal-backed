import uuid
from types import SimpleNamespace

from app.database import session
from app.executors.base import ExecutionFailed
from app.executors.terraform import TerraformExecutor
from app.jobs.worker import execute
from app.models import Deployment, Job
from conftest import new_user


def idem(headers):
    return {**headers, 'Idempotency-Key': str(uuid.uuid4())}


def manual_approval(client, headers):
    response = client.put(
        '/api/v1/settings/blueprints',
        headers=headers,
        json={'auto_approve_for_executors': False},
    )
    assert response.status_code == 200, response.text


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
    manual_approval(client, headers)
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

    requested = client.post(
        f"/api/v1/blueprints/{blueprint['id']}/execute",
        headers=idem(operator),
        json={},
    )
    assert requested.status_code == 202, requested.text
    assert requested.json()['job']['status'] == 'waiting_approval'
    assert requested.json()['status'] == 'waiting_approval'

    calls = []
    original_execute = TerraformExecutor.execute
    TerraformExecutor.execute = lambda *args: calls.append('called')
    try:
        execute(requested.json()['job']['id'])
    finally:
        TerraformExecutor.execute = original_execute
    assert calls == []
    still_waiting = client.get(
        f"/api/v1/jobs/{requested.json()['job']['id']}",
        headers=headers,
    )
    assert still_waiting.json()['status'] == 'waiting_approval'

    approved = client.post(
        f"/api/v1/jobs/{requested.json()['job']['id']}/approve",
        headers=headers,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()['status'] == 'queued'
    current = client.get(
        f"/api/v1/deployments/{requested.json()['id']}",
        headers=headers,
    )
    assert current.status_code == 200
    assert current.json()['status'] == 'queued'


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


def test_failed_workflow_rollback_destroy_marks_deployment_destroyed(client, headers, monkeypatch, tmp_path):
    credential, provider = infrastructure(client, headers)
    payload = blueprint_payload(credential, provider)
    payload['slug'] = 'rollback-destroy-vm'
    payload['name'] = 'Rollback Destroy VM'
    payload['workflow'] = [
        {'id': 'rollback_destroy', 'type': 'terraform_destroy'},
        {'id': 'apply', 'type': 'terraform_apply'},
        {
            'id': 'health',
            'type': 'health_check',
            'depends_on': ['apply'],
            'rollback': 'rollback_destroy',
        },
    ]
    created = client.post('/api/v1/blueprints', headers=headers, json=payload)
    assert created.status_code == 201, created.text

    launched = client.post(
        f"/api/v1/blueprints/{created.json()['id']}/execute",
        headers=idem(headers),
        json={},
    )
    assert launched.status_code == 202, launched.text

    workspace = tmp_path / 'rollback-workspace'
    workspace.mkdir()
    (workspace / 'terraform.tfstate').write_text(
        '{"outputs":{"vm_id":{"value":777}}}'
    )
    operations = []
    monkeypatch.setattr(
        TerraformExecutor,
        'execute',
        lambda self, operation, context: operations.append(operation) or workspace,
    )
    monkeypatch.setattr(
        'app.jobs.worker.provider_for',
        lambda credential: SimpleNamespace(
            vm_status=lambda node, vm_id: {'status': 'stopped'},
        ),
    )

    execute(launched.json()['job']['id'])

    with session() as db:
        job = db.get(Job, launched.json()['job']['id'])
        deployment = db.get(Deployment, launched.json()['id'])
        assert job.status == 'failed'
        assert deployment.status == 'destroyed'
        assert deployment.active_job_id is None
        assert deployment.destroyed_at is not None
    assert operations == ['terraform.apply', 'terraform.destroy']

def test_blueprint_retry_requires_fresh_approval(client, headers):
    manual_approval(client, headers)
    credential, provider = infrastructure(client, headers)
    created = client.post('/api/v1/blueprints', headers=headers, json=blueprint_payload(
        credential, provider, slug='retry-approval', name='Retry approval', requires_approval=True,
    ))
    assert created.status_code == 201, created.text

    launched = client.post(
        f"/api/v1/blueprints/{created.json()['id']}/execute",
        headers=idem(headers),
        json={},
    )
    assert launched.status_code == 202, launched.text
    job_id = launched.json()['job']['id']
    deployment_id = launched.json()['id']

    approved = client.post(f'/api/v1/jobs/{job_id}/approve', headers=headers)
    assert approved.status_code == 200, approved.text
    assert approved.json()['status'] == 'queued'

    with session() as db:
        job = db.get(Job, job_id)
        deployment = db.get(Deployment, deployment_id)
        assert job.payload['_approval']['status'] == 'approved'
        job.status = 'failed'
        job.error = 'synthetic failure after approval'
        deployment.active_job_id = None
        deployment.status = 'failed'
        db.commit()

    retried = client.post(f'/api/v1/jobs/{job_id}/retry', headers=idem(headers))
    assert retried.status_code == 202, retried.text
    assert retried.json()['status'] == 'waiting_approval'

    with session() as db:
        retry = db.get(Job, retried.json()['id'])
        deployment = db.get(Deployment, deployment_id)
        assert retry.payload['_approval'] == {'status': 'pending'}
        assert deployment.status == 'waiting_approval'
        assert deployment.active_job_id == retry.id


def test_manual_blueprint_apply_cannot_bypass_approval(client, headers):
    manual_approval(client, headers)
    credential, provider = infrastructure(client, headers)
    created = client.post('/api/v1/blueprints', headers=headers, json=blueprint_payload(
        credential, provider, slug='manual-approval', name='Manual approval', requires_approval=True,
    ))
    assert created.status_code == 201, created.text

    launched = client.post(
        f"/api/v1/blueprints/{created.json()['id']}/execute",
        headers=idem(headers),
        json={},
    )
    assert launched.status_code == 202, launched.text
    assert launched.json()['job']['status'] == 'waiting_approval'

    cancelled = client.post(
        f"/api/v1/jobs/{launched.json()['job']['id']}/cancel",
        headers=headers,
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()['status'] == 'cancelled'

    manual = client.post('/api/v1/jobs', headers=idem(headers), json={
        'operation': 'terraform.apply',
        'deployment_id': launched.json()['id'],
    })
    assert manual.status_code == 202, manual.text
    assert manual.json()['status'] == 'waiting_approval'

    with session() as db:
        job = db.get(Job, manual.json()['id'])
        assert job.payload['_approval'] == {'status': 'pending'}



def test_worker_revalidates_blueprint_before_approved_execution(client, headers, monkeypatch):
    manual_approval(client, headers)
    credential, provider = infrastructure(client, headers)
    created = client.post('/api/v1/blueprints', headers=headers, json=blueprint_payload(
        credential,
        provider,
        slug='revoked-before-run',
        name='Revoked before run',
        requires_approval=True,
    ))
    assert created.status_code == 201, created.text

    launched = client.post(
        f"/api/v1/blueprints/{created.json()['id']}/execute",
        headers=idem(headers),
        json={},
    )
    assert launched.status_code == 202, launched.text
    job_id = launched.json()['job']['id']
    assert launched.json()['job']['status'] == 'waiting_approval'

    disabled = client.put(
        f"/api/v1/blueprints/{created.json()['id']}/enabled",
        headers=headers,
        json={'enabled': False},
    )
    assert disabled.status_code == 200, disabled.text

    approved = client.post(f'/api/v1/jobs/{job_id}/approve', headers=headers)
    assert approved.status_code == 200, approved.text

    monkeypatch.setattr(
        TerraformExecutor,
        'execute',
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('Terraform must not run')),
    )
    execute(job_id)

    with session() as db:
        job = db.get(Job, job_id)
        assert job.status == 'failed'
        assert 'no longer active' in job.error


def test_blueprint_auto_approval_for_executor_is_default(client, headers):
    credential, provider = infrastructure(client, headers)
    created = client.post('/api/v1/blueprints', headers=headers, json=blueprint_payload(
        credential, provider, slug='auto-approval-default', name='Auto approval default',
        requires_approval=True,
    ))
    assert created.status_code == 201, created.text

    permissions = [
        'blueprints.read', 'blueprints.execute',
        'deployments.create', 'jobs.execute', 'terraform.execute',
    ]
    _, operator = new_user(
        client,
        headers,
        username='auto-approval-operator',
        permissions=permissions,
    )

    requested = client.post(
        f"/api/v1/blueprints/{created.json()['id']}/execute",
        headers=idem(operator),
        json={},
    )
    assert requested.status_code == 202, requested.text
    assert requested.json()['job']['status'] == 'queued'
    assert requested.json()['status'] == 'queued'

    with session() as db:
        job = db.get(Job, requested.json()['job']['id'])
        approval = (job.payload or {}).get('_approval') or {}
        assert approval['status'] == 'approved'
        assert approval['approved_by'] == job.created_by
        assert approval['automatic'] is True
