"""Existing HTTP entry points, not a separate mock resource API."""
from uuid import uuid4
from types import SimpleNamespace
import pytest
from sqlalchemy import select, func
from conftest import new_user
from app.database import session
from app.models import Deployment, Job, JobLog, ManagedVM, ManagedResource, Token
from app.projects.models import Project, ProjectMembership
from app.tenancy.models import TenantMembership
from app.resource_scope.models import ProjectCredentialAccess
from app.jobs.worker import execute, validate_authorization
from app.executors.base import ExecutionFailed
from app.executors.terraform import TerraformExecutor


def project(client, headers, slug):
    t = client.post('/api/v1/tenants', headers=headers, json={'name': slug, 'slug': slug})
    assert t.status_code == 201, t.text
    p = client.post('/api/v1/projects', headers=headers, json={'tenant_id': t.json()['id'], 'name': slug, 'slug': slug})
    assert p.status_code == 201, p.text
    return p.json()


def scope_headers(headers, project):
    return headers | {'X-Tenant-ID': project['tenant_id'], 'X-Project-ID': project['id']}


def infrastructure(client, headers, label='PVE'):
    c = client.post('/api/v1/credentials', headers=headers, json={'name': label, 'type': 'proxmox',
        'endpoint': 'https://pve.example.com:8006', 'username': 'root@pam',
        'secrets': {'token_id': 'root@pam!terraform', 'token_secret': 'test-secret-private'}})
    assert c.status_code == 201, c.text
    p = client.post('/api/v1/providers', headers=headers, json={'name': label, 'type': 'proxmox', 'credentials_id': c.json()['id']})
    assert p.status_code == 201, p.text
    return c.json(), p.json()


def assign(client, headers, p, c, provider):
    version = client.get('/api/v1/projects/'+p['id'], headers=headers).json()['version']
    response = client.put(f"/api/v1/projects/{p['id']}/infrastructure-access", headers=headers,
        json={'expected_version': version, 'provider_ids': [provider['id']], 'credential_ids': [c['id']]})
    assert response.status_code == 200, response.text
    return response.json()


def member(client, headers, p, username='project-reader'):
    u, uh = new_user(client, headers, username)
    role_id = next(row['id'] for row in client.get('/api/v1/roles?limit=200', headers=headers).json()['items']
                   if row['name'] == 'Project Administrator')
    response = client.post(f"/api/v1/tenants/{p['tenant_id']}/members", headers=headers, json={'user_id': u['id']})
    assert response.status_code == 201, response.text
    response = client.post(f"/api/v1/projects/{p['id']}/members", headers=headers,
                           json={'user_id': u['id'], 'role_ids': [role_id]})
    assert response.status_code == 201, response.text
    return u, scope_headers(uh, p)


def create(client, headers, c, provider, name='vm'):
    payload = {'name': name, 'provider_id': provider['id'], 'credentials_id': c['id'],
               'variables': {'name': name, 'node': 'pve', 'template_id': 9000, 'storage': 'local-lvm'}}
    h = headers | {'Idempotency-Key': str(uuid4())}
    result = client.post('/api/v1/deployments', headers=h, json=payload)
    assert result.status_code == 202, result.text
    return result.json(), h, payload


def test_existing_api_filters_sql_counts_direct_ids_logs_and_replay(system):
    client, headers, _ = system
    p, q = project(client, headers, 'alpha'), project(client, headers, 'beta')
    c, provider = infrastructure(client, headers)
    assign(client, headers, p, c, provider); assign(client, headers, q, c, provider)
    ph, qh = scope_headers(headers, p), scope_headers(headers, q)
    first, replay, payload = create(client, ph, c, provider, 'alpha')
    second, _, _ = create(client, qh, c, provider, 'beta')
    assert first['tenant_id'] == p['tenant_id'] and first['project_id'] == p['id']
    assert first['job']['project_id'] == p['id']
    with session() as db:
        db.add_all([JobLog(job_id=first['job']['id'], message='alpha-only'),
                    JobLog(job_id=second['job']['id'], message='beta-secret')]); db.commit()
    for h, expected in [(ph, first), (qh, second)]:
        listing = client.get('/api/v1/deployments?limit=1', headers=h)
        assert listing.status_code == 200, listing.text
        assert len(listing.json()['items']) == 1 and listing.json()['items'][0]['id'] == expected['id']
    # Even a platform admin explicitly selecting alpha cannot read beta through direct IDs.
    for path in ['/deployments/'+second['id'], '/jobs/'+second['job']['id'], '/jobs/'+second['job']['id']+'/logs']:
        denied = client.get('/api/v1'+path, headers=ph)
        assert denied.status_code == 404 and 'beta-secret' not in denied.text, denied.text
    assert client.get('/api/v1/deployments', headers=headers).json()['items'] == []
    again = client.post('/api/v1/deployments', headers=replay, json=payload)
    assert again.status_code == 202 and again.json()['id'] == first['id'], again.text
    moved = client.post('/api/v1/deployments', headers=replay | scope_headers({}, q), json=payload)
    assert moved.status_code == 409, moved.text
    assert client.post('/api/v1/jobs', headers=ph | {'Idempotency-Key': str(uuid4())},
        json={'operation': 'terraform.destroy', 'deployment_id': second['id']}).status_code == 404


