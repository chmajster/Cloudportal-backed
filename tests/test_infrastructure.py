import json
import stat
import uuid
from types import SimpleNamespace
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy import select, text
from app.config import settings
from app.database import session
from app.models import Credential, Deployment, Idempotency, Job, ManagedVM, User, now
from app.quotas.models import QuotaAllocation, QuotaReservation
from app.security.core import decrypt_secret
from app.executors.base import Cancelled, ExecutionFailed, run_process
from app.executors.terraform import (TerraformExecutor, cleanup_qemu_bootstrap, load_qemu_bootstrap,
                                     prepare_qemu_bootstrap, proxmox_ssh_preflight,
                                     terraform_plan_command, workspace_lock)
from app.deployments.recreate import recreate_resource_address
from app.jobs.worker import execute
from app.jobs.queue import (reconcile_cancelled_jobs, reconcile_deployment_job_statuses,
                            reconcile_persisted_inventory, reconcile_stale_jobs)
from app.terraform.state import persist_state


def terraform_state_workspace(tmp_path, vm_id=101):
    workspace = tmp_path / 'terraform-workspace'
    workspace.mkdir(exist_ok=True)
    (workspace / 'terraform.tfstate').write_text(json.dumps({
        'outputs': {'vm_id': {'value': vm_id}},
    }))
    return workspace


def resources(client,headers):
    cred=client.post('/api/v1/credentials',headers=headers,json={'name':'PVE','type':'proxmox','endpoint':'https://pve.example.com:8006','username':'root@pam','secrets':{'token_id':'root@pam!terraform','token_secret':'very-private-secret-value'}})
    assert cred.status_code==201,cred.text
    provider=client.post('/api/v1/providers',headers=headers,json={'name':'LAB','type':'proxmox','credentials_id':cred.json()['id']})
    assert provider.status_code==201,provider.text
    payload={'name':'test','provider_id':provider.json()['id'],'credentials_id':cred.json()['id'],'variables':{'name':'vm01','node':'pve','template_id':9000,'storage':'local-lvm'}}
    return cred.json(),provider.json(),payload


def deployment(client,headers):
    _,_,payload=resources(client,headers)
    response=client.post('/api/v1/deployments',headers={**headers,'Idempotency-Key':str(uuid.uuid4())},json=payload)
    assert response.status_code==202,response.text
    return response.json()


def test_encryption_masking_and_destination_change(client,headers):
    c,_,_=resources(client,headers)
    with session() as db:
        row=db.get(Credential,c['id'])
        assert b'very-private-secret-value' not in row.encrypted_secret
        assert decrypt_secret(row)['token_secret']=='very-private-secret-value'
    assert c['secret']=='********' and c['configured']
    assert 'very-private-secret-value' not in client.get('/api/v1/credentials',headers=headers).text
    response=client.put(f'/api/v1/credentials/{c["id"]}',headers=headers,json={'name':'PVE','type':'proxmox','endpoint':'https://evil.example.com','username':'root@pam'})
    assert response.status_code==422
    response=client.put(f'/api/v1/credentials/{c["id"]}',headers=headers,json={'name':'Renamed','type':'proxmox','endpoint':'https://pve.example.com:8006','username':'root@pam'})
    assert response.status_code==200,response.text


def test_idempotency_and_concurrent_apply(client,headers):
    _,_,payload=resources(client,headers)
    key=str(uuid.uuid4());auth={**headers,'Idempotency-Key':key}
    first=client.post('/api/v1/deployments',headers=auth,json=payload)
    assert first.status_code==202,first.text
    second=client.post('/api/v1/deployments',headers=auth,json=payload)
    assert second.status_code==202 and second.json()['id']==first.json()['id'],second.text
    assert client.post('/api/v1/deployments',headers=auth,json={**payload,'name':'changed'}).status_code==409
    assert client.post('/api/v1/deployments',headers=headers,json=payload).status_code==400
    response=client.post('/api/v1/jobs',headers={**headers,'Idempotency-Key':str(uuid.uuid4())},json={'operation':'terraform.apply','deployment_id':first.json()['id']})
    assert response.status_code==409,response.text
    with session() as db:
        assert len(db.scalars(select(Deployment)).all())==1
        assert len(db.scalars(select(Job)).all())==1

def test_reconciliation_required_blocks_mutations_until_successful_plan(client, headers):
    created = deployment(client, headers)
    with session() as db:
        original = db.get(Job, created['job']['id'])
        dep = db.get(Deployment, created['id'])
        original.status = 'failed'
        dep.active_job_id = None
        dep.status = 'reconciliation_required'
        db.commit()

    blocked = client.post(
        '/api/v1/jobs',
        headers={**headers, 'Idempotency-Key': str(uuid.uuid4())},
        json={'operation': 'terraform.apply', 'deployment_id': created['id']},
    )
    assert blocked.status_code == 409, blocked.text
    assert 'reconciliation' in blocked.text.lower()

    planned = client.post(
        '/api/v1/jobs',
        headers={**headers, 'Idempotency-Key': str(uuid.uuid4())},
        json={'operation': 'terraform.plan', 'deployment_id': created['id']},
    )
    assert planned.status_code == 202, planned.text
    with session() as db:
        plan_job = db.get(Job, planned.json()['id'])
        dep = db.get(Deployment, created['id'])
        assert plan_job.payload['previous_status'] == 'reconciliation_required'
        plan_job.status = 'successful'
        db.commit()

    with session() as db:
        reconcile_deployment_job_statuses(db)
        db.commit()

    with session() as db:
        dep = db.get(Deployment, created['id'])
        assert dep.status == 'successful'
        assert dep.active_job_id is None


def test_recreate_deployment_queues_idempotent_replace_apply(client, headers):
    created = deployment(client, headers)
    cancelled = client.post('/api/v1/jobs/' + created['job']['id'] + '/cancel', headers=headers)
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()['status'] == 'cancelled'

    key = str(uuid.uuid4())
    auth = {**headers, 'Idempotency-Key': key}
    first = client.post('/api/v1/deployments/' + created['id'] + '/recreate', headers=auth, json={})
    second = client.post('/api/v1/deployments/' + created['id'] + '/recreate', headers=auth, json={})

    assert first.status_code == 202, first.text
    assert second.status_code == 202, second.text
    assert second.json()['id'] == first.json()['id']
    assert first.json()['operation'] == 'terraform.apply'

    with session() as db:
        job = db.get(Job, first.json()['id'])
        dep = db.get(Deployment, created['id'])
        assert job.payload['_recreate'] is True
        assert job.payload['previous_status'] == 'cancelled'
        assert dep.active_job_id == job.id
        assert dep.status == 'queued'


