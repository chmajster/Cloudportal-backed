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
state.tags = 'linux, production';
state.ansibleEnabled = true;
state.playbookId = 'bootstrap-linux';
state.ansibleCredentialId = '17';
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
};
console.log(JSON.stringify(core.buildPayload(state, data)));
""")

    deployment = result['deployment']
    assert deployment['provider_id'] == 7
    assert deployment['credentials_id'] == 5
    assert deployment['hostname_scheme_id'] == 11
    assert deployment['hostname_values'] == {'location': 'wro', 'env': 'prod', 'role': 'web'}
    assert deployment['ipam_pool_id'] == 13
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
        'generate_hostname',
        'allocate_ip',
        'clone_vm',
        'cloud_init',
        'set_tags',
        'terraform_apply',
        'wait_for_agent',
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
        'generate_hostname',
        'clone_vm',
        'cloud_init',
        'terraform_apply',
        'wait_for_agent',
    ]
    assert result['workflow'][1]['depends_on'] == ['hostname']