def test_scoped_grants_are_not_global_and_assignments_are_explicit(system):
    client, headers, _ = system
    p, q = project(client, headers, 'grants'), project(client, headers, 'other')
    c, provider = infrastructure(client, headers)
    ph = scope_headers(headers, p)
    assert client.get('/api/v1/providers', headers=ph).json()['items'] == []
    assert client.get(f"/api/v1/credentials/{c['id']}", headers=ph).status_code == 404
    assign(client, headers, p, c, provider)
    u, uh = member(client, headers, p)
    assert client.get('/api/v1/auth/me', headers=uh).json()['permissions'] == []
    response = client.get('/api/v1/providers', headers=uh)
    assert response.status_code == 200 and len(response.json()['items']) == 1, response.text
    assert client.get('/api/v1/providers', headers=scope_headers(uh, q)).status_code == 404
    assert client.get('/api/v1/users', headers=uh).status_code == 403
    gated = client.post('/api/v1/deployments', headers=uh | {'Idempotency-Key': str(uuid4())},
        json={'name': 'delegated', 'provider_id': provider['id'], 'credentials_id': c['id'],
              'variables': {'name':'delegated','node':'pve','template_id':9000,'storage':'local-lvm'}})
    assert gated.status_code == 409 and gated.json()['detail']['code'] == 'PROJECT_EXECUTION_REQUIRES_PLATFORM_ADMIN'

    assert client.put(f"/api/v1/projects/{p['id']}/infrastructure-access", headers=uh,
        json={'expected_version': 2, 'provider_ids': [], 'credential_ids': []}).status_code == 403
    with session() as db:
        db.get(ProjectMembership, (p['id'], u['id'])).status = 'disabled'; db.commit()
    assert client.get('/api/v1/providers', headers=uh).status_code == 404


def test_api_token_ceiling_and_invalid_context_never_fall_back(system):
    client, headers, _ = system
    p = project(client, headers, 'ceiling'); c, provider = infrastructure(client, headers)
    assign(client, headers, p, c, provider)
    token = client.post('/api/v1/tokens', headers=headers, json={'name': 'reader', 'scopes': ['governance.admin', 'providers.read']})
    assert token.status_code == 201, token.text
    h = scope_headers({'Authorization': 'Bearer '+token.json()['token']}, p)
    assert client.get('/api/v1/providers', headers=h).status_code == 200
    assert client.get('/api/v1/credentials', headers=h).status_code == 403
    assert client.get('/api/v1/providers', headers=headers | {'X-Project-ID': p['id']}).status_code == 422
    assert client.get('/api/v1/providers', headers=headers | {'X-Tenant-ID': 'bad', 'X-Project-ID': p['id']}).status_code == 422
    assert client.get('/api/v1/providers?project_id='+str(uuid4()), headers=scope_headers(headers,p)).status_code == 422
    assert client.get('/api/v1/providers', headers=headers | {'X-Tenant-ID': str(uuid4()), 'X-Project-ID': p['id']}).status_code == 404


def test_worker_persists_scope_and_revoked_assignment_prevents_executor(system, monkeypatch):
    client, headers, _ = system
    p = project(client, headers, 'worker'); c, provider = infrastructure(client, headers)
    assign(client, headers, p, c, provider)
    d, replay, payload = create(client, scope_headers(headers, p), c, provider)
    calls = []
    monkeypatch.setattr(TerraformExecutor, 'execute', lambda *args: calls.append(True))
    with session() as db:
        job = db.get(Job, d['job']['id'])
        assert job.project_id == p['id'] and job.tenant_id == p['tenant_id']
        validate_authorization(db, job)
    with session() as db:
        db.delete(db.get(ProjectCredentialAccess, (p['id'], c['id']))); db.commit()
    denied_replay = client.post('/api/v1/deployments', headers=replay, json=payload)
    assert denied_replay.status_code == 404, denied_replay.text
    execute(d['job']['id'])
    assert calls == []
    with session() as db:
        job = db.get(Job, d['job']['id']); dep = db.get(Deployment, d['id'])
        assert job.status == 'failed', job.error
        assert dep.active_job_id is None
        assert dep.project_id == job.project_id == p['id']


