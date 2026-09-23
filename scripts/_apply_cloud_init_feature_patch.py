"""Temporary branch-only authoring helper; remove before merging the PR."""
from pathlib import Path
import hashlib


EXPECTED = {
    'app/executors/terraform.py': 'bc90d5dc4d6b923702d9e8b9899314e7a5f4a37e',
    'app/web/features/blueprint-wizard-core.js': '9b969f5e4779d92b6e7800f33085388aea5ee52c',
    'app/web/features/blueprint-wizard.js': '06d6651d39f1d5d38c7f73151ede1013986ddc8a',
    'app/api/schemas.py': '258ca4ede3b1e18ebdbcbbf6623c2aa18b920b77',
    'app/api/automation.py': 'ac7faf12aa7f41c37d5d1bda9ec9251d0d7bbbcf',
    'terraform/templates/proxmox-vm/main.tf': '36cbb860e6f55fa81838f1f590fba30175575606',
    'tests/test_blueprint_wizard_contract.py': '317affffdb4c1449ed082d39705d8622f5285ea2',
}
contents = {}
for name, expected in EXPECTED.items():
    raw = Path(name).read_bytes()
    actual = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
    if actual != expected:
        raise SystemExit('Refusing to overwrite changed source: ' + name)
    contents[name] = raw.decode('utf-8')


def replace(name, before, after, count=1):
    source = contents[name]
    if source.count(before) != count:
        raise SystemExit('Patch anchor mismatch in ' + name + ': ' + before[:100])
    contents[name] = source.replace(before, after, count)


executor = 'app/executors/terraform.py'
replace(executor, 'from app.catalog import ',
    'from app.automation.cloud_init import (blueprint_snapshot, node_ssh_environment,\n'
    '                                       validate_cloud_init_workflow, validate_cloud_init_workspace,\n'
    '                                       validate_snippet_storage)\nfrom app.catalog import ')
replace(executor, 'def guest_credential_runtime_variables(deployment):',
    'def guest_credential_runtime_variables(deployment, *, blueprint=None):')
replace(executor, "    blueprint = ((deployment.workflow or {}).get('blueprint') or {})\n    credential_id = blueprint.get('guest_credential_id')",
    "    if blueprint is None:\n        blueprint = ((deployment.workflow or {}).get('blueprint') or {})\n    credential_id = blueprint.get('guest_credential_id')")
replace(executor, 'class TerraformExecutor(Executor):', '''def prepare_first_boot_cloud_init(context, workspace, credential, env, secret):
    """Check first-boot dependencies before submitting any mutating Terraform work."""
    try:
        validate_cloud_init_workspace(workspace)
        variables = context.deployment.variables or {}
        if variables.get('install_qemu_guest_agent'):
            storage = variables.get('cloud_init_snippet_storage')
            if not storage:
                validate_snippet_storage([], storage)
            node_ssh_environment(env, os.environ, secret, credential.username)
            from app.providers.proxmox import ProxmoxProvider
            provider = ProxmoxProvider(credential)
            try:
                storages = provider.discover('storages', node=variables.get('node'))
            except Exception:
                raise ExecutionFailed(
                    'Cloud-init preflight could not read snippets storage on the target Proxmox node; '
                    'check provider connectivity and storage permissions'
                ) from None
            validate_snippet_storage(storages or [], storage)
            readiness = provider.ssh_preflight(env)
            if not readiness.get('ok'):
                raise ExecutionFailed(
                    'Cloud-init QEMU Guest Agent installation requires Proxmox node SSH: '
                    + str(readiness.get('reason') or 'ssh_not_ready')
                    + '. Configure PROXMOX_VE_SSH_* for the worker; guest SSH bootstrap fallback is disabled'
                )
    except ValueError as exc:
        raise ExecutionFailed(str(exc)) from None
    context.log(
        'cloud-init.provisioning-mode: native; target credential configured at first boot; '
        + ('QEMU Guest Agent installation via vendor-data' if variables.get('install_qemu_guest_agent')
           else 'QEMU Guest Agent installation disabled')
    )


class TerraformExecutor(Executor):''')
replace(executor, '        deployment, credential = context.deployment, context.credential\n',
    '        deployment, credential = context.deployment, context.credential\n        native_cloud_init = False\n')
replace(executor, '''            job_blueprint = (context.job.payload or {}).get('blueprint') or {}
            guest_credential_id = job_blueprint.get('guest_credential_id')
            bootstrap_required = bool(qemu_install or guest_credential_id)
            if bootstrap_required and operation in {'terraform.plan', 'terraform.apply'}:
''', '''            job_blueprint = blueprint_snapshot(context)
            guest_credential_id = job_blueprint.get('guest_credential_id')
            bootstrap_required = bool(qemu_install or guest_credential_id)
            cloud_steps = job_blueprint.get('steps') or []
            if any(step.get('type') == 'cloud_init' for step in cloud_steps):
                # Snippet destroy/refresh also needs the node's SSH environment.
                node_ssh_environment(env, os.environ, secret, credential.username)
            if operation in {'terraform.plan', 'terraform.apply'}:
                try:
                    native_cloud_init = validate_cloud_init_workflow(cloud_steps)
                except ValueError as exc:
                    raise ExecutionFailed(str(exc)) from None
            if native_cloud_init:
                prepare_first_boot_cloud_init(context, workspace, credential, env, secret)
            elif bootstrap_required and operation in {'terraform.plan', 'terraform.apply'}:
''')
replace(executor, '            guest_variables, guest_password = guest_credential_runtime_variables(deployment)\n',
    '            if native_cloud_init:\n'
    '                guest_variables, guest_password = guest_credential_runtime_variables(\n'
    '                    deployment, blueprint=job_blueprint,\n'
    '                )\n'
    '            else:\n'
    '                guest_variables, guest_password = guest_credential_runtime_variables(deployment)\n')

