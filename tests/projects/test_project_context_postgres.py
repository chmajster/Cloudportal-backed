"""Real PostgreSQL serialization for concurrent context selections."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from app.database import engine, session
from app.models import Token
from app.projects import context
from app.projects.context_routes import SelectionInput
from app.tenancy.authorization import Principal
from conftest import new_user
from test_projects_api import create_tenant, create_project, add_member


def test_concurrent_initial_selection_has_exactly_one_winner(system):
    if engine().dialect.name != 'postgresql':
        pytest.skip('PostgreSQL row-lock test')
    client, h, _ = system
    t = create_tenant(client, h, 'selection-race'); p = create_project(client, h, t['id'], 'race')
    u, _ = new_user(client, h, 'selection-racer'); add_member(client, h, t['id'], p['id'], u['id'])
    with session() as db:
        principal = Principal.from_token(db.scalar(select(Token).where(Token.user_id == u['id'], Token.kind == 'session')))
    barrier = Barrier(2)
    def choose(_):
        with session() as db:
            barrier.wait(timeout=10)
            try:
                context.context_set(db, principal, SelectionInput(tenant_id=t['id'], project_id=p['id'], expected_version=0))
                db.commit(); return 'selected'
            except HTTPException as exc:
                db.rollback(); return exc.detail['code']
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(choose, range(2))) == ['VERSION_CONFLICT', 'selected']
