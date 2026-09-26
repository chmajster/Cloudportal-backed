variable "name" { type = string }
variable "node" { type = string }
variable "vm_id" {
  type    = number
  default = null
}
variable "template_id" { type = number }
variable "template_node" {
  type    = string
  default = null
}
variable "cpu" { type = number }
variable "memory" { type = number }
variable "disk" { type = number }
variable "network" { type = string }
variable "storage" { type = string }
variable "vlan_id" {
  type    = number
  default = null
}
variable "ssh_username" { type = string }
variable "ssh_public_key" {
  type    = string
  default = null
}
variable "ssh_password" {
  type      = string
  default   = null
  sensitive = true
}
variable "install_qemu_guest_agent" {
  type    = bool
  default = false
}
variable "qemu_guest_agent_bootstrap" {
  type    = bool
  default = false
}
variable "bootstrap_username" {
  type    = string
  default = "cloudportal-bootstrap"
}
variable "bootstrap_public_key" {
  type    = string
  default = null
}
variable "cloud_init_snippet_storage" {
  type    = string
  default = null
}
variable "ipv4_address" {
  type    = string
  default = null
}
variable "ipv4_gateway" {
  type    = string
  default = null
}
variable "dns_servers" {
  type    = list(string)
  default = []
}
variable "dns_domain" {
  type    = string
  default = null
}
variable "tags" {
  type    = list(string)
  default = []
}

# Worker-only NoCloud media references; not accepted as public VM inputs.
variable "cloud_init_seed_path" {
  type    = string
  default = null
}
variable "cloud_init_seed_checksum" {
  type    = string
  default = null
}
variable "cloud_init_seed_storage" {
  type    = string
  default = null
}
variable "cloud_init_seed_interface" {
  type    = string
  default = null
}
variable "cloud_init_seed_mac" {
  type    = string
  default = null
}
