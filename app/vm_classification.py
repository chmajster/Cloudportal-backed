from __future__ import annotations

from app.models import Setting

SETTING_KEY = 'vm_classification'
TENANT_SETTING_PREFIX = 'vm_classification:'
ENVIRONMENTS = ('test', 'dev', 'nonprod', 'prod')
DEFAULT_ENVIRONMENTS = {name: True for name in ENVIRONMENTS}
DEFAULT_APMIDS = ('LEO',)
DEFAULT_HOSTNAME_DEFAULTS = {'location': 'wro', 'role': 'server'}


def normalize_apmids(values):
    result = []
    for value in values or []:
        text = str(value).strip().upper()
        if text and text not in result:
            result.append(text)
    return result


def tenant_setting_key(tenant_id):
    return TENANT_SETTING_PREFIX + str(tenant_id)


def vm_classification_settings(db):
    row = db.get(Setting, SETTING_KEY)
    raw = dict(row.value) if row and isinstance(row.value, dict) else {}
    environments = dict(DEFAULT_ENVIRONMENTS)
    stored = raw.get('environments')
    if isinstance(stored, dict):
        for name in ENVIRONMENTS:
            if name in stored:
                environments[name] = bool(stored[name])
    apmids = list(DEFAULT_APMIDS)
    for text in normalize_apmids(raw.get('apmids')):
        if text not in apmids:
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
    apmids = [
        *DEFAULT_APMIDS,
        *(value for value in data.apmids if value not in DEFAULT_APMIDS),
    ]
    value = {
        'environments': {name: bool(data.environments[name]) for name in ENVIRONMENTS},
        'apmids': apmids,
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

def tenant_vm_classification_settings(db, tenant_id):
    global_settings = vm_classification_settings(db)
    row = db.get(Setting, tenant_setting_key(tenant_id))
    raw = dict(row.value) if row and isinstance(row.value, dict) else {}
    if row is None:
        apmids = list(global_settings['apmids'])
    else:
        requested = normalize_apmids(raw.get('apmids'))
        apmids = [
            *DEFAULT_APMIDS,
            *(value for value in requested if value not in DEFAULT_APMIDS),
        ]

    enabled_environments = [
        name for name in ENVIRONMENTS
        if global_settings['environments'].get(name, False)
    ]
    apmid_environments = {
        apmid: list(enabled_environments)
        for apmid in apmids
    }
    classifications = [
        f'{apmid}.{environment.upper()}'
        for apmid in apmids
        for environment in enabled_environments
    ]
    return {
        'tenant_id': str(tenant_id),
        'environments': dict(global_settings['environments']),
        'apmids': apmids,
        'apmid_environments': apmid_environments,
        'classifications': classifications,
        'hostname_defaults': dict(global_settings['hostname_defaults']),
    }


def vm_classification_for_tenant(db, tenant_id):
    scoped = tenant_vm_classification_settings(db, tenant_id)
    return {
        'environments': scoped['environments'],
        'apmids': scoped['apmids'],
        'hostname_defaults': scoped['hostname_defaults'],
    }


def save_tenant_apmids(db, tenant_id, apmids):
    requested = normalize_apmids(apmids)
    value = {
        'apmids': [
            *DEFAULT_APMIDS,
            *(item for item in requested if item not in DEFAULT_APMIDS),
        ],
    }
    key = tenant_setting_key(tenant_id)
    row = db.get(Setting, key)
    if row is None:
        row = Setting(key=key, value=value)
        db.add(row)
    else:
        row.value = value
    db.flush()
    return tenant_vm_classification_settings(db, tenant_id)

