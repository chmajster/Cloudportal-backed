from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager

from app.config import settings


class InstanceOperationBusy(RuntimeError):
    """Raised when an exclusive instance operation blocks a new mutation."""


def _lock_root():
    root = settings().data_dir / ".instance-operation"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError("Instance operation lock directory must be a real directory")
    os.chmod(root, 0o700)
    return root


def _open_lock(name: str):
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(_lock_root() / name, flags, 0o600)
    os.fchmod(fd, 0o600)
    return os.fdopen(fd, "a+b", buffering=0)


def _acquire(stream, mode: int, *, blocking: bool) -> None:
    flags = mode if blocking else mode | fcntl.LOCK_NB
    try:
        fcntl.flock(stream.fileno(), flags)
    except BlockingIOError as exc:
        raise InstanceOperationBusy(
            "Instance backup or restore is quiescing mutating operations"
        ) from exc


def _release(stream) -> None:
    if stream is None:
        return
    try:
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    finally:
        stream.close()


@contextmanager
def normal_instance_operation(*, blocking: bool = True):
    """Admit a normal mutation and keep it visible to exclusive operations."""

    gate = _open_lock("gate.lock")
    activity = None
    try:
        _acquire(gate, fcntl.LOCK_SH, blocking=blocking)
        activity = _open_lock("activity.lock")
        try:
            _acquire(activity, fcntl.LOCK_SH, blocking=blocking)
        except Exception:
            _release(activity)
            activity = None
            raise
        _release(gate)
        gate = None
        try:
            yield
        finally:
            _release(activity)
            activity = None
    finally:
        _release(gate)
        _release(activity)


@contextmanager
def exclusive_instance_operation():
    """Block new mutations, drain active ones and serialize backup/restore."""

    gate = _open_lock("gate.lock")
    activity = None
    try:
        _acquire(gate, fcntl.LOCK_EX, blocking=True)
        activity = _open_lock("activity.lock")
        _acquire(activity, fcntl.LOCK_EX, blocking=True)
        try:
            yield
        finally:
            _release(activity)
            activity = None
    finally:
        _release(gate)
        _release(activity)
