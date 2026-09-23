# First-boot Cloud-init in Proxmox workflows

## Contract

A `cloud_init` workflow step prepares first-boot configuration before Terraform
creates/starts the guest. It is not an SSH step. The selected guest SSH credential
supplies the final login user, password and/or derived public key through native
Proxmox Cloud-init. Private keys must never be sent to the guest. Passwords must
not be put into Blueprint JSON, job payloads, previews or log messages.

When `install_qemu_guest_agent` is enabled, Cloud-init installs `qemu-guest-agent`
and enables/starts its service at first boot. The Terraform guest-agent channel
stays enabled, but Terraform must not wait for guest IP discovery. The workflow's
post-apply readiness stages handle QGA and DHCP discovery instead.

## Transport prerequisite

The bundled bpg/proxmox provider uploads vendor-data snippets over SSH to the
Proxmox node, not to the guest. A writable, enabled `snippets` storage and working
node SSH configuration are therefore prerequisites for automatic package
installation. Validate these before creating a VM. Do not silently fall back to
SSH guest bootstrap for an explicit Cloud-init workflow: that recreates the
DHCP/QGA circular dependency. Native Cloud-init login configuration without QGA
installation does not itself require a vendor-data snippet or node SSH.

References: bpg/terraform-provider-proxmox v0.111.1,
`docs/resources/virtual_environment_file.md` (Snippets),
`docs/resources/virtual_environment_vm.md` (initialization and agent.wait_for_ip);
cloud-init's Cloud-config examples (packages, runcmd, users and passwords).

## Existing guests and saved plans

Cloud-init first-boot configuration is for new guests cloned from a prepared
Cloud-init-capable template. It does not repair an already booted guest by
changing a Blueprint. Never delete/recreate existing guests, drop Terraform
state, or replace an approved saved plan automatically. Retain legacy bootstrap
handling for jobs planned before the new Cloud-init path was selected.

## Implementation status

This document records the implementation contract. Backend integration, wizard
configuration and regression validation are in progress on the feature branch;
see the pull request for the current completion and test status.
