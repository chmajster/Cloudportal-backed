import uuid
from types import SimpleNamespace

from app.database import session
from app.executors.base import ExecutionFailed
from app.executors.terraform import TerraformExecutor
from app.jobs import worker
from app.jobs.worker import execute
from app.jobs.queue import expire_waiting_approvals
from app.models import Deployment, Job, TerraformPlan
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
    setting = client.put(
        '/api/v1/settings/blueprints',
        headers=headers,
        json={'auto_approve_for_executors': False},
    )
    assert setting.status_code == 200, setting.text
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


def test_waiting_approval_guest_credential_cannot_be_rotated(client, headers):
    setting = client.put(
        '/api/v1/settings/blueprints',
        headers=headers,
        json={'auto_approve_for_executors': False},
    )
    assert setting.status_code == 200, setting.text

    credential, provider = infrastructure(client, headers)
    guest = client.post('/api/v1/credentials', headers=headers, json={
        'name': 'Pending VM login',
        'type': 'ssh',
        'endpoint': 'ssh://pending-vm.example.com:22',
        'username': 'vmadmin',
        'secrets': {'password': 'pending-password-1'},
    })
    assert guest.status_code == 201, guest.text

    payload = blueprint_payload(credential, provider, requires_approval=True)
    payload['slug'] = 'pending-guest-credential'
    payload['name'] = 'Pending guest credential'
    payload['deployment']['guest_credential_id'] = guest.json()['id']
    created = client.post('/api/v1/blueprints', headers=headers, json=payload)
    assert created.status_code == 201, created.text

    launched = client.post(
        f"/api/v1/blueprints/{created.json()['id']}/execute",
        headers=idem(headers),
        json={},
    )
    assert launched.status_code == 202, launched.text
    assert launched.json()['job']['status'] == 'waiting_approval'

    rotated = client.put(
        f"/api/v1/credentials/{guest.json()['id']}",
        headers=headers,
        json={
            'name': 'Pending VM login',
            'type': 'ssh',
            'endpoint': 'ssh://pending-vm.example.com:22',
            'username': 'vmadmin',
            'secrets': {'password': 'pending-password-2'},
        },
    )
    assert rotated.status_code == 409, rotated.text
    assert 'job' in rotated.text.lower()


def test_manual_plan_authorizes_guest_credential_from_deployment_workflow(client, headers, monkeypatch):
    credential, provider = infrastructure(client, headers)
    guest = client.post('/api/v1/credentials', headers=headers, json={
        'name': 'Plan VM login',
        'type': 'ssh',
        'endpoint': 'ssh://plan-vm.example.com:22',
        'username': 'vmadmin',
        'secrets': {'password': 'plan-password'},
    })
    assert guest.status_code == 201, guest.text

    payload = blueprint_payload(credential, provider)
    payload['slug'] = 'plan-guest-credential'
    payload['name'] = 'Plan guest credential'
    payload['deployment']['guest_credential_id'] = guest.json()['id']
    created = client.post('/api/v1/blueprints', headers=headers, json=payload)
    assert created.status_code == 201, created.text
    launched = client.post(
        f"/api/v1/blueprints/{created.json()['id']}/execute",
        headers=idem(headers),
        json={},
    )
    assert launched.status_code == 202, launched.text

    with session() as db:
        initial = db.get(Job, launched.json()['job']['id'])
        deployment = db.get(Deployment, launched.json()['id'])
        initial.status = 'failed'
        deployment.active_job_id = None
        deployment.status = 'failed'
        db.commit()

    manual = client.post('/api/v1/jobs', headers=idem(headers), json={
        'operation': 'terraform.plan',
        'deployment_id': launched.json()['id'],
    })
    assert manual.status_code == 202, manual.text

    seen = []
    monkeypatch.setattr(
        'app.resource_scope.database.reference_visible',
        lambda db, kind, key, scope: seen.append((kind, int(key))) or True,
    )
    with session() as db:
        job = db.get(Job, manual.json()['id'])
        assert not (job.payload or {}).get('blueprint')
        worker.validate_authorization(db, job)

    assert ('credential', guest.json()['id']) in seen


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


