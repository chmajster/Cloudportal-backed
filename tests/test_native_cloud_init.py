"""NoCloud first-boot contracts, without access to a live hypervisor."""
from contextlib import nullcontext
import io
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
import yaml

from app.executors import cloud_init as seed
from app.executors.base import ExecutionFailed


def context(tmp_path):
    steps = [
        {'id': 'cloud_init', 'type': 'cloud_init', 'depends_on': []},
        {'id': 'apply', 'type': 'terraform_apply', 'depends_on': ['cloud_init']},
    ]
    return SimpleNamespace(
        job=SimpleNamespace(id='job-1', payload={'blueprint': {'steps': steps, 'guest_credential_id': 7}}),
        deployment=SimpleNamespace(
            id='deployment-1', workspace='deployment-1', provider='proxmox', template='proxmox-vm', workflow={},
            variables={'name': 'vm01', 'node': 'pve1', 'template_id': 9001, 'storage': 'local-lvm',
                       'ssh_username': 'clouduser', 'install_qemu_guest_agent': True},
        ),
        credential=SimpleNamespace(type='proxmox', username='api@pve', endpoint='https://pve.test:8006', verify_ssl=True),
        check=lambda: None, stage=lambda message: None, log=lambda message: None,
    )


@pytest.fixture
def provider(monkeypatch):
    from app.providers import proxmox
    calls = []
    class Provider:
        def __init__(self, credential):
            pass
        def discover(self, resource, node):
            calls.append((resource, node))
            return [{'storage': 'local', 'content': 'iso,vztmpl', 'active': 1, 'enabled': 1}]
        def vm_config(self, node, vm_id):
            calls.append(('vm_config', node, vm_id))
            return {'ostype': 'l26', 'scsi0': 'local-lvm:base-9001-disk-0', 'ide2': 'local-lvm:vm-9001-cloudinit,media=cdrom'}
        def ssh_preflight(self, *args, **kwargs):
            pytest.fail('Cloud-init must not require Proxmox SSH')
        def guest_addresses(self, *args, **kwargs):
            pytest.fail('Cloud-init must not require guest IP or QGA')
    monkeypatch.setattr(proxmox, 'ProxmoxProvider', Provider)
    return calls


def test_cloud_init_precedes_plan_and_apply(tmp_path):
    ctx = context(tmp_path)
    assert seed.native_cloud_init_requested(ctx)
    ctx.job.payload['blueprint']['steps'].insert(1, {'id': 'plan', 'type': 'terraform_plan', 'depends_on': []})
    with pytest.raises(ExecutionFailed, match='precede every'):
        seed.native_cloud_init_requested(ctx)


@pytest.mark.parametrize('changes', [
    {'conditions': {'provider': 'proxmox'}}, {'rollback': 'rollback'}, {'retry': 1},
])
def test_cloud_init_is_unconditional(tmp_path, changes):
    ctx = context(tmp_path)
    ctx.job.payload['blueprint']['steps'][0].update(changes)
    with pytest.raises(ExecutionFailed, match='unconditional'):
        seed.native_cloud_init_requested(ctx)


def test_no_cloud_init_keeps_legacy_workflow(tmp_path):
    ctx = context(tmp_path)
    ctx.job.payload['blueprint']['steps'] = [{'id': 'apply', 'type': 'terraform_apply'}]
    assert seed.native_cloud_init_requested(ctx) is False
    ctx.job.payload = {}
    del ctx.deployment.workflow
    assert seed.native_cloud_init_requested(ctx) is False


def test_non_proxmox_cloud_init_is_rejected(tmp_path):
    ctx = context(tmp_path)
    ctx.deployment.provider = 'aws'
    with pytest.raises(ExecutionFailed, match='proxmox-vm'):
        seed.native_cloud_init_requested(ctx)


