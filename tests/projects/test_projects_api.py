"""Real HTTP authentication, idempotency, OpenAPI, audit and event integration."""
from uuid import uuid4
from sqlalchemy import func, select
from app.database import session
from app.models import Audit, EventRecord, Role, Token
from app.projects.models import ProjectMembership
from app.tenancy.models import TenantMembership
from conftest import new_user


def setup_project(system):
    client, headers, _ = system
    t=client.post('/api/v1/tenants',headers=headers,json={'name':'Engineering','slug':'engineering'})
    assert t.status_code==201,t.text
    p=client.post('/api/v1/projects',headers=headers,json={'tenant_id':t.json()['id'],'name':'Production','slug':'production'})
    assert p.status_code==201,p.text
    user, user_headers=new_user(client,headers,'project-user')
    r=client.post(f"/api/v1/tenants/{t.json()['id']}/members",headers=headers,json={'user_id':user['id']})
    assert r.status_code==201,r.text
    with session() as db:
        role_id=db.scalar(select(Role.id).where(Role.name=='Project Administrator'))
    r=client.post(f"/api/v1/projects/{p.json()['id']}/members",headers=headers,json={'user_id':user['id'],'role_ids':[role_id]})
    assert r.status_code==201,r.text
    return client,headers,t.json(),p.json(),user,user_headers


def test_projects_http_isolation_context_and_audit(system):
    client,headers,t,p,user,h=setup_project(system)
    foreign=client.post('/api/v1/projects',headers=headers,json={'tenant_id':t['id'],'name':'Private','slug':'private'}).json()
    page=client.get('/api/v1/projects?limit=1',headers=h)
    assert page.status_code==200,page.text
    assert page.json()['total']==1 and page.json()['items'][0]['id']==p['id']
    for ident in [foreign['id'],str(uuid4())]:
        denied=client.get(f'/api/v1/projects/{ident}',headers=h)
        assert denied.status_code==404 and denied.json()['detail']['code']=='PROJECT_NOT_FOUND'
    selected=client.put('/api/v1/project-context',headers=h,json={'tenant_id':t['id'],'project_id':p['id'],'expected_version':0})
    assert selected.status_code==200,selected.text
    assert selected.json()['selected']['id']==p['id']
    forged=client.put('/api/v1/project-context',headers=h,json={'tenant_id':str(uuid4()),'project_id':p['id'],'expected_version':1})
    assert forged.status_code==404
    history=client.get(f"/api/v1/projects/{p['id']}/audit",headers=h)
    assert history.status_code==200,history.text
    assert {'project.created','project.member.added','project.context.selected'} <= {row['action'] for row in history.json()['items']}
    assert foreign['id'] not in history.text
    assert all('token_id' not in item and 'ip' not in item for item in history.json()['items'])
    with session() as db:
        assert db.scalar(select(func.count()).select_from(EventRecord).where(EventRecord.type=='project.created'))==2
        db.get(TenantMembership,(t['id'],user['id'])).status='disabled';db.commit()
    assert client.get('/api/v1/project-context',headers=h).status_code==404
    assert client.get('/api/v1/projects',headers=h).json()['items']==[]
    assert client.delete('/api/v1/project-context',headers=h).status_code==200


def test_project_idempotency_rechecks_current_permission(system):
    client,headers,t,p,user,h=setup_project(system)
    key=str(uuid4()); body={'tenant_id':t['id'],'name':'Once','slug':'once'}
    repeated=headers|{'Idempotency-Key':key}
    a=client.post('/api/v1/projects',headers=repeated,json=body)
    b=client.post('/api/v1/projects',headers=repeated,json=body)
    assert a.status_code==b.status_code==201 and a.json()==b.json()
    from app.security.core import digest
    with session() as db:
        token=db.scalar(select(Token).where(Token.token_hash==digest(headers['Authorization'].removeprefix('Bearer '))))
        token.scopes=[x for x in token.scopes if x!='projects.create'];db.commit()
    denied=client.post('/api/v1/projects',headers=repeated,json=body)
    assert denied.status_code==403


def test_project_replay_rechecks_eligible_tenant_member(system):
    client,headers,t,p,user,h=setup_project(system)
    bob,_=new_user(client,headers,'eligible-bob')
    client.post(f"/api/v1/tenants/{t['id']}/members",headers=headers,json={'user_id':bob['id']})
    repeated=h|{'Idempotency-Key':str(uuid4())};body={'user_id':bob['id']}
    path=f"/api/v1/projects/{p['id']}/members"
    first=client.post(path,headers=repeated,json=body)
    assert first.status_code==201,first.text
    assert client.post(path,headers=repeated,json=body).json()==first.json()
    with session() as db:
        db.get(TenantMembership,(t['id'],bob['id'])).status='disabled';db.commit()
    assert client.post(path,headers=repeated,json=body).status_code==422


def test_project_openapi_and_default(system):
    client,headers,_=system
    from app.projects.permissions import DEFAULT_PROJECT_ID
    r=client.get(f'/api/v1/projects/{DEFAULT_PROJECT_ID}',headers=headers)
    assert r.status_code==200 and r.json()['is_system']
    assert client.get('/api/v1/projects').status_code==401
    schema=client.get('/openapi.json').json()
    for path in ['/api/v1/projects','/api/v1/projects/{project_id}/members','/api/v1/project-context']:
        assert path in schema['paths']
    assert schema['components']['schemas']['ProjectCreate']['additionalProperties'] is False
    assert 'tenant_id' not in schema['components']['schemas']['ProjectUpdate']['properties']
    with session() as db:
        infra=db.scalar(select(Role).where(Role.name=='Infrastructure Administrator'))
        assert not any(x.name.startswith('projects.') for x in infra.permissions)
