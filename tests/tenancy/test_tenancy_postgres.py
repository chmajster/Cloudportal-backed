"""Real PostgreSQL row-lock tests; SQLite is intentionally not a substitute."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, current_thread

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text

from app.database import session
from app.models import Role, Token
from app.rbac.locking import governance_lock
from app.security.core import digest
from app.tenancy import service
from app.tenancy import authorization
from app.tenancy.authorization import Principal
from app.tenancy.models import TenantMembership
from app.tenancy.schemas import MemberCreate, MemberUpdate, TenantCreate
from conftest import new_user

pytestmark = pytest.mark.skipif(not os.environ.get('TEST_DATABASE_URL', '').startswith('postgresql'),
                                reason='PostgreSQL required for row-lock concurrency verification')


def actor_from_header(db, headers):
    plain = headers['Authorization'].removeprefix('Bearer ')
    return Principal.from_token(db.scalar(select(Token).where(Token.token_hash == digest(plain))))


def prepare(system):
    client, headers, _ = system
    alice, alice_headers = new_user(client, headers, 'concurrent-tenant-admin')
    with session() as db:
        admin = actor_from_header(db, headers)
        actor = actor_from_header(db, alice_headers)
        tenant = service.tenant_create(db, admin, TenantCreate(name='Concurrent', slug='concurrent'))
        role_id = db.scalar(select(Role.id).where(Role.name == 'Tenant Administrator'))
        service.member_create(db, admin, tenant['id'], MemberCreate(user_id=alice['id'], role_ids=[role_id]))
        db.commit()
    return admin, actor, tenant['id']


def test_two_concurrent_member_updates_have_one_winner(system):
    admin, alice, tenant_id = prepare(system)
    barrier = Barrier(2)
    def update():
        with session() as db:
            db.execute(text("SET LOCAL lock_timeout = '5s'"))
            barrier.wait(timeout=5)
            try:
                result = service.member_update(db, admin, tenant_id, alice.user_id,
                    MemberUpdate(status='disabled', expected_version=1))
                db.commit()
                return result['version']
            except HTTPException as error:
                db.rollback()
                return error.detail['code']
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: update(), range(2)))
    assert results.count(2) == 1 and results.count('VERSION_CONFLICT') == 1
    with session() as db:
        assert db.get(TenantMembership, (tenant_id, alice.user_id)).version == 2


def test_waiting_writer_rechecks_membership_after_revocation(system, monkeypatch):
    admin, alice, tenant_id = prepare(system)
    started = Event()
    original = authorization.governance_lock
    def observed_lock(db):
        if current_thread().name.startswith('waiting-tenant'):
            started.set()
        return original(db)
    monkeypatch.setattr(authorization, 'governance_lock', observed_lock)
    def write():
        with session() as db:
            db.execute(text("SET LOCAL lock_timeout = '5s'"))
            try:
                # This must wait on the same row used for global RBAC mutations.
                service.member_create(db, alice, tenant_id, MemberCreate(user_id=admin.user_id))
                db.commit()
                return 'unexpected-success'
            except HTTPException as error:
                db.rollback()
                return error.detail['code']
    with session() as revoker:
        governance_lock(revoker)
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix='waiting-tenant') as pool:
            future = pool.submit(write)
            try:
                assert started.wait(timeout=5)
                service.member_update(revoker, admin, tenant_id, alice.user_id,
                    MemberUpdate(status='disabled', expected_version=1))
                revoker.commit()
            finally:
                revoker.rollback()
            assert future.result(timeout=10) == 'TENANT_NOT_FOUND'
    with session() as db:
        assert db.get(TenantMembership, (tenant_id, admin.user_id)) is None


def test_pending_token_timestamp_does_not_deadlock_revocation(system):
    admin, alice, tenant_id = prepare(system)
    from sqlalchemy import event, update
    from app.database import engine
    from app.models import now
    waiting = Event()
    def observe_statement(connection, cursor, statement, parameters, context, executemany):
        if (current_thread().name.startswith('pending-token')
                and 'FROM settings' in statement and 'FOR UPDATE' in statement):
            # This fires after any autoflush. Without no_autoflush around the
            # governance lock, the writer would already hold the token row here.
            waiting.set()
    def write():
        with session() as db:
            db.execute(text("SET LOCAL lock_timeout = '5s'"))
            token = db.get(Token, alice.token_id)
            token.last_used_at = now()  # Same pending write as authenticate().
            try:
                service.member_create(db, alice, tenant_id, MemberCreate(user_id=admin.user_id))
                db.commit()
                return 'unexpected-success'
            except HTTPException as error:
                db.rollback()
                return error.detail['code']
    event.listen(engine(), 'before_cursor_execute', observe_statement)
    try:
        with session() as revoker:
            revoker.execute(text("SET LOCAL lock_timeout = '3s'"))
            governance_lock(revoker)
            with ThreadPoolExecutor(max_workers=1, thread_name_prefix='pending-token') as pool:
                future = pool.submit(write)
                try:
                    assert waiting.wait(timeout=5)
                    revoker.execute(update(Token).where(Token.id == alice.token_id).values(revoked_at=now()))
                    revoker.commit()
                finally:
                    revoker.rollback()
                assert future.result(timeout=10) == 'AUTHENTICATION_REQUIRED'
    finally:
        event.remove(engine(), 'before_cursor_execute', observe_statement)
