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
        'wait_for_vm',
        lambda context, workspace, timeout=600: observed.append((workspace, timeout)) or ['192.0.2.10'],
    )

    worker.run_blueprint_workflow(context, executor)

    assert context.ansible is None
    assert executor.operations == ['terraform.apply']
    assert observed == [('/tmp/workspace', 30)]
    assert any('workflow.step.completed:agent:wait_for_agent' in value for value in context.stages)
