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


def test_blueprint_resume_uses_completed_apply_checkpoint(monkeypatch, tmp_path):
    steps = [
        {'id': 'apply', 'type': 'terraform_apply', 'depends_on': [], 'retry': 0, 'timeout': 30},
        {'id': 'health', 'type': 'health_check', 'depends_on': ['apply'], 'retry': 0, 'timeout': 30},
    ]
    context = FakeContext(steps)
    context.blueprint_workflow_completed = False
    context.job.payload['_workflow_runtime'] = {
        'completed_steps': [],
        'provider_applied': True,
        'inventory_synced': True,
        'plan_ready': False,
        'plan_sha256': None,
    }
    executor = FakeExecutor()
    restored = tmp_path / 'restored-workspace'
    restored.mkdir()
    observed = []

    monkeypatch.setattr(worker, 'restore_recovery_workspace', lambda _context: restored)
    monkeypatch.setattr(worker, 'persist_workflow_runtime', lambda _context, _runtime: None)
    monkeypatch.setattr(
        worker,
        'health_check_vm',
        lambda _context, workspace: observed.append(workspace) or True,
    )

    workspace = worker.run_blueprint_workflow(context, executor)

    assert workspace == restored
    assert executor.operations == []
    assert observed == [restored]
    assert any('provider apply checkpoint already completed' in value for value in context.logs)
    assert context.blueprint_workflow_completed is True


def test_blueprint_resume_does_not_repeat_completed_ansible(monkeypatch, tmp_path):
    steps = [
        {'id': 'apply', 'type': 'terraform_apply', 'depends_on': [], 'retry': 0, 'timeout': 30},
        {'id': 'ansible', 'type': 'run_ansible_playbook', 'depends_on': ['apply'], 'retry': 0, 'timeout': 30},
        {'id': 'health', 'type': 'health_check', 'depends_on': ['ansible'], 'retry': 0, 'timeout': 30},
    ]
    context = FakeContext(steps)
    context.blueprint_workflow_completed = False
    context.ansible = SimpleNamespace(inventory=None)
    context.job.payload['_workflow_runtime'] = {
        'completed_steps': ['apply', 'ansible'],
        'provider_applied': True,
        'inventory_synced': True,
        'plan_ready': False,
        'plan_sha256': None,
    }
    restored = tmp_path / 'restored-ansible-workspace'
    restored.mkdir()

    monkeypatch.setattr(worker, 'restore_recovery_workspace', lambda _context: restored)
    monkeypatch.setattr(worker, 'persist_workflow_runtime', lambda _context, _runtime: None)
    monkeypatch.setattr(worker, 'health_check_vm', lambda _context, _workspace: True)
    monkeypatch.setattr(
        worker.AnsibleExecutor,
        'execute',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError('completed Ansible step must not run again')
        ),
    )

    worker.run_blueprint_workflow(context, FakeExecutor())

    assert context.blueprint_workflow_completed is True


def test_recovery_preserves_original_blueprint_execution_channel():
    job = SimpleNamespace(
        source='Recovery',
        payload={'_auto_resume': {'authorization_source': 'CloudPortal'}},
    )
    assert worker.blueprint_execution_channel(job) == 'cloudportal'

    job.payload['_auto_resume']['authorization_source'] = 'Scheduler'
    assert worker.blueprint_execution_channel(job) == 'backend'