core = 'app/web/features/blueprint-wizard-core.js'
replace(core, "  const WORKFLOW_TYPES = [\n    'terraform_plan'", "  const WORKFLOW_TYPES = [\n    'cloud_init', 'terraform_plan'")
replace(core, "    add('apply', 'terraform_apply');", "    if (options.cloudInit) add('cloud_init', 'cloud_init');\n    add('apply', 'terraform_apply');")
replace(core, 'state.installQemuGuestAgent && !state.guestCredentialId ? state.cloudInitSnippetStorage : null',
    'state.installQemuGuestAgent ? state.cloudInitSnippetStorage : null')
replace(core, '    const autoWorkflow = workflow({\n      hostname:',
    "    const autoWorkflow = workflow({\n      cloudInit: state.providerType === 'proxmox',\n      hostname:")

wizard = 'app/web/features/blueprint-wizard.js'
replace(wizard, '!parts.scope || !parts.ui)', '!parts.scope || !parts.ui || !parts.cloudInit)')
replace(wizard, '        return parts.core.workflow({\n          hostname:',
    "        return parts.core.workflow({\n          cloudInit: state.providerType === 'proxmox',\n          hostname:")
replace(wizard, "const proxmoxOnly = new Set(['wait_for_vm',", "const proxmoxOnly = new Set(['cloud_init', 'wait_for_vm',")
replace(wizard, '          Object.assign(errors, validateWorkflow());',
    '          Object.assign(errors, validateWorkflow());\n          Object.assign(errors, parts.cloudInit.validate(state));')
replace(wizard, '''        const snippetAvailable = Boolean(state.cloudInitSnippetStorage);
        const sshReady = state.qemuAgentSshReady !== false;
''', '')
replace(wizard, "installControl.addEventListener('change', event => { state.installQemuGuestAgent = event.currentTarget.checked; render(); });",
    "installControl.addEventListener('change', event => { captureCurrentStep(); state.installQemuGuestAgent = event.currentTarget.checked; render(); });")
replace(wizard, "        waitControl.addEventListener('change', event => {\n          state.waitAgent", "        waitControl.addEventListener('change', event => {\n          captureCurrentStep();\n          state.waitAgent")
replace(wizard, 'Hostname, IPAM, cloud-init i tagi są przygotowywane przed runtime; workflow pokazuje tylko faktycznie wykonywane operacje.',
    'Cloud-init przygotowuje konto i konfigurację pierwszego startu przed Terraform. Instalacja pakietów następuje wewnątrz VM przy jej pierwszym uruchomieniu.')
start = contents[wizard].index('          isProxmox && state.installQemuGuestAgent && (state.guestCredentialId || !snippetAvailable || !sshReady)')
end = contents[wizard].index('          isProxmox ? install : null,', start)
contents[wizard] = contents[wizard][:start] + '          isProxmox ? parts.cloudInit.render({ state, data, capture: captureCurrentStep, rerender: render }) : null,\n' + contents[wizard][end:]

schema = 'app/api/schemas.py'
replace(schema, "        approval_steps = [step for step in self.workflow if step.type == 'approval']",
    "        from app.automation.cloud_init import validate_cloud_init_workflow\n"
    "        validate_cloud_init_workflow([step.model_dump() for step in self.workflow])\n\n"
    "        approval_steps = [step for step in self.workflow if step.type == 'approval']")
api = 'app/api/automation.py'
replace(api, "    proxmox_only_steps = {\n        'wait_for_vm'", "    proxmox_only_steps = {\n        'cloud_init', 'wait_for_vm'")

template = 'terraform/templates/proxmox-vm/main.tf'
replace(template, '  agent { enabled = true }', '''  agent {
    enabled = true
    # Cloud-init installs QGA at first boot; the workflow owns IP readiness.
    wait_for_ip {
      disabled = true
    }
  }''')
replace(template, '      - [ systemctl, enable, --now, qemu-guest-agent ]', '''      - |
        set -eu
        if command -v systemctl >/dev/null 2>&1; then
          systemctl enable qemu-guest-agent || true
          systemctl start qemu-guest-agent
          systemctl is-active --quiet qemu-guest-agent
        elif command -v rc-service >/dev/null 2>&1; then
          rc-update add qemu-guest-agent default
          rc-service qemu-guest-agent start
        else
          echo "Unsupported service manager for qemu-guest-agent" >&2
          exit 1
        fi''')

tests = 'tests/test_blueprint_wizard_contract.py'
for function in (
    'test_wizard_payload_preserves_hostname_ipam_ansible_and_provider_credentials',
    'test_wizard_can_wait_for_preinstalled_qemu_agent_without_installing_it',
    'test_wizard_can_install_qemu_agent_without_waiting_for_it',
):
    start = contents[tests].index('def ' + function + '(')
    end = contents[tests].find('\ndef ', start + 1)
    end = len(contents[tests]) if end < 0 else end
    section = contents[tests][start:end]
    if function == 'test_wizard_payload_preserves_hostname_ipam_ansible_and_provider_credentials':
        section = section.replace("assert deployment['variables']['cloud_init_snippet_storage'] is None", "assert deployment['variables']['cloud_init_snippet_storage'] == 'local'")
    if function == 'test_wizard_can_install_qemu_agent_without_waiting_for_it':
        section = section.replace("== ['terraform_apply']", "== ['cloud_init', 'terraform_apply']")
    else:
        section = section.replace("        'terraform_apply',\n", "        'cloud_init',\n        'terraform_apply',\n")
    contents[tests] = contents[tests][:start] + section + contents[tests][end:]

for name, content in contents.items():
    Path(name).write_text(content, encoding='utf-8')
    print('Updated ' + name)
