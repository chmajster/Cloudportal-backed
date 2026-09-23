from pathlib import Path


def replace(path, old, new, expected=1):
    target = Path(path)
    text = target.read_text(encoding='utf-8')
    if text.count(old) != expected:
        raise RuntimeError(f'Unexpected integration anchor in {path}: {old[:80]!r}')
    target.write_text(text.replace(old, new), encoding='utf-8')


# Preserve ISO provisioning and strengthen the retained recovery guards.
replace('app/executors/terraform.py',
        'if qemu_bootstrap_paths(workspace)[0].exists():',
        'if any(path.exists() for path in qemu_bootstrap_paths(workspace)):')
replace('app/executors/cloud_init.py',
        "if len(seeds) != 1 or seeds[0].get('conditions') or seeds[0].get('rollback'):",
        "if len(seeds) != 1 or seeds[0].get('conditions') or seeds[0].get('retry') or seeds[0].get('rollback'):")
replace('app/web/features/blueprint-wizard-cloud-init.js',
        'if (seeds.length !== 1 || Object.keys(seeds[0].conditions || {}).length || seeds[0].rollback) {',
        'if (seeds.length !== 1 || Object.keys(seeds[0].conditions || {}).length || seeds[0].retry || seeds[0].rollback) {')
replace('app/web/features/blueprint-wizard.js',
        "waitControl.addEventListener('change', event => {\n          state.waitAgent = event.currentTarget.checked;",
        "waitControl.addEventListener('change', event => {\n          const checked = event.currentTarget.checked;\n          captureCurrentStep();\n          state.waitAgent = checked;")

# This former assertion deliberately forbade the requested declaration.
replace('tests/test_web_console.py',
        '    assert "add(\'cloud_init\', \'cloud_init\')" not in script',
        '''    assert "if (options.cloudInit) add('cloud_init', 'cloud_init')" in script
    assert "registerExtension('blueprint-wizard-cloud-init'" in script
    assert 'Użytkownik, hasło lub klucz z Dostępów' in script
    assert 'NoCloud ISO (CIDATA) przez API Proxmoxa' in script
    assert 'Podgląd Cloud-init bez sekretów' in script
    assert '!parts.cloudInit.enabled(state) && isProxmox' in script''')

# Exercise the loaded wizard modules, rather than a core-only bundle.
replace('tests/test_blueprint_wizard_contract.py',
        'const core = window.BlueprintWizardParts.core;',
        "eval(require('fs').readFileSync({json.dumps(str(CORE.with_name('blueprint-wizard-cloud-init.js')))}, 'utf8'));\nconst core = window.BlueprintWizardParts.core;", expected=2)
replace('tests/test_blueprint_wizard_contract.py',
        "assert deployment['variables']['cloud_init_snippet_storage'] == 'local'",
        "assert deployment['variables']['cloud_init_snippet_storage'] is None")
replace('tests/test_blueprint_wizard_contract.py',
        "assert result['deployment']['variables']['cloud_init_snippet_storage'] == 'local'",
        "assert result['deployment']['variables']['cloud_init_snippet_storage'] is None")

# Retain #156's behavioral cases against the final ISO API transport.
p = 'tests/test_cloud_init_workflow.py'
replace(p, "assert 'systemctl start qemu-guest-agent' in result['enabled']",
        "assert ['systemctl', 'start', 'qemu-guest-agent'] in yaml.safe_load(result['enabled'])['runcmd']")
replace(p, 'parts.cloudInit.addStep(state); parts.cloudInit.addStep(state);',
        'parts.cloudInit.toggleStep(state, true); parts.cloudInit.toggleStep(state, true);')
replace(p, 'def test_ui_reports_missing_snippets_before_submission():',
        'def test_ui_does_not_require_snippets_for_native_iso():')
replace(p, "assert 'cloud_init_snippet_storage' in result['enabled']", "assert result['enabled'] == {}")
replace(p, '''    document = textwrap.dedent(source.split('data = <<-EOF\\n', 1)[1].split('\\n    EOF', 1)[0])
    config = yaml.safe_load(document)
    assert config['packages'] == ['qemu-guest-agent']
    command = config['runcmd'][0]
    assert isinstance(command, str)
    assert 'systemctl enable qemu-guest-agent || true' in command
    assert 'systemctl start qemu-guest-agent\\n' in command
    assert 'systemctl is-active --quiet qemu-guest-agent' in command
    assert 'wait_for_ip {\\n      disabled = true' in source
    assert 'enabled = true' in source
    assert 'private_key' not in document
    assert 'password' not in document''',
        '''    from app.executors.cloud_init import render_seed
    files = render_seed({'name': 'guest', 'install_qemu_guest_agent': True},
                        instance_id='test', mac_address='02:00:11:22:33:44')
    config = yaml.safe_load(files['user-data'])
    assert config['packages'] == ['qemu-guest-agent']
    command = config['runcmd'][0][2]
    assert 'systemctl enable qemu-guest-agent' in command
    assert 'systemctl start qemu-guest-agent\\n' in command
    assert 'systemctl is-active --quiet qemu-guest-agent' in command
    assert 'wait_for_ip {\\n      disabled = true' in source
    assert 'enabled = true' in source
    assert 'content_type = "iso"' in source
    assert 'proxmox_virtual_environment_file.cloud_init_seed[0].id' in source
    assert 'private_key' not in files['user-data']''')
