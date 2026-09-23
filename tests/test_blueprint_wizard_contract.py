import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / 'app' / 'web' / 'features' / 'blueprint-wizard-core.js'


def run_core(expression: str):
    node = shutil.which('node')
    if not node:
        pytest.skip('node is required for Blueprint wizard contract tests')
    script = f"""
global.window = {{}};
global.registerExtension = (_name, initialize) => initialize();
eval(require('fs').readFileSync({json.dumps(str(CORE))}, 'utf8'));
const core = window.BlueprintWizardParts.core;
{expression}
"""
    result = subprocess.run([node, '-e', script], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def test_wizard_payload_preserves_hostname_ipam_ansible_and_provider_credentials():
    result = run_core("""
const state = core.stateDefaults();
state.name = 'WWW Production';
state.slug = 'www-production';
state.providerId = '7';
state.providerType = 'proxmox';
state.terraformTemplateId = 'proxmox-vm';
state.node = 'pve01';
state.selectedTemplateVmid = '9000';
state.selectedTemplateNode = 'pve01';
state.cpu = 4;
state.memory = 8192;
state.disk = 80;
state.storage = 'local-lvm';
state.network = 'vmbr20';
state.hostnameEnabled = true;
state.hostnameSchemeId = '11';
state.hostnameValues = { location: 'wro', env: 'prod', role: 'web' };
state.ipMode = 'ipam';
state.ipamPoolId = '13';
state.environment = 'dev';
state.apmid = 'IAASTEAM';
state.selectEnvironmentOnExecute = true;
state.selectApmidOnExecute = true;
state.tags = 'linux, production';
state.ansibleEnabled = true;
state.playbookId = 'bootstrap-linux';
state.ansibleCredentialId = '17';
state.guestCredentialId = '18';
state.cloudInitSnippetStorage = 'local';
state.waitAgent = true;

const data = {
  providers: [{ id: 7, type: 'proxmox', credentials_id: 5, name: 'LAB' }],
  templates: [{
    id: 'proxmox-vm',
    provider: 'proxmox',
    variables_schema: { properties: { name: { type: 'string' } }, required: ['name'] },
  }],
  playbooks: [{
    id: 'bootstrap-linux',
    transport: 'ssh',
    required_variables: ['hostname'],
  }],
  schemes: [{
    id: 11,
    pattern: '{location}-{env}-{role}-{number}',
  }],
};
console.log(JSON.stringify(core.buildPayload(state, data)));
""")

    deployment = result['deployment']
    assert deployment['provider_id'] == 7
    assert deployment['credentials_id'] == 5
    assert deployment['hostname_scheme_id'] == 11
    assert deployment['hostname_values'] == {'env': 'prod'}
    assert deployment['ipam_pool_id'] == 13
    assert deployment['guest_credential_id'] == 18
    assert deployment['variables']['install_qemu_guest_agent'] is True
    assert deployment['variables']['cloud_init_snippet_storage'] == 'local'
    assert deployment['environment'] == 'dev'
    assert deployment['apmid'] == 'IAASTEAM'
    assert deployment['select_environment_on_execute'] is True
    assert deployment['select_apmid_on_execute'] is True
    assert deployment['name'] == '{{ hostname }}'
    assert deployment['variables']['name'] == '{{ hostname }}'
    assert deployment['ansible']['playbook'] == 'bootstrap-linux'
    assert deployment['ansible']['credentials_id'] == 17
    assert deployment['ansible']['variables']['hostname'] == '{{ hostname }}'
    assert deployment['variables']['tags'] == [
        'linux', 'production', 'apmid-iaasteam', 'env-dev', 'iaasteam.dev'
    ]

    workflow_types = [step['type'] for step in result['workflow']]
    assert workflow_types == [
        'terraform_apply',
        'wait_for_agent',
        'wait_for_ip',
        'run_ansible_playbook',
    ]


def test_wizard_slug_and_default_workflow_are_deterministic():
    result = run_core("""
console.log(JSON.stringify({
  slug: core.slugify('  Serwer WWW / Produkcja  '),
  workflow: core.workflow({ hostname: true, ipam: false, tags: false, waitAgent: true, ansible: false }),
}));
""")
    assert result['slug'] == 'serwer-www-produkcja'
    assert [step['type'] for step in result['workflow']] == [
        'terraform_apply',
        'wait_for_agent',
        'wait_for_ip',
    ]
    assert result['workflow'][1]['depends_on'] == ['apply']


def test_wizard_drops_hostname_defaults_not_used_by_selected_pattern():
    result = run_core("""
const state = core.stateDefaults();
state.providerId = '7';
state.providerType = 'proxmox';
state.terraformTemplateId = 'proxmox-vm';
state.node = 'pve01';
state.selectedTemplateVmid = '9000';
state.selectedTemplateNode = 'pve01';
state.storage = 'local-lvm';
state.hostnameEnabled = true;
state.hostnameSchemeId = '22';
state.hostnameValues = {
  location: 'wro',
  role: 'server',
  env: 'test',
  environment: 'test',
  application: 'portal',
};

const data = {
  providers: [{ id: 7, type: 'proxmox', credentials_id: 5 }],
  templates: [{
    id: 'proxmox-vm',
    provider: 'proxmox',
    variables_schema: { properties: { name: { type: 'string' } } },
  }],
  playbooks: [],
  schemes: [{ id: 22, pattern: 'srl{number}' }],
};

console.log(JSON.stringify(core.buildDeployment(state, data)));
""")
    assert result['hostname_scheme_id'] == 22
    assert result['hostname_values'] == {}


def test_wizard_keeps_only_defaults_required_by_selected_pattern():
    result = run_core("""
const state = core.stateDefaults();
state.providerId = '7';
state.providerType = 'proxmox';
state.terraformTemplateId = 'proxmox-vm';
state.node = 'pve01';
state.selectedTemplateVmid = '9000';
state.selectedTemplateNode = 'pve01';
state.storage = 'local-lvm';
state.hostnameEnabled = true;
state.hostnameSchemeId = '23';
state.hostnameValues = {
  location: 'wro',
  role: 'server',
  env: 'dev',
  environment: 'prod',
  application: 'portal',
};

const data = {
  providers: [{ id: 7, type: 'proxmox', credentials_id: 5 }],
  templates: [{
    id: 'proxmox-vm',
    provider: 'proxmox',
    variables_schema: { properties: { name: { type: 'string' } } },
  }],
  playbooks: [],
  schemes: [{ id: 23, pattern: '{location}-{environment}-{role}-{number}' }],
};

console.log(JSON.stringify(core.buildDeployment(state, data)));
""")
    assert result['hostname_values'] == {'environment': 'prod'}


def test_wizard_runtime_classification_switches_default_to_fixed_values():
    result = run_core("""
const state = core.stateDefaults();
state.providerId = '7';
state.providerType = 'proxmox';
state.terraformTemplateId = 'proxmox-vm';
state.node = 'pve01';
state.selectedTemplateVmid = '9000';
state.selectedTemplateNode = 'pve01';
state.storage = 'local-lvm';
state.environment = 'test';
state.apmid = 'LEO';

const data = {
  providers: [{ id: 7, type: 'proxmox', credentials_id: 5 }],
  templates: [{
    id: 'proxmox-vm',
    provider: 'proxmox',
    variables_schema: { properties: { name: { type: 'string' } } },
  }],
  playbooks: [],
  schemes: [],
};

console.log(JSON.stringify(core.buildDeployment(state, data)));
""")
    assert result['environment'] == 'test'
    assert result['apmid'] == 'LEO'
    assert result['select_environment_on_execute'] is False
    assert result['select_apmid_on_execute'] is False


def test_wizard_allows_qemu_agent_guest_bootstrap_without_snippet_storage():
    node = shutil.which('node')
    if not node:
        pytest.skip('node is required for Blueprint wizard contract tests')
    script = f"""
global.window = {{}};
global.registerExtension = (_name, initialize) => initialize();
eval(require('fs').readFileSync({json.dumps(str(CORE))}, 'utf8'));
const core = window.BlueprintWizardParts.core;
const state = core.stateDefaults();
state.providerId = '7';
state.providerType = 'proxmox';
state.terraformTemplateId = 'proxmox-vm';
state.node = 'pve01';
state.selectedTemplateVmid = '9000';
state.selectedTemplateNode = 'pve01';
state.storage = 'local-lvm';
state.network = 'vmbr0';
state.installQemuGuestAgent = true;
state.waitAgent = true;
state.cloudInitSnippetStorage = '';
const data = {{
  providers: [{{ id: 7, type: 'proxmox', credentials_id: 5 }}],
  templates: [{{ id: 'proxmox-vm', provider: 'proxmox', variables_schema: {{ properties: {{ name: {{ type: 'string' }} }} }} }}],
  playbooks: [],
  schemes: [],
}};
try {{
  core.buildDeployment(state, data);
  console.log(JSON.stringify({{ ok: true }}));
}} catch (error) {{
  console.log(JSON.stringify({{ ok: false, message: error.message }}));
}}
"""
    result = subprocess.run([node, '-e', script], check=True, capture_output=True, text=True)
    payload = json.loads(result.stdout)
    assert payload['ok'] is True


def test_wizard_ansible_does_not_force_guest_agent():
    result = run_core("""
console.log(JSON.stringify(core.workflow({
  hostname: true,
  ipam: true,
  tags: true,
  waitAgent: false,
  ansible: true,
})));
""")
    assert [step['type'] for step in result] == [
        'terraform_apply',
        'wait_for_ip',
        'run_ansible_playbook',
    ]

def test_wizard_can_wait_for_preinstalled_qemu_agent_without_installing_it():
    result = run_core("""
const state = core.stateDefaults();
state.slug = 'preinstalled-agent';
state.name = 'Preinstalled Agent';
state.providerId = '7';
state.providerType = 'proxmox';
state.terraformTemplateId = 'proxmox-vm';
state.node = 'pve01';
state.selectedTemplateVmid = '9000';
state.selectedTemplateNode = 'pve01';
state.storage = 'local-lvm';
state.network = 'vmbr0';
state.installQemuGuestAgent = false;
state.waitAgent = true;
state.cloudInitSnippetStorage = '';

const data = {
  providers: [{ id: 7, type: 'proxmox', credentials_id: 5 }],
  templates: [{
    id: 'proxmox-vm',
    provider: 'proxmox',
    variables_schema: { properties: { name: { type: 'string' } } },
  }],
  playbooks: [],
  schemes: [],
};

console.log(JSON.stringify(core.buildPayload(state, data)));
""")

    assert result['deployment']['variables']['install_qemu_guest_agent'] is False
    assert result['deployment']['variables']['cloud_init_snippet_storage'] is None
    assert [step['type'] for step in result['workflow']] == [
        'terraform_apply',
        'wait_for_agent',
        'wait_for_ip',
    ]


def test_wizard_can_install_qemu_agent_without_waiting_for_it():
    result = run_core("""
const state = core.stateDefaults();
state.slug = 'install-agent-no-wait';
state.name = 'Install Agent No Wait';
state.providerId = '7';
state.providerType = 'proxmox';
state.terraformTemplateId = 'proxmox-vm';
state.node = 'pve01';
state.selectedTemplateVmid = '9000';
state.selectedTemplateNode = 'pve01';
state.storage = 'local-lvm';
state.network = 'vmbr0';
state.installQemuGuestAgent = true;
state.waitAgent = false;
state.cloudInitSnippetStorage = 'local';

const data = {
  providers: [{ id: 7, type: 'proxmox', credentials_id: 5 }],
  templates: [{
    id: 'proxmox-vm',
    provider: 'proxmox',
    variables_schema: { properties: { name: { type: 'string' } } },
  }],
  playbooks: [],
  schemes: [],
};

console.log(JSON.stringify(core.buildPayload(state, data)));
""")

    assert result['deployment']['variables']['install_qemu_guest_agent'] is True
    assert result['deployment']['variables']['cloud_init_snippet_storage'] == 'local'
    assert [step['type'] for step in result['workflow']] == ['terraform_apply']

