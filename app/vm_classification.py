from __future__ import annotations

from app.models import Setting

SETTING_KEY = 'vm_classification'
ENVIRONMENTS = ('test', 'dev', 'nonprod', 'prod')
DEFAULT_ENVIRONMENTS = {name: True for name in ENVIRONMENTS}


def vm_classification_settings(db):
    row = db.get(Setting, SETTING_KEY)
    raw = dict(row.value) if row and isinstance(row.value, dict) else {}
    environments = dict(DEFAULT_ENVIRONMENTS)
    stored = raw.get('environments')
    if isinstance(stored, dict):
        for name in ENVIRONMENTS:
            if name in stored:
                environments[name] = bool(stored[name])
    apmids = []
    for value in raw.get('apmids') or []:
        text = str(value).strip().upper()
        if text and text not in apmids:
            apmids.append(text)
    return {'environments': environments, 'apmids': apmids}


def save_vm_classification_settings(db, data):
    value = {
        'environments': {name: bool(data.environments[name]) for name in ENVIRONMENTS},
        'apmids': list(data.apmids),
    }
    row = db.get(Setting, SETTING_KEY)
    if row is None:
        row = Setting(key=SETTING_KEY, value=value)
        db.add(row)
    else:
        row.value = value
    db.flush()
    return value
