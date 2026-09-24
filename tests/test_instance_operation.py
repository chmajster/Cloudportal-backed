import pytest

from app.instance_operation import (
    InstanceOperationBusy,
    exclusive_instance_operation,
    normal_instance_operation,
)


def test_exclusive_operation_rejects_new_mutations(system):
    with normal_instance_operation(blocking=False):
        pass

    with exclusive_instance_operation():
        with pytest.raises(InstanceOperationBusy):
            with normal_instance_operation(blocking=False):
                pass
