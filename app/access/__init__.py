"""Unified Organization > Project > Resource access domain.

Resource scope authorization remains part of the public ``app.access`` API so
inventory and Day-2 routes share the same request and worker checks.
"""

from app.access.resource_authorization import (
    deployment_predicate,
    entity_deployment_predicate,
    entity_job_predicate,
    ensure_deployment_access,
    ensure_deployment_entity,
    ensure_inventory_resource_access,
    ensure_inventory_vm_access,
    ensure_job_access,
    ensure_job_entity,
    ensure_not_terraform_managed,
    ensure_request_scope,
    inventory_resource_predicate,
    inventory_vm_predicate,
    job_predicate,
    managed_vm_for_access,
    request_permissions,
)

__all__ = [
    'deployment_predicate',
    'entity_deployment_predicate',
    'entity_job_predicate',
    'ensure_deployment_access',
    'ensure_deployment_entity',
    'ensure_inventory_resource_access',
    'ensure_inventory_vm_access',
    'ensure_job_access',
    'ensure_job_entity',
    'ensure_not_terraform_managed',
    'ensure_request_scope',
    'inventory_resource_predicate',
    'inventory_vm_predicate',
    'job_predicate',
    'managed_vm_for_access',
    'request_permissions',
]
