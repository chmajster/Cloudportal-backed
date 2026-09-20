from types import SimpleNamespace

import pytest

from app.executors.base import ExecutionFailed
from app.jobs import worker


class FakeContext:
    def __init__(self, steps, variables=None, provider='proxmox', executor='terraform'):
        self.job = SimpleNamespace(
            id='job-12345678',
            payload={'blueprint': {'steps': steps, 'requires_approval': True, 'variables': {}}},
        )
        self.deployment = SimpleNamespace(
            id='deployment-1',
            name='vm01',
            provider=provider,
            executor=executor,
            variables=variables or {'node': 'pve01', 'tags': ['env-dev', 'apmid-leo']},
        )
        self.credential = object()
        self.ansible = None
        self.ansible_credential = None
        self.step_deadline = None
        self.rollback_destroyed = False
        self.logs = []
        self.stages = []

    def log(self, message):
        self.logs.append(message)

    def stage(self, action):
        self.stages.append(action)

    def check(self):
        return None


class FakeExecutor:
    def __init__(self):
        self.operations = []

    def execute(self, operation, context):
        self.operations.append(operation)
        return '/tmp/workspace'


def test_blueprint_workflow_order_respects_dependencies():
    ordered = worker.blueprint_workflow_order([
        {'id': 'apply', 'type': 'terraform_apply', 'depends_on': ['clone']},
        {'id': 'hostname', 'type': 'generate_hostname', 'depends_on': []},
        {'id': 'clone', 'type': 'clone_vm', 'depends_on': ['hostname']},
    ])
    assert [step['id'] for step in ordered] == ['hostname', 'clone', 'apply']


def test_blueprint_workflow_order_rejects_cycle():
    with pytest.raises(ExecutionFailed, match='cycle'):
        worker.blueprint_workflow_order([
            {'id': 'a', 'type': 'condition', 'depends_on': ['b']},
            {'id': 'b', 'type': 'condition', 'depends_on': ['a']},
        ])


def test_blueprint_conditions_use_runtime_facts():
    context = FakeContext([])
    assert worker.blueprint_conditions_match(
        {'type': 'condition', 'conditions': {'provider': 'proxmox', 'environment': 'dev', 'apmid': 'LEO'}},
        context,
    )
    assert not worker.blueprint_conditions_match(
        {'type': 'condition', 'conditions': {'environment': 'prod'}},
        context,
    )


def test_blueprint_workflow_materializes_declarative_steps_at_apply(monkeypatch):
    steps = [
        {'id': 'hostname', 'type': 'generate_hostname', 'depends_on': [], 'retry': 0, 'timeout': 30},
        {'id': 'clone', 'type': 'clone_vm', 'depends_on': ['hostname'], 'retry': 0, 'timeout': 30},
        {'id': 'cloud', 'type': 'cloud_init', 'depends_on': ['clone'], 'retry': 0, 'timeout': 30},
        {'id': 'apply', 'type': 'terraform_apply', 'depends_on': ['cloud'], 'retry': 0, 'timeout': 30},
    ]
    context = FakeContext(steps)
    executor = FakeExecutor()

    monkeypatch.setattr(
        worker,
        'register_managed_inventory',
        lambda context, workspace: {
            'external_id': '101',
            'vm_id': 101,
            'node': 'pve01',
        },
    )

    workspace = worker.run_blueprint_workflow(context, executor)

    assert workspace == '/tmp/workspace'
    assert executor.operations == ['terraform.apply']
    assert any('workflow.step.completed:clone:clone_vm' in value for value in context.stages)
    assert any('workflow.step.completed:cloud:cloud_init' in value for value in context.stages)
    assert any('workflow.step.materialized: clone:clone_vm' in value for value in context.logs)
    assert any('workflow.step.materialized: cloud:cloud_init' in value for value in context.logs)


def test_blueprint_workflow_implicit_apply_preserves_legacy_clone_only_workflow(monkeypatch):
    steps = [
        {'id': 'clone', 'type': 'clone_vm', 'depends_on': [], 'retry': 0, 'timeout': 30},
    ]
    context = FakeContext(steps)
    executor = FakeExecutor()

    monkeypatch.setattr(
        worker,
        'register_managed_inventory',
        lambda context, workspace: {
            'external_id': '102',
            'vm_id': 102,
            'node': 'pve01',
        },
    )

    worker.run_blueprint_workflow(context, executor)

    assert executor.operations == ['terraform.apply']
    assert any('implicit terraform_apply' in value for value in context.logs)