replace(p, "'cloud_init_snippet_storage': 'local', 'ipv4_address': None}",
        "'cloud_init_snippet_storage': None, 'ipv4_address': None,\n                 'storage': 'local-lvm', 'template_id': 9001}")
replace(p, "deployment = SimpleNamespace(id='deployment-id', workspace='workspace', template='proxmox-vm',",
        "deployment = SimpleNamespace(id='deployment-id', workspace='workspace', provider='proxmox', template='proxmox-vm',")
replace(p, 'deployment=deployment, credential=credential, log=logs.append, stage=logs.append)',
        'deployment=deployment, credential=credential, check=lambda: None, log=logs.append, stage=logs.append)')
replace(p, '''    class Provider:
        ready = True
        def __init__(self, value):
            assert value is credential
        def discover(self, resource, node=None):
            assert (resource, node) == ('storages', 'pve01')
            return [{'storage': 'local', 'content': 'snippets', 'active': 1}]
        def ssh_preflight(self, env):
            assert 'guest-password-secret' not in env.values()
            return {'ok': self.ready, 'reason': None if self.ready else 'ssh_auth_missing'}''',
        '''    class Provider:
        available = True
        offline = False
        def __init__(self, value):
            assert value is credential
        def discover(self, resource, node=None):
            assert (resource, node) == ('storages', 'pve01')
            if self.offline:
                raise OSError('Provider unavailable')
            return [{'storage': 'local', 'content': 'iso', 'active': 1}] if self.available else []
        def vm_config(self, node, vm_id):
            assert (node, vm_id) == ('pve01', 9001)
            return {'ostype': 'l26', 'ide2': 'local-lvm:vm-9001-cloudinit,media=cdrom'}
        def ssh_preflight(self, env):
            pytest.fail('Native Cloud-init must not require node SSH')
        def guest_addresses(self, *args):
            pytest.fail('Native Cloud-init must not require a known guest DHCP address')''')
replace(p, "        assert variables['ssh_username'] == 'finaluser'",
        "        assert variables['cloud_init_seed_path'].endswith('.iso')")
replace(p, "        assert env['TF_VAR_ssh_password'] == 'guest-password-secret'\n        assert 'guest-password-secret' in sensitive",
        "        assert 'TF_VAR_ssh_password' not in env\n        assert 'guest-password-secret' not in json.dumps(env)\n        assert seed_config(variables['cloud_init_seed_path'])['users'][0]['name'] == 'finaluser'")
replace(p, "@pytest.mark.parametrize('failure', ['missing_storage', 'node_ssh', 'legacy_marker'])",
        "@pytest.mark.parametrize('failure', ['missing_storage', 'provider_offline', 'legacy_marker', 'legacy_key'])")
replace(p, '''    if failure == 'missing_storage':
        context.deployment.variables['cloud_init_snippet_storage'] = None
    elif failure == 'node_ssh':
        provider.ready = False''',
        '''    if failure == 'missing_storage':
        provider.available = False
    elif failure == 'provider_offline':
        provider.offline = True''')
replace(p, "        (workspace / module.QEMU_BOOTSTRAP_MARKER).write_text('{}')",
        "        name = module.QEMU_BOOTSTRAP_KEY if failure == 'legacy_key' else module.QEMU_BOOTSTRAP_MARKER\n        (workspace / name).write_text('existing-recovery-data')")
replace(p, "    provider.ready = False\n    module.TerraformExecutor().execute('terraform.apply', context)\n    assert calls[-1][1]['ssh_username'] == 'finaluser'",
        "    module.TerraformExecutor().execute('terraform.apply', context)\n    config = seed_config(calls[-1][1]['cloud_init_seed_path'])\n    assert config['users'][0]['name'] == 'finaluser'\n    assert 'packages' not in config")
replace(p, "    (workspace / 'execution.tfplan').write_bytes(b'approved-plan-fixture')\n    context.apply_saved_terraform_plan = True",
        "    module.prepare_native_seed(context, workspace, {'ssh_username': 'finaluser', 'ssh_public_key': None}, 'guest-password-secret')\n    (workspace / 'execution.tfplan').write_bytes(b'approved-plan-fixture')\n    context.apply_saved_terraform_plan = True")
