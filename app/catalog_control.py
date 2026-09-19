from fastapi import HTTPException
from sqlalchemy import select

from app.models import Setting

CATALOG_SETTING_KEY = 'catalog_availability'
KINDS = {'templates', 'playbooks'}


def catalog_availability(db):
    row = db.get(Setting, CATALOG_SETTING_KEY)
    value = row.value if row and isinstance(row.value, dict) else {}
    return {
        'disabled_templates': sorted(set(value.get('disabled_templates') or [])),
        'disabled_playbooks': sorted(set(value.get('disabled_playbooks') or [])),
    }


def catalog_item_enabled(db, kind: str, item_id: str) -> bool:
    if kind not in KINDS:
        raise RuntimeError('Unsupported catalog kind')
    state = catalog_availability(db)
    return item_id not in set(state['disabled_' + kind])


def require_catalog_item_enabled(db, kind: str, item_id: str):
    if not catalog_item_enabled(db, kind, item_id):
        label = 'Terraform / OpenTofu template' if kind == 'templates' else 'Ansible playbook'
        raise HTTPException(409, f'{label} is disabled: {item_id}')


def catalog_item_public(db, kind: str, item: dict) -> dict:
    return {**item, 'enabled': catalog_item_enabled(db, kind, item['id'])}


def set_catalog_item_enabled(db, kind: str, item_id: str, enabled: bool):
    if kind not in KINDS:
        raise RuntimeError('Unsupported catalog kind')
    row = db.scalar(select(Setting).where(Setting.key == CATALOG_SETTING_KEY).with_for_update())
    if row is None:
        row = Setting(key=CATALOG_SETTING_KEY, value={
            'disabled_templates': [],
            'disabled_playbooks': [],
        })
        db.add(row)
        db.flush()

    value = dict(row.value or {})
    key = 'disabled_' + kind
    disabled = set(value.get(key) or [])
    if enabled:
        disabled.discard(item_id)
    else:
        disabled.add(item_id)
    value[key] = sorted(disabled)
    value.setdefault('disabled_templates', [])
    value.setdefault('disabled_playbooks', [])
    row.value = value
    db.flush()
    return enabled
