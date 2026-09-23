"""First-boot Cloud-init regressions; no live Proxmox or guest SSH is used."""
import copy
import json
from contextlib import contextmanager, nullcontext
from pathlib import Path
import shutil
import subprocess
import textwrap
from types import SimpleNamespace

import pytest
import yaml

from app.automation.cloud_init import (
    blueprint_snapshot, node_ssh_environment, validate_cloud_init_workflow,
    validate_cloud_init_workspace, validate_snippet_storage,
)


ROOT = Path(__file__).resolve().parents[1]


def workflow():
    return [
        {'id': 'cloud_init', 'type': 'cloud_init', 'depends_on': []},
        {'id': 'apply', 'type': 'terraform_apply', 'depends_on': ['cloud_init']},
        {'id': 'agent', 'type': 'wait_for_agent', 'depends_on': ['apply']},
        {'id': 'ip', 'type': 'wait_for_ip', 'depends_on': ['agent']},
    ]


def test_cloud_init_is_explicit_and_legacy_workflows_remain_legacy():
    assert validate_cloud_init_workflow(workflow()) is True
    assert validate_cloud_init_workflow([{'id': 'apply', 'type': 'terraform_apply'}]) is False
    assert validate_cloud_init_workflow([]) is False


@pytest.mark.parametrize('mutation', ['parallel', 'after_apply', 'duplicate', 'condition', 'retry', 'missing', 'cycle'])
def test_cloud_init_rejects_unsafe_workflow(mutation):
    steps = workflow()
    if mutation == 'parallel':
        steps[1]['depends_on'] = []
    elif mutation == 'after_apply':
        steps[0]['depends_on'] = ['apply']
        steps[1]['depends_on'] = []
    elif mutation == 'duplicate':
        steps.append({'id': 'other_cloud', 'type': 'cloud_init'})
    elif mutation == 'condition':
        steps[0]['conditions'] = {'provider': 'proxmox'}
    elif mutation == 'retry':
        steps[0]['retry'] = 1
    elif mutation == 'missing':
        steps[1]['depends_on'] = ['missing']
    else:
        steps[0]['depends_on'] = ['apply']
    with pytest.raises(ValueError):
        validate_cloud_init_workflow(steps)


def test_cloud_init_precedes_the_approved_plan_not_just_apply():
    steps = workflow()
    steps.insert(1, {'id': 'plan', 'type': 'terraform_plan', 'depends_on': []})
    steps.insert(2, {'id': 'approval', 'type': 'approval', 'depends_on': ['plan']})
    steps[3]['depends_on'] = ['cloud_init', 'approval']
    with pytest.raises(ValueError, match='ancestor'):
        validate_cloud_init_workflow(steps)
    steps[1]['depends_on'] = ['cloud_init']
    assert validate_cloud_init_workflow(steps)


@pytest.mark.parametrize('marker', ['.cloudportal-qemu-bootstrap.json', '.cloudportal-qemu-bootstrap-key'])
def test_legacy_bootstrap_identity_is_preserved(tmp_path, marker):
    path = tmp_path / marker
    path.write_text('existing-secret-or-metadata')
    with pytest.raises(ValueError, match='recovery'):
        validate_cloud_init_workspace(tmp_path)
    assert path.read_text() == 'existing-secret-or-metadata'


@pytest.mark.parametrize('content', ['images,snippets', ['images', 'snippets']])
def test_snippet_storage_supports_both_provider_content_formats(content):
    validate_snippet_storage([{'storage': 'local', 'content': content, 'active': 1}], 'local')


@pytest.mark.parametrize('rows,selected', [
    ([], None), ([], 'local'),
    ([{'storage': 'local', 'content': 'images'}], 'local'),
    ([{'storage': 'local', 'content': 'snippets', 'disable': 1}], 'local'),
    ([{'storage': 'local', 'content': 'snippets', 'active': 0}], 'local'),
    ([{'storage': 'local', 'content': 'snippets', 'enabled': 0}], 'local'),
])
def test_bad_snippet_storage_is_rejected(rows, selected):
    with pytest.raises(ValueError):
        validate_snippet_storage(rows, selected)


def test_node_ssh_environment_does_not_copy_unrelated_or_guest_secrets():
    env = {}
    node_ssh_environment(env, {
        'PROXMOX_VE_SSH_USERNAME': 'node-admin',
        'PROXMOX_VE_SSH_PASSWORD': 'node-password',
        'GUEST_PASSWORD': 'must-not-copy',
        'AWS_SECRET_ACCESS_KEY': 'must-not-copy-either',
    }, {'token_secret': 'api-token'}, 'api@pve')
    assert env == {'PROXMOX_VE_SSH_USERNAME': 'node-admin', 'PROXMOX_VE_SSH_PASSWORD': 'node-password'}


