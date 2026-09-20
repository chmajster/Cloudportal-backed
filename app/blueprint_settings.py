from __future__ import annotations

from app.models import Setting

SETTING_KEY = 'blueprint_execution'
DEFAULTS = {
    'auto_approve_for_executors': True,
}


def blueprint_execution_settings(db):
    row = db.get(Setting, SETTING_KEY)
    raw = dict(row.value) if row and isinstance(row.value, dict) else {}
    return {
        'auto_approve_for_executors': bool(
            raw.get('auto_approve_for_executors', DEFAULTS['auto_approve_for_executors'])
        ),
    }


def save_blueprint_execution_settings(db, data):
    value = {
        'auto_approve_for_executors': bool(data.auto_approve_for_executors),
    }
    row = db.get(Setting, SETTING_KEY)
    if row is None:
        row = Setting(key=SETTING_KEY, value=value)
        db.add(row)
    else:
        row.value = value
    db.flush()
    return value
