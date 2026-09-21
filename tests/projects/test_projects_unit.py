from dataclasses import FrozenInstanceError
from uuid import uuid4
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from app.models import RolePermission, UserRole
from app.projects import service
from app.projects.authorization import authorize, resolve_scope
from app.projects.models import Project, ProjectMembership, UserProjectContext
from app.projects.permissions import DEFAULT_PROJECT_ID
from app.projects.schemas import ProjectCreate, ProjectUpdate, ContextInput
from app.tenancy import service as tenants
from app.tenancy.models import Tenant, TenantMembership
from app.tenancy.permissions import DEFAULT_TENANT_ID
from app.tenancy.schemas import TenantCreate, MemberCreate, MemberUpdate, MemberRoles


def error(code, fn):
    with pytest.raises(HTTPException) as e: fn()
    assert e.value.detail['code'] == code


def tenant(d, slug='engineering'):
    return tenants.tenant_create(d.db, d.admin, TenantCreate(name=slug, slug=slug))


def project(d, t, slug='production'):
    return service.project_create(d.db, d.admin, ProjectCreate(tenant_id=t['id'], name=slug, slug=slug))


def member(d, t, p, actor, role=None):
    if d.db.get(TenantMembership, (t['id'], actor.user_id)) is None:
        tenants.member_create(d.db, d.admin, t['id'], MemberCreate(user_id=actor.user_id))
    return service.member_create(d.db, d.admin, p['id'], MemberCreate(user_id=actor.user_id, role_ids=[(role or d.project_role).id]))


def test_project_isolation_and_database_pagination(domain):
    d=domain; t=tenant(d); foreign=project(d,t,'aaa'); own=project(d,t,'zzz')
    member(d,t,own,d.alice,d.viewer_role)
    assert [x['id'] for x in service.project_list(d.db,d.alice,limit=1)['items']] == [own['id']]
    assert service.project_list(d.db,d.alice,limit=1)['total'] == 1
    assert service.project_list(d.db,d.alice,offset=1)['items'] == []
    error('PROJECT_NOT_FOUND', lambda: service.project_get(d.db,d.alice,foreign['id']))
    error('PROJECT_NOT_FOUND', lambda: service.project_get(d.db,d.alice,str(uuid4())))
    other=tenant(d,'finance')
    assert service.project_list(d.db,d.alice,tenant_id=other['id'])['total'] == 0
    error('PROJECT_NOT_FOUND', lambda: authorize(d.db,d.alice,own['id'],'projects.read',tenant_id=other['id']))
    assert d.db.scalar(select(func.count()).select_from(UserRole).where(UserRole.user_id == d.alice.user_id)) == 0


def test_parent_delegation_is_bounded_to_own_tenant(domain):
    d=domain; t=tenant(d); p=project(d,t); other=project(d,tenant(d,'finance'))
    tenants.member_create(d.db,d.admin,t['id'],MemberCreate(user_id=d.alice.user_id,role_ids=[d.tenant_role.id]))
    assert service.project_get(d.db,d.alice,p['id'])['id']==p['id']
    error('PROJECT_NOT_FOUND',lambda: service.project_get(d.db,d.alice,other['id']))
    created=service.project_create(d.db,d.alice,ProjectCreate(tenant_id=t['id'],name='Test',slug='test'))
    assert service.project_get(d.db,d.alice,created['id'])['name']=='Test'
    error('GRANT_EXCEEDS_SCOPE',lambda: service.member_roles(d.db,d.alice,created['id'],d.alice.user_id,
        MemberRoles(role_ids=[d.tenant_role.id],expected_version=1)))


