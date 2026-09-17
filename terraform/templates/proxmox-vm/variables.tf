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
