"""Merge gates: legacy Day-2 must not bypass resource scope or newer RBAC."""
import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import engine, session
from app.day2 import worker
from app.day2.models import Day2ActionRequest
from app.models import Credential, Job, Role
from app.resource_scope.columns import DEFAULT_PROJECT_ID, DEFAULT_TENANT_ID
from schema_helpers import historical_deployment, legacy_deployment
from test_day2 import resource, submit
from test_resource_scope_api import project, infrastructure, assign, create, scope_headers


def test_day2_rejects_explicit_other_project_before_provider(resource):
    client, headers, vm, fake = resource
    other = project(client, headers, 'day2-explicit')
    response = client.get(f'/api/v1/resources/{vm}/actions', headers=scope_headers(headers, other))
    assert response.status_code == 409, response.text
    assert response.json()['detail']['code'] == 'GOVERNED_DAY2_REQUIRED'
    assert fake.calls == []


def test_day2_worker_and_history_close_when_installation_becomes_multi_project(resource):
    client, headers, vm, fake = resource
    queued = submit(resource)
    assert queued.status_code == 202, queued.text
    pending = queued.json()
    other = project(client, headers, 'day2-multi')
    credential, provider = infrastructure(client, headers, 'other-project')
    assign(client, headers, other, credential, provider)
    create(client, scope_headers(headers, other), credential, provider, 'outside-default')
    for path in (f'/api/v1/resources/{vm}/actions', '/api/v1/day2-actions',
                 '/api/v1/day2-actions/' + pending['action_request_id']):
        response = client.get(path, headers=headers)
        assert response.status_code == 409, response.text
        assert response.json()['detail']['code'] == 'GOVERNED_DAY2_REQUIRED'
    denied = submit(resource)
    assert denied.status_code == 409, denied.text
    worker.execute(pending['job_id'])
    assert fake.calls == []
    with session() as db:
        assert db.get(Job, pending['job_id']).status == 'failed'
        assert db.get(Day2ActionRequest, pending['action_request_id']).error_code == 'PERMISSION_DENIED'


def test_merge_preserves_day2_defaults_without_granting_governance_admin(system):
    with session() as db:
        roles = {role.name: {p.name for p in role.permissions} for role in db.scalars(select(Role))}
    assert 'day2.power' in roles['Operator']
    assert 'day2.view' in roles['Viewer']
    assert 'day2.view' in roles['Auditor']
    assert 'day2.admin' in roles['Infrastructure Administrator']
    assert not any(p.startswith('governance.') for p in roles['Infrastructure Administrator'])
    assert 'governance.admin' in roles['Administrator']


@pytest.mark.parametrize('starting_revision', ['b752ca806e19', '9d2c4e7a1b60'])
def test_merge_migration_upgrades_both_published_tips(tmp_path, monkeypatch, starting_revision):
    monkeypatch.setenv('CP_DATABASE_URL', 'sqlite:///' + str(tmp_path / 'merge-upgrade.db'))
    settings.cache_clear(); engine.cache_clear()
    config = Config('alembic.ini')
    try:
        command.upgrade(config, starting_revision)
        uid, did, cid, pid = legacy_deployment(engine())
        command.upgrade(config, 'head'); command.upgrade(config, 'head')
        assert ScriptDirectory.from_config(config).get_heads() == ['c864db917f20']
        assert {'project_credential_access', 'user_project_contexts', 'day2_action_requests'} <= set(inspect(engine()).get_table_names())
        with engine().connect() as connection:
            row = historical_deployment(connection, did)
            assert (row['tenant_id'], row['project_id']) == (DEFAULT_TENANT_ID, DEFAULT_PROJECT_ID)
            assert row['provider_id'] == pid and row['created_by'] == uid
        with Session(engine()) as db:
            assert db.get(Credential, cid).encrypted_secret == b'unchanged'
    finally:
        engine().dispose(); engine.cache_clear(); settings.cache_clear()