@pytest.mark.parametrize('change',['project_member','tenant_member','tenant_disabled','token_revoke','user_disable'])
def test_revocations_take_effect_without_relogin(domain,change):
    d=domain;t=tenant(d);p=project(d,t);member(d,t,p,d.alice)
    if change=='project_member': d.db.get(ProjectMembership,(p['id'],d.alice.user_id)).status='disabled'
    elif change=='tenant_member': d.db.get(TenantMembership,(t['id'],d.alice.user_id)).status='disabled'
    elif change=='tenant_disabled': d.db.get(Tenant,t['id']).status='disabled'
    elif change=='token_revoke':
        from app.models import now
        d.tokens[1].revoked_at=now()
    else: d.users[1].is_active=False
    d.db.flush()
    error('AUTHENTICATION_REQUIRED' if change in {'token_revoke','user_disable'} else 'PROJECT_NOT_FOUND',
          lambda: service.project_get(d.db,d.alice,p['id']))


def test_token_ceiling_and_role_expansion(domain):
    d=domain;t=tenant(d);p=project(d,t);m=member(d,t,p,d.alice,d.viewer_role)
    d.viewer_role.permissions.append(d.perms['projects.update']);d.db.flush()
    assert 'projects.update' not in service.project_permissions(d.db,d.alice,p['id'])['permissions']
    service.member_roles(d.db,d.admin,p['id'],d.alice.user_id,MemberRoles(role_ids=[d.viewer_role.id],expected_version=m['version']))
    assert 'projects.update' in service.project_permissions(d.db,d.alice,p['id'])['permissions']
    d.tokens[1].kind='api';d.tokens[1].scopes=['projects.read'];d.db.flush()
    assert service.project_permissions(d.db,d.alice,p['id'])['permissions']==['projects.read']
    error('SCOPED_PERMISSION_REQUIRED',lambda: authorize(d.db,d.alice,p['id'],'projects.update'))
    d.db.execute(delete(RolePermission).where(RolePermission.role_id==d.viewer_role.id,
                                            RolePermission.permission_id==d.perms['projects.read'].id));d.db.flush()
    error('PROJECT_NOT_FOUND',lambda: service.project_get(d.db,d.alice,p['id']))


def test_eligible_directory_never_exposes_foreign_tenants(domain):
    d=domain;t=tenant(d);p=project(d,t);member(d,t,p,d.alice)
    other=tenant(d,'private');q=project(d,other);member(d,other,q,d.eve)
    tenants.member_create(d.db,d.admin,t['id'],MemberCreate(user_id=d.bob.user_id))
    result=service.eligible_members(d.db,d.alice,p['id'],limit=1)
    assert result['items']==[{'user_id':d.bob.user_id,'username':'bob'}]
    assert result['total']==1
    error('MEMBER_NOT_ASSIGNABLE',lambda:service.member_create(d.db,d.alice,p['id'],MemberCreate(user_id=d.eve.user_id)))
    m=service.member_create(d.db,d.alice,p['id'],MemberCreate(user_id=d.bob.user_id,role_ids=[d.viewer_role.id]))
    assert m['tenant_id']==t['id'] and m['project_id']==p['id']
    error('GRANT_EXCEEDS_SCOPE',lambda:service.member_roles(d.db,d.alice,p['id'],d.bob.user_id,
        MemberRoles(role_ids=[d.admin_role.id],expected_version=m['version'])))


def test_last_manager_and_revision_conflict(domain):
    d=domain;t=tenant(d);p=project(d,t);m=member(d,t,p,d.alice)
    error('VERSION_CONFLICT',lambda:service.member_update(d.db,d.alice,p['id'],d.alice.user_id,
        MemberUpdate(status='disabled',expected_version=999)))
    with pytest.raises(HTTPException) as e, d.db.begin_nested():
        service.member_delete(d.db,d.alice,p['id'],d.alice.user_id,m['version'])
    assert e.value.detail['code']=='PROJECT_MANAGER_REQUIRED'
    assert service.project_get(d.db,d.alice,p['id'])['id']==p['id']


