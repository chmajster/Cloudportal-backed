"""Concurrency gates require PostgreSQL; SQLite is never counted as a lock test."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
import time
import pytest
from fastapi import HTTPException
from sqlalchemy import select, delete
from app.database import engine, session
from app.models import Token, RolePermission, Permission
from app.projects import service
from app.projects.models import ProjectMembership
from app.projects.schemas import ProjectUpdate
from app.tenancy.authorization import Principal, lock_authorization
from test_projects_api import create_tenant, create_project, add_member
from conftest import new_user


def test_concurrent_self_removal_retains_one_human_manager(system):
    if engine().dialect.name != 'postgresql':
        pytest.skip('PostgreSQL row-lock test')
    client, headers, _ = system
    t = create_tenant(client, headers, 'race'); p = create_project(client, headers, t['id'], 'race')
    users = [new_user(client, headers, n)[0] for n in ('manager-a', 'manager-b')]
    for u in users:
        add_member(client, headers, t['id'], p['id'], u['id'])
    with session() as db:
        principals = [Principal.from_token(db.scalar(select(Token).where(Token.user_id == u['id'], Token.kind == 'session'))) for u in users]
    barrier = Barrier(2)
    def remove(principal):
        with session() as db:
            barrier.wait(timeout=10)
            try:
                service.member_delete(db, principal, p['id'], principal.user_id, 1)
                db.commit(); return 'deleted'
            except HTTPException as exc:
                db.rollback(); return exc.detail['code']
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(remove, principals))
    assert sorted(results) == ['PROJECT_MANAGER_REQUIRED', 'deleted']
    with session() as db:
        assert len(db.scalars(select(ProjectMembership).where(ProjectMembership.project_id == p['id'])).all()) == 1


def test_role_revocation_while_waiting_for_governance_lock_is_rechecked(system):
    if engine().dialect.name != 'postgresql':
        pytest.skip('PostgreSQL row-lock test')
    client, headers, _ = system
    t = create_tenant(client, headers, 'revoke'); p = create_project(client, headers, t['id'], 'revoke')
    u, _ = new_user(client, headers, 'waiting-manager')
    membership = add_member(client, headers, t['id'], p['id'], u['id'])
    with session() as db:
        principal = Principal.from_token(db.scalar(select(Token).where(Token.user_id == u['id'], Token.kind == 'session')))
    started = Event()
    values = {k: p[k] for k in ('name', 'slug', 'description', 'status', 'labels', 'metadata', 'default_environment')}
    def update():
        with session() as db:
            started.set()
            try:
                service.project_update(db, principal, p['id'], ProjectUpdate(**values, expected_version=1))
                db.commit(); return 'updated'
            except HTTPException as exc:
                db.rollback(); return exc.detail['code']
    with session() as holder, ThreadPoolExecutor(max_workers=1) as pool:
        lock_authorization(holder)
        future = pool.submit(update)
        try:
            assert started.wait(5)
            time.sleep(.1)
            assert not future.done()
            permission_id = holder.scalar(select(Permission.id).where(Permission.name == 'projects.update'))
            holder.execute(delete(RolePermission).where(RolePermission.role_id.in_(membership['role_ids']), RolePermission.permission_id == permission_id))
            holder.commit()
        finally:
            holder.rollback()
        assert future.result(timeout=10) == 'SCOPED_PERMISSION_REQUIRED'
