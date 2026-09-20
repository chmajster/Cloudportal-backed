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
    assert any('workflow.step.prepared:clone:clone_vm' in value for value in context.stages)
    assert any('workflow.step.prepared:cloud:cloud_init' in value for value in context.stages)
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
