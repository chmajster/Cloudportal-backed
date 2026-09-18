variable "name" { type = string }
variable "datacenter" { type = string }
variable "datastore" { type = string }
variable "cluster" { type = string }
variable "network" { type = string }
variable "template" { type = string }
variable "folder" {
  type    = string
  default = null
}
variable "cpu" { type = number }
variable "memory" { type = number }
variable "disk" { type = number }
