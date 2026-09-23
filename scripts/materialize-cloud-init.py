"""One-shot, hash-guarded source edit for PR 155; removed before merge."""
from pathlib import Path
import hashlib
import json

EXPECTED = {
    'app/executors/terraform.py': 'bc90d5dc4d6b923702d9e8b9899314e7a5f4a37e',
    'app/web/features/blueprint-wizard-core.js': '9b969f5e4779d92b6e7800f33085388aea5ee52c',
    'app/web/features/blueprint-wizard.js': '06d6651d39f1d5d38c7f73151ede1013986ddc8a',
    'terraform/templates/proxmox-vm/main.tf': '386b251df3b9d428cca7c0ce62d30751382ebf8a',
    'terraform/templates/proxmox-vm/variables.tf': '4e44a79ed3318bd5f10d9d97f21eee9a7b5cfecd',
    'terraform/templates/proxmox-vm/template.json': '02c2184e0143b5267c3fe9a1c4d70914fafce147',
    'requirements.txt': '59ea78c24890af50396afd6a89761a2592c9fed8',
    'Dockerfile': '3745eac66fc598c82e939c6acd248a35549fc848',
}
for name, expected in EXPECTED.items():
    raw = Path(name).read_bytes()
    actual = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
    if actual != expected:
        raise SystemExit('Refusing to overwrite changed source: ' + name)


def replace(source, old, new, count=1):
    if source.count(old) != count:
        raise SystemExit('Unexpected source anchor: ' + old[:100])
    return source.replace(old, new)


p = Path('app/executors/terraform.py')
s = p.read_text()
s = replace(s, 'from app.executors.base import Executor, ExecutionFailed, execution_environment, run_process\n',
            'from app.executors.base import Executor, ExecutionFailed, execution_environment, run_process\nfrom app.executors.cloud_init import blueprint_snapshot, native_cloud_init_requested, prepare_native_seed\n')
s = replace(s, 'def guest_credential_runtime_variables(deployment):', 'def guest_credential_runtime_variables(deployment, *, blueprint=None):')
s = replace(s, "    blueprint = ((deployment.workflow or {}).get('blueprint') or {})\n", "    if blueprint is None:\n        blueprint = ((deployment.workflow or {}).get('blueprint') or {})\n")
s = replace(s, "        if provider_type == 'proxmox':\n", "        native_seed = operation in {'terraform.plan', 'terraform.apply'} and native_cloud_init_requested(context)\n        if provider_type == 'proxmox':\n")
s = replace(s, "            if bootstrap_required and operation in {'terraform.plan', 'terraform.apply'}:", "            if bootstrap_required and not native_seed and operation in {'terraform.plan', 'terraform.apply'}:")
s = replace(s, "        if operation in {'terraform.plan', 'terraform.apply'} and not (\n", "        if operation in {'terraform.plan', 'terraform.apply'} and not native_seed and not (\n")
anchor = '                restore_state(deployment.id, workspace)\n'
s = replace(s, anchor, anchor + '''                if native_seed:
                    if qemu_bootstrap_paths(workspace)[0].exists():
                        raise ExecutionFailed(
                            'Legacy guest bootstrap is still pending; preserve its workspace and '
                            'finish recovery before starting a new Cloud-init deployment'
                        )
                    context.stage('cloud-init.preparing')
                    guest_variables, guest_password = guest_credential_runtime_variables(
                        deployment, blueprint=blueprint_snapshot(context),
                    )
                    runtime_variables.update(prepare_native_seed(
                        context, workspace, guest_variables, guest_password,
                    ))
                    # Native media contains a password hash; never place the
                    # cleartext password in Terraform env, tfvars, or state.
                    guest_password = None
''')
p.write_text(s)

p = Path('app/web/features/blueprint-wizard-core.js')
s = p.read_text()
s = replace(s, "    'terraform_plan', 'terraform_apply', 'wait_for_vm', 'wait_for_agent',", "    'cloud_init', 'terraform_plan', 'terraform_apply', 'wait_for_vm', 'wait_for_agent',")
s = replace(s, "    add('apply', 'terraform_apply');", "    if (options.cloudInit) add('cloud_init', 'cloud_init');\n    add('apply', 'terraform_apply');")
s = replace(s, "      installQemuGuestAgent: true,", "      cloudInitEnabled: true,\n      installQemuGuestAgent: true,")
s = replace(s, "      waitAgent: state.providerType === 'proxmox' && state.waitAgent,", "      cloudInit: state.providerType === 'proxmox' && state.cloudInitEnabled !== false,\n      waitAgent: state.providerType === 'proxmox' && state.waitAgent,")
s = replace(s, "        cloud_init_snippet_storage: state.installQemuGuestAgent && !state.guestCredentialId ? state.cloudInitSnippetStorage : null,", "        cloud_init_snippet_storage: !parts.cloudInit?.enabled(state) && state.installQemuGuestAgent && !state.guestCredentialId ? state.cloudInitSnippetStorage : null,")
p.write_text(s)

