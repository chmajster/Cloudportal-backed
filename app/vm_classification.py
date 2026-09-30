from __future__ import annotations

from sqlalchemy import select

from app.access.models import OrganizationAPMID
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
    relational = list(db.scalars(
        select(OrganizationAPMID).where(
            OrganizationAPMID.organization_id == str(tenant_id),
            OrganizationAPMID.enabled.is_(True),
        ).order_by(OrganizationAPMID.is_system.desc(), OrganizationAPMID.code)
    ))
    if relational:
        apmids = [row.code for row in relational]
    else:
        # Compatibility-only read for installations between code deploy and the
        # Alembic backfill. New writes never update this legacy setting.
        legacy = db.get(Setting, tenant_setting_key(tenant_id))
        raw = dict(legacy.value) if legacy and isinstance(legacy.value, dict) else {}
        requested = normalize_apmids(raw.get('apmids')) if legacy is not None else list(global_settings['apmids'])
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
    from app.access.groups import ensure_apmid_groups
    from app.access.service import delete_apmid, ensure_apmid
    from app.tenancy.models import Tenant

    organization = db.get(Tenant, str(tenant_id))
    if organization is None:
        raise ValueError('Organization not found')
    requested = [
        *DEFAULT_APMIDS,
        *(item for item in normalize_apmids(apmids) if item not in DEFAULT_APMIDS),
    ]
    existing = list(db.scalars(select(OrganizationAPMID).where(
        OrganizationAPMID.organization_id == str(tenant_id)
    )))
    existing_by_code = {row.code: row for row in existing}
    for code in requested:
        ensure_apmid(db, organization, code, is_system=(code in DEFAULT_APMIDS))
        ensure_apmid_groups(db, organization, code)
    for code, row in existing_by_code.items():
        if code not in requested and not row.is_system:
            delete_apmid(db, organization, code)
    db.flush()
    return tenant_vm_classification_settings(db, tenant_id)

