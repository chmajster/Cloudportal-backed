# First-boot Cloud-init in Proxmox workflows

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
