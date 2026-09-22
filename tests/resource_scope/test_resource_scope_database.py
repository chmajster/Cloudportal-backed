"""SQL isolation, identity-map safety and database foreign-key invariants."""
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event, select, func, update, delete, text
from sqlalchemy.orm import Session, aliased
from sqlalchemy.exc import IntegrityError
from app.database import Base
from app import models as m
from app.projects.models import Project
from app.tenancy.models import Tenant
from app.resource_scope.models import ProjectCredentialAccess, ProjectProviderAccess
from app.resource_scope.authorization import Scope, DEFAULT_SCOPE
from app.resource_scope.database import ScopedSession, bind_scope
from app.inventory_sync import sync_deployment_inventory


@pytest.fixture
def store():
    engine=create_engine('sqlite://')
    @event.listens_for(engine, 'connect')
    def foreign_keys(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    foreign=Scope(str(uuid4()),str(uuid4()))
    with Session(engine) as db:
        db.add(m.User(id=1,username='owner',email='owner@example.com',password_hash='unused'))
        for scope in (DEFAULT_SCOPE,foreign):
            db.add(Tenant(id=scope.tenant_id,name=scope.tenant_id,slug=scope.tenant_id))
        db.flush()
        for scope in (DEFAULT_SCOPE,foreign):
            db.add(Project(id=scope.project_id,tenant_id=scope.tenant_id,name=scope.project_id,slug=scope.project_id))
        db.add(m.Credential(id=1,name='credential',type='proxmox',encrypted_secret=b'no-secret'))
        db.flush();db.add(m.Provider(id=1,name='provider',type='proxmox',credentials_id=1));db.flush()
        for scope in (DEFAULT_SCOPE,foreign):
            db.add(ProjectCredentialAccess(tenant_id=scope.tenant_id,project_id=scope.project_id,credential_id=1))
            db.add(ProjectProviderAccess(tenant_id=scope.tenant_id,project_id=scope.project_id,provider_id=1))
        db.commit()
    ids=[]
    with ScopedSession(engine) as db:
        for scope in (DEFAULT_SCOPE,foreign):
            d=m.Deployment(name=scope.project_id,provider_id=1,credentials_id=1,template='proxmox-vm',
                variables={'node':'pve'},created_by=1,tenant_id=scope.tenant_id,project_id=scope.project_id)
            db.add(d);db.flush()
            j=m.Job(deployment_id=d.id,operation='terraform.apply',created_by=1,request_id=str(uuid4()))
            db.add(j);db.flush()
            log=m.JobLog(job_id=j.id,message=scope.project_id);db.add(log);db.flush()
            ids.append((d.id,j.id,log.id))
        db.commit()
    yield engine,foreign,ids
    engine.dispose()


def test_orm_select_count_alias_join_and_get_are_project_filtered(store):
    engine,scope,ids=store
    for current,expected,other in [(DEFAULT_SCOPE,ids[0],ids[1]),(scope,ids[1],ids[0])]:
        with ScopedSession(engine) as db:
            bind_scope(db,current)
            assert list(db.scalars(select(m.Deployment.id))) == [expected[0]]
            assert db.scalar(select(func.count()).select_from(m.Deployment)) == 1
            assert db.get(m.Deployment,other[0]) is None
            assert db.get(m.Job,other[1]) is None
            assert db.get(m.JobLog,other[2]) is None
            a=aliased(m.Deployment)
            assert list(db.scalars(select(a.id))) == [expected[0]]
            assert list(db.scalars(select(m.Job.id).join(m.Deployment,m.Job.deployment_id==m.Deployment.id))) == [expected[1]]
            assert len(list(db.scalars(select(m.JobLog)))) == 1
            assert len(list(db.scalars(select(ProjectCredentialAccess)))) == 1
            cp=aliased(m.Credential)
            assert list(db.scalars(select(cp.id))) == [1]


@pytest.mark.parametrize('model,index',[(m.Deployment,0),(m.Job,1),(m.JobLog,2)])
def test_binding_rejects_preloaded_foreign_identity_map(store,model,index):
    engine,scope,ids=store
    with ScopedSession(engine) as db:
        cached=db.get(model,ids[1][index])
        with pytest.raises(HTTPException) as error:
            bind_scope(db,DEFAULT_SCOPE)
        assert error.value.detail['code']=='SCOPE_SESSION_REUSE'
        assert cached is not None


def test_bulk_writes_filter_and_cannot_change_scope(store):
    engine,scope,ids=store
    with ScopedSession(engine) as db:
        bind_scope(db,DEFAULT_SCOPE)
        assert db.execute(update(m.Deployment).values(name='changed')).rowcount==1
        with pytest.raises(HTTPException):
            db.execute(update(m.Deployment).values(project_id=scope.project_id))
        with pytest.raises(HTTPException):
            db.execute(text('SELECT * FROM deployments'))
        db.commit()
    with ScopedSession(engine) as db:
        assert db.get(m.Deployment,ids[0][0]).name=='changed'
        assert db.get(m.Deployment,ids[1][0]).name==scope.project_id


def test_ownership_is_immutable_and_children_inherit_parent(store):
    engine,scope,ids=store
    with ScopedSession(engine) as db:
        row=m.Job(deployment_id=ids[1][0],operation='terraform.destroy',created_by=1,request_id=str(uuid4()))
        db.add(row);db.flush()
        assert (row.tenant_id,row.project_id)==(scope.tenant_id,scope.project_id)
        row.project_id=DEFAULT_SCOPE.project_id
        with pytest.raises(HTTPException) as error:db.flush()
        assert error.value.detail['code']=='SCOPE_IMMUTABLE'


def test_parent_change_must_remain_in_same_scope(store):
    engine,scope,ids=store
    with ScopedSession(engine) as db:
        row=db.get(m.Job,ids[0][1]);row.deployment_id=ids[1][0]
        with pytest.raises(HTTPException) as error:db.flush()
        assert error.value.detail['code']=='SCOPE_MISMATCH'


def test_database_rejects_cross_project_parent_even_without_orm_guard(store):
    engine,scope,ids=store
    with engine.begin() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(update(m.Job.__table__).where(m.Job.__table__.c.id==ids[0][1]).values(deployment_id=ids[1][0]))


def test_unbound_inventory_sync_cannot_steal_foreign_unadopted_vm(store):
    engine,scope,ids=store
    with ScopedSession(engine) as db:
        db.add(m.ManagedVM(provider_id=1,vm_id=333,node='pve',created_by=1,
                           tenant_id=scope.tenant_id,project_id=scope.project_id));db.commit()
    with ScopedSession(engine) as db:
        with pytest.raises(RuntimeError,match='another project'):
            sync_deployment_inventory(db,db.get(m.Deployment,ids[0][0]),{'vm_id':{'value':333}})


def test_rollback_does_not_replay_reference_registration(store):
    engine,scope,ids=store
    with ScopedSession(engine) as db:
        db.add(m.Provider(name='provider',type='proxmox',credentials_id=1))
        with pytest.raises(IntegrityError):db.flush()
        db.rollback()
        provider=m.Provider(name='after-rollback',type='proxmox',credentials_id=1)
        db.add(provider);db.commit()
        assert db.get(ProjectProviderAccess,(DEFAULT_SCOPE.project_id,provider.id)) is not None


def test_preloaded_credential_cannot_bypass_assignment_filter(store):
    engine,scope,ids=store
    with ScopedSession(engine) as db:
        db.delete(db.get(ProjectCredentialAccess,(DEFAULT_SCOPE.project_id,1)));db.commit()
        cached=db.get(m.Credential,1)
        with pytest.raises(HTTPException) as error:bind_scope(db,DEFAULT_SCOPE)
        assert error.value.detail['code']=='SCOPE_SESSION_REUSE' and cached is not None


def test_delayed_raw_task_cannot_replace_foreign_vm_ownership(store):
    from app.providers.task_reconcile import _apply_success
    engine,scope,ids=store
    with ScopedSession(engine) as db:
        db.add(m.ManagedVM(provider_id=1,vm_id=444,node='pve',created_by=1,
                           tenant_id=scope.tenant_id,project_id=scope.project_id));db.commit()
    with ScopedSession(engine) as db:
        with pytest.raises(HTTPException) as error:
            _apply_success(db,None,{'provider_id':1,'action':'restore','target_vm_id':444,
                                    'target_node':'pve','created_by':1})
        assert error.value.status_code==404
        row=db.scalar(select(m.ManagedVM).where(m.ManagedVM.vm_id==444))
        assert row.project_id==scope.project_id
