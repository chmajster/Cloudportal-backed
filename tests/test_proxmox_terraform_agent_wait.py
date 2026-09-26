"""Static template contracts for post-apply guest-agent provisioning.

These checks do not replace Terraform validation or a live Proxmox deployment.
They intentionally need no database, provider credentials, or HCL dependencies.
"""

from pathlib import Path
import re
import unittest


TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / 'terraform' / 'templates' / 'proxmox-vm' / 'main.tf'
)


def block(source, declaration, indent=0):
    """Read a block from the repository's terraform-fmt formatted template."""
    prefix = ' ' * indent
    match = re.search(
        rf'^{prefix}{re.escape(declaration)}\s*\{{(?P<body>.*?)^{prefix}\}}',
        source,
        flags=re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise AssertionError(f'Missing template block: {declaration}')
    return match.group('body')


class ProxmoxTerraformAgentWaitTests(unittest.TestCase):
    def setUp(self):
        self.source = TEMPLATE.read_text(encoding='utf-8')
        self.vm = block(self.source, 'resource "proxmox_virtual_environment_vm" "vm"')

    def test_qemu_agent_channel_remains_enabled(self):
        self.assertRegex(self.vm, r'(?m)^  agent\s*\{\s*enabled\s*=\s*true\s*(?:\}|$)')

    def test_provider_ip_wait_is_unconditionally_disabled(self):
        # A conditional based on bootstrap/install flags would still block
        # refresh or deployments that intentionally do not install the agent.
        agent = block(self.vm, 'agent', indent=2)
        wait = block(agent, 'wait_for_ip', indent=4)
        self.assertRegex(wait, r'(?m)^\s*disabled\s*=\s*true\s*$')

    def test_legacy_qemu_snippet_refresh_is_null_safe_during_native_iso_migration(self):
        snippet = block(
            self.source,
            'resource "proxmox_virtual_environment_file" "qemu_guest_agent_cloud_init"',
        )
        self.assertIn('var.cloud_init_snippet_storage != null', snippet)
        self.assertIn(
            'coalesce(var.cloud_init_snippet_storage, var.cloud_init_seed_storage, var.storage)',
            snippet,
        )
        self.assertIn('var.cloud_init_snippet_storage != null', self.vm)

    def test_primary_ip_output_does_not_require_agent_network_data(self):
        output = block(self.source, 'output "primary_ip"')
        self.assertRegex(output, r'(?m)^\s*value\s*=\s*local\.configured_primary_ip\s*$')
        self.assertNotRegex(self.source, r'proxmox_virtual_environment_vm\.vm\.(?:ipv[46]_addresses|network_interface_names)')

    def test_vm_id_output_is_preserved_for_worker_inventory(self):
        self.assertRegex(
            self.source,
            r'output\s+"vm_id"\s*\{\s*value\s*=\s*proxmox_virtual_environment_vm\.vm\.vm_id\s*\}',
        )


if __name__ == '__main__':
    unittest.main()
