variable "name" { type = string }
variable "node" { type = string }
variable "storage" { type = string }
variable "network" {
  type    = string
  default = "vmbr0"
}
variable "vlan_id" {
  type    = number
  default = null
}
variable "cpu" {
  type    = number
  default = 2
  validation {
    condition     = var.cpu >= 1 && var.cpu <= 128
    error_message = "cpu must be between 1 and 128."
  }
}
variable "memory" {
  type    = number
  default = 4096
  validation {
    condition     = var.memory >= 128 && var.memory <= 1048576
    error_message = "memory must be between 128 and 1048576 MiB."
  }
}
variable "import_file_ids" {
  type = list(string)
  validation {
    condition     = length(var.import_file_ids) >= 1 && length(var.import_file_ids) <= 8
    error_message = "import_file_ids must contain between 1 and 8 Proxmox import volumes."
  }
}
variable "disk_bus" {
  type    = string
  default = "scsi"
  validation {
    condition     = contains(["scsi", "sata", "virtio"], var.disk_bus)
    error_message = "disk_bus must be scsi, sata or virtio."
  }
}
variable "started" {
  type    = bool
  default = true
}
variable "qemu_guest_agent" {
  type    = bool
  default = false
}
variable "tags" {
  type    = list(string)
  default = []
}
