"""PostgreSQL concurrency gates; never substitute SQLite locking semantics."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, current_thread
import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from app.database import session
from app.models import Token
from app.rbac.locking import governance_lock
from app.security.core import digest
from app.projects import service
from app.tenancy import authorization
from app.tenancy.authorization import Principal
from app.tenancy.models import TenantMembership
from app.tenancy.schemas import MemberUpdate
from .test_projects_api import setup_project

pytestmark=pytest.mark.skipif(not os.environ.get('TEST_DATABASE_URL','').startswith('postgresql'),
                             reason='Real PostgreSQL required')


def principal(db,headers):
    return Principal.from_token(db.scalar(select(Token).where(
        Token.token_hash==digest(headers['Authorization'].removeprefix('Bearer ')))))


def test_two_project_member_updates_have_exactly_one_winner(system):
    _,headers,_,p,user,_=setup_project(system)
    with session() as db: admin=principal(db,headers)
    barrier=Barrier(2)
    def change(_):
        with session() as db:
            db.execute(text("SET LOCAL lock_timeout = '5s'"));barrier.wait(timeout=5)
            try:
                result=service.member_update(db,admin,p['id'],user['id'],MemberUpdate(status='disabled',expected_version=1))
                db.commit();return result['version']
            except HTTPException as e:
                db.rollback();return e.detail['code']
    with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(change,range(2)))
    assert results.count(2)==1 and results.count('VERSION_CONFLICT')==1


def test_waiting_project_writer_rechecks_parent_membership(system,monkeypatch):
    _,headers,t,p,user,h=setup_project(system)
    with session() as db: actor=principal(db,h)
    waiting=Event();original=authorization.governance_lock
    def observed(db):
        if current_thread().name.startswith('project-writer'): waiting.set()
        return original(db)
    monkeypatch.setattr(authorization,'governance_lock',observed)
    def write():
        with session() as db:
            db.execute(text("SET LOCAL lock_timeout = '5s'"))
            try:
                service.member_update(db,actor,p['id'],user['id'],MemberUpdate(status='active',expected_version=1))
                db.commit();return 'unexpected-success'
            except HTTPException as e:
                db.rollback();return e.detail['code']
    with session() as db:
        governance_lock(db)
        with ThreadPoolExecutor(max_workers=1,thread_name_prefix='project-writer') as pool:
            future=pool.submit(write)
            try:
                assert waiting.wait(timeout=5)
                db.get(TenantMembership,(t['id'],user['id'])).status='disabled';db.commit()
            finally: db.rollback()
            assert future.result(timeout=10)=='PROJECT_NOT_FOUND'
