from __future__ import annotations

import base64
import binascii
import re
from copy import deepcopy

from fastapi import HTTPException
from sqlalchemy import select

from app.models import Blueprint, Setting


SETTING_KEY = 'blueprint_avatars'
MAX_AVATARS = 100
MAX_AVATAR_BYTES = 131072
SLUG = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$')
DATA_URI = re.compile(
    r'^(?:data:)?image/(?:x-icon|vnd\.microsoft\.icon);base64,([A-Za-z0-9+/=]+)$',
    re.IGNORECASE,
)


def normalize_blueprint_avatar_data_uri(value: str) -> str:
    text = str(value or '').strip()
    match = DATA_URI.fullmatch(text)
    if not match:
        raise HTTPException(
            422,
            'Blueprint avatar must use data:image/x-icon;base64,...',
        )

    try:
        raw = base64.b64decode(match.group(1), validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, 'Blueprint avatar contains invalid base64 data') from None

    if not raw:
        raise HTTPException(422, 'Blueprint avatar is empty')
    if len(raw) > MAX_AVATAR_BYTES:
        raise HTTPException(413, 'Blueprint avatar exceeds 128 KiB')
    if len(raw) < 6 or raw[:4] != b'\x00\x00\x01\x00':
        raise HTTPException(422, 'Blueprint avatar must contain a valid ICO image')
    if int.from_bytes(raw[4:6], 'little') < 1:
        raise HTTPException(422, 'Blueprint avatar ICO contains no images')

    encoded = base64.b64encode(raw).decode('ascii')
    return 'data:image/x-icon;base64,' + encoded


def _locked_store(db):
    row = db.scalar(select(Setting).where(Setting.key == SETTING_KEY).with_for_update())
    if row is None:
        row = Setting(key=SETTING_KEY, value={'items': []})
        db.add(row)
        db.flush()
    raw = row.value if isinstance(row.value, dict) else {}
    items = raw.get('items') if isinstance(raw, dict) else []
    return row, [deepcopy(item) for item in items if isinstance(item, dict)]


def list_blueprint_avatars(db):
    row = db.get(Setting, SETTING_KEY)
    raw = row.value if row and isinstance(row.value, dict) else {}
    items = raw.get('items') if isinstance(raw, dict) else []
    result = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        identifier = str(item.get('id') or '').strip()
        name = str(item.get('name') or '').strip()
        data_uri = str(item.get('data_uri') or '').strip()
        if not SLUG.fullmatch(identifier) or not name or not data_uri:
            continue
        result.append({
            'id': identifier,
            'name': name[:100],
            'data_uri': data_uri,
        })
    return sorted(result, key=lambda item: (item['name'].lower(), item['id'].lower()))


def blueprint_avatar(db, avatar_id: str):
    identifier = str(avatar_id or '').strip()
    for item in list_blueprint_avatars(db):
        if item['id'] == identifier:
            return item
    raise HTTPException(404, 'Blueprint avatar not found')


def save_blueprint_avatar(db, data, *, avatar_id: str | None = None):
    row, items = _locked_store(db)
    identifier = str(avatar_id or data.id or '').strip()
    if not SLUG.fullmatch(identifier):
        raise HTTPException(422, 'Invalid Blueprint avatar identifier')

    existing_index = next(
        (index for index, item in enumerate(items) if str(item.get('id') or '') == identifier),
        None,
    )
    if avatar_id is None and existing_index is not None:
        raise HTTPException(409, 'Blueprint avatar already exists')
    if avatar_id is not None and existing_index is None:
        raise HTTPException(404, 'Blueprint avatar not found')
    if data.id and str(data.id).strip() != identifier:
        raise HTTPException(422, 'Blueprint avatar id cannot be changed')
    if avatar_id is None and len(items) >= MAX_AVATARS:
        raise HTTPException(409, f'Blueprint avatar limit reached ({MAX_AVATARS})')

    record = {
        'id': identifier,
        'name': str(data.name or '').strip(),
        'data_uri': normalize_blueprint_avatar_data_uri(data.data_uri),
    }
    if not record['name']:
        raise HTTPException(422, 'Blueprint avatar name is required')

    if existing_index is None:
        items.append(record)
    else:
        items[existing_index] = record

    row.value = {'items': items}
    db.flush()
    return deepcopy(record)


def delete_blueprint_avatar(db, avatar_id: str):
    identifier = str(avatar_id or '').strip()
    row, items = _locked_store(db)
    index = next(
        (index for index, item in enumerate(items) if str(item.get('id') or '') == identifier),
        None,
    )
    if index is None:
        raise HTTPException(404, 'Blueprint avatar not found')

    referenced = db.scalar(
        select(Blueprint.id).where(Blueprint.avatar_id == identifier).limit(1)
    )
    if referenced is not None:
        raise HTTPException(
            409,
            'Blueprint avatar is assigned to a Blueprint; change the Blueprint avatar first',
        )

    del items[index]
    row.value = {'items': items}
    db.flush()
    return {'deleted': True}
