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

resource "proxmox_virtual_environment_file" "qemu_guest_agent_cloud_init" {
  count        = var.install_qemu_guest_agent ? 1 : 0
  content_type = "snippets"
  datastore_id = var.cloud_init_snippet_storage
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
  started   = true
  tags      = sort(var.tags)
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
    bridge  = var.network
    vlan_id = var.vlan_id
  }
  agent { enabled = true }
  initialization {
    datastore_id        = var.storage
    vendor_data_file_id = var.install_qemu_guest_agent ? proxmox_virtual_environment_file.qemu_guest_agent_cloud_init[0].id : null
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
      username = var.ssh_username
      keys     = var.ssh_public_key == null ? [] : [var.ssh_public_key]
    }
  }
}
locals {
  configured_primary_ip    = var.ipv4_address == null ? null : split("/", var.ipv4_address)[0]
  reported_ipv4_addresses = distinct(compact(flatten([
    try(proxmox_virtual_environment_vm.vm.ipv4_addresses, [])
  ])))
  reported_primary_ip      = try([
    for address in local.reported_ipv4_addresses : address
    if can(regex("^([0-9]{1,3}\\.){3}[0-9]{1,3}$", address))
      && address != "127.0.0.1"
      && !startswith(address, "169.254.")
  ][0], null)
}

output "vm_id" { value = proxmox_virtual_environment_vm.vm.vm_id }
output "primary_ip" {
  value = local.configured_primary_ip != null ? local.configured_primary_ip : local.reported_primary_ip
}
