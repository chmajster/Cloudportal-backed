import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.api.schemas import BlueprintInput


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_UI = ROOT / 'app' / 'web' / 'features' / 'blueprint-classic-workflow.js'


def run_workflow_ui(expression: str):
    node = shutil.which('node')
    if not node:
        pytest.skip('node is required for Blueprint workflow UI contract tests')
    script = f"""
global.window = {{}};
global.registerExtension = (_name, initialize) => initialize();
eval(require('fs').readFileSync({json.dumps(str(WORKFLOW_UI))}, 'utf8'));
const workflow = window.BlueprintClassicWorkflow;
{expression}
"""
    result = subprocess.run(
        [node, '-e', script],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def blueprint_payload(workflow):
    return {
        'slug': 'workflow-test',
        'name': 'Workflow Test',
        'deployment': {
            'name': 'vm-test',
            'provider_id': 1,
            'credentials_id': 1,
            'template': 'proxmox-vm',
            'variables': {},
        },
        'workflow': workflow,
    }


def test_classic_workflow_ui_reports_exact_missing_self_and_cycle_dependencies():
    result = run_workflow_ui("""
console.log(JSON.stringify({
  missing: workflow.dependencyErrors([
    {id:'apply', depends_on:['prepare']},
  ]),
  self: workflow.dependencyErrors([
    {id:'apply', depends_on:['apply']},
  ]),
  cycle: workflow.dependencyErrors([
    {id:'a', depends_on:['b']},
    {id:'b', depends_on:['a']},
  ]),
}));
""")

    assert result['missing'] == [
        'Krok „apply” zależy od nieistniejących kroków: prepare.'
    ]
    assert result['self'] == [
        'Krok „apply” nie może zależeć od samego siebie.'
    ]
    assert any('a → b → a' in message or 'b → a → b' in message for message in result['cycle'])


def test_classic_workflow_ui_normalizes_dependency_input():
    result = run_workflow_ui("""
console.log(JSON.stringify(
  workflow.splitReferences(' apply, prepare\\napply ,, finish ')
));
""")
    assert result == ['apply', 'prepare', 'finish']


def test_blueprint_schema_reports_exact_missing_dependency():
    with pytest.raises(ValueError, match=r'Workflow step "apply" depends on missing step.*prepare'):
        BlueprintInput.model_validate(blueprint_payload([
            {'id': 'apply', 'type': 'terraform_apply', 'depends_on': ['prepare']},
        ]))


def test_blueprint_schema_reports_exact_self_dependency():
    with pytest.raises(ValueError, match=r'Workflow step "apply" cannot depend on itself'):
        BlueprintInput.model_validate(blueprint_payload([
            {'id': 'apply', 'type': 'terraform_apply', 'depends_on': ['apply']},
        ]))
