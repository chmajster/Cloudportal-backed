variable "name" { type = string }
variable "node" { type = string }
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