def test_job_snapshot_takes_priority_over_later_deployment_configuration():
    context = SimpleNamespace(
        job=SimpleNamespace(payload={'blueprint': {'guest_credential_id': 17}}),
        deployment=SimpleNamespace(workflow={'blueprint': {'guest_credential_id': 99}}),
    )
    assert blueprint_snapshot(context)['guest_credential_id'] == 17


def run_ui(expression):
    executable = shutil.which('node')
    if not executable:
        pytest.skip('node is required')
    scripts = ['blueprint-wizard-core.js', 'blueprint-wizard-cloud-init.js']
    setup = 'global.window = {}; global.registerExtension = () => {};\n'
    for name in scripts:
        path = ROOT / 'app/web/features' / name
        setup += 'eval(require("fs").readFileSync(' + json.dumps(str(path)) + ', "utf8"));\n'
    setup += 'const parts = window.BlueprintWizardParts;\n'
    result = subprocess.run([executable, '-e', setup + expression], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def test_ui_preview_uses_metadata_not_passwords_or_private_keys():
    result = run_ui('''
const state = parts.core.stateDefaults();
state.providerType = 'proxmox'; state.guestCredentialId = '4';
const credentials = [{id: 4, username: 'finaluser', supports_cloud_init_password: true,
  supports_cloud_init_ssh_key: true, password: 'UNIQUE_PASSWORD_SECRET', private_key: 'UNIQUE_PRIVATE_KEY'}];
const enabled = parts.cloudInit.preview(state, credentials);
state.installQemuGuestAgent = false;
console.log(JSON.stringify({enabled, disabled: parts.cloudInit.preview(state, credentials)}));
''')
    assert 'UNIQUE_PASSWORD_SECRET' not in json.dumps(result)
    assert 'UNIQUE_PRIVATE_KEY' not in json.dumps(result)
    assert 'finaluser' in result['enabled']
    assert yaml.safe_load(result['enabled'])['packages'] == ['qemu-guest-agent']
    assert 'packages:' not in result['disabled']
    assert 'systemctl start qemu-guest-agent' in result['enabled']


def test_ui_adds_cloud_init_before_both_plan_and_apply_without_losing_approval():
    result = run_ui('''
const state = parts.core.stateDefaults(); state.providerType = 'proxmox'; state.advancedWorkflow = true;
state.workflow = [
 {id:'plan', type:'terraform_plan', depends_on:[]},
 {id:'approve', type:'approval', depends_on:['plan']},
 {id:'apply', type:'terraform_apply', depends_on:['approve']}];
parts.cloudInit.addStep(state); parts.cloudInit.addStep(state);
console.log(JSON.stringify({steps:state.workflow, errors:parts.cloudInit.validate(state)}));
''')
    assert len(result['steps']) == 4
    assert result['errors'] == {}
    assert result['steps'][-1]['depends_on'] == ['approve', 'cloud_init']
    assert validate_cloud_init_workflow(result['steps'])


def test_ui_reports_missing_snippets_before_submission():
    result = run_ui('''
const state = parts.core.stateDefaults(); state.providerType = 'proxmox'; state.cloudInitSnippetStorage = '';
const enabled = parts.cloudInit.validate(state); state.installQemuGuestAgent = false;
console.log(JSON.stringify({enabled, disabled:parts.cloudInit.validate(state)}));
''')
    assert 'cloud_init_snippet_storage' in result['enabled']
    assert result['disabled'] == {}


def test_template_installs_agent_at_first_boot_but_does_not_wait_in_terraform():
    source = (ROOT / 'terraform/templates/proxmox-vm/main.tf').read_text()
    document = textwrap.dedent(source.split('data = <<-EOF\n', 1)[1].split('\n    EOF', 1)[0])
    config = yaml.safe_load(document)
    assert config['packages'] == ['qemu-guest-agent']
    command = config['runcmd'][0]
    assert isinstance(command, str)
    assert 'systemctl enable qemu-guest-agent || true' in command
    assert 'systemctl start qemu-guest-agent\n' in command
    assert 'systemctl is-active --quiet qemu-guest-agent' in command
    assert 'wait_for_ip {\n      disabled = true' in source
    assert 'enabled = true' in source
    assert 'private_key' not in document
    assert 'password' not in document


@pytest.fixture
def executor_case(tmp_path, monkeypatch):
    from app.executors import terraform as module
    import app.providers.proxmox as proxmox
    variables = {'name': 'guest', 'node': 'pve01', 'install_qemu_guest_agent': True,
                 'cloud_init_snippet_storage': 'local', 'ipv4_address': None}
    blueprint = {'guest_credential_id': 17, 'steps': workflow()}
    logs, calls = [], []
    deployment = SimpleNamespace(id='deployment-id', workspace='workspace', template='proxmox-vm',
                                 variables=variables, workflow={'blueprint': copy.deepcopy(blueprint)})
    credential = SimpleNamespace(type='proxmox', endpoint='https://pve.invalid:8006', username='api@pve', verify_ssl=True)
    context = SimpleNamespace(job=SimpleNamespace(id='job-id', payload={'blueprint': blueprint}),
                              deployment=deployment, credential=credential, log=logs.append, stage=logs.append)
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'main.tf').write_text('terraform {}\n')
    monkeypatch.setattr(module, 'settings', lambda: SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr(module, 'template_definition', lambda _: ({'provider': 'proxmox'}, source))
    monkeypatch.setattr(module, 'decrypt_secret', lambda _: {'token_id': 'token', 'token_secret': 'token-secret'})
    monkeypatch.setattr(module, 'distributed_deployment_lock', lambda _: nullcontext())
    monkeypatch.setattr(module, 'restore_state', lambda *_: None)
    monkeypatch.setattr(module, 'prepare_qemu_bootstrap', lambda *_: pytest.fail('guest SSH bootstrap must not be used'))

    def guest_variables(_deployment, *, blueprint):
        assert blueprint['guest_credential_id'] == 17
        return {'ssh_username': 'finaluser', 'ssh_public_key': None}, 'guest-password-secret'
    monkeypatch.setattr(module, 'guest_credential_runtime_variables', guest_variables)

    class Provider:
        ready = True
        def __init__(self, value):
            assert value is credential
        def discover(self, resource, node=None):
            assert (resource, node) == ('storages', 'pve01')
            return [{'storage': 'local', 'content': 'snippets', 'active': 1}]
        def ssh_preflight(self, env):
            assert 'guest-password-secret' not in env.values()
            return {'ok': self.ready, 'reason': None if self.ready else 'ssh_auth_missing'}
    monkeypatch.setattr(proxmox, 'ProxmoxProvider', Provider)

    def run(command, workspace, env, _context, sensitive):
        variables = json.loads((workspace / 'terraform.tfvars.json').read_text())
        calls.append((command, variables, dict(env), list(sensitive)))
        if command[1] == 'plan':
            (workspace / 'execution.tfplan').write_bytes(b'approved-plan-fixture')
    monkeypatch.setattr(module, 'run_process', run)
    return module, context, calls, logs, Provider


def test_dhcp_native_credentials_never_create_bootstrap_account(executor_case):
    module, context, calls, logs, _ = executor_case
    original = copy.deepcopy(context.deployment.variables)
    workspace = module.TerraformExecutor().execute('terraform.apply', context)
    assert [call[0][1] for call in calls] == ['init', 'plan', 'apply']
    for _, variables, env, sensitive in calls:
        assert variables['qemu_guest_agent_bootstrap'] is False
        assert variables['ssh_username'] == 'finaluser'
        assert 'bootstrap_username' not in variables
        assert 'guest-password-secret' not in json.dumps(variables)
        assert env['TF_VAR_ssh_password'] == 'guest-password-secret'
        assert 'guest-password-secret' in sensitive
    assert context.deployment.variables == original
    assert 'guest-password-secret' not in json.dumps(context.job.payload)
    assert 'guest-password-secret' not in '\n'.join(logs)
    assert not (workspace / module.QEMU_BOOTSTRAP_MARKER).exists()
    assert not (workspace / module.QEMU_BOOTSTRAP_KEY).exists()


@pytest.mark.parametrize('failure', ['missing_storage', 'node_ssh', 'legacy_marker'])
def test_native_preflight_failure_never_starts_terraform(executor_case, failure):
    module, context, calls, _, provider = executor_case
    if failure == 'missing_storage':
        context.deployment.variables['cloud_init_snippet_storage'] = None
    elif failure == 'node_ssh':
        provider.ready = False
    else:
        workspace = module.settings().data_dir / 'workspaces' / context.deployment.workspace
        workspace.mkdir(parents=True)
        (workspace / module.QEMU_BOOTSTRAP_MARKER).write_text('{}')
    with pytest.raises(module.ExecutionFailed):
        module.TerraformExecutor().execute('terraform.apply', context)
    assert calls == []


def test_native_account_without_agent_install_does_not_need_node_ssh(executor_case):
    module, context, calls, _, provider = executor_case
    context.deployment.variables.update(install_qemu_guest_agent=False, cloud_init_snippet_storage=None)
    provider.ready = False
    module.TerraformExecutor().execute('terraform.apply', context)
    assert calls[-1][1]['ssh_username'] == 'finaluser'
    assert calls[-1][1]['qemu_guest_agent_bootstrap'] is False


def test_native_saved_plan_is_reused_not_replanned(executor_case):
    module, context, calls, logs, _ = executor_case
    workspace = module.settings().data_dir / 'workspaces' / context.deployment.workspace
    workspace.mkdir(parents=True)
    (workspace / 'execution.tfplan').write_bytes(b'approved-plan-fixture')
    context.apply_saved_terraform_plan = True
    module.TerraformExecutor().execute('terraform.apply', context)
    assert [call[0][1] for call in calls] == ['init', 'apply']
    assert 'terraform.plan.reuse' in logs
