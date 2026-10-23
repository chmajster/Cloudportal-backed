from app.access import (
    ensure_inventory_resource_access,
    ensure_inventory_vm_access,
    inventory_resource_predicate,
    inventory_vm_predicate,
)


def test_resource_scope_helpers_remain_available_from_access_package():
    assert callable(ensure_inventory_resource_access)
    assert callable(ensure_inventory_vm_access)
    assert callable(inventory_resource_predicate)
    assert callable(inventory_vm_predicate)
