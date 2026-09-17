import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from sqlalchemy import select
from app.database import session
from app.models import Credential, Deployment, Idempotency, Job
from app.security.core import decrypt_secret
from app.executors.base import ExecutionFailed
from app.executors.terraform import TerraformExecutor, workspace_lock
from app.jobs.worker import execute


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


def test_worker_rechecks_revoked_permissions(client,headers,monkeypatch):
    d=deployment(client,headers)
    with session() as db:
        from app.models import Token, now
        token=db.get(Token,db.get(Job,d['job']['id']).token_id);token.revoked_at=now();db.commit()
    called=[];monkeypatch.setattr(TerraformExecutor,'execute',lambda *a:called.append(1))
    execute(d['job']['id'])
    with session() as db:assert db.get(Job,d['job']['id']).status=='failed'
    assert not called


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


def test_dispatcher_and_rq_execute_durable_job(client,headers,monkeypatch):
    from app.jobs.queue import dispatch_once, queue
    from rq import SimpleWorker
    from rq.serializers import JSONSerializer
    from app.security.core import redis_client
    d=deployment(client,headers)
    monkeypatch.setattr(TerraformExecutor,'execute',lambda *a:None)
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
    assert len(observed)==3
