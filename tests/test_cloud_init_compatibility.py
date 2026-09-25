"""Compatibility checks for native Cloud-init across editors and API callers."""
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from app.automation.cloud_init import blueprint_snapshot, node_ssh_environment, validate_cloud_init_workflow


ROOT = Path(__file__).resolve().parents[1]


def test_non_blueprint_context_has_no_implicit_cloud_init():
    assert blueprint_snapshot(SimpleNamespace(job=SimpleNamespace(payload={}), deployment=SimpleNamespace())) == {}


@pytest.mark.parametrize('auth', [
    {'PROXMOX_VE_SSH_PRIVATE_KEY': '/node/key'},
    {'PROXMOX_VE_SSH_PASSWORD': 'explicit-node-password'},
    {'PROXMOX_VE_SSH_AGENT': 'true'},
    {'PROXMOX_VE_SSH_AUTH_SOCK': '/run/node-agent.sock'},
])
def test_explicit_node_ssh_is_not_overwritten_by_api_authentication(auth):
    source = {'PROXMOX_VE_SSH_USERNAME': 'node-operator', **auth}
    env = {}
    node_ssh_environment(env, source, {'password': 'api-password'}, 'root@pam')
    assert env == source


def test_node_password_fallback_retains_configured_username():
    env = {}
    node_ssh_environment(env, {'PROXMOX_VE_SSH_USERNAME': 'node-operator'}, {'password': 'node-password'}, 'root@pam')
    assert env['PROXMOX_VE_SSH_USERNAME'] == 'node-operator'
    assert env['PROXMOX_VE_SSH_PASSWORD'] == 'node-password'


def node_json(script):
    executable = shutil.which('node')
    if not executable:
        pytest.skip('node is required')
    result = subprocess.run([executable, '-e', script], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def test_legacy_blueprint_editors_are_replaced_by_step_by_step_wizard():
    source = (ROOT / 'app/web/features/blueprints.js').read_text()
    assert 'async function proxmoxBlueprintForm' not in source
    assert 'async function blueprintForm' not in source
    assert "registerCommand('blueprints.proxmoxTemplateWizard', item => item ? window.BlueprintWizard.open({ item }) : window.BlueprintWizard.open())" in source


def test_quick_workflow_builds_required_cloud_init_before_awx():
    helper = ROOT / 'app/web/features/blueprint-form-utils.js'
    script = 'global.window = {}; global.registerExtension = (_name, initialize) => initialize();\n'
    script += 'eval(require("fs").readFileSync(' + json.dumps(str(helper)) + ', "utf8"));\n'
    script += '''
const steps = window.BlueprintFormUtils.blueprintWorkflow({
  cloudInit: true,
  waitAgent: false,
  ansible: false,
  guestAccess: false,
  awx: true,
  awxRetry: 4,
  awxTimeout: 420,
});
console.log(JSON.stringify(steps));
'''
    result = node_json(script)
    assert [step['type'] for step in result] == [
        'cloud_init', 'terraform_apply', 'wait_for_ip', 'register_awx',
    ]
    assert result[-1]['retry'] == 4
    assert result[-1]['timeout'] == 420
    assert validate_cloud_init_workflow(result)


def test_wizard_cloud_init_action_is_available_only_for_proxmox():
    source = (ROOT / 'app/web/features/blueprint-wizard-cloud-init.js').read_text()
    assert "state.providerType === 'proxmox'" in source
    assert "Utwórz Cloud-init przy pierwszym starcie VM" in source
    guards = ROOT / 'app/web/features/blueprint-provisioning-guards.js'
    script = 'global.window = {}; global.registerExtension = (_name, initialize) => initialize();\n'
    script += 'eval(require("fs").readFileSync(' + json.dumps(str(guards)) + ', "utf8"));\n'
    script += '''
const choices = [['cloud_init', 'Cloud-init'], ['terraform_apply', 'Apply']];
const fn = window.BlueprintProvisioningGuards.workflowChoicesForProvider;
console.log(JSON.stringify({proxmox:fn(choices,'proxmox'), aws:fn(choices,'aws')}));
'''
    result = node_json(script)
    assert result['proxmox'][0][0] == 'cloud_init'
    assert result['aws'] == [['terraform_apply', 'Apply']]


def test_api_schema_rejects_cloud_init_parallel_to_apply():
    from app.api.schemas import BlueprintInput
    payload = {
        'slug': 'native-cloud-init', 'name': 'Native Cloud-init',
        'deployment': {'name': 'guest', 'provider_id': 1, 'credentials_id': 2, 'variables': {}},
        'workflow': [
            {'id': 'cloud', 'type': 'cloud_init'},
            {'id': 'apply', 'type': 'terraform_apply', 'depends_on': ['cloud']},
        ],
    }
    assert BlueprintInput.model_validate(payload).workflow[0].type == 'cloud_init'
    payload['workflow'][1]['depends_on'] = []
    with pytest.raises(ValueError, match='Cloud-init must be an ancestor'):
        BlueprintInput.model_validate(payload)
