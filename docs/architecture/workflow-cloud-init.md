# First-boot Cloud-init in Proxmox workflows

## Configure a Blueprint

Open **Blueprinty > Nowy Blueprint — kreator > Workflow**. For Proxmox, the
standard workflow begins with a `cloud_init` declaration. The **Cloud-init:
pierwszy start systemu** panel selects the final guest account from **Dane
dostępowe**. An SSH credential can contain a password, a private key, or both.
Only its ID is saved in the Blueprint. A manual username/public-key option also
remains available.

Enable **Instaluj QEMU Guest Agent automatycznie** and select **Storage
vendor-data (snippets)** to install the agent at first boot. The preview shows
what is generated without displaying passwords/private keys. The separate
**Czekaj na QEMU Guest Agent po Terraform apply** option controls the workflow's
post-apply readiness stage; installation and waiting remain separate settings.

The usual sequence is:

```text
cloud_init -> terraform_apply -> wait_for_agent -> wait_for_ip -> optional Ansible
```

In advanced workflows, Cloud-init can be added with **Dodaj Cloud-init przed
Terraform**. The helper retains existing dependencies, including approvals.
Cloud-init must precede both `terraform_plan` and `terraform_apply` when a saved
plan is used. It is a declaration, not a retryable or conditional guest SSH step.
The API rejects invalid ordering, duplicate declarations, conditions and retries.

Existing native Cloud-init Blueprints open in the full editor even through the
old quick-edit entry point: the quick form rebuilds the DAG and would otherwise
silently discard Cloud-init or plan/approval dependencies. The classic action
selector also exposes **Cloud-init: pierwszy start systemu** for Proxmox.

## What executes

Native Proxmox Cloud-init generates guest user-data for the final username,
password and/or derived public key. The selected account is not replaced with a
`cpbootstrap` account in this path. No guest private key is sent to Terraform or
the VM. The password is resolved only during execution, passed through the
existing sensitive runtime mechanism and excluded from Blueprint JSON, job
payloads, previews and normal logs. Terraform plans/states are still sensitive
artifacts and must retain the application's existing protected persistence and
backup controls.

When `install_qemu_guest_agent` is enabled, a separate vendor-data snippet adds:

```yaml
#cloud-config
package_update: true
packages:
  - qemu-guest-agent
runcmd:
  - |
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
    fi
```

A static systemd unit may not support `enable`, so only that operation is
allowed to fail; service startup and active-state checks are not ignored.

The Terraform QGA channel remains enabled, with `agent.wait_for_ip.disabled =
true`. Terraform does not wait for a guest agent that first-boot initialization
is still installing. Workflow readiness checks run after apply, and the worker
then obtains the guest's DHCP address through QGA. No initial SSH connection to
the guest or pre-known DHCP address is required for this Cloud-init path.

## Infrastructure prerequisites

The template must support Cloud-init, be prepared for a fresh first boot and use
a supported guest operating system. The VM needs working networking/DNS and
access to package repositories to install `qemu-guest-agent`.

The bundled bpg/proxmox provider uploads vendor-data snippets over SSH to the
**Proxmox node**, not to the guest. An enabled, active storage with `snippets`
content and working node SSH are prerequisites for automatic package
installation. Configure node SSH separately in the worker environment using
`PROXMOX_VE_SSH_*`; an API token is not an SSH password, and the selected VM
credential is never substituted as the node credential. Explicit SSH credentials
are preferred over the existing provider-password fallback.

Preflight checks storage visibility/content/status for the target node and
node SSH readiness before starting Terraform. Upload itself still requires
write permissions and the correct target-node SSH routing; the snippet is a
Terraform dependency of VM creation. Missing prerequisites stop execution
instead of silently falling back to the DHCP-dependent guest bootstrap.

Native account configuration with QGA installation disabled does not itself
require a vendor-data snippet or node SSH. If a workflow then waits for QGA or
uses DHCP discovery, the template must already contain a functioning agent.

References: bpg/terraform-provider-proxmox v0.111.1,
`docs/resources/virtual_environment_file.md` (Snippets), `docs/index.md` (SSH),
`docs/resources/virtual_environment_vm.md` (initialization and agent.wait_for_ip);
cloud-init's Cloud-config examples (packages, runcmd, users and passwords).

## Existing guests, jobs and approved plans

Changing a Blueprint does not rerun first-boot Cloud-init on an existing guest,
repair an already failed job or change an immutable job snapshot. Legacy
workflows without a `cloud_init` declaration retain their old execution path.
Existing bootstrap metadata/keys are not deleted when switching modes: the
executor refuses the switch and requests recovery instead.

Never delete/recreate existing guests, drop Terraform state/locks, force a job
status to success, or replace an approved saved plan automatically. Reconcile
the existing VM/state before retrying an interrupted deployment. A changed
credential or Cloud-init configuration intended for an approved saved plan
requires the normal new-plan and approval process.

## Validation

`tests/test_cloud_init_workflow.py` covers ordering, storage validation, DHCP
without guest SSH bootstrap, credential isolation, approved-plan reuse and
rendered vendor-data. `tests/test_cloud_init_compatibility.py` covers the API
schema, explicit node authentication and preservation of an edited DAG.
`tests/test_blueprint_wizard_contract.py` checks the actual wizard payload.

```sh
python -m pytest -q --tb=short tests/test_cloud_init_workflow.py tests/test_cloud_init_compatibility.py tests/test_blueprint_wizard_contract.py
python scripts/check-module-boundaries.py
find app/web -type f -name '*.js' -print0 | xargs -0 -n1 node --check
terraform fmt -check -recursive terraform
bash scripts/validate-terraform-templates.sh
pytest -q --tb=short
```

Unit/contract tests mock Proxmox and are not a substitute for a live deployment.
The pull request records the actual CI results and remaining live-validation
limitations. No production server or existing VM was modified by this change.