def test_provider_raw_surfaces_and_foreign_import_fail_closed(system, monkeypatch):
    from app.providers.proxmox import ProxmoxProvider
    client, headers, _ = system
    p = project(client, headers, 'raw'); c, provider = infrastructure(client, headers)
    assign(client, headers, p, c, provider)
    d, _, _ = create(client, scope_headers(headers,p), c, provider)
    with session() as db:
        db.add(ManagedVM(provider_id=provider['id'], deployment_id=d['id'], node='pve', vm_id=701,
                         name='private', created_by=1)); db.commit()
    calls=[]
    monkeypatch.setattr(ProxmoxProvider, 'discover', lambda *_: calls.append(True) or [])
    result=client.post('/api/v1/inventory/vms/import', headers=headers | {'Idempotency-Key': str(uuid4())},
                      json={'provider_id':provider['id'], 'vm_id':701})
    assert result.status_code == 404 and calls == [], result.text
    for h in [headers, scope_headers(headers,p)]:
        result=client.get(f"/api/v1/providers/{provider['id']}/vms/pve/701/status",headers=h)
        assert result.status_code in (404,409), result.text
    with session() as db:
        db.add(ManagedVM(provider_id=provider['id'], node='pve', vm_id=702, name='legacy', created_by=1)); db.commit()
    monkeypatch.setattr(ProxmoxProvider, 'discover', lambda *_: [
        {'vmid':701,'name':'private'}, {'vmid':702,'name':'legacy'}, {'vmid':703,'name':'unattributed-pending'},
        {'name':'missing-provider-id'}])
    own=client.get(f"/api/v1/providers/{provider['id']}/vms", headers=scope_headers(headers,p))
    default=client.get(f"/api/v1/providers/{provider['id']}/vms",headers=headers)
    assert [row['vmid'] for row in own.json()['items']] == [701],own.text
    assert [row['vmid'] for row in default.json()['items']] == [702],default.text


def test_worker_success_creates_inventory_in_the_saved_project(system, monkeypatch, tmp_path):
    import json
    client, headers, _ = system
    p = project(client, headers, 'provisioned'); c, provider = infrastructure(client, headers)
    assign(client, headers, p, c, provider)
    d, _, _ = create(client, scope_headers(headers,p), c, provider)
    workspace=tmp_path/'terraform';workspace.mkdir()
    (workspace/'terraform.tfstate').write_text(json.dumps({'outputs':{'vm_id':{'value':881}}}))
    monkeypatch.setattr(TerraformExecutor,'execute',lambda *args:workspace)
    execute(d['job']['id'])
    with session() as db:
        job=db.get(Job,d['job']['id']);dep=db.get(Deployment,d['id'])
        assert job.status=='successful',job.error
        for row in [dep,job,db.scalar(select(ManagedVM).where(ManagedVM.deployment_id==dep.id)),
                    db.scalar(select(ManagedResource).where(ManagedResource.deployment_id==dep.id))]:
            assert (row.tenant_id,row.project_id)==(p['tenant_id'],p['id'])
    own=client.get('/api/v1/inventory/vms',headers=scope_headers(headers,p))
    default=client.get('/api/v1/inventory/vms',headers=headers)
    assert len(own.json()['items'])==1 and default.json()['items']==[],(own.text,default.text)


def test_console_capability_rechecks_token_revocation(system, monkeypatch):
    from app.providers.proxmox import ProxmoxProvider
    from app.models import now
    client,headers,_=system
    c,provider=infrastructure(client,headers,'console')
    monkeypatch.setattr(ProxmoxProvider,'console_session',lambda *_:{'port':5900,'ticket':'ephemeral','password':'rfb'})
    response=client.post(f"/api/v1/providers/{provider['id']}/vms/pve/111/console",headers=headers)
    assert response.status_code==200,response.text
    body=response.json()
    assert client.get(body['local_rfb_module']).status_code==200
    legacy_asset=body['rfb_module']
    assert client.get(legacy_asset).status_code==200
    with session() as db:
        token=db.scalar(select(Token).where(Token.kind=='api'));token.revoked_at=now();db.commit()
    response=client.get(legacy_asset)
    assert response.status_code==401,response.text


