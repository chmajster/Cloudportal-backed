import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / 'app' / 'web' / 'features' / 'blueprint-wizard-core.js'
RUNTIME_APMID = ROOT / 'app' / 'web' / 'features' / 'blueprint-runtime-apmid.js'


def run_core(expression: str):
    node = shutil.which('node')
    if not node:
        pytest.skip('node is required for Blueprint wizard contract tests')
    script = f"""
global.window = {{}};
global.registerExtension = (_name, initialize) => initialize();
eval(require('fs').readFileSync({json.dumps(str(CORE))}, 'utf8'));
eval(require('fs').readFileSync({json.dumps(str(CORE.with_name('blueprint-wizard-cloud-init.js')))}, 'utf8'));
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
state.templateGuestCredentialId = '19';
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
    assert deployment['hostname_values'] == {}
    assert deployment['ipam_pool_id'] == 13
    assert deployment['guest_credential_id'] == 18
    assert deployment['template_guest_credential_id'] == 19
    assert deployment['variables']['install_qemu_guest_agent'] is True
    assert deployment['variables']['cloud_init_snippet_storage'] is None
    assert 'environment' not in deployment
    assert 'apmid' not in deployment
    assert deployment['select_environment_on_execute'] is True
    assert deployment['select_apmid_on_execute'] is True
    assert deployment['name'] == '{{ hostname }}'
    assert deployment['variables']['name'] == '{{ hostname }}'
    assert deployment['ansible']['playbook'] == 'bootstrap-linux'
    assert deployment['ansible']['credentials_id'] == 17
    assert deployment['ansible']['variables']['hostname'] == '{{ hostname }}'
    assert deployment['ansible_runs'] == [deployment['ansible']]
    assert deployment['variables']['tags'] == ['linux', 'production']

    workflow_types = [step['type'] for step in result['workflow']]
    assert workflow_types == [
        'cloud_init',
        'terraform_apply',
        'wait_for_agent',
        'wait_for_ip',
        'wait_for_ssh',
        'run_ansible_playbook',
    ]


def test_wizard_payload_preserves_multiple_ansible_runbooks_in_order():
    result = run_core("""
const state = core.stateDefaults();
state.name = 'Multi Ansible';
state.slug = 'multi-ansible';
state.providerId = '7';
state.providerType = 'proxmox';
state.terraformTemplateId = 'proxmox-vm';
state.node = 'pve01';
state.selectedTemplateVmid = '9000';
state.selectedTemplateNode = 'pve01';
state.storage = 'local-lvm';
state.network = 'vmbr0';
state.hostnameEnabled = false;
state.manualVmName = 'multi-ansible';
state.ansibleEnabled = true;
state.ansibleRuns = [
  { playbook: 'bootstrap-linux', credentials_id: 17, variables: { timezone: 'Europe/Warsaw' } },
  { playbook: 'linux-system-update', credentials_id: 18, variables: {} },
];

const data = {
  providers: [{ id: 7, type: 'proxmox', credentials_id: 5 }],
  templates: [{
    id: 'proxmox-vm',
    provider: 'proxmox',
    variables_schema: { properties: { name: { type: 'string' } } },
  }],
  playbooks: [
    { id: 'bootstrap-linux', transport: 'ssh', required_variables: [] },
    { id: 'linux-system-update', transport: 'ssh', required_variables: [] },
  ],
  schemes: [],
};

console.log(JSON.stringify(core.buildPayload(state, data)));
""")

    runs = result['deployment']['ansible_runs']
    assert [run['playbook'] for run in runs] == [
        'bootstrap-linux',
        'linux-system-update',
    ]
    assert [run['credentials_id'] for run in runs] == [17, 18]
    assert result['deployment']['ansible'] == runs[0]
    assert [step['type'] for step in result['workflow']] == [
        'cloud_init',
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
    assert result['workflow'][2]['type'] == 'wait_for_ip'
    assert result['workflow'][2]['timeout'] == 180


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


def test_wizard_scope_headers_are_emitted_only_for_complete_scope():
    result = run_core("""