def test_unknown_workflow_step_fails_explicitly():
    context = FakeContext([
        {'id': 'mystery', 'type': 'not-supported', 'depends_on': [], 'retry': 0, 'timeout': 30},
    ])
    with pytest.raises(ExecutionFailed, match='Unsupported Blueprint workflow steps'):
        worker.run_blueprint_workflow(context, FakeExecutor())


def test_wait_for_agent_runs_without_ansible(monkeypatch):
    steps = [
        {'id': 'apply', 'type': 'terraform_apply', 'depends_on': [], 'retry': 0, 'timeout': 30},
        {'id': 'agent', 'type': 'wait_for_agent', 'depends_on': ['apply'], 'retry': 0, 'timeout': 30},
    ]
    context = FakeContext(steps)
    executor = FakeExecutor()
    observed = []

    monkeypatch.setattr(
        worker,
        'register_managed_inventory',
        lambda context, workspace: {
            'external_id': '103',
            'vm_id': 103,
            'node': 'pve01',
        },
    )
    monkeypatch.setattr(
        worker,
        'wait_for_agent',
        lambda context, workspace, timeout=600: observed.append((workspace, timeout)) or True,
    )

    worker.run_blueprint_workflow(context, executor)

    assert context.ansible is None
    assert executor.operations == ['terraform.apply']
    assert observed == [('/tmp/workspace', 30)]
    assert any('workflow.step.completed:agent:wait_for_agent' in value for value in context.stages)


def test_blueprint_workflow_sets_completion_marker_only_after_all_steps(monkeypatch):
    steps = [
        {'id': 'apply', 'type': 'terraform_apply', 'depends_on': [], 'retry': 0, 'timeout': 30},
        {'id': 'health', 'type': 'health_check', 'depends_on': ['apply'], 'retry': 0, 'timeout': 30},
    ]
    context = FakeContext(steps)
    context.blueprint_workflow_completed = False
    executor = FakeExecutor()

    monkeypatch.setattr(
        worker,
        'register_managed_inventory',
        lambda context, workspace: {'external_id': '104', 'vm_id': 104, 'node': 'pve01'},
    )
    monkeypatch.setattr(
        worker,
        'provider_for',
        lambda credential: SimpleNamespace(execution_availability=lambda: {'ok': True}),
    )

    worker.run_blueprint_workflow(context, executor)

    assert context.blueprint_workflow_completed is True
    assert context.stages[-1] == 'workflow.completed'


def test_blueprint_workflow_does_not_mark_completed_when_post_apply_step_fails(monkeypatch):
    steps = [
        {'id': 'apply', 'type': 'terraform_apply', 'depends_on': [], 'retry': 0, 'timeout': 30},
        {'id': 'health', 'type': 'health_check', 'depends_on': ['apply'], 'retry': 0, 'timeout': 30},
    ]
    context = FakeContext(steps)
    context.blueprint_workflow_completed = False
    executor = FakeExecutor()

    monkeypatch.setattr(
        worker,
        'register_managed_inventory',
        lambda context, workspace: {'external_id': '105', 'vm_id': 105, 'node': 'pve01'},
    )
    monkeypatch.setattr(
        worker,
        'provider_for',
        lambda credential: SimpleNamespace(
            execution_availability=lambda: {'ok': False, 'reason': 'provider check failed'}
        ),
    )

    with pytest.raises(ExecutionFailed, match='health_check failed'):
        worker.run_blueprint_workflow(context, executor)

    assert context.blueprint_workflow_completed is False


def test_declarative_step_after_apply_fails_at_runtime(monkeypatch):
    steps = [
        {'id': 'apply', 'type': 'terraform_apply', 'depends_on': [], 'retry': 0, 'timeout': 30},
        {'id': 'tags', 'type': 'set_tags', 'depends_on': ['apply'], 'retry': 0, 'timeout': 30},
    ]
    context = FakeContext(steps)
    executor = FakeExecutor()
    monkeypatch.setattr(
        worker,
        'register_managed_inventory',
        lambda context, workspace: {'external_id': '106', 'vm_id': 106, 'node': 'pve01'},
    )

    with pytest.raises(ExecutionFailed, match='cannot run after terraform_apply'):
        worker.run_blueprint_workflow(context, executor)