def test_destroy_replay_and_project_deletion_guard(system):
    client,headers,_=system
    p=project(client,headers,'destroy-replay');c,provider=infrastructure(client,headers)
    assign(client,headers,p,c,provider)
    h=scope_headers(headers,p);d,_,_=create(client,h,c,provider)
    with session() as db:
        dep=db.get(Deployment,d['id']);old=db.get(Job,d['job']['id'])
        dep.active_job_id=None;dep.status='successful';old.status='successful';db.commit()
    replay=h|{'Idempotency-Key':str(uuid4())}
    path='/api/v1/deployments/'+d['id']+'/destroy'
    one=client.post(path,headers=replay);two=client.post(path,headers=replay)
    assert one.status_code==two.status_code==202 and one.json()==two.json(),(one.text,two.text)
    version=client.get('/api/v1/projects/'+p['id'],headers=headers).json()['version']
    response=client.delete(f"/api/v1/projects/{p['id']}?expected_version={version}",headers=headers)
    assert response.status_code==409,response.text


def test_legacy_default_idempotency_response_is_upgraded_without_recreation(system):
    from app.models import Idempotency
    client,headers,_=system
    c,provider=infrastructure(client,headers)
    d,replay,payload=create(client,headers,c,provider)
    with session() as db:
        old=db.scalar(select(Idempotency).where(Idempotency.path=='/api/v1/deployments'))
        legacy=dict(old.response)
        legacy.pop('tenant_id');legacy.pop('project_id')
        legacy['job']=dict(legacy['job']);legacy['job'].pop('tenant_id');legacy['job'].pop('project_id')
        old.response=legacy;db.commit()
    response=client.post('/api/v1/deployments',headers=replay,json=payload)
    assert response.status_code==202 and response.json()['id']==d['id'],response.text
    assert response.json()['project_id']==d['project_id']
    with session() as db:assert db.scalar(select(func.count()).select_from(Deployment))==1


def test_access_assignment_validation_pagination_revision_and_no_auto_regrowth(system):
    client,headers,_=system
    p=project(client,headers,'assignments');c,provider=infrastructure(client,headers)
    path=f"/api/v1/projects/{p['id']}/infrastructure-access"
    invalid=client.put(path,headers=headers,json={'expected_version':1,'provider_ids':[provider['id']],'credential_ids':[]})
    assert invalid.status_code==422,invalid.text
    record=assign(client,headers,p,c,provider)
    assert client.put(path,headers=headers,json={'expected_version':1,'provider_ids':[],'credential_ids':[]}).status_code==409
    page=client.get(path+'?limit=1&offset=1',headers=headers)
    assert page.status_code==200 and page.json()['provider_ids']==[] and page.json()['provider_total']==1,page.text
    assert client.get(path+'?limit=201',headers=headers).status_code==422
    removed=client.put(path,headers=headers,json={'expected_version':record['version'],'provider_ids':[],'credential_ids':[]})
    assert removed.status_code==200,removed.text
    for _ in range(2):
        assert client.get('/api/v1/providers',headers=scope_headers(headers,p)).json()['items']==[]
        assert client.get('/api/v1/credentials',headers=scope_headers(headers,p)).json()['items']==[]


def test_implicit_default_access_changes_conflict_with_stale_replacement(system):
    from app.resource_scope.authorization import DEFAULT_SCOPE
    client,headers,_=system
    c,provider=infrastructure(client,headers,'initial')
    path=f'/api/v1/projects/{DEFAULT_SCOPE.project_id}/infrastructure-access'
    before=client.get(path,headers=headers).json()
    infrastructure(client,headers,'new-reference')
    after=client.get(path,headers=headers).json()
    assert after['version']>before['version']
    stale=client.put(path,headers=headers,json={'expected_version':before['version'],
                       'provider_ids':before['provider_ids'],'credential_ids':before['credential_ids']})
    assert stale.status_code==409,stale.text
    parameters=client.get('/openapi.json').json()['paths']['/api/v1/deployments']['post']['parameters']
    assert {('header','X-Tenant-ID'),('header','X-Project-ID')} <= {(p['in'],p['name']) for p in parameters}