def test_recreate_plan_targets_only_approved_template_resource():
    address = recreate_resource_address('proxmox-vm')
    assert address == 'proxmox_virtual_environment_vm.vm'

    command = terraform_plan_command('terraform', 'terraform.apply', address)
    assert '-replace=proxmox_virtual_environment_vm.vm' in command
    assert '-destroy' not in command

    destroy = terraform_plan_command('terraform', 'terraform.destroy', address)
    assert '-destroy' in destroy
    assert not any(argument.startswith('-replace=') for argument in destroy)



def test_token_idempotency_does_not_store_plaintext(client,headers):
    auth={**headers,'Idempotency-Key':str(uuid.uuid4())};body={'name':'limited','scopes':['users.read']}
    first=client.post('/api/v1/tokens',headers=auth,json=body)
    second=client.post('/api/v1/tokens',headers=auth,json=body)
    assert first.status_code==201 and second.status_code==201
    assert 'token' in first.json() and 'token' not in second.json()
    with session() as db:
        assert first.json()['token'] not in str(db.scalars(select(Idempotency)).first().response)


def test_deployment_status_is_running_while_terraform_job_executes(client, headers, monkeypatch, tmp_path):
    d = deployment(client, headers)
    workspace = terraform_state_workspace(tmp_path, vm_id=303)
    observed = {}

    def execute_and_observe(*args):
        with session() as db:
            job = db.get(Job, d['job']['id'])
            dep = db.get(Deployment, d['id'])
            observed['job_status'] = job.status
            observed['deployment_status'] = dep.status
            observed['active_job_id'] = dep.active_job_id
        return workspace

    monkeypatch.setattr(TerraformExecutor, 'execute', execute_and_observe)
    execute(d['job']['id'])

    assert observed == {
        'job_status': 'running',
        'deployment_status': 'running',
        'active_job_id': d['job']['id'],
    }
    current = client.get('/api/v1/deployments/' + d['id'], headers=headers).json()
    assert current['status'] == 'successful'
    assert current['active_job_id'] is None


def test_dispatcher_reconciles_deployment_status_from_job(client, headers, monkeypatch):
    from app.jobs.queue import reconcile_deployment_job_statuses

    d = deployment(client, headers)
    with session() as db:
        job = db.get(Job, d['job']['id'])
        dep = db.get(Deployment, d['id'])
        job.status = 'running'
        dep.status = 'queued'
        db.commit()

    with session() as db:
        reconcile_deployment_job_statuses(db)
        db.commit()

    current = client.get('/api/v1/deployments/' + d['id'], headers=headers).json()
    assert current['status'] == 'running'
    assert current['active_job_id'] == d['job']['id']

    with session() as db:
        job = db.get(Job, d['job']['id'])
        job.status = 'successful'
        db.commit()

    with session() as db:
        reconcile_deployment_job_statuses(db)
        db.commit()

    current = client.get('/api/v1/deployments/' + d['id'], headers=headers).json()
    assert current['status'] == 'successful'
    assert current['active_job_id'] is None


def test_terraform_failure_and_retry(client,headers,monkeypatch):
    d=deployment(client,headers)
    def fail(_self, operation, context):
        context.stage('terraform.plan')
        raise ExecutionFailed('terraform exited with code 1')
    monkeypatch.setattr(TerraformExecutor,'execute',fail)
    execute(d['job']['id'])
    result=client.get('/api/v1/jobs/'+d['job']['id'],headers=headers).json()
    assert result['status']=='failed' and 'code 1' in result['error']
    assert result['current_stage']=='terraform.plan'
    assert client.get('/api/v1/deployments/'+d['id'],headers=headers).json()['active_job_id'] is None
    logs=client.get('/api/v1/jobs/'+d['job']['id']+'/logs',headers=headers).json()
    assert logs['items'] and logs['request_id']==result['request_id']

    retried=client.post(
        '/api/v1/jobs/'+d['job']['id']+'/retry',
        headers={**headers,'Idempotency-Key':str(uuid.uuid4())},
    )
    assert retried.status_code==202,retried.text
    assert retried.json()['current_stage'] is None


def test_cancel_before_execution(client,headers,monkeypatch):
    d=deployment(client,headers)
    assert client.post('/api/v1/jobs/'+d['job']['id']+'/cancel',headers=headers).status_code==200
    monkeypatch.setattr(TerraformExecutor,'execute',lambda *a: (_ for _ in ()).throw(AssertionError('must not run')))
    execute(d['job']['id'])
    assert client.get('/api/v1/jobs/'+d['job']['id'],headers=headers).json()['status']=='cancelled'


