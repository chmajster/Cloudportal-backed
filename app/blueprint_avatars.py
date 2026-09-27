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
    r'^(?:(?:data:)?image/(?:x-icon|vnd\.microsoft\.icon)|x-icon);base64,([A-Za-z0-9+/=]+)$',
    re.IGNORECASE,
)


DEFAULT_AVATAR_SEED_VERSION = 'linux-os-v1'
DEFAULT_BLUEPRINT_AVATARS = (
    {
        'id': 'ubuntu',
        'name': 'Ubuntu',
        'data_uri': 'data:image/x-icon;base64,AAABAAEAQEAAAAEAIACDAQAAFgAAAIlQTkcNChoKAAAADUlIRFIAAABAAAAAQAgGAAAAqmlx3gAAAUpJREFUeNrtm0ESgyAMRYXhDK7rneopPZTreol21Q5jFUUSSMzPyg1oXn4SQHWv5+PdGTbfGTcAAAAAAAAAAAAAAAAAAAAAsGlBuwP9NP+ul3HIHu+0bodjx9eWA8J8CjRRQGn0UuNzVRDultO36gL9NJ+KtjoAuZFdQzgaf8siyKUEcW3wyNF1dMWtA74PVFrAqPp81RSIH7pUsiknKdPBS85bVW2Qq0jtQaC6H6sCqCK4NQ/V3IEi4jWkynWv7C5wVno1oeBApBYA7nU5FAAAwgFoKWxQACeAZRz+lLClDOqCyXU4cnkh1CodqHabKjZDqXnE7QW4FLHnqAoFcB6KiOwCscNaToRwJtgKwBV5xw6aezfIVWS9huinZF86f7AYddEAaq8w8YGE9d2g0/7HiNlvhFS3QQAAAAAAAAAAAAAAAAAAAABobh8w6pg2+PBJ/wAAAABJRU5ErkJggg==',
    },
    {
        'id': 'sles',
        'name': 'SUSE Linux Enterprise Server (SLES)',
        'data_uri': 'data:image/x-icon;base64,AAABAAEAQEAAAAEAIABLAwAAFgAAAIlQTkcNChoKAAAADUlIRFIAAABAAAAAQAgGAAAAqmlx3gAAAxJJREFUeNrtm01ME0EUx/9dKU3btMXSFpBQy4clRBGBECI9EGMgXIxH0Vs1RoPEsyReTQ/ewY+DJB40xoMmEg2NBj9CYoy2iikGA0mh2jblS2gLLkI92Viz1e4nxb536k4nM//3m7dvZmdnVYdGL6VQwMagwI0AEAACQAAIAAEgAASgYK1IysZ8XR7FhDd7ByRpRyXFs4CSjksNQhSA7XRcKhCCAXA5L1VYKtm/IAB/dq6k41JrYXay81z9870tme247/IpETJCRz9fnOfSwycKaCX4P4y+mCigCCAABIAAEADaD+BpnsZeHDBVoUxjwtomi0U2julEFI+++jAWC6TrXdzXA7ejEwAwOO3FzZlnnO39Xo/L7ode48rkg/S1Q2/FuZqjnBoUAdBT3pT+rWa0MKq1cOitWGITGQDksAZjJYbbzqOYKeLUoAiAFFIYmLiL57FJaBg1qvVWdJcdxNomK9rBv0UKALgdnShmirJqOGnvkB/A6sY6vNEJbKVSWN/cgH85CP9yUJF79tcoZ9PAF4CgJGhUazHYchrH97SiWm+DCirFklbs+4qkGgRvirab69BurgMAfNtI4knkPa7PPMUSmxDlYF9tF/pquzLKLry7hfGFKQDAwy9v0VHqzKpBkWkwtLaYcW1S63Ci6jCGWs5gl0remXU0+gGXP97LqkGRCDj26iqchgq07q6Gq9QJl6UeAFBvqECjqUpUPvhXEgSAkbAPI2EfpwbFFkJTq2HcmR1Hv28YjyP+dLlFY1AsH2TTIHsEtJlrMJdcwAIbh11nQYOhMv1fMDEvq9Oexl4ssnGMxQJZNcgO4EbrWc7yF7FP+ByP5Jzc/MtBuN9c41XPqjGip7wJp+yu7VsKB1ZCsGlMKCnW4cfWFmaT8xiNTuB28KXsYT807cUR2340l+zl1NBf182rvZzfC+yELTEhOulxmAAQAAJAAHIxoa+e8nkGoAjgCyCfo0DoOoWRqtN8cV72JCj2QILczvNdpdIZITolRucEpftoqmBPitJSmAAQAAJAAAgAASAABGAH2k/nP2uuq/MQTAAAAABJRU5ErkJggg==',
    },
    {
        'id': 'rhel',
        'name': 'Red Hat Enterprise Linux (RHEL)',
        'data_uri': 'data:image/x-icon;base64,AAABAAEAQEAAAAEAIAAKAQAAFgAAAIlQTkcNChoKAAAADUlIRFIAAABAAAAAQAgGAAAAqmlx3gAAANFJREFUeNrt2ssNwyAMAFBAXSD779iOQE85R+InHJ4HIPAwNkjJ35RqOjhKOjwAAAAAAAAAAAAAAAAAYElcda/HZ175HH5a/C/n9wL07vwsnBIl7WcdHUUwUtGbkQUlyuLDZcCsxY8et5y683d8VnxkdAu7ah02pi4QYcdnHjMZEHnyI7IgNMCIo1Z2ntyK8btfgzv0/B6MZoAdLzstELrAm666LfOSAQA2veoqgovCPcBvcoogAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAcEr8ASRIQVGQnZ/VAAAAAElFTkSuQmCC',
    },
)


