from __future__ import annotations

import re
from copy import deepcopy

import yaml
from fastapi import HTTPException
from sqlalchemy import select

from app.models import Setting


SETTING_KEY = 'custom_ansible_playbooks'
MAX_PLAYBOOK_BYTES = 262144
MAX_PLAYBOOKS = 100
SLUG = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$')
VARIABLE = SLUG
BANNED_KEYS = {
    'connection',
    'delegate_to',
    'local_action',
    'vars_files',
    'roles',
    'import_playbook',
    'action',
    'ansible_connection',
    'ansible_host',
    'ansible_password',
    'ansible_ssh_private_key_file',
    'ansible_winrm_transport',
}
BANNED_TASK_MODULES = {
    'script',
    'ansible.builtin.script',
    'fetch',
    'ansible.builtin.fetch',
    'synchronize',
    'ansible.posix.synchronize',
    'template',
    'ansible.builtin.template',
    'include_tasks',
    'ansible.builtin.include_tasks',
    'import_tasks',
    'ansible.builtin.import_tasks',
    'include_role',
    'ansible.builtin.include_role',
    'import_role',
    'ansible.builtin.import_role',
    'add_host',
    'ansible.builtin.add_host',
    'include_vars',
    'ansible.builtin.include_vars',
}
CONTROLLER_LOOKUP = re.compile(
    r'''(?i)(?:\blookup\s*\(|\bquery\s*\(|\bq\s*\(|hostvars\s*\[\s*['"](?:localhost|127\.0\.0\.1)['"])'''
)
CONTROLLER_CONNECTION_OVERRIDE = re.compile(
    r'(?i)\bansible_(?:connection|host|password|ssh_private_key_file|winrm_transport)\b\s*[:=]'
)


def _record_store(db, *, lock=False):
    query = select(Setting).where(Setting.key == SETTING_KEY)
    if lock:
        query = query.with_for_update()
    row = db.scalar(query)
    if row is None:
        if not lock:
            return None, {}
        row = Setting(key=SETTING_KEY, value={'items': {}})
        db.add(row)
        db.flush()
    value = dict(row.value or {})
    items = value.get('items')
    if not isinstance(items, dict):
        items = {}
    return row, dict(items)


def _safe_variable_definitions(raw):
    if raw is None:
        return {}
    if not isinstance(raw, dict) or len(raw) > 100:
        raise HTTPException(422, 'Custom playbook variables must be an object with at most 100 entries')
    result = {}
    for name, definition in raw.items():
        if not isinstance(name, str) or not VARIABLE.fullmatch(name) or name.lower().startswith('ansible_'):
            raise HTTPException(422, 'Invalid custom playbook variable name')
        if hasattr(definition, 'model_dump'):
            definition = definition.model_dump()
        if not isinstance(definition, dict) or set(definition) - {'pattern', 'required'}:
            raise HTTPException(422, f'Invalid variable definition: {name}')
        pattern = definition.get('pattern', r'.{1,8192}')
        if not isinstance(pattern, str) or not pattern or len(pattern) > 512:
            raise HTTPException(422, f'Invalid variable pattern: {name}')
        try:
            re.compile(pattern)
        except re.error:
            raise HTTPException(422, f'Invalid regular expression for variable: {name}') from None
        result[name] = {
            'required': bool(definition.get('required', False)),
            'pattern': pattern,
        }
    return result