def test_running_cancel_is_visible_and_orphan_is_finalized(client, headers):
    d = deployment(client, headers)
    with session() as db:
        job = db.get(Job, d['job']['id'])
        job.status = 'running'
        job.heartbeat_at = now() - timedelta(seconds=120)
        dep = db.get(Deployment, d['id'])
        dep.status = 'running'
        db.commit()

    response = client.post('/api/v1/jobs/' + d['job']['id'] + '/cancel', headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'cancelling'
    assert response.json()['cancel_requested'] is True
    assert client.get('/api/v1/deployments/' + d['id'], headers=headers).json()['status'] == 'cancelling'

    repeated = client.post('/api/v1/jobs/' + d['job']['id'] + '/cancel', headers=headers)
    assert repeated.status_code == 200
    assert repeated.json()['status'] == 'cancelling'

    with session() as db:
        reconcile_cancelled_jobs(db)
        db.commit()

    job = client.get('/api/v1/jobs/' + d['job']['id'], headers=headers).json()
    dep = client.get('/api/v1/deployments/' + d['id'], headers=headers).json()
    assert job['status'] == 'cancelled'
    assert dep['status'] == 'cancelled'
    assert dep['active_job_id'] is None


def test_inventory_reconcile_recovers_vm_from_persisted_state(client, headers, tmp_path):
    d = deployment(client, headers)
    workspace = terraform_state_workspace(tmp_path, vm_id=612)
    assert persist_state(d['id'], workspace)

    with session() as db:
        job = db.get(Job, d['job']['id'])
        job.status = 'running'
        dep = db.get(Deployment, d['id'])
        dep.status = 'running'
        db.commit()
        assert db.scalar(select(ManagedVM).where(ManagedVM.deployment_id == d['id'])) is None

    repaired = client.post('/api/v1/inventory/reconcile', headers=headers)
    assert repaired.status_code == 200, repaired.text
    assert repaired.json()['repaired_count'] == 1
    with session() as db:
        job = db.get(Job, d['job']['id'])
        reservation = db.get(QuotaReservation, job.payload['_quota_reservation_id'])
        assert reservation.status == 'committed'
        allocation = db.scalar(select(QuotaAllocation).where(
            QuotaAllocation.subject_type == 'deployment',
            QuotaAllocation.subject_id == d['id'],
        ))
        assert allocation is not None and allocation.dimensions['vm_count'] == 1

    inventory = client.get('/api/v1/inventory/vms?management_mode=terraform', headers=headers).json()['items']
    row = next(item for item in inventory if item['deployment_id'] == d['id'])
    assert row['vm_id'] == 612
    assert row['node'] == 'pve'
    assert row['lifecycle_status'] == 'active'

    repeated = client.post('/api/v1/inventory/reconcile', headers=headers)
    assert repeated.status_code == 200
    assert repeated.json()['repaired_count'] == 0


def test_lost_worker_auto_resumes_after_persisted_state_reconciliation(
    client, headers, monkeypatch, tmp_path
):
    d = deployment(client, headers)
    workspace = terraform_state_workspace(tmp_path, vm_id=613)
    assert persist_state(d['id'], workspace)

    with session() as db:
        job = db.get(Job, d['job']['id'])
        payload = dict(job.payload or {})
        payload['_recreate'] = True
        job.payload = payload
        job.status = 'running'
        job.heartbeat_at = now() - timedelta(seconds=settings().execution_timeout + 181)
        dep = db.get(Deployment, d['id'])
        dep.status = 'running'
        db.commit()

    with session() as db:
        reconcile_persisted_inventory(db)
        assert reconcile_stale_jobs(db) == 1
        db.commit()

    with session() as db:
        original = db.get(Job, d['job']['id'])
        resumed = db.scalar(select(Job).where(Job.retry_of == original.id))
        reservation = db.get(QuotaReservation, original.payload['_quota_reservation_id'])
        dep = db.get(Deployment, d['id'])

        assert original.status == 'failed'
        assert 'automatic resume queued as job' in original.error
        assert reservation.status == 'committed'
        assert resumed is not None
        assert resumed.status == 'queued'
        assert resumed.source == 'Recovery'
        assert resumed.payload['_auto_resume']['from_persisted_state'] is True
        assert resumed.payload['_auto_resume']['authorization_source'] == original.source
        assert resumed.payload['_auto_resume']['count'] == 1
        assert '_recreate' not in resumed.payload
        assert '_quota_checked' not in resumed.payload
        assert '_quota_reservation_id' not in resumed.payload
        assert dep.active_job_id == resumed.id
        assert dep.status == 'recovery_queued'
        resumed_id = resumed.id

    monkeypatch.setattr(settings(), 'data_dir', tmp_path)
    monkeypatch.setattr(
        'app.jobs.worker.provider_for',
        lambda _credential: SimpleNamespace(
            execution_availability=lambda: {'ok': True},
        ),
    )
    operations = []
    monkeypatch.setattr(
        TerraformExecutor,
        'execute',
        lambda _executor, operation, _context: operations.append(operation) or workspace,
    )

    execute(resumed_id)

    assert operations == ['terraform.apply']
    with session() as db:
        resumed = db.get(Job, resumed_id)
        dep = db.get(Deployment, d['id'])
        assert resumed.status == 'successful'
        assert dep.status == 'successful'
        assert dep.active_job_id is None


def test_lost_worker_auto_resumes_from_durable_apply_checkpoint(
    client, headers, tmp_path
):
    d = deployment(client, headers)
    workspace = terraform_state_workspace(tmp_path, vm_id=614)
    assert persist_state(d['id'], workspace)

    repaired = client.post('/api/v1/inventory/reconcile', headers=headers)
    assert repaired.status_code == 200, repaired.text
    assert repaired.json()['repaired_count'] == 1

    with session() as db:
        job = db.get(Job, d['job']['id'])
        payload = dict(job.payload or {})
        payload.pop('_state_recovery', None)
        payload['_workflow_runtime'] = {
            'completed_steps': ['apply'],
            'provider_applied': True,
            'inventory_synced': True,
            'plan_ready': False,
            'plan_sha256': None,
            'ansible_ran': False,
        }
        job.payload = payload
        job.status = 'running'
        job.heartbeat_at = now() - timedelta(seconds=settings().execution_timeout + 181)
        dep = db.get(Deployment, d['id'])
        dep.status = 'running'
        dep.active_job_id = job.id
        db.commit()

    with session() as db:
        assert reconcile_stale_jobs(db) == 1
        db.commit()

    with session() as db:
        original = db.get(Job, d['job']['id'])
        resumed = db.scalar(select(Job).where(Job.retry_of == original.id))
        dep = db.get(Deployment, d['id'])

        assert '_state_recovery' not in original.payload
        assert resumed is not None
        assert resumed.status == 'queued'
        assert resumed.source == 'Recovery'
        assert resumed.payload['_workflow_runtime']['provider_applied'] is True
        assert resumed.payload['_workflow_runtime']['inventory_synced'] is True
        assert resumed.payload['_auto_resume']['authorization_source'] == original.source
        assert dep.active_job_id == resumed.id
        assert dep.status == 'recovery_queued'


def test_worker_rechecks_revoked_permissions(client,headers,monkeypatch):
    d=deployment(client,headers)
    with session() as db:
        from app.models import Token, now
        token=db.get(Token,db.get(Job,d['job']['id']).token_id);token.revoked_at=now();db.commit()
    called=[];monkeypatch.setattr(TerraformExecutor,'execute',lambda *a:called.append(1))
    execute(d['job']['id'])
    with session() as db:assert db.get(Job,d['job']['id']).status=='failed'
    assert not called



def test_failed_job_can_be_retried_with_lineage(client, headers, monkeypatch, tmp_path):
    d = deployment(client, headers)

    def fail(*args):
        raise ExecutionFailed('terraform exited with code 1')

    monkeypatch.setattr(TerraformExecutor, 'execute', fail)
    execute(d['job']['id'])
    failed = client.get('/api/v1/jobs/' + d['job']['id'], headers=headers).json()
    assert failed['status'] == 'failed'
    assert failed['attempt'] == 1
    assert failed['retry_of'] is None

    retry = client.post(
        '/api/v1/jobs/' + failed['id'] + '/retry',
        headers={**headers, 'Idempotency-Key': str(uuid.uuid4())},
    )
    assert retry.status_code == 202, retry.text
    retried = retry.json()
    assert retried['retry_of'] == failed['id']
    assert retried['attempt'] == 2
    assert retried['status'] == 'queued'

    workspace = terraform_state_workspace(tmp_path, vm_id=404)
    monkeypatch.setattr(TerraformExecutor, 'execute', lambda *args: workspace)
    execute(retried['id'])
    completed = client.get('/api/v1/jobs/' + retried['id'], headers=headers).json()
    assert completed['status'] == 'successful'

    inventory = client.get('/api/v1/inventory/vms?management_mode=terraform', headers=headers)
    assert any(row['vm_id'] == 404 and row['deployment_id'] == d['id'] for row in inventory.json()['items'])
    logs = client.get('/api/v1/jobs/' + retried['id'] + '/logs', headers=headers).json()['items']
    messages = [row['message'] for row in logs]
    assert 'inventory.synchronizing' in messages
    assert any('inventory.vm.registered:' in message and 'VMID 404' in message for message in messages)

    assert client.post(
        '/api/v1/jobs/' + retried['id'] + '/retry',
        headers={**headers, 'Idempotency-Key': str(uuid.uuid4())},
    ).status_code == 409

def test_reapply_can_reconcile_changed_proxmox_vm_identity(client, headers, monkeypatch, tmp_path):
    d = deployment(client, headers)

    first_workspace = terraform_state_workspace(tmp_path, vm_id=501)
    monkeypatch.setattr(TerraformExecutor, 'execute', lambda *args: first_workspace)
    execute(d['job']['id'])
    assert client.get('/api/v1/jobs/' + d['job']['id'], headers=headers).json()['status'] == 'successful'

    second = client.post(
        '/api/v1/jobs',
        headers={**headers, 'Idempotency-Key': str(uuid.uuid4())},
        json={'operation': 'terraform.apply', 'deployment_id': d['id']},
    )
    assert second.status_code == 202, second.text

    second_workspace = terraform_state_workspace(tmp_path, vm_id=502)
    monkeypatch.setattr(TerraformExecutor, 'execute', lambda *args: second_workspace)
    execute(second.json()['id'])
    assert client.get('/api/v1/jobs/' + second.json()['id'], headers=headers).json()['status'] == 'successful'

    inventory = client.get('/api/v1/inventory/vms?management_mode=terraform', headers=headers).json()['items']
    linked = [row for row in inventory if row['deployment_id'] == d['id']]
    assert len(linked) == 1
    assert linked[0]['vm_id'] == 502


def test_terraform_reuses_init_and_shared_provider_cache(client, headers, monkeypatch):
    d = deployment(client, headers)
    with session() as db:
        dep = db.get(Deployment, d['id'])
        credential = db.get(Credential, dep.credentials_id)
        job = db.get(Job, d['job']['id'])

    commands = []
    envs = []
    logs = []

    def fake_process(argv, cwd, env, context, secrets=()):
        commands.append(list(argv))
        envs.append(dict(env))
        if len(argv) > 1 and argv[1] == 'init':
            (cwd / '.terraform' / 'providers').mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr('app.executors.terraform.run_process', fake_process)
    context = SimpleNamespace(
        deployment=dep,
        credential=credential,
        job=job,
        stage=lambda value: None,
        check=lambda: None,
        log=lambda value: logs.append(value),
    )

    executor = TerraformExecutor()
    executor.execute('terraform.plan', context)
    executor.execute('terraform.plan', context)

    verbs = [argv[1] for argv in commands if len(argv) > 1]
    assert verbs == ['init', 'plan', 'plan']
    assert any('terraform.init.cached' in line for line in logs)
    assert envs
    cache_dirs = {env['TF_PLUGIN_CACHE_DIR'] for env in envs}
    assert len(cache_dirs) == 1
    cache_dir = Path(next(iter(cache_dirs)))
    assert cache_dir.name == 'terraform-plugin-cache'
    assert cache_dir.exists()
    assert (settings().data_dir / 'workspaces' / dep.workspace / '.cloudportal-terraform-init.json').exists()


def test_workspace_lock_excludes_second_executor(tmp_path):
    import pytest
    with workspace_lock(tmp_path/'workspace'):
        with pytest.raises(ExecutionFailed):
            with workspace_lock(tmp_path/'workspace'):pass


def test_arbitrary_command_and_ansible_variable_injection(client,headers):
    assert client.post('/api/v1/execute-command',headers=headers,json={'command':'whoami'}).status_code==404
    for spec in [
        {'operation':'shell','command':'id'},
        {'operation':'ansible.execute','ansible':{'playbook':'../../etc/passwd','credentials_id':1,'inventory':{'hosts':['127.0.0.1']}}},
        {'operation':'ansible.execute','ansible':{'playbook':'bootstrap-linux','credentials_id':1,'inventory':{'hosts':['127.0.0.1']},'variables':{'ansible_connection':'local'}}},
        {'operation':'ansible.execute','ansible':{'playbook':'bootstrap-linux','credentials_id':1,'inventory':{'hosts':['host ansible_connection=local']}}},
    ]:
        assert client.post('/api/v1/jobs',headers=headers,json=spec).status_code==422


def test_proxmox_adapter_discovery(client,headers,monkeypatch):
    from app.providers.proxmox import ProxmoxProvider
    _,p,_=resources(client,headers)
    monkeypatch.setattr(ProxmoxProvider,'_get',lambda self,path:[{'node':'pve'}] if path=='/nodes' else [])
    response=client.get(f'/api/v1/providers/{p["id"]}/nodes',headers=headers)
    assert response.status_code==200 and response.json()['items']==[{'node':'pve'}]


def test_request_id_and_worker_offline(client,headers):
    key=str(uuid.uuid4())
    response=client.get('/api/v1/health',headers={'X-Request-ID':key})
    assert response.status_code==503 and response.headers['X-Request-ID']==key
    assert response.json()['checks']['workers']['online']==0


def test_ansible_failure_is_reported(client,headers,monkeypatch):
    from app.executors.ansible import AnsibleExecutor
    credential=client.post('/api/v1/credentials',headers=headers,json={'name':'SSH','type':'ssh','username':'clouduser','secrets':{'password':'ssh-password-1234','known_hosts':'host ssh-ed25519 example'}})
    assert credential.status_code==201,credential.text
    response=client.post('/api/v1/jobs',headers={**headers,'Idempotency-Key':str(uuid.uuid4())},json={'operation':'ansible.execute','ansible':{'playbook':'validate-linux','credentials_id':credential.json()['id'],'inventory':{'hosts':['192.0.2.10']}}})
    assert response.status_code==202,response.text
    def fail(*a):raise ExecutionFailed('ansible-playbook exited with code 2')
    monkeypatch.setattr(AnsibleExecutor,'execute',fail)
    execute(response.json()['id'])
    assert client.get('/api/v1/jobs/'+response.json()['id'],headers=headers).json()['status']=='failed'


def test_dispatch_capacity_lock_serializes_postgresql_dispatchers():
    from app.jobs.queue import DISPATCH_CAPACITY_LOCK_KEY, acquire_dispatch_capacity_lock

    with session() as first:
        if first.get_bind().dialect.name != 'postgresql':
            pytest.skip('PostgreSQL advisory lock test')
        acquire_dispatch_capacity_lock(first)
        with session() as second:
            acquired = second.execute(
                text('SELECT pg_try_advisory_xact_lock(:lock_key)'),
                {'lock_key': DISPATCH_CAPACITY_LOCK_KEY},
            ).scalar()
            assert acquired is False
            second.rollback()


def test_parallel_dispatch_capacity_uses_runtime_setting(client, headers):
    from app.jobs.queue import parallel_dispatch_capacity

    configured = client.put(
        '/api/v1/settings/execution',
        headers=headers,
        json={'max_parallel_jobs': 3},
    )
    assert configured.status_code == 200, configured.text

    created = deployment(client, headers)
    with session() as db:
        job = db.get(Job, created['job']['id'])
        job.status = 'running'
        db.commit()

    with session() as db:
        assert parallel_dispatch_capacity(db) == 2
        assert parallel_dispatch_capacity(db, active_rq_jobs=1) == 1
        assert parallel_dispatch_capacity(db, active_rq_jobs=2) == 0


def test_dispatcher_and_rq_execute_durable_job(client,headers,monkeypatch,tmp_path):
    from app.jobs.queue import dispatch_once, queue
    from rq import SimpleWorker
    from rq.serializers import JSONSerializer
    from app.security.core import redis_client
    d=deployment(client,headers)
    workspace=terraform_state_workspace(tmp_path)
    monkeypatch.setattr(TerraformExecutor,'execute',lambda *a:workspace)
    dispatch_once()
    SimpleWorker([queue()],connection=redis_client(),serializer=JSONSerializer).work(burst=True,logging_level='ERROR')
    assert client.get('/api/v1/jobs/'+d['job']['id'],headers=headers).json()['status']=='successful'


def test_request_body_limit(client,headers):
    response=client.post('/api/v1/users',headers={**headers,'Content-Type':'application/json'},content='x'*(1024*1024+1))
    assert response.status_code==413


def test_master_key_missing_is_not_replaced(system):
    from app.config import settings
    from app.bootstrap import generate_key
    import pytest
    settings().master_key_file.unlink()
    with pytest.raises(RuntimeError,match='Master key missing'):
        generate_key()
    assert not settings().master_key_file.exists()


def test_parallel_idempotency_postgresql(client,headers):
    import os,pytest
    if not os.environ.get('TEST_DATABASE_URL'):
        pytest.skip('PostgreSQL concurrency gate runs in CI')
    _,_,payload=resources(client,headers)
    auth={**headers,'Idempotency-Key':str(uuid.uuid4())}
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda _:client.post('/api/v1/deployments',headers=auth,json=payload),range(2)))
    assert [r.status_code for r in responses]==[202,202],[r.text for r in responses]
    assert len({r.json()['id'] for r in responses})==1


