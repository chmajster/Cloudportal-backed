from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

# Instance backup archives live on the API host's protected CP_DATA_DIR. Running
# these jobs in the general RQ pool would move file ownership to an arbitrary
# worker host. A single local executor keeps backup/restore on the host that owns
# uploaded/generated archives; PostgreSQL advisory locks provide cross-host
# serialization against normal workers.
_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="instance-backup-api")


def submit_local(function, *args):
    return _EXECUTOR.submit(function, *args)