def _walk_yaml(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key), child
            yield from _walk_yaml(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_yaml(child)


def _validate_task_sources(document):
    for key, value in _walk_yaml(document):
        if key.startswith('with_'):
            raise HTTPException(422, f'Custom playbook cannot use legacy controller-side lookup loop: {key}')
        if key in BANNED_KEYS:
            raise HTTPException(422, f'Custom playbook cannot use controller-side directive: {key}')
        if key in BANNED_TASK_MODULES:
            raise HTTPException(422, f'Custom playbook cannot use controller-side module: {key}')
        if key in {'copy', 'ansible.builtin.copy', 'win_copy', 'ansible.windows.win_copy'}:
            if (
                isinstance(value, dict) and value.get('src')
                or isinstance(value, str) and re.search(r'(?i)(?:^|\\s)src\\s*=', value)
            ):
                raise HTTPException(422, 'Custom playbook copy tasks must use content, not controller-side src')
        if key in {'unarchive', 'ansible.builtin.unarchive'}:
            if not isinstance(value, dict):
                raise HTTPException(422, 'Custom playbook unarchive tasks must use mapping syntax with remote_src=true')
            if value.get('src') and value.get('remote_src') is not True:
                raise HTTPException(422, 'Custom playbook unarchive tasks require remote_src=true')


def validate_custom_playbook_source(source):
    if not isinstance(source, str):
        raise HTTPException(422, 'Custom playbook YAML is required')
    size = len(source.encode('utf-8'))
    if not source.strip() or size > MAX_PLAYBOOK_BYTES or '\x00' in source:
        raise HTTPException(422, 'Custom playbook YAML must be between 1 byte and 256 KiB')
    if CONTROLLER_LOOKUP.search(source):
        raise HTTPException(422, 'Controller-side lookup/query and localhost hostvars are not allowed')
    if CONTROLLER_CONNECTION_OVERRIDE.search(source):
        raise HTTPException(422, 'Custom playbook cannot override Ansible controller connection variables')
    try:
        document = yaml.safe_load(source)
    except yaml.YAMLError as exc:
        raise HTTPException(422, 'Custom playbook YAML is invalid') from exc
    if not isinstance(document, list) or not document:
        raise HTTPException(422, 'Custom playbook must contain a non-empty list of plays')
    for play in document:
        if not isinstance(play, dict):
            raise HTTPException(422, 'Each custom playbook play must be an object')
        hosts = play.get('hosts')
        if hosts not in {'all', '*'}:
            raise HTTPException(422, 'Custom playbook plays must target hosts: all')
        tasks = play.get('tasks')
        if tasks is not None and not isinstance(tasks, list):
            raise HTTPException(422, 'Custom playbook tasks must be a list')
    _validate_task_sources(document)
    return source


def custom_playbook_definition(db, playbook_id):
    if not isinstance(playbook_id, str) or not SLUG.fullmatch(playbook_id):
        return None
    _, items = _record_store(db)
    item = items.get(playbook_id)
    if not isinstance(item, dict):
        return None
    return deepcopy(item)


def list_custom_playbook_definitions(db):
    _, items = _record_store(db)
    return [
        deepcopy(item)
        for _, item in sorted(items.items())
        if isinstance(item, dict)
    ]


def save_custom_playbook(db, data, *, playbook_id=None, created_by=None):
    row, items = _record_store(db, lock=True)
    identifier = str(playbook_id or data.id or '').strip()
    if not SLUG.fullmatch(identifier):
        raise HTTPException(422, 'Invalid custom playbook identifier')
    existing = items.get(identifier)
    creating = existing is None
    if creating and len(items) >= MAX_PLAYBOOKS:
        raise HTTPException(409, f'Custom playbook limit reached ({MAX_PLAYBOOKS})')

    source = validate_custom_playbook_source(data.content)
    variables = _safe_variable_definitions(data.variables)
    transport = str(data.transport)
    if transport not in {'ssh', 'winrm'}:
        raise HTTPException(422, 'Custom playbook transport must be ssh or winrm')
    wait = ('wait-linux.yml' if transport == 'ssh' else 'wait-windows.yml') if data.wait_for_connection else None
    validate = ('validate-linux.yml' if transport == 'ssh' else 'validate-windows.yml') if data.validate_after else None
    version = 1 if creating else int(existing.get('version') or 1) + 1

    record = {
        'id': identifier,
        'name': str(data.name).strip(),
        'description': str(data.description or '').strip() or None,
        'category': str(data.category or 'Własne').strip() or 'Własne',
        'file': f'custom-{identifier}.yml',
        'transport': transport,
        'wait': wait,
        'validate': validate,
        'variables': variables,
        'version': version,
        'content': source,
        'custom': True,
        'created_by': int(existing.get('created_by') or created_by or 0) if existing else int(created_by or 0),
    }
    items[identifier] = record
    row.value = {'items': items}
    db.flush()
    return deepcopy(record)


def delete_custom_playbook(db, playbook_id):
    row, items = _record_store(db, lock=True)
    if playbook_id not in items:
        raise HTTPException(404, 'Custom playbook not found')
    del items[playbook_id]
    row.value = {'items': items}
    db.flush()


def custom_playbook_snapshot(definition):
    if not definition or not definition.get('custom'):
        return None
    return {
        key: deepcopy(definition.get(key))
        for key in (
            'id', 'name', 'description', 'category', 'file', 'transport',
            'wait', 'validate', 'variables', 'version', 'content', 'custom',
        )
    }