def test_system_project_and_nonempty_tenant_protection(domain):
    d=domain;t=tenant(d);p=project(d,t)
    error('TENANT_NOT_EMPTY',lambda:tenants.tenant_delete(d.db,d.admin,t['id'],t['version']))
    error('SYSTEM_PROJECT_PROTECTED',lambda:service.project_delete(d.db,d.admin,DEFAULT_PROJECT_ID,1))
    default=service.project_get(d.db,d.admin,DEFAULT_PROJECT_ID)
    data={k:default[k] for k in ['name','slug','status','labels','metadata','description','default_environment']}
    error('SYSTEM_PROJECT_PROTECTED',lambda:service.project_update(d.db,d.admin,DEFAULT_PROJECT_ID,
        ProjectUpdate(**{**data,'name':'Rename'},expected_version=1)))
    service.project_delete(d.db,d.admin,p['id'],p['version'])
    tenants.tenant_delete(d.db,d.admin,t['id'],t['version'])


def test_context_is_authorized_versioned_and_does_not_fallback(domain):
    d=domain;t=tenant(d);p=project(d,t);member(d,t,p,d.alice)
    assert service.context_get(d.db,d.alice)=={'selected':None,'version':0}
    selected=service.context_set(d.db,d.alice,ContextInput(tenant_id=t['id'],project_id=p['id'],expected_version=0))
    assert selected['version']==1
    scope=resolve_scope(d.db,d.alice)
    assert scope.project_id==p['id'] and scope.source=='preference'
    with pytest.raises(FrozenInstanceError): scope.project_id='tampered'
    error('VERSION_CONFLICT',lambda:service.context_set(d.db,d.alice,ContextInput(tenant_id=t['id'],project_id=p['id'],expected_version=0)))
    error('PROJECT_REQUIRED',lambda:resolve_scope(d.db,d.alice,tenant_id=t['id']))
    error('PROJECT_NOT_FOUND',lambda:resolve_scope(d.db,d.alice,project_id=str(uuid4())))
    d.db.get(TenantMembership,(t['id'],d.alice.user_id)).status='disabled';d.db.flush()
    error('PROJECT_NOT_FOUND',lambda:resolve_scope(d.db,d.alice))
    assert service.context_clear(d.db,d.alice,None)=={'selected':None,'version':2}
    assert service.context_get(d.db,d.alice)=={'selected':None,'version':2}
    assert resolve_scope(d.db,d.admin).project_id==DEFAULT_PROJECT_ID


def test_cross_tenant_membership_foreign_key(domain):
    d=domain;t=tenant(d);p=project(d,t);other=tenant(d,'other')
    tenants.member_create(d.db,d.admin,other['id'],MemberCreate(user_id=d.alice.user_id))
    with pytest.raises(IntegrityError),d.db.begin_nested():
        d.db.add(ProjectMembership(project_id=p['id'],tenant_id=other['id'],user_id=d.alice.user_id))
        d.db.flush()


@pytest.mark.parametrize('payload',[{'tenant_id':str(uuid4())},{'default_environment':'x y'}, {'metadata':{'__proto__':{}}}])
def test_protected_or_invalid_input_is_rejected(payload):
    values={'name':'Test','slug':'test','expected_version':1,**payload}
    with pytest.raises(ValidationError): ProjectUpdate(**values)


def test_project_context_clear_does_not_reuse_an_old_revision(domain):
    d=domain;t=tenant(d);p=project(d,t);member(d,t,p,d.alice)
    request=ContextInput(tenant_id=t['id'],project_id=p['id'],expected_version=0)
    service.context_set(d.db,d.alice,request)
    assert service.context_clear(d.db,d.alice,1)['version']==2
    error('VERSION_CONFLICT',lambda:service.context_set(d.db,d.alice,request))
    current=service.context_set(d.db,d.alice,request.model_copy(update={'expected_version':2}))
    assert current['version']==3
    error('VERSION_CONFLICT',lambda:service.context_clear(d.db,d.alice,1))
    assert service.context_get(d.db,d.alice)['selected']['id']==p['id']

def test_context_database_rejects_partially_cleared_scope(domain):
    d=domain
    with pytest.raises(IntegrityError),d.db.begin_nested():
        d.db.add(UserProjectContext(user_id=d.alice.user_id,tenant_id=DEFAULT_TENANT_ID,project_id=None))
        d.db.flush()
