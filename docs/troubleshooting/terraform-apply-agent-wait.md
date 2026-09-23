# Proxmox first-boot Cloud-init and Terraform agent waits

## Configure a new workflow

In **Nowy Blueprint -> Workflow**, enable **Utwórz Cloud-init przy pierwszym
starcie VM** (enabled by default for the bundled Proxmox template). Select
**Użytkownik, hasło lub klucz z Dostępów**. The selection is the same guest SSH
credential shown in the VM parameters; the Blueprint stores only its ID.

Enable **Instaluj QEMU Guest Agent automatycznie** to include the package and
service activation in Cloud-init. The password-free preview updates with this
option. The generated runtime workflow is:

```text
Cloud-init configuration -> Terraform apply -> Wait Agent -> Wait IP -> Ansible (optional)
```

In advanced mode, Cloud-init is an available step type. There must be exactly
one unconditional Cloud-init step, preceding every Terraform plan/apply step.
The configuration panel can add/remove this step and wires its dependencies.
Cloud-init is first-boot configuration, not an SSH script run after VM creation.

## How DHCP without a preinstalled agent works

The worker creates a NoCloud ISO labelled `CIDATA`, containing `user-data`,
`meta-data` and `network-config`. Terraform uploads it as ISO content through the
Proxmox HTTP API, then attaches it before the cloned VM starts. This path does
not need Proxmox SSH, snippet storage, guest SSH or a known guest address.

The seed creates the target user directly. No temporary `cpbootstrap` account is
used. With installation selected, Cloud-init installs `qemu-guest-agent`, enables
its service where supported, starts it, and checks that it is active. Static and
indirect systemd units are started without treating their lack of an install
section as an error; OpenRC is also supported by the generated service script.

DHCP/static/IPAM configuration targets the primary NIC by its explicit MAC.
The worker discovers the DHCP address through QGA only after the guest has had
an opportunity to install and start QGA. The Terraform template keeps the agent
channel enabled but does not itself wait for the agent's network data:

```hcl
agent {
  enabled = true
  wait_for_ip {
    disabled = true
  }
}
```

## Prerequisites and limits

- Use a Linux cloud image with working Cloud-init/NoCloud support and a clean
  first-boot state. This change does not install Cloud-init into arbitrary images.
  Windows/Cloudbase-init is not implemented by this path.
- The target node needs active ISO-capable storage. The worker prefers the VM's
  storage if it supports ISO, then `local`, then another active ISO storage.
  The API credential needs permissions to read the source VM and storage and to
  upload ISO images. Missing ISO storage is reported before Terraform clones a VM.
- The guest needs DHCP or valid static/IPAM networking and access to its package
  repositories to install the agent. Guest package/DNS/firewall failures remain
  real errors; the workflow does not force a successful job status.
- The inherited Cloud-init CD-ROM is replaced with the seed ISO. Multiple inherited
  Cloud-init drives or no safe CD-ROM slot are rejected, not overwritten blindly.

## Credentials and approved plans

Cleartext guest passwords are never written to Blueprint variables, job payloads,
Terraform tfvars/environment/state, or the browser preview by this path. The
worker hashes them with SHA-512-crypt (100000 rounds and a random per-deployment
salt). Only public keys derived from selected private keys enter the guest.

The seed ISO contains the password hash and public key. Protect access to ISO
storage and use strong passwords: a password hash is still sensitive material.
Local seed files and their manifest use mode `0600` in the deployment workspace.
The API token follows the existing provider environment/redaction behavior.

Keep the worker data volume/workspace persistent and shared at the same path
between workers. The manifest pins the generated ISO checksum. An approved saved
plan must use the same ISO bytes and credential material; missing/changed media
or a changed credential fails closed. There is no silent regeneration or plan
substitution during approved apply. Restore the original workspace/media when
recovering an approved plan. ISO resources are managed by Terraform and removed
when their deployment is destroyed.

## Existing failed or running jobs

An application update does not rewrite an already-running Terraform process,
existing job snapshot or approved plan. Existing workflows without the explicit
Cloud-init predecessor retain the legacy snippet/SSH-bootstrap behavior. In that
legacy mode, DHCP without a working agent can still produce:

```text
Guest bootstrap cannot discover a DHCP address before SSH provisioning.
```

Use the new Cloud-init workflow for a new deployment. Do not delete an existing
VM, remove Terraform state/locks, or run concurrent apply merely to bypass the
old error. Inspect the persisted state and existing VM first. Retrofitting this
first-boot mode onto a managed VM is rejected unless an explicit rebuild was
requested; this feature never initiates that rebuild on its own. Guest day-2
configuration or recovery of the existing deployment is a separate operation.

## Validation

`tests/test_native_cloud_init.py` covers generated users/networking/agent commands,
ISO readback and permissions, saved-media integrity, existing-VM safety, executor
credential handling, and JavaScript workflow contracts. The original four
Terraform agent-wait regression tests are retained. Backend CI also validates
Terraform templates, all browser JavaScript, the backend suite and installers.
These checks do not replace an end-to-end test on a real Proxmox guest.

Primary references: Cloud-init NoCloud and module documentation; bpg/proxmox
v0.111.1 `docs/resources/virtual_environment_file.md` (ISO uploads use HTTP) and
`docs/resources/virtual_environment_vm.md` (`agent.wait_for_ip.disabled`).