def test_run_process_terminates_child_when_context_is_cancelled(tmp_path):
    import sys
    import time
    import pytest

    class Context:
        calls = 0
        lines = []

        def check(self):
            self.calls += 1
            if self.calls >= 2:
                raise Cancelled('Cancellation requested')

        def log(self, line):
            self.lines.append(line)

    context = Context()
    started = time.monotonic()
    with pytest.raises(Cancelled):
        run_process(
            [sys.executable, '-c', 'import time; time.sleep(30)'],
            tmp_path,
            {'PATH': '/usr/bin:/bin'},
            context,
        )
    assert time.monotonic() - started < 5


def test_raw_process_failure_and_redaction(tmp_path):
    import pytest,sys
    from app.executors.base import run_process,redact
    class Context:
        lines=[]
        def check(self):pass
        def log(self,line):self.lines.append(line)
    context=Context()
    with pytest.raises(ExecutionFailed,match='code 7'):
        run_process([sys.executable,'-c',"print('token=private-value');raise SystemExit(7)"],tmp_path,{'PATH':'/usr/bin:/bin'},context,['private-value'])
    assert 'private-value' not in '\n'.join(context.lines)
    assert '[REDACTED]' in '\n'.join(context.lines)
    assert 'KEYLINE' not in redact('KEYLINE',['BEGIN\nKEYLINE\nEND'])


