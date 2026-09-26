terraform {
  required_version = ">= 1.8.0"
  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = "~> 0.111.0"
    }
  }
}
# Credentials are provided in the worker environment, never in generated HCL/tfvars.
provider "proxmox" {}

# ISO uploads use the Proxmox HTTP API, unlike snippets (which need host SSH).
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

resource "proxmox_virtual_environment_file" "qemu_guest_agent_cloud_init" {
  count = (
    var.cloud_init_seed_path == null
    && var.install_qemu_guest_agent
    && !var.qemu_guest_agent_bootstrap
    && var.cloud_init_snippet_storage != null
  ) ? 1 : 0
  content_type = "snippets"
  # Keep this argument non-null while Terraform refreshes a legacy snippet
  # instance that is about to be removed after migration to native NoCloud ISO.
  datastore_id = coalesce(var.cloud_init_snippet_storage, var.cloud_init_seed_storage, var.storage)
  node_name    = var.node

  source_raw {
    data = <<-EOF
    #cloud-config
    package_update: true
    packages:
      - qemu-guest-agent
    runcmd:
      - [ systemctl, enable, --now, qemu-guest-agent ]
    EOF

    file_name = "cloudportal-${var.name}-qemu-guest-agent.yaml"
  }
}

resource "proxmox_virtual_environment_vm" "vm" {
  name      = var.name
  node_name = var.node
  vm_id     = var.vm_id
  started         = true
  stop_on_destroy = true
  tags            = sort(var.tags)
  clone {
    vm_id        = var.template_id
    datastore_id = var.storage
    node_name    = coalesce(var.template_node, var.node)
    full         = true
  }
  cpu { cores = var.cpu }
  memory { dedicated = var.memory }
  disk {
    datastore_id = var.storage
    interface    = "scsi0"
    size         = var.disk
  }
  network_device {
    bridge      = var.network
    vlan_id     = var.vlan_id
    mac_address = var.cloud_init_seed_mac
  }
  agent {
    enabled = true
    # The worker owns guest bootstrap and readiness checks after apply.
    # Waiting here can block installation of the agent we are waiting for.
    # Keep the QGA channel enabled; skip only Terraform's guest IP lookup.
    wait_for_ip {
      disabled = true
    }
  }
  dynamic "cdrom" {
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
      datastore_id = var.storage
      vendor_data_file_id = (
        var.cloud_init_seed_path == null
        && var.install_qemu_guest_agent
        && !var.qemu_guest_agent_bootstrap
        && var.cloud_init_snippet_storage != null
      ) ? proxmox_virtual_environment_file.qemu_guest_agent_cloud_init[0].id : null
      dynamic "dns" {
        for_each = length(var.dns_servers) > 0 || var.dns_domain != null ? [1] : []
        content {
          domain  = var.dns_domain
          servers = var.dns_servers
        }
      }
      ip_config {
        ipv4 {
          address = var.ipv4_address == null ? "dhcp" : var.ipv4_address
          gateway = var.ipv4_address == null ? null : var.ipv4_gateway
        }
      }
      user_account {
        username = var.qemu_guest_agent_bootstrap ? var.bootstrap_username : var.ssh_username
        password = var.qemu_guest_agent_bootstrap ? null : var.ssh_password
        keys     = var.qemu_guest_agent_bootstrap ? compact([var.bootstrap_public_key]) : (var.ssh_public_key == null ? [] : [var.ssh_public_key])
      }
    }
  }
}
locals {
  configured_primary_ip = var.ipv4_address == null ? null : split("/", var.ipv4_address)[0]
}

output "vm_id" { value = proxmox_virtual_environment_vm.vm.vm_id }
output "primary_ip" {
  # DHCP address selection is performed by the worker using QEMU Guest Agent
  # and the MAC address of net0. The provider can expose unrelated interfaces.
  value = local.configured_primary_ip
}
