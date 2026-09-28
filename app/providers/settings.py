from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import session as db_session
from app.models import Provider, Setting
from app.providers.registry import DEFAULT_PROVIDER_ENABLED, PROVIDER_LABELS, PROVIDER_TYPES


SETTING_KEY = 'provider_platforms'


def _stored_enabled(row):
    if row is None or not isinstance(row.value, dict):
        return None
    value = row.value.get('enabled')
    return value if isinstance(value, dict) else None


def _provider_counts_query(db):
    return {
        str(provider_type): int(count)
        for provider_type, count in db.execute(
            select(Provider.type, func.count(Provider.id)).group_by(Provider.type)
        ).all()
    }


def global_provider_counts(db):
    """Return provider counts without request project scoping."""
    if db.info.get('resource_scope') is None:
        return _provider_counts_query(db)
    with db_session() as global_db:
        return _provider_counts_query(global_db)


def platform_enabled_map(db):
    """Return effective platform switches.

    Before the first explicit save, legacy configured platform types remain
    enabled so upgrading an existing installation never disables working
    provider connections. A clean installation has no Provider rows, therefore
    only Proxmox is enabled by default.
    """
    enabled = dict(DEFAULT_PROVIDER_ENABLED)
    row = db.get(Setting, SETTING_KEY)
    stored = _stored_enabled(row)
    if stored is not None:
        for name in PROVIDER_TYPES:
            if name in stored:
                enabled[name] = bool(stored[name])
        return enabled

    configured_types = set(global_provider_counts(db))
    for name in PROVIDER_TYPES:
        if name in configured_types:
            enabled[name] = True
    return enabled


def platform_enabled(db, provider_type: str) -> bool:
    return bool(platform_enabled_map(db).get(str(provider_type).lower(), False))


def require_platform_enabled(db, provider_type: str):
    name = str(provider_type or '').strip().lower()
    if name not in PROVIDER_TYPES:
        raise HTTPException(422, f'Unsupported infrastructure provider: {provider_type}')
    if not platform_enabled(db, name):
        raise HTTPException(409, f'Provider {PROVIDER_LABELS[name]} is disabled')
    return name


def platform_states(db):
    enabled = platform_enabled_map(db)
    counts = global_provider_counts(db)
    return [
        {
            'name': name,
            'label': PROVIDER_LABELS[name],
            'enabled': bool(enabled[name]),
            'configured': counts.get(name, 0) > 0,
            'connection_count': counts.get(name, 0),
            'connection_status': 'unknown' if counts.get(name, 0) > 0 else 'not_configured',
        }
        for name in PROVIDER_TYPES
    ]


def platform_state(db, provider_type: str):
    name = str(provider_type or '').strip().lower()
    if name not in PROVIDER_TYPES:
        raise HTTPException(404, 'Provider platform not found')
    return next(item for item in platform_states(db) if item['name'] == name)


def save_platform_enabled(db, provider_type: str, enabled: bool):
    name = str(provider_type or '').strip().lower()
    if name not in PROVIDER_TYPES:
        raise HTTPException(404, 'Provider platform not found')

    # Seed the row with the legacy-compatible effective state. PostgreSQL's
    # ON CONFLICT serializes the first-write race; FOR UPDATE then prevents
    # concurrent toggles from overwriting each other.
    initial = platform_enabled_map(db)
    initial_value = {'enabled': {key: bool(initial[key]) for key in PROVIDER_TYPES}}
    if db.get_bind().dialect.name == 'postgresql':
        db.execute(
            pg_insert(Setting)
            .values(key=SETTING_KEY, value=initial_value)
            .on_conflict_do_nothing(index_elements=[Setting.key])
        )
        db.flush()
    elif db.get(Setting, SETTING_KEY) is None:
        db.add(Setting(key=SETTING_KEY, value=initial_value))
        db.flush()

    row = db.scalar(select(Setting).where(Setting.key == SETTING_KEY).with_for_update())
    stored = _stored_enabled(row) or {}
    effective = dict(DEFAULT_PROVIDER_ENABLED)
    for key in PROVIDER_TYPES:
        if key in stored:
            effective[key] = bool(stored[key])
    effective[name] = bool(enabled)
    row.value = {'enabled': {key: bool(effective[key]) for key in PROVIDER_TYPES}}
    db.flush()
    return platform_state(db, name)