def test_ansible_secrets_are_unsafe_data(client,headers,monkeypatch,tmp_path):
    from app.executors.ansible import AnsibleExecutor
    from app.api.schemas import AnsibleInput
    from app.jobs.worker import Context
    from types import SimpleNamespace
    credential=client.post('/api/v1/credentials',headers=headers,json={'name':'SSH','type':'ssh','username':'clouduser','secrets':{'password':"{{ lookup('pipe', 'id') }}",'known_hosts':'host ssh-ed25519 example'}}).json()
    with session() as db:c=db.get(Credential,credential['id'])
    observed=[]
    def inspect(argv,cwd,env,context,secrets):
        raw=(cwd/'variables.yml').read_text()
        assert '!unsafe' in raw
        assert "lookup" in raw
        observed.append(raw)
    monkeypatch.setattr('app.executors.ansible.run_process',inspect)
    context=SimpleNamespace(ansible=AnsibleInput(playbook='validate-linux',credentials_id=c.id,inventory={'hosts':['192.0.2.1']}),ansible_credential=c,stage=lambda _:None)
    AnsibleExecutor().execute('ansible.execute',context)
    # validate-linux runs controlled wait-for-connection + validation once; no duplicate validation pass.
    assert len(observed)==2


def test_successful_plan_does_not_mark_failed_deployment_as_provisioned(client,headers,monkeypatch):
    d=deployment(client,headers)
    with session() as db:
        old=db.get(Job,d['job']['id']);old.status='failed'
        dep=db.get(Deployment,d['id']);dep.status='failed';dep.active_job_id=None;db.commit()
    job=client.post('/api/v1/jobs',headers={**headers,'Idempotency-Key':str(uuid.uuid4())},json={'operation':'terraform.plan','deployment_id':d['id']}).json()
    monkeypatch.setattr(TerraformExecutor,'execute',lambda *a:None)
    execute(job['id'])
    assert client.get('/api/v1/jobs/'+job['id'],headers=headers).json()['status']=='successful'
    assert client.get('/api/v1/deployments/'+d['id'],headers=headers).json()['status']=='failed'


def test_workflow_preserves_ssh_credential_and_pending_job_identity(client, headers):
    c, p, payload = resources(client, headers)
    ssh_data = {'name': 'Workflow SSH', 'type': 'ssh', 'username': 'clouduser',
                'secrets': {'password': 'ssh-private-password', 'known_hosts': 'host ssh-ed25519 test'}}
    ssh = client.post('/api/v1/credentials', headers=headers, json=ssh_data).json()
    payload['ansible'] = {'playbook': 'bootstrap-linux', 'credentials_id': ssh['id'],
                          'variables': {'hostname': 'vm-one', 'timezone': 'Europe/Warsaw'}}
    d = client.post('/api/v1/deployments', headers={**headers, 'Idempotency-Key': str(uuid.uuid4())}, json=payload).json()
    assert d['workflow']['ansible']['variables']['timezone'] == 'Europe/Warsaw'
    assert client.delete(f'/api/v1/credentials/{ssh["id"]}', headers=headers).status_code == 409
    assert client.put(f'/api/v1/credentials/{ssh["id"]}', headers=headers, json=ssh_data).status_code == 409
    assert client.post(f'/api/v1/jobs/{d["job"]["id"]}/cancel', headers=headers).status_code == 200
    # Cancelling the first apply does not discard the workflow; a later apply can retry it.
    assert client.delete(f'/api/v1/credentials/{ssh["id"]}', headers=headers).status_code == 409
    assert client.put(f'/api/v1/credentials/{ssh["id"]}', headers=headers, json=ssh_data).status_code == 200


