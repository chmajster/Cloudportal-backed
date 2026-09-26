from app.jobs.proxmox_provision import parse_task_progress


def test_parse_proxmox_clone_progress_uses_newest_real_percentage():
    rows = [
        {'n': 1, 't': 'create full clone of drive scsi0'},
        {'n': 2, 't': 'transferred 1.2 GiB of 32 GiB (3.75%)'},
        {'n': 3, 't': 'transferred 16 GiB of 32 GiB (50.00%)'},
        {'n': 4, 't': 'transferred 27 GiB of 32 GiB (84.38%)'},
    ]

    assert parse_task_progress(rows) == 84.38


def test_parse_proxmox_clone_progress_does_not_invent_percentage():
    rows = [
        {'n': 1, 't': 'starting clone task'},
        {'n': 2, 't': 'copying volume local-lvm:vm-9000-disk-0'},
    ]

    assert parse_task_progress(rows) is None


def test_parse_proxmox_clone_progress_clamps_to_valid_provider_values():
    rows = [
        {'n': 1, 't': 'invalid 130% value'},
        {'n': 2, 't': 'valid 100% value'},
    ]

    assert parse_task_progress(rows) == 100.0
