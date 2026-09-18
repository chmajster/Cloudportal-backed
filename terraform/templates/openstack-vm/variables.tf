variable "name" { type = string }
variable "region" { type = string }
variable "image_name" { type = string }
variable "flavor_name" { type = string }
variable "network_name" { type = string }
variable "key_pair" {
  type    = string
  default = null
}
variable "security_groups" { type = list(string) }