def test_render_dhcp_target_user_and_agent(tmp_path):
    variables = context(tmp_path).deployment.variables | {'ssh_username': 'operator', 'ssh_public_key': 'ssh-ed25519 PUBLIC test'}
    files = seed.render_seed(variables, instance_id='one', mac_address='02:00:11:22:33:44', password_hash='$6$HASHED')
    user = yaml.safe_load(files['user-data'])
    assert user['users'][0]['name'] == 'operator'
    assert user['users'][0]['hashed_passwd'] == '$6$HASHED'
    assert user['users'][0]['lock_passwd'] is False
    assert user['users'][0]['ssh_authorized_keys'] == ['ssh-ed25519 PUBLIC test']
    assert user['ssh_pwauth'] is True
    assert user['chpasswd']['expire'] is False
    assert user['packages'] == ['qemu-guest-agent']
    assert 'systemctl enable qemu-guest-agent' in user['runcmd'][0][2]
    assert 'systemctl start qemu-guest-agent' in user['runcmd'][0][2]
    network = yaml.safe_load(files['network-config'])['ethernets']['primary']
    assert network['dhcp4'] is True
    assert network['match']['macaddress'] == '02:00:11:22:33:44'
    assert 'cpbootstrap' not in json.dumps(files)


def test_existing_template_account_is_preserved_by_cloud_init(tmp_path):
    variables = context(tmp_path).deployment.variables | {
        'ssh_username': 'predefined',
        'ssh_public_key': 'ssh-ed25519 SHOULD-NOT-BE-INJECTED test',
        'install_qemu_guest_agent': True,
    }
    files = seed.render_seed(
        variables,
        instance_id='existing-account',
        mac_address='02:00:11:22:33:44',
        password_hash='$6$SHOULD-NOT-BE-USED',
        guest_account_mode='existing_template',
    )
    user_data = yaml.safe_load(files['user-data'])
    assert 'users' not in user_data
    assert 'chpasswd' not in user_data
    assert 'ssh_pwauth' not in user_data
    assert 'disable_root' not in user_data
    assert 'predefined' not in files['user-data']
    assert 'SHOULD-NOT-BE-INJECTED' not in files['user-data']
    assert 'SHOULD-NOT-BE-USED' not in files['user-data']
    assert user_data['packages'] == ['qemu-guest-agent']
    assert user_data['hostname'] == 'vm01'


def test_static_ip_and_dns_are_in_seed(tmp_path):
    variables = context(tmp_path).deployment.variables | {
        'ipv4_address': '192.0.2.10/24', 'ipv4_gateway': '192.0.2.1',
        'dns_servers': ['192.0.2.53'], 'dns_domain': 'lab.example',
        'install_qemu_guest_agent': False,
    }
    files = seed.render_seed(variables, instance_id='one', mac_address='02:00:11:22:33:44')
    nic = yaml.safe_load(files['network-config'])['ethernets']['primary']
    assert nic['dhcp4'] is False
    assert nic['addresses'] == ['192.0.2.10/24']
    assert nic['routes'] == [{'to': '0.0.0.0/0', 'via': '192.0.2.1'}]
    assert nic['nameservers'] == {'addresses': ['192.0.2.53'], 'search': ['lab.example']}
    user = yaml.safe_load(files['user-data'])
    assert 'packages' not in user and 'runcmd' not in user
    assert user['ssh_pwauth'] is False


@pytest.mark.parametrize('username', ['bad user', 'root;id', 'a' * 33, '../root'])
def test_invalid_username_rejected(tmp_path, username):
    with pytest.raises(ExecutionFailed, match='username'):
        seed.render_seed(context(tmp_path).deployment.variables | {'ssh_username': username}, instance_id='one', mac_address='02:00:11:22:33:44')


def test_iso_storage_selection_and_preflight():
    assert seed.choose_iso_storage([
        {'storage': 'local-lvm', 'content': 'images'},
        {'storage': 'shared', 'content': ['iso'], 'enabled': 0},
        {'storage': 'local', 'content': 'iso,vztmpl', 'active': 1},
    ], 'local-lvm') == 'local'
    with pytest.raises(ExecutionFailed, match='ISO-capable'):
        seed.choose_iso_storage([{'storage': 'local-lvm', 'content': 'images'}], 'local-lvm')