def normalize_blueprint_avatar_data_uri(value: str) -> str:
    text = str(value or '').strip()
    match = DATA_URI.fullmatch(text)
    if not match:
        raise HTTPException(
            422,
            'Blueprint avatar must use x-icon;base64,... or data:image/x-icon;base64,...',
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

    image_count = int.from_bytes(raw[4:6], 'little')
    if image_count < 1 or image_count > 256:
        raise HTTPException(422, 'Blueprint avatar ICO contains an invalid image count')

    directory_end = 6 + image_count * 16
    if len(raw) < directory_end:
        raise HTTPException(422, 'Blueprint avatar ICO directory is truncated')
    for index in range(image_count):
        entry = 6 + index * 16
        image_size = int.from_bytes(raw[entry + 8:entry + 12], 'little')
        image_offset = int.from_bytes(raw[entry + 12:entry + 16], 'little')
        if (
            image_size < 1
            or image_offset < directory_end
            or image_offset + image_size > len(raw)
        ):
            raise HTTPException(422, 'Blueprint avatar ICO contains an invalid image entry')

    encoded = base64.b64encode(raw).decode('ascii')
    return 'data:image/x-icon;base64,' + encoded


def _seed_default_blueprint_avatars(items):
    seeded = [deepcopy(item) for item in items if isinstance(item, dict)]
    existing_ids = {str(item.get('id') or '').strip() for item in seeded}
    changed = False
    for item in DEFAULT_BLUEPRINT_AVATARS:
        if item['id'] in existing_ids:
            continue
        seeded.append(deepcopy(item))
        existing_ids.add(item['id'])
        changed = True
    return seeded, changed


def _store_items(row, items):
    raw = deepcopy(row.value) if isinstance(row.value, dict) else {}
    versions = {
        str(value)
        for value in raw.get('default_seed_versions', [])
        if isinstance(value, str) and value
    }
    versions.add(DEFAULT_AVATAR_SEED_VERSION)
    raw['items'] = items
    raw['default_seed_versions'] = sorted(versions)
    row.value = raw


def _locked_store(db):
    row = db.scalar(select(Setting).where(Setting.key == SETTING_KEY).with_for_update())
    if row is None:
        row = Setting(key=SETTING_KEY, value={'items': [], 'default_seed_versions': []})
        db.add(row)
        db.flush()

    raw = row.value if isinstance(row.value, dict) else {}
    items = [deepcopy(item) for item in raw.get('items', []) if isinstance(item, dict)]
    versions = {
        str(value)
        for value in raw.get('default_seed_versions', [])
        if isinstance(value, str) and value
    }
    if DEFAULT_AVATAR_SEED_VERSION not in versions:
        items, _ = _seed_default_blueprint_avatars(items)
        _store_items(row, items)
        db.flush()
    return row, items


def list_blueprint_avatars(db):
    _, items = _locked_store(db)
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

    _store_items(row, items)
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
    _store_items(row, items)
    db.flush()
    return {'deleted': True}