def test_notification_rollback_runs_when_step_fails(monkeypatch):
    steps = [
        {'id': 'rollback_notice', 'type': 'notification', 'depends_on': [], 'conditions': {'message': 'rollback ran'}, 'retry': 0, 'timeout': 30},
        {'id': 'apply', 'type': 'terraform_apply', 'depends_on': [], 'retry': 0, 'timeout': 30},
        {'id': 'health', 'type': 'health_check', 'depends_on': ['apply'], 'rollback': 'rollback_notice', 'retry': 0, 'timeout': 30},
    ]
    context = FakeContext(steps)
    executor = FakeExecutor()
    monkeypatch.setattr(
        worker,
        'register_managed_inventory',
        lambda context, workspace: {'external_id': '107', 'vm_id': 107, 'node': 'pve01'},
    )
    monkeypatch.setattr(
        worker,
        'provider_for',
        lambda credential: SimpleNamespace(
            execution_availability=lambda: {'ok': False, 'reason': 'health failed'}
        ),
    )

    with pytest.raises(ExecutionFailed, match='health_check failed'):
        worker.run_blueprint_workflow(context, executor)

    assert any('workflow.rollback.start:health:rollback_notice:notification' in value for value in context.stages)
    assert any('workflow.rollback.completed:health:rollback_notice:notification' in value for value in context.stages)
    assert 'workflow.rollback.notification: rollback ran' in context.logs


def test_snapshot_waits_for_proxmox_task(monkeypatch):
    context = FakeContext([])
    statuses = iter([
        {'status': 'running'},
        {'status': 'stopped', 'exitstatus': 'OK'},
    ])
    provider = SimpleNamespace(
        create_snapshot=lambda *args: 'UPID:test',
        task_status=lambda node, upid: next(statuses),
    )
    monkeypatch.setattr(worker, 'vm_id_from_state', lambda workspace: 108)
    monkeypatch.setattr(worker, 'provider_for', lambda credential: provider)
    monkeypatch.setattr(worker.time, 'sleep', lambda _seconds: None)

    worker.create_blueprint_snapshot(
        context,
        '/tmp/workspace',
        {'id': 'snapshot', 'type': 'create_snapshot', 'timeout': 30},
    )

    assert any('workflow.snapshot.created: bp-job-1234-snapshot task=UPID:test' in value for value in context.logs)


