from __future__ import annotations

from app.models import Setting

SETTING_KEY = 'vm_classification'
ENVIRONMENTS = ('test', 'dev', 'nonprod', 'prod')
DEFAULT_ENVIRONMENTS = {name: True for name in ENVIRONMENTS}
DEFAULT_HOSTNAME_DEFAULTS = {'location': 'wro', 'role': 'server'}


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
    hostname_defaults = dict(DEFAULT_HOSTNAME_DEFAULTS)
    stored_defaults = raw.get('hostname_defaults')
    if isinstance(stored_defaults, dict):
        for name in ('location', 'role'):
            text = str(stored_defaults.get(name) or '').strip().lower()
            if text:
                hostname_defaults[name] = text
    return {'environments': environments, 'apmids': apmids, 'hostname_defaults': hostname_defaults}


def save_vm_classification_settings(db, data):
    current = vm_classification_settings(db)
    value = {
        'environments': {name: bool(data.environments[name]) for name in ENVIRONMENTS},
        'apmids': list(data.apmids),
        'hostname_defaults': dict(data.hostname_defaults or current['hostname_defaults']),
    }
    row = db.get(Setting, SETTING_KEY)
    if row is None:
        row = Setting(key=SETTING_KEY, value=value)
        db.add(row)
    else:
        row.value = value
    db.flush()
    return value