def test_template_cdrom_replaces_only_cloudinit_or_free_slot():
    assert seed.seed_interface({'scsi0': 'local:disk', 'ide2': 'local:vm-9001-cloudinit,media=cdrom'}) == 'ide2'
    assert seed.seed_interface({'ide2': 'local:iso/important.iso,media=cdrom'}) == 'ide0'
    with pytest.raises(ExecutionFailed, match='multiple'):
        seed.seed_interface({'ide2': 'local:cloudinit', 'sata0': 'local:cloudinit'})
    with pytest.raises(ExecutionFailed, match='Windows'):
        seed.seed_interface({'ostype': 'win11'})


def test_iso_roundtrip_label_filenames_and_permissions(tmp_path):
    import pycdlib
    path = tmp_path / 'seed.iso'
    files = seed.render_seed(context(tmp_path).deployment.variables, instance_id='unique', mac_address='02:00:11:22:33:44')
    seed.write_iso(path, files)
    assert path.stat().st_mode & 0o777 == 0o600
    iso = pycdlib.PyCdlib()
    iso.open(str(path))
    try:
        assert iso.pvd.volume_identifier.decode().strip() == 'CIDATA'
        for name, text in files.items():
            stream = io.BytesIO()
            iso.get_file_from_iso_fp(stream, rr_path='/' + name)
            assert stream.getvalue().decode() == text
    finally:
        iso.close()


def test_password_hash_is_salted_and_errors_do_not_echo_secret(monkeypatch):
    password = 'Secret-Never-In-Logs-42'
    hashed = seed.hash_password(password, '0123456789abcdef')
    assert hashed.startswith('$6$rounds=100000$0123456789abcdef$')
    assert password not in hashed
    monkeypatch.setattr(seed.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=1, stdout=password, stderr=password))
    with pytest.raises(ExecutionFailed) as error:
        seed.hash_password(password, '0123456789abcdef')
    assert password not in str(error.value)


def test_preparation_does_not_need_dhcp_agent_or_ssh(tmp_path, provider):
    ctx = context(tmp_path)
    logs = []
    ctx.log = logs.append
    password = 'This-Is-Only-A-Test-Password'
    result = seed.prepare_native_seed(ctx, tmp_path, {'ssh_username': 'operator'}, password)
    assert result['cloud_init_seed_storage'] == 'local'
    assert result['cloud_init_seed_interface'] == 'ide2'
    assert result['qemu_guest_agent_bootstrap'] is False
    assert provider == [('storages', 'pve1'), ('vm_config', 'pve1', 9001)]
    assert password not in json.dumps(result)
    assert password not in (tmp_path / seed.MANIFEST).read_text()
    assert password.encode() not in Path(result['cloud_init_seed_path']).read_bytes()
    assert password not in '\n'.join(logs)


def test_saved_plan_reuses_exact_media_without_provider_discovery(tmp_path, provider, monkeypatch):
    ctx = context(tmp_path)
    first = seed.prepare_native_seed(ctx, tmp_path, {}, 'Test-Password')
    before = Path(first['cloud_init_seed_path']).read_bytes()
    ctx.apply_saved_terraform_plan = True
    from app.providers import proxmox
    monkeypatch.setattr(proxmox, 'ProxmoxProvider', lambda credential: pytest.fail('Saved media must not rediscover or rewrite provider configuration'))
    assert seed.prepare_native_seed(ctx, tmp_path, {}, 'Test-Password') == first
    assert Path(first['cloud_init_seed_path']).read_bytes() == before
    with pytest.raises(ExecutionFailed, match='changed'):
        seed.prepare_native_seed(ctx, tmp_path, {}, 'Changed-Password')
    Path(first['cloud_init_seed_path']).write_bytes(b'tampered')
    with pytest.raises(ExecutionFailed, match='missing or changed'):
        seed.prepare_native_seed(ctx, tmp_path, {}, 'Test-Password')


