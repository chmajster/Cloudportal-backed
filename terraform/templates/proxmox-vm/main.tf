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
resource "proxmox_virtual_environment_vm" "vm" {
  name      = var.name
  node_name = var.node
  started   = true
  clone {
    vm_id     = var.template_id
    node_name = coalesce(var.template_node, var.node)
    full      = true
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
    datastore_id = var.storage
    ip_config {
      ipv4 { address = "dhcp" }
    }
    user_account {
      username = var.ssh_username
      keys     = var.ssh_public_key == null ? [] : [var.ssh_public_key]
    }
  }
}
output "vm_id" { value = proxmox_virtual_environment_vm.vm.vm_id }
