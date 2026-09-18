terraform {
  required_version = ">= 1.8.0"
  required_providers {
    openstack = {
      source  = "terraform-provider-openstack/openstack"
      version = ">= 2.0, < 4.0"
    }
  }
}

provider "openstack" {
  region = var.region
}

resource "openstack_compute_instance_v2" "vm" {
  name            = var.name
  image_name      = var.image_name
  flavor_name     = var.flavor_name
  key_pair        = var.key_pair
  security_groups = var.security_groups

  network {
    name = var.network_name
  }

  metadata = {
    managed_by = "Cloudportal-backed"
  }
}

output "resource_id" {
  value = openstack_compute_instance_v2.vm.id
}

output "primary_ip" {
  value = openstack_compute_instance_v2.vm.access_ip_v4
}
