from __future__ import annotations

from app.config import settings
from app.models import Setting


SETTING_KEY = 'job_execution'
DEFAULT_MAX_PARALLEL_JOBS = 10


def job_execution_settings(db):
    default_limit = DEFAULT_MAX_PARALLEL_JOBS
    row = db.get(Setting, SETTING_KEY)
    raw = dict(row.value) if row and isinstance(row.value, dict) else {}
    try:
        max_parallel_jobs = int(raw.get('max_parallel_jobs', default_limit))
    except (TypeError, ValueError):
        max_parallel_jobs = default_limit
    return {
        'max_parallel_jobs': max(1, min(64, max_parallel_jobs)),
    }


def save_job_execution_settings(db, data):
    value = {
        'max_parallel_jobs': max(1, min(64, int(data.max_parallel_jobs))),
    }
    row = db.get(Setting, SETTING_KEY)
    if row is None:
        row = Setting(key=SETTING_KEY, value=value)
        db.add(row)
    else:
        row.value = value
    db.flush()
    return value
