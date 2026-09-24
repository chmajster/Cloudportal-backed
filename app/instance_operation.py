from __future__ import annotations

import fcntl
import hashlib
import os
from contextlib import contextmanager

from sqlalchemy import text

from app.config import settings
from app.database import engine


class InstanceOperationBusy(RuntimeError):
    """Raised when an exclusive instance operation blocks a new mutation."""


def _lock_key(name: str) -> int:
    digest = hashlib.sha256(("cloudportal-instance-operation:" + name).encode()).digest()[:8]
    return int.from_bytes(digest, byteorder="big", signed=True)


_GATE_KEY = _lock_key("gate")
_ACTIVITY_KEY = _lock_key("activity")


def _lock_root():
    root = settings().data_dir / ".instance-operation"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError("Instance operation lock directory must be a real directory")
    os.chmod(root, 0o700)
    return root


def _open_file_lock(name: str):
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(_lock_root() / name, flags, 0o600)
    os.fchmod(fd, 0o600)
    return os.fdopen(fd, "a+b", buffering=0)


def _acquire_file(stream, mode: int, *, blocking: bool) -> None:
    flags = mode if blocking else mode | fcntl.LOCK_NB
    try:
        fcntl.flock(stream.fileno(), flags)
    except BlockingIOError as exc:
        raise InstanceOperationBusy(
            "Instance backup or restore is quiescing mutating operations"
        ) from exc


def _release_file(stream) -> None:
    if stream is None:
        return
    try:
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    finally:
        stream.close()


def _pg_connection():
    return engine().connect().execution_options(isolation_level="AUTOCOMMIT")


def _pg_acquire(connection, key: int, *, shared: bool, blocking: bool) -> None:
    if shared:
        function = "pg_advisory_lock_shared" if blocking else "pg_try_advisory_lock_shared"
    else:
        function = "pg_advisory_lock" if blocking else "pg_try_advisory_lock"
    acquired = connection.execute(
        text(f"SELECT {function}(:lock_key)"),
        {"lock_key": key},
    ).scalar()
    if not blocking and acquired is not True:
        raise InstanceOperationBusy(
            "Instance backup or restore is quiescing mutating operations"
        )


def _pg_release(connection, key: int, *, shared: bool) -> None:
    function = "pg_advisory_unlock_shared" if shared else "pg_advisory_unlock"
    connection.execute(text(f"SELECT {function}(:lock_key)"), {"lock_key": key})


@contextmanager
def _postgres_normal_operation(*, blocking: bool):
    connection = _pg_connection()
    gate = False
    activity = False
    try:
        _pg_acquire(connection, _GATE_KEY, shared=True, blocking=blocking)
        gate = True
        _pg_acquire(connection, _ACTIVITY_KEY, shared=True, blocking=blocking)
        activity = True
        _pg_release(connection, _GATE_KEY, shared=True)
        gate = False
        yield
    finally:
        try:
            if activity:
                _pg_release(connection, _ACTIVITY_KEY, shared=True)
            if gate:
                _pg_release(connection, _GATE_KEY, shared=True)
        finally:
            connection.close()


@contextmanager
def _postgres_exclusive_operation():
    connection = _pg_connection()
    gate = False
    activity = False
    try:
        _pg_acquire(connection, _GATE_KEY, shared=False, blocking=True)
        gate = True
        _pg_acquire(connection, _ACTIVITY_KEY, shared=False, blocking=True)
        activity = True
        yield
    finally:
        try:
            if activity:
                _pg_release(connection, _ACTIVITY_KEY, shared=False)
            if gate:
                _pg_release(connection, _GATE_KEY, shared=False)
        finally:
            connection.close()


@contextmanager
def _file_normal_operation(*, blocking: bool):
    gate = _open_file_lock("gate.lock")
    activity = None
    try:
        _acquire_file(gate, fcntl.LOCK_SH, blocking=blocking)
        activity = _open_file_lock("activity.lock")
        try:
            _acquire_file(activity, fcntl.LOCK_SH, blocking=blocking)
        except Exception:
            _release_file(activity)
            activity = None
            raise
        _release_file(gate)
        gate = None
        try:
            yield
        finally:
            _release_file(activity)
            activity = None
    finally:
        _release_file(gate)
        _release_file(activity)


@contextmanager
def _file_exclusive_operation():
    gate = _open_file_lock("gate.lock")
    activity = None
    try:
        _acquire_file(gate, fcntl.LOCK_EX, blocking=True)
        activity = _open_file_lock("activity.lock")
        _acquire_file(activity, fcntl.LOCK_EX, blocking=True)
        try:
            yield
        finally:
            _release_file(activity)
            activity = None
    finally:
        _release_file(gate)
        _release_file(activity)


@contextmanager
def normal_instance_operation(*, blocking: bool = True):
    """Admit one mutation while remaining visible to a cross-host exclusive fence."""

    if engine().dialect.name == "postgresql":
        with _postgres_normal_operation(blocking=blocking):
            yield
    else:
        # SQLite exists only for local development/tests and has no cross-host mode.
        with _file_normal_operation(blocking=blocking):
            yield


@contextmanager
def exclusive_instance_operation():
    """Block new mutations and drain active work across every PostgreSQL-connected host."""

    if engine().dialect.name == "postgresql":
        with _postgres_exclusive_operation():
            yield
    else:
        with _file_exclusive_operation():
            yield