p = Path('app/web/features/blueprint-wizard.js')
s = p.read_text()
s = replace(s, '!parts.core || !parts.hostname || !parts.network || !parts.scope || !parts.ui', '!parts.core || !parts.hostname || !parts.network || !parts.scope || !parts.ui || !parts.cloudInit')
s = replace(s, "          install_qemu_guest_agent: 'installQemuGuestAgent',", "          cloud_init_enabled: 'cloudInitEnabled',\n          install_qemu_guest_agent: 'installQemuGuestAgent',")
s = replace(s, "        } else if (state.step === 6) {\n", "        } else if (state.step === 6) {\n          const cloudCredential = root.querySelector('[name=\"cloud_init_guest_credential_id\"]');\n          if (cloudCredential) state.guestCredentialId = cloudCredential.value;\n")
s = replace(s, "        return errors;\n      }\n\n      function validateStep(index)", "        Object.assign(errors, parts.cloudInit.validate(state));\n        return errors;\n      }\n\n      function validateStep(index)")
s = replace(s, "          waitAgent: state.providerType === 'proxmox' && state.waitAgent,", "          cloudInit: state.providerType === 'proxmox' && state.cloudInitEnabled !== false,\n          waitAgent: state.providerType === 'proxmox' && state.waitAgent,")
s = replace(s, "const proxmoxOnly = new Set(['wait_for_vm'", "const proxmoxOnly = new Set(['cloud_init', 'wait_for_vm'")
s = replace(s, 'isProxmox && state.installQemuGuestAgent && (state.guestCredentialId || !snippetAvailable || !sshReady)', '!parts.cloudInit.enabled(state) && isProxmox && state.installQemuGuestAgent && (state.guestCredentialId || !snippetAvailable || !sshReady)')
s = replace(s, '          isProxmox ? install : null,', '          parts.cloudInit.render({ state, data, rerender: render, capture: captureCurrentStep }),\n          isProxmox ? install : null,')
s = replace(s, "installControl.addEventListener('change', event => { state.installQemuGuestAgent = event.currentTarget.checked; render(); });", "installControl.addEventListener('change', event => { const checked = event.currentTarget.checked; captureCurrentStep(); state.installQemuGuestAgent = checked; render(); });")
s = replace(s, "? 'Automatyczna instalacja przez cloud-init: włączona. Snippet storage: ' + (state.cloudInitSnippetStorage || 'brak')", "? (parts.cloudInit.enabled(state) ? 'Automatyczna instalacja z NoCloud ISO przez API Proxmoxa' : 'Starszy tryb instalacji agenta; snippet storage: ' + (state.cloudInitSnippetStorage || 'brak'))")
s = replace(s, "            ['Credential VM', state.guestCredentialId", "            ['Cloud-init', parts.cloudInit.enabled(state) ? 'NoCloud ISO przez API; konfiguracja przy pierwszym starcie' : 'Starszy tryb'],\n            ['Credential VM', state.guestCredentialId")
p.write_text(s)

p = Path('terraform/templates/proxmox-vm/main.tf')
s = p.read_text()
s = replace(s, 'var.install_qemu_guest_agent && !var.qemu_guest_agent_bootstrap', 'var.cloud_init_seed_path == null && var.install_qemu_guest_agent && !var.qemu_guest_agent_bootstrap', count=2)
s = replace(s, 'resource "proxmox_virtual_environment_file" "qemu_guest_agent_cloud_init" {', '''# ISO uploads use the Proxmox HTTP API, unlike snippets (which need host SSH).
resource "proxmox_virtual_environment_file" "cloud_init_seed" {
  count        = var.cloud_init_seed_path != null ? 1 : 0
  content_type = "iso"
  datastore_id = var.cloud_init_seed_storage
  node_name    = var.node
  overwrite    = false
  source_file {
    path      = var.cloud_init_seed_path
    checksum  = var.cloud_init_seed_checksum
    file_name = basename(var.cloud_init_seed_path)
  }
}

resource "proxmox_virtual_environment_file" "qemu_guest_agent_cloud_init" {''')
s = replace(s, '    vlan_id = var.vlan_id\n', '    vlan_id = var.vlan_id\n    mac_address = var.cloud_init_seed_mac\n')
start = s.index('  initialization {\n')
end = s.index('\n  }\n}', start) + len('\n  }')
body = s[start + len('  initialization {\n'):end - len('\n  }')]
s = s[:start] + '''  dynamic "cdrom" {
    for_each = var.cloud_init_seed_path != null ? [1] : []
    content {
      file_id   = proxmox_virtual_environment_file.cloud_init_seed[0].id
      interface = var.cloud_init_seed_interface
    }
  }
  # Never attach two competing CIDATA sources. Native ISO replaces the cloned
  # cloud-init CD-ROM; the generated Proxmox initialization drive is legacy-only.
  dynamic "initialization" {
    for_each = var.cloud_init_seed_path == null ? [1] : []
    content {
''' + '\n'.join('  ' + line for line in body.splitlines()) + '\n    }\n  }' + s[end:]
p.write_text(s)
p = Path('terraform/templates/proxmox-vm/variables.tf')
s = p.read_text() + '\n# Worker-only NoCloud media references; not accepted as public VM inputs.\n'
for name in ['path', 'checksum', 'storage', 'interface', 'mac']:
    s += 'variable "cloud_init_seed_' + name + '" {\n  type    = string\n  default = null\n}\n'
p.write_text(s)
p = Path('terraform/templates/proxmox-vm/template.json')
data = json.loads(p.read_text())
data['version'] = 6
p.write_text(json.dumps(data, indent=2) + '\n')
p = Path('requirements.txt')
p.write_text(p.read_text() + 'pycdlib>=1.14,<2\n')
p = Path('Dockerfile')
p.write_text(replace(p.read_text(), 'ca-certificates openssh-client sshpass', 'ca-certificates openssl openssh-client sshpass'))
print('Applied guarded edits to eight files; no server, VM, credential or database changes.')
