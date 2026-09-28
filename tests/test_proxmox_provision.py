from types import SimpleNamespace

import pytest

from app.executors.base import Cancelled, ExecutionFailed
from app.jobs import proxmox_provision
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


class DestroyContext:
    def __init__(self, *, force=False):
        self.job = SimpleNamespace(payload={'force': True} if force else {})
        self.stages = []
        self.logs = []
        self.progress_events = []
        self.quota_provider_submitted = False

    def stage(self, value):
        self.stages.append(value)

    def log(self, value):
        self.logs.append(value)

    def progress(self, percent, message, **kwargs):
        self.progress_events.append((percent, message, kwargs))

    def check(self):
        return None


class DestroyAdapter:
    def __init__(self, *, fail_status=False, fail_stop=False):
        self.fail_status = fail_status
        self.fail_stop = fail_stop
        self.calls = []

    def vm_status(self, node, vm_id):
        self.calls.append(('status', node, vm_id))
        if self.fail_status:
            raise RuntimeError('status unavailable')
        return {'status': 'running'}

    def vm_power(self, node, vm_id, action):
        self.calls.append(('power', node, vm_id, action))
        if self.fail_stop:
            raise RuntimeError('hard stop unavailable')
        return None

    def delete_vm(self, node, vm_id, *, purge=False, destroy_unreferenced_disks=False):
        self.calls.append(('delete', node, vm_id, purge, destroy_unreferenced_disks))
        return None


def test_proxmox_destroy_keeps_precheck_failure_terminal_without_force(monkeypatch):
    adapter = DestroyAdapter(fail_status=True)
    context = DestroyContext()
    monkeypatch.setattr(
        proxmox_provision,
        'identity',
        lambda _context: ('pve', 124, adapter),
    )

    with pytest.raises(ExecutionFailed, match='Nie udało się sprawdzić VM przed usunięciem'):
        proxmox_provision.destroy(context)

    assert adapter.calls == [('status', 'pve', 124)]


def test_forced_proxmox_destroy_bypasses_precheck_and_stop_failures(monkeypatch):
    adapter = DestroyAdapter(fail_status=True, fail_stop=True)
    context = DestroyContext(force=True)
    monkeypatch.setattr(
        proxmox_provision,
        'identity',
        lambda _context: ('pve', 124, adapter),
    )

    assert proxmox_provision.destroy(context) is True

    assert adapter.calls == [
        ('status', 'pve', 124),
        ('power', 'pve', 124, 'stop'),
        ('delete', 'pve', 124, True, False),
    ]
    assert context.stages == [
        'proxmox.destroy.check',
        'proxmox.destroy.force_stop',
        'proxmox.destroy.delete',
    ]
    assert any('proxmox.destroy.force_precheck_failed' in message for message in context.logs)
    assert any('proxmox.destroy.force_stop_failed' in message for message in context.logs)


def test_forced_proxmox_destroy_does_not_swallow_cancellation(monkeypatch):
    adapter = DestroyAdapter()
    context = DestroyContext(force=True)

    def cancelled_power(node, vm_id, action):
        adapter.calls.append(('power', node, vm_id, action))
        raise Cancelled('cancelled')

    adapter.vm_power = cancelled_power
    monkeypatch.setattr(
        proxmox_provision,
        'identity',
        lambda _context: ('pve', 124, adapter),
    )

    with pytest.raises(Cancelled):
        proxmox_provision.destroy(context)

    assert ('delete', 'pve', 124, True, False) not in adapter.calls


def test_forced_proxmox_destroy_preserves_control_plane_execution_failure(monkeypatch):
    adapter = DestroyAdapter()
    context = DestroyContext(force=True)

    def task_power(node, vm_id, action):
        adapter.calls.append(('power', node, vm_id, action))
        return 'UPID:pve:stop'

    adapter.vm_power = task_power
    monkeypatch.setattr(
        proxmox_provision,
        'identity',
        lambda _context: ('pve', 124, adapter),
    )
    monkeypatch.setattr(
        proxmox_provision,
        'wait_task',
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ExecutionFailed('Job authorization has been revoked')
        ),
    )

    with pytest.raises(ExecutionFailed, match='authorization has been revoked'):
        proxmox_provision.destroy(context)

    assert ('delete', 'pve', 124, True, False) not in adapter.calls


def test_forced_proxmox_destroy_continues_after_provider_task_failure(monkeypatch):
    adapter = DestroyAdapter()
    context = DestroyContext(force=True)

    def task_power(node, vm_id, action):
        adapter.calls.append(('power', node, vm_id, action))
        return 'UPID:pve:stop'

    adapter.vm_power = task_power
    monkeypatch.setattr(
        proxmox_provision,
        'identity',
        lambda _context: ('pve', 124, adapter),
    )
    monkeypatch.setattr(
        proxmox_provision,
        'wait_task',
        lambda *args, **kwargs: (_ for _ in ()).throw(
            proxmox_provision.ProxmoxTaskFailed('hard stop failed in Proxmox')
        ),
    )

    assert proxmox_provision.destroy(context) is True
    assert ('delete', 'pve', 124, True, False) in adapter.calls
    assert any('ProxmoxTaskFailed' in message for message in context.logs)
