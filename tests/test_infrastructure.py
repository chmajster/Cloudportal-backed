import json
import uuid
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from sqlalchemy import select
from app.database import session
from app.models import Credential, Deployment, Idempotency, Job, ManagedVM, User, now
from app.security.core import decrypt_secret
from app.executors.base import Cancelled, ExecutionFailed, run_process
from app.executors.terraform import TerraformExecutor, workspace_lock
from app.jobs.worker import execute
from app.jobs.queue import reconcile_cancelled_jobs
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
    def fail(*args):raise ExecutionFailed('terraform exited with code 1')
    monkeypatch.setattr(TerraformExecutor,'execute',fail)
    execute(d['job']['id'])
    result=client.get('/api/v1/jobs/'+d['job']['id'],headers=headers).json()
    assert result['status']=='failed' and 'code 1' in result['error']
    assert client.get('/api/v1/deployments/'+d['id'],headers=headers).json()['active_job_id'] is None
    logs=client.get('/api/v1/jobs/'+d['job']['id']+'/logs',headers=headers).json()
    assert logs['items'] and logs['request_id']==result['request_id']


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
        job.heartbeat_at = now() - timedelta(seconds=30)
        dep = db.get(Deployment, d['id'])
        dep.status = 'running'
        db.commit()

    response = client.post('/api/v1/jobs/' + d['job']['id'] + '/cancel', headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'cancelling'
    assert response.json()['cancel_requested'] is True
    assert client.get('/api/v1/deployments/' + d['id'], headers=headers).json()['status'] == 'cancelling'

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

    inventory = client.get('/api/v1/inventory/vms?management_mode=terraform', headers=headers).json()['items']
    row = next(item for item in inventory if item['deployment_id'] == d['id'])
    assert row['vm_id'] == 612
    assert row['node'] == 'pve'
    assert row['lifecycle_status'] == 'active'

    repeated = client.post('/api/v1/inventory/reconcile', headers=headers)
    assert repeated.status_code == 200
    assert repeated.json()['repaired_count'] == 0


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