def test_pending_standalone_ansible_job_preserves_credential(client, headers):
    ssh = client.post('/api/v1/credentials', headers=headers, json={
        'name': 'Standalone SSH', 'type': 'ssh', 'username': 'clouduser',
        'secrets': {'password': 'ssh-password', 'known_hosts': 'host ssh-ed25519 test'}}).json()
    j = client.post('/api/v1/jobs', headers={**headers, 'Idempotency-Key': str(uuid.uuid4())}, json={
        'operation': 'ansible.execute', 'ansible': {'playbook': 'validate-linux', 'credentials_id': ssh['id'],
                                                  'inventory': {'hosts': ['192.0.2.1']}}}).json()
    assert client.delete(f'/api/v1/credentials/{ssh["id"]}', headers=headers).status_code == 409
    assert client.post(f'/api/v1/jobs/{j["id"]}/cancel', headers=headers).status_code == 200
    assert client.delete(f'/api/v1/credentials/{ssh["id"]}', headers=headers).status_code == 200


def test_queued_job_survives_refresh_but_not_logout(system, monkeypatch, tmp_path):
    client, headers, admin = system
    _, _, payload = resources(client, headers)
    with session() as db:
        db.get(User, 1).must_change_password = False
        db.commit()
    pair = client.post('/api/v1/auth/login', json={'username': 'admin', 'password': admin['password']}).json()
    actor = {'Authorization': 'Bearer ' + pair['access_token'], 'Idempotency-Key': str(uuid.uuid4())}
    d = client.post('/api/v1/deployments', headers=actor, json=payload).json()
    rotated = client.post('/api/v1/auth/refresh', json={'refresh_token': pair['refresh_token']}).json()
    calls = []
    workspace = terraform_state_workspace(tmp_path)
    def successful_apply(*args):
        calls.append(1)
        return workspace
    monkeypatch.setattr(TerraformExecutor, 'execute', successful_apply)
    execute(d['job']['id'])
    assert client.get(f'/api/v1/jobs/{d["job"]["id"]}', headers=headers).json()['status'] == 'successful'
    actor = {'Authorization': 'Bearer ' + rotated['access_token'], 'Idempotency-Key': str(uuid.uuid4())}
    d2 = client.post('/api/v1/deployments', headers=actor, json={**payload, 'name': 'after-refresh'}).json()
    assert client.post('/api/v1/auth/logout', headers=actor).status_code == 200
    execute(d2['job']['id'])
    assert client.get(f'/api/v1/jobs/{d2["job"]["id"]}', headers=headers).json()['status'] == 'failed'
    assert calls == [1]


def test_node_discovery_filters_resources_and_never_leaks_provider_secrets(client, headers, monkeypatch):
    from app.providers.proxmox import ProxmoxProvider
    _, provider, _ = resources(client, headers)
    observed = []
    def get(self, path):
        observed.append(path)
        if path == '/cluster/resources?type=vm':
            return [{'vmid': 100, 'node': 'pve01', 'type': 'qemu'}, {'vmid': 101, 'node': 'pve02', 'type': 'qemu'}]
        return [{'storage': 'local', 'password': 'never-publish', 'content': 'images'}]
    monkeypatch.setattr(ProxmoxProvider, '_get', get)
    response = client.get(f'/api/v1/providers/{provider["id"]}/storages?node=pve02', headers=headers)
    assert observed[-1] == '/nodes/pve02/storage' and 'never-publish' not in response.text
    response = client.get(f'/api/v1/providers/{provider["id"]}/vms?node=pve02', headers=headers)
    assert [r['vmid'] for r in response.json()['items']] == [101]


def test_openapi_describes_public_response_contract(client):
    schema = client.get('/openapi.json').json()
    assert schema['openapi'].startswith('3.')
    for path, method, status in [('/users', 'post', '201'), ('/auth/login', 'post', '200'),
                                 ('/credentials/{id}', 'get', '200'), ('/deployments', 'post', '202'),
                                 ('/jobs/{id}/logs', 'get', '200')]:
        response = schema['paths']['/api/v1' + path][method]['responses'][status]['content']['application/json']['schema']
        assert response.get('$ref'), (path, response)
    for model, forbidden in [('UserOutput', 'password_hash'), ('CredentialOutput', 'encrypted_secret'), ('TokenOutput', 'token_hash')]:
        assert forbidden not in schema['components']['schemas'][model]['properties']
    parameters = schema['paths']['/api/v1/deployments']['post']['parameters']
    assert any(p['name'] == 'Idempotency-Key' and p['required'] for p in parameters)
    recreate_parameters = schema['paths']['/api/v1/deployments/{id}/recreate']['post']['parameters']
    assert any(p['name'] == 'Idempotency-Key' and p['required'] for p in recreate_parameters)