const state = core.stateDefaults();
const empty = core.scopeHeaders(state);
state.tenantId = 'tenant-1';
const partial = core.scopeHeaders(state);
state.projectId = 'project-1';
const complete = core.scopeHeaders(state);
console.log(JSON.stringify({ empty, partial, complete }));
""")

    assert result['empty'] == {}
    assert result['partial'] == {}
    assert result['complete'] == {
        'X-Tenant-ID': 'tenant-1',
        'X-Project-ID': 'project-1',
    }


def test_runtime_apmid_flag_normalizes_legacy_values_and_read_has_safe_fallback():
    node = shutil.which('node')
    if not node:
        pytest.skip('node is required for Blueprint runtime classification tests')
    script = f"""
global.window = {{}};
global.registerExtension = (_name, initialize) => initialize();
eval(require('fs').readFileSync({json.dumps(str(RUNTIME_APMID))}, 'utf8'));
const runtime = window.BlueprintRuntimeApmid;
const trueValues = [true, 1, 'true', '1', 'yes', 'tak', 'on'].map(runtime.runtimeFlag);
const falseValues = [false, 0, null, '', 'false', '0', 'no', 'nie', 'off'].map(runtime.runtimeFlag);
const item = {{ deployment: {{ select_apmid_on_execute: 'true' }} }};
const form = {{ elements: {{}} }};
const read = runtime.read(form, {{ apmidSelectable: true, defaultApmid: 'LEO', environmentSelectable: true, defaultEnvironment: 'dev' }});
console.log(JSON.stringify({{
  trueValues,
  falseValues,
  selectable: runtime.allowsRuntimeApmid(item, ''),
  read,
}}));
"""
    result = subprocess.run([node, '-e', script], check=True, capture_output=True, text=True)
    payload = json.loads(result.stdout)
    assert payload['trueValues'] == [True] * 7
    assert payload['falseValues'] == [False] * 9
    assert payload['selectable'] is True
    assert payload['read'] == {'apmid': 'LEO', 'environment': 'dev'}


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
eval(require('fs').readFileSync({json.dumps(str(CORE.with_name('blueprint-wizard-cloud-init.js')))}, 'utf8'));
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
        'cloud_init',
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
    assert result['deployment']['variables']['cloud_init_snippet_storage'] is None
    assert [step['type'] for step in result['workflow']] == ['cloud_init', 'terraform_apply']




def test_wizard_payload_builds_automatic_awx_onboarding_after_cloud_init():
    result = run_core("""
const state = core.stateDefaults();
state.name = 'AWX VM';
state.slug = 'awx-vm';
state.providerId = '7';
state.providerType = 'proxmox';
state.terraformTemplateId = 'proxmox-vm';
state.node = 'pve01';
state.selectedTemplateVmid = '9000';
state.selectedTemplateNode = 'pve01';
state.storage = 'local-lvm';
state.network = 'vmbr0';
state.hostnameEnabled = false;
state.manualVmName = 'srv001';
state.cloudInitEnabled = true;
state.waitAgent = false;
state.awxEnabled = true;
state.awxCredentialId = '77';
state.awxOrganizationId = '9';
state.awxProjectId = '21';
state.awxInventoryId = '12';
state.awxInventoryName = 'Linux Servers';
state.awxGroupByEnvironment = true;
state.awxGroupByApmid = true;
state.awxJobTemplateId = '33';
state.environment = 'prod';
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

console.log(JSON.stringify(core.buildPayload(state, data)));
""")

    assert result['deployment']['awx'] == {
        'credential_id': 77,
        'organization_id': 9,
        'project_id': 21,
        'inventory_id': 12,
        'inventory_name': 'Linux Servers',
        'group_by_environment': True,
        'group_by_apmid': True,
        'job_template_id': 33,
        'remove_on_destroy': True,
    }
    assert [step['type'] for step in result['workflow']] == [
        'cloud_init',
        'terraform_apply',
        'wait_for_ip',
        'register_awx',
    ]
    assert result['workflow'][-2]['type'] == 'wait_for_ip'
    assert result['workflow'][-2]['timeout'] == 180
    assert result['workflow'][-1]['depends_on'] == ['guest_ip']
    assert result['workflow'][-1]['retry'] == 3
    assert result['workflow'][-1]['timeout'] == 300


def test_wizard_pending_hostname_scheme_is_created_by_bundle_not_embedded_as_nan_id():
    result = run_core("""
