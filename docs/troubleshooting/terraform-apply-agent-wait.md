# Terraform apply waits although the Proxmox VM already exists

## Symptom and cause

A job remains at `Terraform: zastosuj` and Terraform emits `Still creating...`
after the VM appears in Proxmox. This message alone does not prove that all
provider operations have finished: inspect the Proxmox task and guest status.

In the bundled Proxmox VM template, `agent { enabled = true }` previously left
provider IP polling enabled. bpg/proxmox 0.111.1 defaults the agent data timeout
to 15 minutes. The worker calls `ensure_qemu_guest_bootstrap()` only after
`executor.execute('terraform.apply', context)` returns. A guest agent that is
meant to be installed by that bootstrap can therefore block Terraform before
its own installation starts.

## Template fix

Keep the guest-agent channel enabled, but leave guest IP discovery and readiness
to the worker:

```hcl
agent {
  enabled = true
  wait_for_ip {
    disabled = true
  }
}
```

This is supported by the pinned `~> 0.111.0` provider. It skips the provider's
agent IP lookup during apply and refresh; it does not disable QGA on the VM,
install QGA, skip actual Terraform completion, or mark a job successful.

The provider's agent-derived `ipv4_addresses`, `ipv6_addresses`, and
`network_interface_names` are empty with this option. The bundled template does
not use them for outputs: `vm_id` remains available, and `primary_ip` comes from
the configured static address. DHCP discovery remains the worker's concern.

Provider reference: `bpg/terraform-provider-proxmox`, tag `v0.111.1`,
`docs/resources/virtual_environment_vm.md`, `agent.wait_for_ip.disabled`.

## DHCP is a separate bootstrap prerequisite

With `guest-bootstrap`, the worker must reach the temporary account over SSH
before it can install the agent. Its current DHCP discovery uses QGA. If the
template has no functioning agent and no static/IPAM address was supplied,
skipping Terraform's IP wait cannot discover the guest address on its own.

Use a static/IPAM address reachable from the worker, or a cloud-init template
with QEMU Guest Agent already installed and running. The snippet-based install
path is another existing option where its Proxmox SSH/storage prerequisites are
met; selecting a target guest credential currently forces guest-bootstrap.
Do not work around this by treating a missing IP as success or by scanning an
unrelated network. The current bootstrap reports an actionable DHCP discovery
failure rather than completing guest configuration without connectivity.

## An already-running job

Updating the application/template does not rewrite a running Terraform process
or a previously saved plan. Inspect the current job and Proxmox tasks first.
When the guest OS is accessible, check whether `qemu-guest-agent` is installed
and active inside the VM. For an Ubuntu/Debian guest, installing and starting it
may allow an apply still waiting for agent data to complete:

```sh
sudo apt-get update
sudo apt-get install -y qemu-guest-agent
sudo systemctl start qemu-guest-agent
sudo systemctl status qemu-guest-agent --no-pager
```

These commands run **inside the guest VM**, not on the Proxmox host. They do not
change an already-failed job into a successful one and are not a guarantee that
other VM initialization errors are absent.

Do not delete the VM, remove state/locks, run a second apply, or force a completed
job status merely because the VM is visible. After an interrupted/failed run,
check the persisted Terraform state and resource mapping before retrying. Check
whether the plan proposes replacement or creation and review any configured
rollback/destroy-on-failure policy. Never bypass approval or silently substitute
a newly generated plan for an approved saved plan.

## Validation

`tests/test_proxmox_terraform_agent_wait.py` provides static regression contracts
for the enabled QGA channel, unconditional provider wait bypass, and preserved
outputs. Full Backend CI additionally validates Terraform templates and the
backend suite. A real Proxmox deployment is still required to validate guest
networking and SSH/bootstrap end to end.