def test_vm_agent_and_ip_waits_have_distinct_semantics(monkeypatch, tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    (workspace / 'terraform.tfstate').write_text(
        '{"outputs":{"vm_id":{"value":109}}}'
    )
    context = FakeContext([])
    calls = []

    class Provider:
        def vm_status(self, node, vm_id):
            calls.append(('vm_status', node, vm_id))
            return {'status': 'running'}

        def guest_agent_ready(self, node, vm_id):
            calls.append(('agent', node, vm_id))
            return True

        def guest_addresses(self, node, vm_id):
            calls.append(('addresses', node, vm_id))
            return ['2001:db8::10', '192.0.2.109']

    monkeypatch.setattr(worker, 'provider_for', lambda credential: Provider())
    observed_ip = []
    monkeypatch.setattr(worker, 'update_inventory_primary_ip', lambda context, address: observed_ip.append(address))

    assert worker.wait_for_vm(context, workspace, timeout=5) is True
    assert worker.wait_for_agent(context, workspace, timeout=5) is True
    assert worker.wait_for_ip(context, workspace, timeout=5) == ['192.0.2.109']
    assert observed_ip == ['192.0.2.109']
    assert [item[0] for item in calls] == ['vm_status', 'agent', 'addresses']


def test_legacy_implicit_apply_runs_before_first_runtime_vm_step(monkeypatch):
    steps = [
        {'id': 'clone', 'type': 'clone_vm', 'depends_on': [], 'retry': 0, 'timeout': 30},
        {'id': 'vm', 'type': 'wait_for_vm', 'depends_on': ['clone'], 'retry': 0, 'timeout': 30},
    ]
    context = FakeContext(steps)
    executor = FakeExecutor()
    order = []

    monkeypatch.setattr(
        worker,
        'register_managed_inventory',
        lambda context, workspace: {'external_id': '110', 'vm_id': 110, 'node': 'pve01'},
    )
    monkeypatch.setattr(
        worker,
        'wait_for_vm',
        lambda context, workspace, timeout=600: order.append(('wait', workspace)) or True,
    )
    original_execute = executor.execute
    executor.execute = lambda operation, context: order.append(('execute', operation)) or original_execute(operation, context)

    worker.run_blueprint_workflow(context, executor)

    assert order[0] == ('execute', 'terraform.apply')
    assert order[1] == ('wait', '/tmp/workspace')


def test_skipped_dependency_blocks_downstream_step(monkeypatch):
    steps = [
        {
            'id': 'gate',
            'type': 'condition',
            'depends_on': [],
            'conditions': {'environment': 'prod'},
            'retry': 0,
            'timeout': 30,
        },
        {
            'id': 'apply',
            'type': 'terraform_apply',
            'depends_on': ['gate'],
            'retry': 0,
            'timeout': 30,
        },
    ]
    context = FakeContext(steps)
    executor = FakeExecutor()

    with pytest.raises(ExecutionFailed, match='terraform_apply was skipped or blocked'):
        worker.run_blueprint_workflow(context, executor)

    assert executor.operations == []
    assert any('workflow.step.skipped: gate:condition' in value for value in context.logs)
    assert any('workflow.step.blocked: apply:terraform_apply' in value for value in context.logs)


def test_wait_for_ip_uses_configured_static_address_without_guest_agent(monkeypatch, tmp_path):
    workspace = tmp_path / 'workspace-static'
    workspace.mkdir()
    (workspace / 'terraform.tfstate').write_text(
        '{"outputs":{"vm_id":{"value":111}}}'
    )
    context = FakeContext([], variables={
        'node': 'pve01',
        'ipv4_address': '192.0.2.111/24',
        'tags': [],
    })
    observed = []
    monkeypatch.setattr(worker, 'update_inventory_primary_ip', lambda context, address: observed.append(address))
    monkeypatch.setattr(
        worker,
        'provider_for',
        lambda credential: (_ for _ in ()).throw(AssertionError('provider must not be used')),
    )

    assert worker.wait_for_ip(context, workspace, timeout=5) == ['192.0.2.111']
    assert observed == ['192.0.2.111']


def test_runtime_wait_logs_safe_provider_error(monkeypatch, tmp_path):
    from fastapi import HTTPException

    workspace = tmp_path / 'workspace-error'
    workspace.mkdir()
    (workspace / 'terraform.tfstate').write_text(
        '{"outputs":{"vm_id":{"value":112}}}'
    )
    context = FakeContext([])
    times = iter([0, 0, 10, 10])
    monkeypatch.setattr(worker.time, 'monotonic', lambda: next(times, 10))
    monkeypatch.setattr(worker.time, 'sleep', lambda _seconds: None)
    monkeypatch.setattr(
        worker,
        'provider_for',
        lambda credential: SimpleNamespace(
            vm_status=lambda node, vm_id: (_ for _ in ()).throw(HTTPException(502, 'Proxmox API unavailable'))
        ),
    )

    with pytest.raises(ExecutionFailed, match='last provider error: Proxmox API unavailable'):
        worker.wait_for_vm(context, workspace, timeout=1)

    assert any('workflow.wait_for_vm.retry: Proxmox API unavailable' in value for value in context.logs)


def test_terraform_destroy_rollback_uses_global_execution_timeout(monkeypatch):
    steps = [
        {'id': 'rollback_destroy', 'type': 'terraform_destroy', 'depends_on': [], 'retry': 0, 'timeout': 30},
        {'id': 'apply', 'type': 'terraform_apply', 'depends_on': [], 'retry': 0, 'timeout': 30},
        {'id': 'health', 'type': 'health_check', 'depends_on': ['apply'], 'rollback': 'rollback_destroy', 'retry': 0, 'timeout': 30},
    ]
    context = FakeContext(steps)
    context.rollback_destroyed = False
    executor = FakeExecutor()
    deadlines = []

    monkeypatch.setattr(
        worker,
        'register_managed_inventory',
        lambda context, workspace: {'external_id': '113', 'vm_id': 113, 'node': 'pve01'},
    )
    monkeypatch.setattr(
        worker,
        'provider_for',
        lambda credential: SimpleNamespace(
            execution_availability=lambda: {'ok': False, 'reason': 'health failed'}
        ),
    )
    monkeypatch.setattr(worker, 'mark_destroyed_after_rollback', lambda: None, raising=False)

    original_execute = executor.execute
    def execute(operation, ctx):
        if operation == 'terraform.destroy':
            deadlines.append(ctx.step_deadline)
        return original_execute(operation, ctx)
    executor.execute = execute

    with pytest.raises(ExecutionFailed, match='health_check failed'):
        worker.run_blueprint_workflow(context, executor)

    assert 'terraform.destroy' in executor.operations
    assert deadlines
