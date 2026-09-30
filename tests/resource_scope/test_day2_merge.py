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
from app.models import Credential, Job, ManagedVM, Provider, Role
from app.resource_scope.columns import DEFAULT_PROJECT_ID, DEFAULT_TENANT_ID
from schema_helpers import historical_deployment, legacy_deployment
from test_day2 import key, resource, submit
from test_resource_scope_api import project, infrastructure, assign, create, scope_headers


def test_day2_explicit_target_is_isolated_and_runs_in_selected_project(resource):
    client, headers, vm, fake = resource
    other = project(client, headers, 'day2-explicit')

    # A target from the default project must remain invisible in another scope.
    denied = client.get(f'/api/v1/resources/{vm}/actions', headers=scope_headers(headers, other))
    assert denied.status_code == 404, denied.text
    assert fake.capability_calls == 0

    with session() as db:
        default_vm = db.get(ManagedVM, vm)
        provider = db.get(Provider, default_vm.provider_id)
        provider_id = provider.id
        credential_id = provider.credentials_id
        owner_id = default_vm.created_by

    assign(client, headers, other, {'id': credential_id}, {'id': provider_id})
    with session() as db:
        scoped_vm = ManagedVM(
            provider_id=provider_id,
            node='pve',
            vm_id=701,
            name='project-vm',
            created_by=owner_id,
            tenant_id=other['tenant_id'],
            project_id=other['id'],
        )
        db.add(scoped_vm)
        db.commit()
        scoped_vm_id = scoped_vm.id

    scoped_headers = scope_headers(headers, other)
    catalog = client.get(f'/api/v1/resources/{scoped_vm_id}/actions', headers=scoped_headers)
    assert catalog.status_code == 200, catalog.text
    assert fake.capability_calls == 1

    created = client.post(
        f'/api/v1/resources/{scoped_vm_id}/actions/power_on',
        headers=key(scoped_headers),
        json={'parameters': {}, 'reason': 'project scoped test'},
    )
    assert created.status_code == 202, created.text
    worker.execute(created.json()['job_id'])
    assert fake.calls == [(scoped_vm_id, 'power_on', {})]
    with session() as db:
        assert db.get(Job, created.json()['job_id']).status == 'successful'
        assert db.get(Day2ActionRequest, created.json()['action_request_id']).status == 'SUCCEEDED'


def test_day2_scoped_worker_survives_multi_project_while_global_history_stays_closed(resource):
    client, headers, vm, fake = resource
    queued = submit(resource)
    assert queued.status_code == 202, queued.text
    pending = queued.json()

    other = project(client, headers, 'day2-multi')
    credential, provider = infrastructure(client, headers, 'other-project')
    assign(client, headers, other, credential, provider)
    create(client, scope_headers(headers, other), credential, provider, 'outside-default')

    explicit = client.get(f'/api/v1/resources/{vm}/actions', headers=headers)
    assert explicit.status_code == 200, explicit.text

    for path in ('/api/v1/day2-actions', '/api/v1/day2-actions/' + pending['action_request_id']):
        response = client.get(path, headers=headers)
        assert response.status_code == 409, response.text
        assert response.json()['detail']['code'] == 'GOVERNED_DAY2_REQUIRED'

    worker.execute(pending['job_id'])
    assert fake.calls == [(vm, 'power_on', {})]
    with session() as db:
        assert db.get(Job, pending['job_id']).status == 'successful'
        assert db.get(Day2ActionRequest, pending['action_request_id']).status == 'SUCCEEDED'

    # Explicit resource execution remains available after a second project exists.
    again = submit(resource)
    assert again.status_code == 202, again.text


def test_merge_preserves_day2_defaults_without_granting_governance_admin(system):
    with session() as db:
        roles = {role.name: {p.name for p in role.permissions} for role in db.scalars(select(Role))}
    assert 'day2.power' in roles['Operator']
    assert 'day2.view' in roles['Viewer']
    assert 'day2.view' in roles['Auditor']
    assert 'day2.admin' in roles['Infrastructure Administrator']
    assert 'quotas.read' in roles['Infrastructure Administrator']
    assert 'quotas.tenant.manage' not in roles['Infrastructure Administrator']
    assert 'quotas.tenant.manage' in roles['Tenant Administrator']
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
        heads = ScriptDirectory.from_config(config).get_heads()
        assert len(heads) == 1
        assert heads[0] not in {'b752ca806e19', '9d2c4e7a1b60'}
        assert {'project_credential_access', 'user_project_contexts', 'day2_action_requests',
                'quota_reservations', 'quota_allocations'} <= set(inspect(engine()).get_table_names())
        with engine().connect() as connection:
            row = historical_deployment(connection, did)
            assert (row['tenant_id'], row['project_id']) == (DEFAULT_TENANT_ID, DEFAULT_PROJECT_ID)
            assert row['provider_id'] == pid and row['created_by'] == uid
        with Session(engine()) as db:
            assert db.get(Credential, cid).encrypted_secret == b'unchanged'
    finally:
        engine().dispose(); engine.cache_clear(); settings.cache_clear()
