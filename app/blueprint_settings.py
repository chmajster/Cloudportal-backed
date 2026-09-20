from __future__ import annotations

from app.models import Setting

SETTING_KEY = 'blueprint_execution'
DEFAULTS = {
    'auto_approve_for_executors': True,
    'approval_timeout_hours': 48,
}


def blueprint_execution_settings(db):
    row = db.get(Setting, SETTING_KEY)
    raw = dict(row.value) if row and isinstance(row.value, dict) else {}
    return {
        'auto_approve_for_executors': bool(
            raw.get('auto_approve_for_executors', DEFAULTS['auto_approve_for_executors'])
        ),
        'approval_timeout_hours': max(
            1, min(720, int(raw.get('approval_timeout_hours', DEFAULTS['approval_timeout_hours']) or 48))
        ),
    }


def save_blueprint_execution_settings(db, data):
    value = {
        'auto_approve_for_executors': bool(data.auto_approve_for_executors),
        'approval_timeout_hours': int(data.approval_timeout_hours),
    }
    row = db.get(Setting, SETTING_KEY)
    if row is None:
        row = Setting(key=SETTING_KEY, value=value)
        db.add(row)
    else:
        row.value = value
    db.flush()
    return value