replace(p, 'def test_dhcp_native_credentials_never_create_bootstrap_account(executor_case):',
        '''def seed_config(path):
    import io
    import pycdlib
    iso = pycdlib.PyCdlib()
    iso.open(str(path))
    try:
        output = io.BytesIO()
        iso.get_file_from_iso_fp(output, rr_path='/user-data')
        return yaml.safe_load(output.getvalue())
    finally:
        iso.close()


def test_dhcp_native_credentials_never_create_bootstrap_account(executor_case):''')
replace('tests/test_native_cloud_init.py',
        "{'conditions': {'provider': 'proxmox'}}, {'rollback': 'rollback'},",
        "{'conditions': {'provider': 'proxmox'}}, {'rollback': 'rollback'}, {'retry': 1},")

Path('docs/architecture/workflow-cloud-init.md').write_text('''# First-boot Cloud-init in Proxmox workflows

## Configure a Blueprint

Open **Blueprinty > Nowy Blueprint > Workflow**. Use **Cloud-init — użytkownik
i przygotowanie systemu** and **Utwórz Cloud-init przy pierwszym starcie VM**.
Select **Użytkownik, hasło lub klucz z Dostępów** to create the final guest
account from a saved SSH credential. The Blueprint stores its ID only.
Manual username/public key configuration remains available in VM settings.

Enable **Instaluj QEMU Guest Agent automatycznie** to include the package and
service enable/start/readiness commands in user-data. The separate **Czekaj na
QEMU Guest Agent po Terraform apply** option controls the post-apply check.

```text
cloud_init -> terraform_apply -> wait_for_agent -> wait_for_ip -> optional Ansible
```

Cloud-init is prepared before every Terraform plan/apply. In advanced workflows,
the Cloud-init toggle adds the predecessor without discarding plan/approval
dependencies. Conditions, retry and rollback are invalid for this declaration.
Existing native Blueprints open in the full editor even through the classic
quick-edit entry point, preserving their complete dependency graph.

## Transport and credentials

The integrated implementation uses #155's locally generated **NoCloud ISO**,
not #156's earlier vendor-data/snippets transport. The worker writes user-data,
meta-data and network-config to a CIDATA ISO. Terraform uploads it through the
Proxmox HTTP API and attaches it before first boot. Neither node SSH, snippets,
a known DHCP address nor a guest SSH session is required by this path.

The final account is configured directly; no temporary cpbootstrap account is
created. Guest passwords become salted SHA-512-crypt hashes before entering the
seed. Cleartext passwords and guest private keys do not enter Terraform tfvars,
the Terraform process environment or browser previews. A private credential key
is used only to derive the public key installed in the guest. The ISO contains
a password hash and must remain access-controlled. Local ISO/manifest files
use mode 0600; state, saved plans and backups remain sensitive artifacts.

QEMU Guest Agent stays enabled on the VM, but Terraform's own IP polling is
disabled. Worker readiness checks remain responsible for confirming QGA and
obtaining the guest IP. Static/indirect systemd units are not incorrectly
enabled; start and active checks still run. OpenRC is also supported.

## Prerequisites and recovery

Use a clean Linux Cloud-init/NoCloud template and active ISO-capable storage on
the target Proxmox node. The API credential must be able to inspect the template,
read storage and upload ISO media. The worker selects suitable ISO storage;
it does not require the VM disk datastore to support ISO content.
Networking, DNS and package repositories inside the guest must work for package
installation. This mode does not implement Windows/Cloudbase-init.

A persistent workspace at the same path across workers is required for saved
plans. Approved applies reuse the exact original media, checksum and configuration
fingerprint; missing or changed seed files fail closed rather than regenerate a
plan or silently change a credential. Pending legacy bootstrap markers AND keys
are retained and prevent unsafe switching to first-boot provisioning.

Updating a Blueprint does not retrofit an already booted guest or rewrite an
immutable existing job. Legacy workflows without an explicit cloud_init step
retain their old behavior. Legacy snippet provisioning may still require node
SSH; this is not a prerequisite for the new ISO path. Do not delete existing VMs,
remove state/locks or force job success to recover a failed deployment.

## Validation

The combined tests retain API dependency validation and editor-preservation
coverage from #156, plus ISO round-trip, password isolation, primary-NIC
configuration, service startup and saved-plan protection from #155. Regression
assertions require the explicit Cloud-init step and the final API-only transport.
Full Backend CI must pass before merge. These automated tests use mocked Proxmox;
no live guest deployment or production rollout is implied by a successful CI run.
''', encoding='utf-8')