def test_saved_plan_missing_seed_does_not_regenerate(tmp_path, provider):
    ctx = context(tmp_path)
    ctx.apply_saved_terraform_plan = True
    with pytest.raises(ExecutionFailed, match='no Cloud-init media'):
        seed.prepare_native_seed(ctx, tmp_path, {}, None)
    assert provider == []


def test_existing_legacy_vm_is_not_silently_rebuilt(tmp_path, provider):
    ctx = context(tmp_path)
    (tmp_path / 'terraform.tfstate').write_text(json.dumps({'resources': [
        {'type': 'proxmox_virtual_environment_vm', 'name': 'vm', 'instances': [{'attributes': {'vm_id': 123}}]},
    ]}))
    with pytest.raises(ExecutionFailed, match='existing VM'):
        seed.prepare_native_seed(ctx, tmp_path, {}, None)
    assert provider == []


@pytest.mark.parametrize('unit_state,should_enable', [('static', False), ('indirect', False), ('disabled', True)])
def test_agent_service_is_started_even_for_static_systemd_units(tmp_path, unit_state, should_enable):
    systemctl = tmp_path / 'systemctl'
    systemctl.write_text('''#!/bin/sh
printf '%s\\n' "$*" >> "$COMMAND_LOG"
if [ "$1" = is-enabled ]; then
  echo "$UNIT_STATE"
  [ "$UNIT_STATE" != disabled ]
  exit $?
fi
exit 0
''')
    systemctl.chmod(0o755)
    log = tmp_path / 'commands'
    env = dict(os.environ, PATH=str(tmp_path) + ':' + os.environ['PATH'], COMMAND_LOG=str(log), UNIT_STATE=unit_state)
    subprocess.run(['sh', '-ec', seed.AGENT_START], env=env, check=True)
    commands = log.read_text().splitlines()
    assert ('enable qemu-guest-agent' in commands) is should_enable
    assert 'start qemu-guest-agent' in commands
    assert 'is-active --quiet qemu-guest-agent' in commands