const state = core.stateDefaults();
state.name = 'Pending hostname';
state.slug = 'pending-hostname';
state.providerId = '7';
state.providerType = 'proxmox';
state.terraformTemplateId = 'proxmox-vm';
state.node = 'pve01';
state.selectedTemplateVmid = '9000';
state.selectedTemplateNode = 'pve01';
state.storage = 'local-lvm';
state.network = 'vmbr0';
state.hostnameEnabled = true;
state.hostnameSchemeId = '__pending__';
state.pendingHostnameScheme = {
  name: 'Pending pattern',
  pattern: 'srv-{env}-{number}',
  next_number: 1,
  padding: 3,
  is_active: true,
};
state.hostnameValues = { env: 'dev' };
state.environment = 'dev';
state.apmid = 'LEO';

const data = {
  providers: [{ id: 7, type: 'proxmox', credentials_id: 5 }],
  templates: [{
    id: 'proxmox-vm',
    provider: 'proxmox',
    variables_schema: { properties: { name: { type: 'string' } } },
  }],
  playbooks: [],
  schemes: [{
    id: '__pending__',
    name: 'Pending pattern',
    pattern: 'srv-{env}-{number}',
    next_number: 1,
    padding: 3,
    is_active: true,
  }],
};
const payload = core.buildPayload(state, data);
console.log(JSON.stringify({
  hasSchemeId: Object.prototype.hasOwnProperty.call(payload.deployment, 'hostname_scheme_id'),
  hostnameValues: payload.deployment.hostname_values,
  falseValue: core.coerceSchemaValue({type: 'boolean'}, 'false'),
  trueValue: core.coerceSchemaValue({type: 'boolean'}, 'true'),
}));
""")
    assert result['hasSchemeId'] is False
    assert result['hostnameValues'] == {'env': 'dev'}
    assert result['falseValue'] is False
    assert result['trueValue'] is True


def test_blueprint_console_uses_project_scope_and_single_wizard_editor():
    source = (ROOT / 'app' / 'web' / 'features' / 'blueprints.js').read_text()
    view_start = source.index('async function blueprintsView()')
    view_end = source.index('async function executeBlueprint', view_start)
    view = source[view_start:view_end]

    assert '/blueprints/creation-scopes?permission=blueprints.read&limit=200' in view
    assert "'X-Tenant-ID': String(selected.tenant_id)" in view
    assert "'X-Project-ID': String(selected.project_id)" in view
    assert "window.BlueprintWizard.open({" in view
    assert "button('Szybka edycja'" not in view
    assert "permission: null" in source[source.rfind("registerView({ id: 'blueprints'"):]


def test_blueprint_hostname_pattern_is_deferred_until_transactional_save():
    source = (ROOT / 'app' / 'web' / 'features' / 'blueprint-wizard-hostname.js').read_text()
    wizard = (ROOT / 'app' / 'web' / 'features' / 'blueprint-wizard.js').read_text()
    assert "state.hostnameSchemeId = '__pending__'" in source
    assert "api('/hostname-schemes', {" not in source
    assert "'/blueprints/bundle'" in wizard
    assert "/blueprints/${editingItem.id}/bundle" in wizard


def test_blueprint_wizard_uses_scoped_hostname_permissions_and_immutable_edit_scope():
    wizard = (ROOT / 'app' / 'web' / 'features' / 'blueprint-wizard.js').read_text()
    hostname = (ROOT / 'app' / 'web' / 'features' / 'blueprint-wizard-hostname.js').read_text()
    scope = (ROOT / 'app' / 'web' / 'features' / 'blueprint-wizard-scope.js').read_text()

    assert "canCreate: blueprintScope.allows('hostnames.create')" in wizard
    assert "canCreate = false" in hostname
    assert "allowed('hostnames.create')" not in hostname
    assert "tenantSelect.disabled = tenants.length === 1 || Boolean(options.item)" in scope
    assert "projectSelect.disabled = projects.length === 1 || Boolean(options.item)" in scope