def test_blueprint_auto_approval_for_executor_is_default(client, headers):
    credential, provider = infrastructure(client, headers)
    created = client.post('/api/v1/blueprints', headers=headers, json=blueprint_payload(
        credential, provider, requires_approval=True,
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


def test_plan_approval_pauses_and_resumes_exact_plan(client, headers, monkeypatch, tmp_path):
    setting = client.put(
        '/api/v1/settings/blueprints',
        headers=headers,
        json={'auto_approve_for_executors': False, 'approval_timeout_hours': 24},
    )
    assert setting.status_code == 200, setting.text

    credential, provider = infrastructure(client, headers)
    payload = blueprint_payload(credential, provider, requires_approval=True)
    payload['slug'] = 'planned-approval-vm'
    payload['name'] = 'Planned Approval VM'
    payload['workflow'] = [
        {'id': 'plan', 'type': 'terraform_plan'},
        {'id': 'approval', 'type': 'approval', 'depends_on': ['plan']},
        {'id': 'apply', 'type': 'terraform_apply', 'depends_on': ['approval']},
    ]
    created = client.post('/api/v1/blueprints', headers=headers, json=payload)
    assert created.status_code == 201, created.text

    launched = client.post(
        f"/api/v1/blueprints/{created.json()['id']}/execute",
        headers=idem(headers),
        json={},
    )
    assert launched.status_code == 202, launched.text
    assert launched.json()['job']['status'] == 'queued'

    monkeypatch.setattr(worker.settings(), 'data_dir', tmp_path)
    operations = []
    approved_plan = b'approved-binary-plan'

    def terraform_execute(self, operation, context):
        workspace = tmp_path / 'workspaces' / context.deployment.workspace
        workspace.mkdir(parents=True, exist_ok=True)
        operations.append((operation, bool(getattr(context, 'apply_saved_terraform_plan', False))))
        if operation == 'terraform.plan':
            (workspace / 'execution.tfplan').write_bytes(approved_plan)
        elif operation == 'terraform.apply':
            assert (workspace / 'execution.tfplan').read_bytes() == approved_plan
        return workspace

    monkeypatch.setattr(TerraformExecutor, 'execute', terraform_execute)
    monkeypatch.setattr(
        'app.jobs.worker.register_managed_inventory',
        lambda context, workspace: {'external_id': '801', 'vm_id': 801, 'node': 'pve01'},
    )

    job_id = launched.json()['job']['id']
    execute(job_id)

    paused = client.get('/api/v1/jobs/' + job_id, headers=headers).json()
    assert paused['status'] == 'waiting_approval'
    assert operations == [('terraform.plan', False)]
    with session() as db:
        stored = db.get(TerraformPlan, launched.json()['id'])
        assert stored is not None
        assert stored.encrypted_plan != approved_plan
        runtime = (db.get(Job, job_id).payload or {})['_workflow_runtime']
        assert runtime['completed_steps'] == ['plan']
        assert runtime['plan_ready'] is True
        assert runtime['plan_sha256'] == stored.plan_sha256

    workspace = tmp_path / 'workspaces' / launched.json()['workspace']
    (workspace / 'execution.tfplan').unlink(missing_ok=True)

    approved = client.post('/api/v1/jobs/' + job_id + '/approve', headers=headers)
    assert approved.status_code == 200, approved.text
    assert approved.json()['status'] == 'queued'

    execute(job_id)

    finished = client.get('/api/v1/jobs/' + job_id, headers=headers).json()
    assert finished['status'] == 'successful'
    assert operations == [
        ('terraform.plan', False),
        ('terraform.apply', True),
    ]
    deployment = client.get('/api/v1/deployments/' + launched.json()['id'], headers=headers).json()
    assert deployment['status'] == 'successful'
    with session() as db:
        assert db.get(TerraformPlan, launched.json()['id']) is None


def test_retry_re_evaluates_current_approval_policy(client, headers):
    credential, provider = infrastructure(client, headers)
    created = client.post('/api/v1/blueprints', headers=headers, json=blueprint_payload(
        credential, provider, requires_approval=True,
    ))
    assert created.status_code == 201, created.text
    launched = client.post(
        f"/api/v1/blueprints/{created.json()['id']}/execute",
        headers=idem(headers),
        json={},
    )
    assert launched.status_code == 202
    job_id = launched.json()['job']['id']

    with session() as db:
        job = db.get(Job, job_id)
        assert job.payload['_approval']['status'] == 'approved'
        job.status = 'failed'
        deployment = db.get(Deployment, launched.json()['id'])
        deployment.active_job_id = None
        deployment.status = 'failed'
        db.commit()

    disabled = client.put(
        '/api/v1/settings/blueprints',
        headers=headers,
        json={'auto_approve_for_executors': False, 'approval_timeout_hours': 6},
    )
    assert disabled.status_code == 200, disabled.text

    retried = client.post(
        '/api/v1/jobs/' + job_id + '/retry',
        headers=idem(headers),
    )
    assert retried.status_code == 202, retried.text
    assert retried.json()['status'] == 'waiting_approval'
    with session() as db:
        retry = db.get(Job, retried.json()['id'])
        approval = retry.payload['_approval']
        assert approval['status'] == 'pending'
        assert 'approved_by' not in approval
        assert approval['expires_at']


def test_waiting_approval_expires_and_removes_plan(client, headers, monkeypatch, tmp_path):
    setting = client.put(
        '/api/v1/settings/blueprints',
        headers=headers,
        json={'auto_approve_for_executors': False, 'approval_timeout_hours': 1},
    )
    assert setting.status_code == 200

    credential, provider = infrastructure(client, headers)
    payload = blueprint_payload(credential, provider, requires_approval=True)
    payload['slug'] = 'expiry-plan-vm'
    payload['name'] = 'Expiry Plan VM'
    payload['workflow'] = [
        {'id': 'plan', 'type': 'terraform_plan'},
        {'id': 'approval', 'type': 'approval', 'depends_on': ['plan']},
        {'id': 'apply', 'type': 'terraform_apply', 'depends_on': ['approval']},
    ]
    created = client.post('/api/v1/blueprints', headers=headers, json=payload)
    assert created.status_code == 201, created.text
    launched = client.post(
        f"/api/v1/blueprints/{created.json()['id']}/execute",
        headers=idem(headers),
        json={},
    )
    assert launched.status_code == 202

    monkeypatch.setattr(worker.settings(), 'data_dir', tmp_path)

    def terraform_execute(self, operation, context):
        workspace = tmp_path / 'workspaces' / context.deployment.workspace
        workspace.mkdir(parents=True, exist_ok=True)
        if operation == 'terraform.plan':
            (workspace / 'execution.tfplan').write_bytes(b'expiring-plan')
        return workspace

    monkeypatch.setattr(TerraformExecutor, 'execute', terraform_execute)
    execute(launched.json()['job']['id'])

    with session() as db:
        job = db.get(Job, launched.json()['job']['id'])
        payload = dict(job.payload)
        approval = dict(payload['_approval'])
        approval['expires_at'] = '2000-01-01T00:00:00'
        payload['_approval'] = approval
        job.payload = payload
        db.commit()

    with session() as db:
        expire_waiting_approvals(db)
        db.commit()

    expired = client.get('/api/v1/jobs/' + launched.json()['job']['id'], headers=headers).json()
    assert expired['status'] == 'cancelled'
    deployment = client.get('/api/v1/deployments/' + launched.json()['id'], headers=headers).json()
    assert deployment['status'] == 'cancelled'
    assert deployment['active_job_id'] is None
    with session() as db:
        assert db.get(TerraformPlan, launched.json()['id']) is None


def test_manual_blueprint_apply_cannot_bypass_central_approval(client, headers):
    setting = client.put(
        '/api/v1/settings/blueprints',
        headers=headers,
        json={'auto_approve_for_executors': False, 'approval_timeout_hours': 48},
    )
    assert setting.status_code == 200, setting.text

    credential, provider = infrastructure(client, headers)
    created = client.post('/api/v1/blueprints', headers=headers, json=blueprint_payload(
        credential,
        provider,
        slug='manual-apply-approval',
        name='Manual apply approval',
        requires_approval=True,
    ))
    assert created.status_code == 201, created.text

    launched = client.post(
        f"/api/v1/blueprints/{created.json()['id']}/execute",
        headers=idem(headers),
        json={},
    )
    assert launched.status_code == 202, launched.text

    with session() as db:
        initial = db.get(Job, launched.json()['job']['id'])
        deployment = db.get(Deployment, launched.json()['id'])
        initial.status = 'failed'
        deployment.active_job_id = None
        deployment.status = 'failed'
        db.commit()

    manual = client.post('/api/v1/jobs', headers=idem(headers), json={
        'operation': 'terraform.apply',
        'deployment_id': launched.json()['id'],
    })
    assert manual.status_code == 202, manual.text
    assert manual.json()['status'] == 'waiting_approval'

    with session() as db:
        job = db.get(Job, manual.json()['id'])
        approval = (job.payload or {}).get('_approval') or {}
        assert approval['status'] == 'pending'
        assert approval['expires_at']


def test_worker_revalidates_blueprint_before_execution(client, headers, monkeypatch):
    setting = client.put(
        '/api/v1/settings/blueprints',
        headers=headers,
        json={'auto_approve_for_executors': False, 'approval_timeout_hours': 48},
    )
    assert setting.status_code == 200, setting.text

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
