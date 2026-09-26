terraform {
  required_version = ">= 1.8.0"
  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = "~> 0.111.0"
    }
  }
}

provider "proxmox" {}

resource "proxmox_virtual_environment_vm" "vm" {
  name            = var.name
  node_name       = var.node
  vm_id           = var.vm_id
  started         = var.started
  stop_on_destroy = true
  tags            = sort(var.tags)
  boot_order      = ["${var.disk_bus}0"]

  cpu {
    cores = var.cpu
  }

  memory {
    dedicated = var.memory
  }

  dynamic "disk" {
    for_each = { for index, file_id in var.import_file_ids : index => file_id }
    content {
      datastore_id = var.storage
      interface    = "${var.disk_bus}${disk.key}"
      import_from  = disk.value
    }
  }

  dynamic "network_device" {
    for_each = { for index in range(var.network_count) : index => index }
    content {
      bridge  = var.network
      vlan_id = var.vlan_id
    }
  }

  agent {
    enabled = var.qemu_guest_agent
    wait_for_ip {
      disabled = true
    }
  }
}

output "vm_id" {
  value = proxmox_virtual_environment_vm.vm.vm_id
}

output "primary_ip" {
  value = ""
}