def test_idle_worker_and_dispatcher_are_visible_in_health(client, headers):
    import os, subprocess, time
    from app.jobs.queue import dispatch_once
    from app.security.core import redis_client
    worker = subprocess.Popen([os.sys.executable, '-m', 'app.jobs.queue'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            checks = client.get('/api/v1/health').json()['checks']
            if checks['workers']['online'] == 1:
                break
            time.sleep(.1)
        assert checks['workers']['online'] == 1
        assert not checks['dispatcher']
        dispatch_once()
        assert client.get('/api/v1/health').json()['checks']['dispatcher']
        # Exceed the API Redis socket timeout: an idle RQ worker must stay connected.
        time.sleep(4)
        assert worker.poll() is None
        assert client.get('/api/v1/health').json()['checks']['workers']['online'] == 1
        redis_client().delete('cp:dispatcher:heartbeat')
        assert not client.get('/api/v1/health').json()['checks']['dispatcher']
    finally:
        worker.terminate()
        output, _ = worker.communicate(timeout=15)
    assert 'TimeoutError' not in output and 'Error connecting' not in output


def test_qemu_guest_bootstrap_key_is_ephemeral_and_reused(tmp_path):
    first = prepare_qemu_bootstrap(tmp_path, '6b517d82-6bf3-4a77-a85e-acdeef123456', 'ssh_auth_missing')
    second = prepare_qemu_bootstrap(tmp_path, 'different-job-id', 'ssh_unreachable')

    assert first['username'].startswith('cpbootstrap')
    assert first['username'] == second['username']
    assert first['public_key'] == second['public_key']
    assert first['reason'] == 'ssh_auth_missing'
    assert stat.S_IMODE(first['private_key_path'].stat().st_mode) == 0o600
    assert load_qemu_bootstrap(tmp_path)['public_key'] == first['public_key']

    cleanup_qemu_bootstrap(tmp_path)
    assert load_qemu_bootstrap(tmp_path) is None
    assert not first['private_key_path'].exists()


def test_proxmox_qemu_agent_ssh_preflight_fails_early(monkeypatch):
    credential = SimpleNamespace(endpoint='https://pve.example.com:8006')

    class Provider:
        def __init__(self, credential):
            self.credential = credential

        def ssh_preflight(self, env):
            return {'ok': False, 'reason': 'ssh_unreachable', 'host': 'pve.example.com', 'port': 22}

    monkeypatch.setattr('app.providers.proxmox.ProxmoxProvider', Provider)

    with pytest.raises(ExecutionFailed, match='SSH preflight failed'):
        proxmox_ssh_preflight(credential, {'PROXMOX_VE_SSH_PORT': '22'})


def test_qemu_bootstrap_verifies_ssh_host_key_through_guest_agent():
    from app.jobs.worker import _verify_guest_ssh_host_key

    class HostKey:
        def get_name(self):
            return 'ssh-ed25519'

        def get_base64(self):
            return 'AAAATESTKEY'

    class Provider:
        def guest_exec(self, node, vm_id, command, timeout=30):
            assert node == 'pve'
            assert vm_id == 101
            assert command == ['/bin/cat', '/etc/ssh/ssh_host_ed25519_key.pub']
            return {'exited': True, 'exitcode': 0, 'out-data': 'ssh-ed25519 AAAATESTKEY vm\n'}

    _verify_guest_ssh_host_key(Provider(), 'pve', 101, HostKey())


def test_guest_credential_bootstrap_works_without_qemu_agent(monkeypatch, tmp_path):
    from app.jobs import worker as worker_module

    key_path = tmp_path / 'bootstrap-key'
    key_path.write_text('bootstrap-private-key')

    class HostKey:
        def get_name(self):
            return 'ssh-ed25519'

        def get_base64(self):
            return 'AAAABOOTSTRAPHOST'

    host_key = HostKey()

    class Transport:
        def get_remote_server_key(self):
            return host_key

    class Client:
        def __init__(self, name):
            self.name = name
            self.closed = False

        def get_transport(self):
            return Transport()

        def close(self):
            self.closed = True

    bootstrap_client = Client('bootstrap')
    verify_client = Client('verify')
    connect_calls = []
    commands = []
    cleaned = []
    stages = []
    logs = []

    monkeypatch.setattr(worker_module, 'load_qemu_bootstrap', lambda workspace: {
        'username': 'cpbootstrap1234',
        'public_key': 'ssh-ed25519 AAAABOOTSTRAP',
        'private_key_path': key_path,
        'reason': 'guest_credential_bootstrap',
    })
    monkeypatch.setattr(worker_module, '_guest_target_credential', lambda context: {
        'credential_id': 42,
        'username': 'vmadmin',
        'public_key': 'ssh-ed25519 AAAATARGET',
        'private_key': 'target-private-key',
        'password': 'target-password',
    })
    monkeypatch.setattr(worker_module, 'configured_deployment_ip', lambda context: '10.0.0.25')
    monkeypatch.setattr(worker_module, 'wait_for_vm', lambda *args, **kwargs: None)
    monkeypatch.setattr(
        worker_module,
        'wait_for_tcp_addresses',
        lambda context, addresses, port, timeout, label: '10.0.0.25',
    )
    monkeypatch.setattr(worker_module, '_guest_private_key', lambda value: object())

    def connect(address, username, **kwargs):
        connect_calls.append((address, username, kwargs))
        return bootstrap_client if username == 'cpbootstrap1234' else verify_client

    def run(client, command, *, stdin_text=None, timeout=300):
        commands.append((client.name, command, stdin_text, timeout))
        return ''

    monkeypatch.setattr(worker_module, '_guest_ssh_connect', connect)
    monkeypatch.setattr(worker_module, '_guest_ssh_run', run)
    monkeypatch.setattr(worker_module, 'cleanup_qemu_bootstrap', lambda workspace: cleaned.append(workspace))

    context = SimpleNamespace(
        deployment=SimpleNamespace(variables={'install_qemu_guest_agent': False}),
        job=SimpleNamespace(payload={'blueprint': {'guest_credential_id': 42}}),
        stage=stages.append,
        check=lambda: None,
        log=logs.append,
    )

    assert worker_module.ensure_qemu_guest_bootstrap(context, tmp_path, timeout=30) is True
    assert stages == ['workflow.guest_credential.bootstrap']
    assert connect_calls[0][1] == 'cpbootstrap1234'
    assert connect_calls[1][1] == 'vmadmin'
    assert connect_calls[1][2]['host_key'] is host_key
    assert any(
        command == 'sudo -n /bin/sh -s' and stdin_text and 'TARGET=vmadmin' in stdin_text
        for _client, command, stdin_text, _timeout in commands
    )
    assert any(
        command == 'sudo -n chpasswd' and stdin_text == 'vmadmin:target-password\n'
        for _client, command, stdin_text, _timeout in commands
    )
    assert any(
        client == 'verify' and command == 'sudo -n /bin/sh -s'
        and stdin_text and 'BOOTSTRAP=cpbootstrap1234' in stdin_text
        for client, command, stdin_text, _timeout in commands
    )
    assert not any('qemu-guest-agent' in (stdin_text or '') for _client, _command, stdin_text, _timeout in commands)
    assert cleaned == [tmp_path]
    assert any('guest-bootstrap.completed' in message for message in logs)
    assert bootstrap_client.closed is True
    assert verify_client.closed is True


def test_proxmox_guest_exec_and_password_use_guest_agent_api(client, headers, monkeypatch):
    from app.providers.proxmox import ProxmoxProvider

    credential, _, _ = resources(client, headers)
    with session() as db:
        row = db.get(Credential, credential['id'])
        provider = ProxmoxProvider(row)

    requests = []
    statuses = iter([
        {'exited': False},
        {'exited': True, 'exitcode': 0, 'out-data': 'ok'},
    ])

    def fake_request(method, path, *, data=None, json_data=None):
        requests.append((method, path, data, json_data))
        if path.endswith('/agent/exec'):
            return {'pid': 42}
        if path.endswith('/agent/set-user-password'):
            return {'result': None}
        raise AssertionError(path)

    monkeypatch.setattr(provider, '_request', fake_request)
    monkeypatch.setattr(provider, '_get', lambda path: next(statuses))
    monkeypatch.setattr('app.providers.proxmox.sleep', lambda seconds: None)

    status = provider.guest_exec('pve', 101, ['/bin/sh', '-c', 'id'], timeout=5)
    assert status['exitcode'] == 0
    assert requests[0][0] == 'POST'
    assert requests[0][1].endswith('/nodes/pve/qemu/101/agent/exec')
    assert requests[0][3] == {'command': ['/bin/sh', '-c', 'id']}

    provider.set_guest_user_password('pve', 101, 'clouduser', 'secret-value')
    assert requests[-1][1].endswith('/agent/set-user-password')
    assert requests[-1][3] == {
        'username': 'clouduser',
        'password': 'secret-value',
        'crypted': False,
    }


def test_provider_qemu_agent_readiness_endpoint(client, headers, monkeypatch):
    _, provider, _ = resources(client, headers)

    class Adapter:
        def ssh_preflight(self):
            return {'ok': False, 'reason': 'ssh_auth_missing', 'host': 'pve.example.com', 'port': 22}

    monkeypatch.setattr('app.api.infrastructure.provider_for', lambda credential: Adapter())
    response = client.get(
        f"/api/v1/providers/{provider['id']}/qemu-agent-readiness",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        'ok': False,
        'reason': 'ssh_auth_missing',
        'host': 'pve.example.com',
        'port': 22,
    }


def test_proxmox_ssh_preflight_distinguishes_auth_failure(client, headers, monkeypatch):
    from app.providers.proxmox import ProxmoxProvider
    import paramiko

    credential, _, _ = resources(client, headers)
    with session() as db:
        row = db.get(Credential, credential['id'])
        provider = ProxmoxProvider(row)

    class Client:
        def set_missing_host_key_policy(self, policy):
            return None

        def connect(self, **kwargs):
            raise paramiko.AuthenticationException('denied')

        def close(self):
            return None

    monkeypatch.setattr('app.providers.proxmox.paramiko.SSHClient', Client)
    result = provider.ssh_preflight({
        'PROXMOX_VE_SSH_USERNAME': 'root',
        'PROXMOX_VE_SSH_PASSWORD': 'wrong-password',
    })
    assert result['ok'] is False
    assert result['reason'] == 'ssh_auth_failed'
    assert result['host'] == 'pve.example.com'
    assert result['port'] == 22


def test_regular_deployment_ansible_uses_ip_inventory(client, headers, monkeypatch, tmp_path):
    from app.executors.ansible import AnsibleExecutor

    _, _, payload = resources(client, headers)
    ssh = client.post('/api/v1/credentials', headers=headers, json={
        'name': 'Post provision SSH',
        'type': 'ssh',
        'username': 'clouduser',
        'secrets': {'password': 'ssh-password'},
    })
    assert ssh.status_code == 201, ssh.text
    payload['ansible'] = {
        'playbook': 'bootstrap-linux',
        'credentials_id': ssh.json()['id'],
        'variables': {'hostname': 'vm01'},
    }
    created = client.post(
        '/api/v1/deployments',
        headers={**headers, 'Idempotency-Key': str(uuid.uuid4())},
        json=payload,
    )
    assert created.status_code == 202, created.text

    workspace = terraform_state_workspace(tmp_path, vm_id=701)
    monkeypatch.setattr(TerraformExecutor, 'execute', lambda *args: workspace)
    monkeypatch.setattr(
        'app.jobs.worker.wait_for_ansible_transport',
        lambda context, workspace, timeout=600, addresses=None: ['192.0.2.70'],
    )
    observed = {}
    monkeypatch.setattr(
        AnsibleExecutor,
        'execute',
        lambda self, operation, context: observed.update(
            operation=operation,
            hosts=list(context.ansible.inventory.hosts),
        ),
    )

    execute(created.json()['job']['id'])

    result = client.get('/api/v1/jobs/' + created.json()['job']['id'], headers=headers).json()
    assert result['status'] == 'successful'
    assert observed == {'operation': 'ansible.execute', 'hosts': ['192.0.2.70']}


def _finish_initial_deployment_job(deployment_body):
    with session() as db:
        dep = db.get(Deployment, deployment_body['id'])
        initial = db.get(Job, dep.active_job_id)
        initial.status = 'successful'
        dep.active_job_id = None
        dep.status = 'successful'
        db.commit()


def test_old_worker_cannot_clear_newer_active_job(client, headers, monkeypatch, tmp_path):
    created = deployment(client, headers)
    _finish_initial_deployment_job(created)

    first = client.post('/api/v1/jobs', headers={
        **headers, 'Idempotency-Key': str(uuid.uuid4())
    }, json={'operation': 'terraform.plan', 'deployment_id': created['id']})
    assert first.status_code == 202, first.text

    holder = {}

    def finish_after_new_job(self, operation, context):
        with session() as db:
            dep = db.get(Deployment, created['id'])
            newer = Job(
                operation='terraform.plan',
                deployment_id=dep.id,
                payload={'previous_status': 'successful'},
                status='queued',
                created_by=context.job.created_by,
                token_id=context.job.token_id,
                request_id=str(uuid.uuid4()),
                ip='127.0.0.1',
                source='API',
            )
            db.add(newer)
            db.flush()
            dep.active_job_id = newer.id
            dep.status = 'queued'
            holder['job_id'] = newer.id
            db.commit()
        return tmp_path

    monkeypatch.setattr(TerraformExecutor, 'execute', finish_after_new_job)
    execute(first.json()['id'])

    with session() as db:
        dep = db.get(Deployment, created['id'])
        old = db.get(Job, first.json()['id'])
        assert old.status == 'successful'
        assert dep.active_job_id == holder['job_id']
        assert dep.status == 'queued'



def test_worker_admits_queued_job_missing_quota_rollout_marker(client, headers, monkeypatch, tmp_path):
    from app.quotas.service import release_reservation

    created = deployment(client, headers)
    with session() as db:
        job = db.get(Job, created['job']['id'])
        reservation = db.get(QuotaReservation, job.payload['_quota_reservation_id'])
        release_reservation(db, reservation.id)
        payload = dict(job.payload or {})
        payload.pop('_quota_checked', None)
        payload.pop('_quota_reservation_id', None)
        job.payload = payload
        db.commit()

    workspace = terraform_state_workspace(tmp_path, vm_id=808)
    monkeypatch.setattr(TerraformExecutor, 'execute', lambda *args: workspace)

    execute(created['job']['id'])

    with session() as db:
        job = db.get(Job, created['job']['id'])
        reservation = db.query(QuotaReservation).filter_by(request_key=f"job:{job.id}").one()
        allocation = db.query(QuotaAllocation).filter_by(
            subject_type='deployment',
            subject_id=created['id'],
        ).one()
        assert job.status == 'successful'
        assert job.payload['_quota_checked'] is True
        assert reservation.status == 'committed'
        assert allocation.dimensions['vm_count'] == 1