def test_blueprint_resume_checkpoints_compatibility_ansible(monkeypatch, tmp_path):
    steps = [
        {'id': 'apply', 'type': 'terraform_apply', 'depends_on': [], 'retry': 0, 'timeout': 30},
    ]
    context = FakeContext(steps)
    context.blueprint_workflow_completed = False
    context.ansible = SimpleNamespace(inventory=None)
    context.job.payload['_workflow_runtime'] = {
        'completed_steps': ['apply'],
        'provider_applied': True,
        'inventory_synced': True,
        'plan_ready': False,
        'plan_sha256': None,
        'ansible_ran': False,
    }
    restored = tmp_path / 'restored-compat-ansible-workspace'
    restored.mkdir()
    calls = []
    checkpoints = []

    monkeypatch.setattr(worker, 'restore_recovery_workspace', lambda _context: restored)
    monkeypatch.setattr(
        worker,
        'wait_for_ansible_transport',
        lambda _context, workspace, **_kwargs: ['192.0.2.20'],
    )
    monkeypatch.setattr(
        worker.AnsibleExecutor,
        'execute',
        lambda _executor, operation, _context: calls.append(operation),
    )
    monkeypatch.setattr(
        worker,
        'persist_workflow_runtime',
        lambda _context, runtime: checkpoints.append(bool(runtime['ansible_ran'])),
    )

    worker.run_blueprint_workflow(context, FakeExecutor())

    assert calls == ['ansible.execute']
    assert checkpoints[-1] is True
    assert True in checkpoints
    assert context.blueprint_workflow_completed is True


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
    monkeypatch.setattr(worker, 'health_check_vm', lambda context, workspace: True)

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
        'health_check_vm',
        lambda context, workspace: (_ for _ in ()).throw(
            ExecutionFailed('Blueprint health_check failed: VM is not running')
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
        'health_check_vm',
        lambda context, workspace: (_ for _ in ()).throw(
            ExecutionFailed('Blueprint health_check failed: VM is not running')
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
        lambda credential: SimpleNamespace(
            vm_status=lambda node, vm_id: {'status': 'running'}
        ),
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
    monkeypatch.setattr(worker.settings(), 'execution_timeout', 3600)
    assert worker.workflow_step_timeout('terraform_destroy', 30) == 3600
    assert worker.workflow_step_timeout('terraform_apply', 30) == 3600
    assert worker.workflow_step_timeout('terraform_plan', 30) == 3600
    assert worker.workflow_step_timeout('wait_for_ip', 30) == 30


def test_plan_step_is_reused_by_apply_step(monkeypatch, tmp_path):
    steps = [
        {'id': 'plan', 'type': 'terraform_plan', 'depends_on': [], 'retry': 0, 'timeout': 30},
        {'id': 'apply', 'type': 'terraform_apply', 'depends_on': ['plan'], 'retry': 0, 'timeout': 30},
    ]
    context = FakeContext(steps)
    calls = []
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    digest = worker.hashlib.sha256(b'approved-plan').hexdigest()

    class Executor:
        def execute(self, operation, ctx):
            calls.append((
                operation,
                bool(getattr(ctx, 'keep_terraform_plan', False)),
                bool(getattr(ctx, 'apply_saved_terraform_plan', False)),
            ))
            if operation == 'terraform.plan':
                (workspace / 'execution.tfplan').write_bytes(b'approved-plan')
            return workspace

    monkeypatch.setattr(worker, 'persist_plan', lambda deployment_id, workspace: digest)
    monkeypatch.setattr(worker, 'restore_plan', lambda deployment_id, workspace, expected_sha256=None: True)
    monkeypatch.setattr(worker, 'delete_plan', lambda deployment_id: None)
    monkeypatch.setattr(
        worker,
        'register_managed_inventory',
        lambda context, workspace: {'external_id': '120', 'vm_id': 120, 'node': 'pve01'},
    )

    worker.run_blueprint_workflow(context, Executor())

    assert calls == [
        ('terraform.plan', True, False),
        ('terraform.apply', False, True),
    ]


def test_health_check_requires_running_vm(monkeypatch):
    context = FakeContext([])
    provider = SimpleNamespace(
        vm_status=lambda node, vm_id: {'status': 'stopped'},
    )
    monkeypatch.setattr(worker, 'provider_for', lambda credential: provider)
    monkeypatch.setattr(worker, 'vm_id_from_state', lambda workspace: 121)

    with pytest.raises(ExecutionFailed, match='VM is not running'):
        worker.health_check_vm(context, '/tmp/workspace')


def test_wait_for_ansible_transport_uses_credential_transport(monkeypatch):
    context = FakeContext([])
    context.ansible_credential = SimpleNamespace(type='ssh')
    observed = []
    monkeypatch.setattr(worker, 'wait_for_ip', lambda context, workspace, timeout=600: ['192.0.2.50'])
    monkeypatch.setattr(
        worker,
        'wait_for_tcp_addresses',
        lambda context, addresses, port, timeout, label: observed.append((list(addresses), port, label)) or addresses[0],
    )

    assert worker.wait_for_ansible_transport(context, '/tmp/workspace', timeout=30) == ['192.0.2.50']
    assert observed[-1] == (['192.0.2.50'], 22, 'SSH')

    context.ansible_credential = SimpleNamespace(type='winrm')
    assert worker.wait_for_ansible_transport(context, '/tmp/workspace', timeout=30) == ['192.0.2.50']
    assert observed[-1] == (['192.0.2.50'], 5986, 'WinRM HTTPS')