def test_executor_selects_native_media_and_keeps_password_out_of_terraform(tmp_path, monkeypatch):
    from app.executors import terraform
    ctx = context(tmp_path)
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'main.tf').write_text('# approved test template\n')
    monkeypatch.setattr(terraform, 'template_definition', lambda template: ({'provider': 'proxmox'}, source))
    monkeypatch.setattr(terraform, 'settings', lambda: SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr(terraform, 'execution_environment', lambda workspace: {'PATH': '/usr/bin'})
    monkeypatch.setattr(terraform, 'distributed_deployment_lock', lambda deployment: nullcontext())
    monkeypatch.setattr(terraform, 'restore_state', lambda *args: False)
    monkeypatch.setattr(terraform, 'decrypt_secret', lambda credential: {'token_id': 'api@pve!test', 'token_secret': 'provider-secret'})
    monkeypatch.setattr(terraform, 'prepare_qemu_bootstrap', lambda *args: pytest.fail('Native Cloud-init must never create an SSH bootstrap account'))
    resolved = []
    def resolve(deployment, *, blueprint=None):
        resolved.append(blueprint['guest_credential_id'])
        return {'ssh_username': 'operator'}, 'guest-secret-never-in-terraform'
    monkeypatch.setattr(terraform, 'guest_credential_runtime_variables', resolve)
    def media(context, workspace, variables, password):
        assert variables['ssh_username'] == 'operator'
        assert password == 'guest-secret-never-in-terraform'
        return {'cloud_init_seed_path': str(workspace / 'seed.iso'), 'qemu_guest_agent_bootstrap': False}
    monkeypatch.setattr(terraform, 'prepare_native_seed', media)
    commands = []
    def process(command, workspace, env, context, sensitive_values):
        variables = json.loads((workspace / 'terraform.tfvars.json').read_text())
        assert 'guest-secret-never-in-terraform' not in json.dumps(variables)
        assert 'guest-secret-never-in-terraform' not in json.dumps(env)
        assert 'TF_VAR_ssh_password' not in env
        assert variables['cloud_init_seed_path'].endswith('seed.iso')
        commands.append(command[1])
    monkeypatch.setattr(terraform, 'run_process', process)
    workspace = terraform.TerraformExecutor().execute('terraform.apply', ctx)
    assert resolved == [7]
    assert commands == ['init', 'plan', 'apply']
    assert not (workspace / 'terraform.tfvars.json').exists()


def test_wizard_cloud_init_contracts():
    root = Path(__file__).resolve().parents[1]
    program = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const sandbox = { window: {}, registerExtension() {} };
vm.createContext(sandbox);
for (const name of ['blueprint-wizard-core.js', 'blueprint-wizard-cloud-init.js']) {
  vm.runInContext(fs.readFileSync('app/web/features/' + name, 'utf8'), sandbox);
}
const parts = sandbox.window.BlueprintWizardParts;
const steps = parts.core.workflow({cloudInit: true, waitAgent: true});
assert.deepEqual(Array.from(steps, row => row.type), ['cloud_init', 'terraform_apply', 'wait_for_agent', 'wait_for_ip']);
assert.deepEqual(Array.from(steps[1].depends_on), ['cloud_init']);
assert.equal(parts.core.workflow({cloudInit: false})[0].type, 'terraform_apply');
const state = parts.core.stateDefaults();
state.providerType = 'proxmox'; state.providerId = '1'; state.terraformTemplateId = 'proxmox-vm';
state.selectedTemplateVmid = '9001'; state.node = 'pve'; state.storage = 'local-lvm'; state.guestCredentialId = '7';
const data = {providers: [{id: 1, type: 'proxmox', credentials_id: 1}], templates: [{id: 'proxmox-vm', provider: 'proxmox'}], schemes: [], playbooks: []};
const payload = parts.core.buildPayload(state, data);
assert.equal(payload.workflow[0].type, 'cloud_init');
assert.equal(payload.deployment.guest_credential_id, 7);
assert.equal(payload.deployment.guest_account_mode, 'cloud_init_managed');
assert.equal(payload.deployment.variables.cloud_init_snippet_storage, null);
state.guestAccountMode = 'existing_template';
const existingPayload = parts.core.buildPayload(state, data);
assert.equal(existingPayload.deployment.guest_account_mode, 'existing_template');
assert.equal(Object.keys(parts.cloudInit.validate(state)).length, 0);
state.guestCredentialId = '';
assert(parts.cloudInit.validate(state).guest_credential_id);
state.guestCredentialId = '7';
state.advancedWorkflow = true; state.workflow = [{id:'plan',type:'terraform_plan',depends_on:[]},{id:'apply',type:'terraform_apply',depends_on:['plan']}];
parts.cloudInit.toggleStep(state, true);
assert.equal(state.workflow[0].type, 'cloud_init');
assert.equal(Object.keys(parts.cloudInit.validate(state)).length, 0);
parts.cloudInit.toggleStep(state, true);
assert.equal(state.workflow.filter(step => step.type === 'cloud_init').length, 1);
state.workflow.find(step => step.id === 'plan').depends_on = [];
assert(parts.cloudInit.validate(state).workflow);
parts.cloudInit.toggleStep(state, false);
assert(!state.workflow.some(step => step.type === 'cloud_init'));
assert.equal(state.guestAccountMode, 'cloud_init_managed');
state.guestAccountMode = 'existing_template';
const preview = parts.cloudInit.preview(state, [{id:7,username:'operator',password:'DO-NOT-LEAK',private_key:'PRIVATE-DO-NOT-LEAK'}]);
assert(preview.includes('operator')); assert(preview.includes('qemu-guest-agent'));
assert(preview.includes('Cloud-init nie utworzy użytkownika'));
assert(!preview.includes('users:'));
assert(!preview.includes('DO-NOT-LEAK'));
state.providerType = 'aws'; assert.equal(parts.cloudInit.enabled(state), false);
'''
    subprocess.run(['node', '-e', program], cwd=root, check=True, capture_output=True, text=True)
