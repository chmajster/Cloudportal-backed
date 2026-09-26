import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
DESIGNER = ROOT / 'app' / 'web' / 'features' / 'blueprint-vra-designer.js'


def run_designer(expression: str):
    node = shutil.which('node')
    if not node:
        pytest.skip('node is required for Blueprint designer contract tests')
    script = f"""
global.window = {{}};
global.registerExtension = () => {{}};
eval(require('fs').readFileSync({json.dumps(str(DESIGNER))}, 'utf8'));
const validate = window.BlueprintVRADesigner.validateReferences;
{expression}
"""
    result = subprocess.run([node, '-e', script], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def test_designer_reference_validation_rejects_provider_credential_and_template_mismatch():
    result = run_designer("""
const data = {
  providers: [
    { id: 7, type: 'proxmox', credentials_id: 5 },
    { id: 8, type: 'aws', credentials_id: 6 },
  ],
  templates: [
    { id: 'proxmox-vm', provider: 'proxmox', enabled: true },
    { id: 'aws-ec2', provider: 'aws', enabled: true },
  ],
};
const base = {
  deployment: { provider_id: 7, credentials_id: 5, template: 'proxmox-vm' },
  workflow: [{ id: 'apply', type: 'terraform_apply' }],
};
const badCredential = JSON.parse(JSON.stringify(base));
badCredential.deployment.credentials_id = 99;
const badTemplate = JSON.parse(JSON.stringify(base));
badTemplate.deployment.template = 'aws-ec2';
console.log(JSON.stringify({
  credential: validate(badCredential, data, {}),
  template: validate(badTemplate, data, {}),
}));
""")

    assert any('credentialem wybranego providera' in error for error in result['credential'])
    assert any('Template nie jest zgodny' in error for error in result['template'])


def test_designer_reference_validation_matches_provider_runtime_constraints():
    result = run_designer("""
const data = {
  providers: [{ id: 8, type: 'aws', credentials_id: 6 }],
  templates: [{ id: 'aws-ec2', provider: 'aws', enabled: true }],
};
const blueprint = {
  deployment: { provider_id: 8, credentials_id: 6, template: 'aws-ec2', ansible: { playbook: 'bootstrap' } },
  workflow: [
    { id: 'apply', type: 'terraform_apply' },
    { id: 'ip', type: 'wait_for_ip' },
  ],
};
console.log(JSON.stringify(validate(blueprint, data, {})));
""")

    assert any('nie obsługuje kroków' in error for error in result)
    assert any('Ansible wymaga providera Proxmox' in error for error in result)


def test_designer_allows_existing_disabled_template_but_rejects_new_selection():
    result = run_designer("""
const data = {
  providers: [{ id: 7, type: 'proxmox', credentials_id: 5 }],
  templates: [{ id: 'legacy-proxmox', provider: 'proxmox', enabled: false }],
};
const blueprint = {
  deployment: { provider_id: 7, credentials_id: 5, template: 'legacy-proxmox' },
  workflow: [{ id: 'apply', type: 'terraform_apply' }],
};
console.log(JSON.stringify({
  existing: validate(blueprint, data, { blueprintId: 10, originalTemplate: 'legacy-proxmox' }),
  changed: validate(blueprint, data, { blueprintId: 10, originalTemplate: 'other-template' }),
}));
""")

    assert not any('wyłączonego template' in error for error in result['existing'])
    assert any('wyłączonego template' in error for error in result['changed'])


def test_designer_canvas_geometry_does_not_use_csp_blocked_style_attributes():
    source = DESIGNER.read_text()
    assert "style: \`width:" not in source
    assert "style: \`left:" not in source
    assert "viewport.style.width = extents.width + 'px'" in source
    assert "viewport.style.height = extents.height + 'px'" in source
    assert "viewport.style.transform = 'scale(' + state.zoom + ')'" in source
    assert "card.style.left = pos.x + 'px'" in source
    assert "card.style.top = pos.y + 'px'" in source
