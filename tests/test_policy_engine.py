from datetime import datetime, timedelta, timezone

from app.policy_engine.engine import evaluate


def policy(policy_id, *, priority=5000, status='enforced', enforcement='hard',
           policy_type='deployment', scope=None, condition=None, effects=None):
    return {
        'id': policy_id,
        'tenant_id': None,
        'project_id': None,
        'priority': priority,
        'status': status,
        'enforcement': enforcement,
        'policy_type': policy_type,
        'scope': scope or {},
        'condition': condition or {},
        'effects': effects or [{'type': 'allow'}],
    }


def context(*, user_id=1, apmid='LEO', environment='dev', cpu=4):
    return {
        'actor': {'id': user_id, 'username': 'user', 'roles': ['Deployer'], 'groups': []},
        'scope': {'tenant_id': 'tenant-1', 'project_id': 'project-1'},
        'request': {'action': 'vm.create', 'phase': 'pre_provision'},
        'resource': {
            'type': 'vm',
            'apmid': apmid,
            'environment': environment,
            'cpu': cpu,
            'tags': ['linux'],
        },
    }


def test_access_whitelist_can_limit_one_apmid_to_dev():
    rule = policy(
        'access-leo-dev',
        policy_type='access',
        scope={
            'actions': ['vm.create'],
            'resource_types': ['vm'],
            'apmids': ['LEO'],
            'environments': ['dev'],
        },
        effects=[{'type': 'allow', 'mode': 'whitelist'}],
    )

    assert evaluate([rule], context(apmid='LEO', environment='dev')).decision == 'allow'

    denied = evaluate([rule], context(apmid='LEO', environment='prod'))
    assert denied.decision == 'deny'
    assert any(item['type'] == 'access_whitelist' for item in denied.violations)


def test_second_binding_can_allow_all_environments_for_one_apmid():
    rule = policy(
        'access-leo-any-env',
        policy_type='access',
        scope={
            'user_ids': [2],
            'actions': ['vm.create'],
            'resource_types': ['vm'],
            'apmids': ['LEO'],
        },
        effects=[{'type': 'allow', 'mode': 'whitelist'}],
    )

    assert evaluate([rule], context(user_id=2, apmid='LEO', environment='prod')).decision == 'allow'
    assert evaluate([rule], context(user_id=2, apmid='ABC', environment='prod')).decision == 'deny'


def test_nested_conditions_and_limit_effect_are_enforced():
    rule = policy(
        'prod-limit',
        condition={
            'all': [
                {'field': 'resource.apmid', 'operator': 'eq', 'value': 'LEO'},
                {'any': [
                    {'field': 'resource.environment', 'operator': 'eq', 'value': 'prod'},
                    {'field': 'resource.environment', 'operator': 'eq', 'value': 'nonprod'},
                ]},
            ],
        },
        effects=[{'type': 'limit_value', 'field': 'resource.cpu', 'max': 8}],
    )

    assert evaluate([rule], context(environment='prod', cpu=8)).decision == 'allow'
    denied = evaluate([rule], context(environment='prod', cpu=16))
    assert denied.decision == 'deny'
    assert denied.violations[0]['max'] == 8


def test_higher_priority_force_value_wins():
    high = policy(
        'high', priority=9000,
        effects=[{'type': 'force_value', 'field': 'resource.storage', 'value': 'ceph-prod'}],
    )
    low = policy(
        'low', priority=1000,
        effects=[{'type': 'force_value', 'field': 'resource.storage', 'value': 'local-lvm'}],
    )

    result = evaluate([low, high], context())
    assert result.effective_context['resource']['storage'] == 'ceph-prod'
    low_effect = next(item for item in result.applied_effects if item['policy_id'] == 'low')
    assert low_effect['ignored'] == 'higher_priority_value_already_selected'


def test_require_approval_is_a_distinct_decision():
    rule = policy(
        'prod-approval',
        scope={'environments': ['prod']},
        effects=[{'type': 'require_approval', 'approver': {'type': 'apmid_owner'}}],
    )

    result = evaluate([rule], context(environment='prod'))
    assert result.decision == 'approval_required'
    assert result.approvals[0]['approver']['type'] == 'apmid_owner'


def test_advisory_deny_only_warns():
    rule = policy(
        'warning', enforcement='advisory',
        effects=[{'type': 'deny', 'message': 'Large VM'}],
    )

    result = evaluate([rule], context())
    assert result.decision == 'allow'
    assert result.warnings == ['Large VM']


def test_dry_run_reports_impact_without_enforcing():
    rule = policy(
        'future-limit', status='dry_run',
        effects=[{'type': 'limit_value', 'field': 'resource.cpu', 'max': 2}],
    )

    result = evaluate([rule], context(cpu=8))
    assert result.decision == 'allow'
    assert result.violations == []
    assert result.dry_run_impacts[0]['violations'][0]['max'] == 2


def test_approved_exception_temporarily_skips_policy():
    rule = policy('blocked', effects=[{'type': 'deny', 'message': 'Blocked'}])
    now = datetime.now(timezone.utc)
    exception = {
        'id': 'exception-1',
        'policy_id': 'blocked',
        'status': 'approved',
        'condition': {'field': 'actor.id', 'operator': 'eq', 'value': 1},
        'valid_from': (now - timedelta(hours=1)).replace(tzinfo=None),
        'valid_until': (now + timedelta(hours=1)).replace(tzinfo=None),
    }

    result = evaluate([rule], context(user_id=1), [exception], instant=now)
    assert result.decision == 'allow'
    assert result.trace[0]['reason'] == 'exception'


def test_policy_api_versions_simulator_and_whitelist(client, headers):
    created = client.post('/api/v1/policies', headers=headers, json={
        'name': 'LEO DEV deploy only',
        'description': 'Whitelist APMID/ENV',
        'policy_type': 'access',
        'priority': 8000,
        'enforcement': 'hard',
        'status': 'enforced',
        'scope_level': 'project',
        'scope': {
            'actions': ['vm.create'],
            'resource_types': ['vm'],
            'apmids': ['LEO'],
            'environments': ['dev'],
        },
        'condition': {},
        'effects': [{'type': 'allow', 'mode': 'whitelist'}],
    })
    assert created.status_code == 201, created.text
    item = created.json()

    allowed = client.post('/api/v1/policies/evaluate', headers=headers, json={
        'action': 'vm.create',
        'resource_type': 'vm',
        'context': {'resource': {'apmid': 'LEO', 'environment': 'dev'}},
    })
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()['decision'] == 'allow'

    denied = client.post('/api/v1/policies/evaluate', headers=headers, json={
        'action': 'vm.create',
        'resource_type': 'vm',
        'context': {'resource': {'apmid': 'LEO', 'environment': 'prod'}},
    })
    assert denied.status_code == 200, denied.text
    assert denied.json()['decision'] == 'deny'

    updated_payload = {
        'name': item['name'],
        'description': item['description'],
        'policy_type': item['policy_type'],
        'priority': item['priority'],
        'enforcement': item['enforcement'],
        'status': 'dry_run',
        'scope_level': item['scope_level'],
        'scope': item['scope'],
        'condition': item['condition'],
        'effects': item['effects'],
        'expected_version': item['version'],
    }
    updated = client.put('/api/v1/policies/' + item['id'], headers=headers, json=updated_payload)
    assert updated.status_code == 200, updated.text
    assert updated.json()['version'] == 2

    versions = client.get('/api/v1/policies/' + item['id'] + '/versions', headers=headers)
    assert versions.status_code == 200, versions.text
    assert [row['version'] for row in versions.json()['items']] == [2, 1]
