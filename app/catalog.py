import importlib
import json
import re
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException
from pydantic import ValidationError

from app.config import settings
from app.database import session
from app.ansible_custom.service import custom_playbook_definition, list_custom_playbook_definitions


SLUG = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$')
PLAYBOOK_FILE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,100}\.ya?ml$')


def _safe_slug(value):
    if not isinstance(value, str) or not SLUG.fullmatch(value):
        raise HTTPException(422, 'Invalid catalog identifier')
    return value


def _read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError, TypeError):
        raise RuntimeError(f'Invalid catalog manifest: {path}') from None


def template_definition(template_id):
    template_id = _safe_slug(template_id)
    root = (settings().source_dir / 'terraform' / 'templates').resolve()
    folder = (root / template_id).resolve()
    if folder.parent != root:
        raise HTTPException(422, 'Invalid Terraform template path')
    manifest = folder / 'template.json'
    if not manifest.is_file():
        raise HTTPException(404, 'Template not found')
    data = _read_json(manifest)
    if data.get('id') != template_id or not SLUG.fullmatch(str(data.get('provider', ''))):
        raise RuntimeError(f'Invalid Terraform template manifest: {manifest}')
    if not isinstance(data.get('version'), int) or data['version'] < 1:
        raise RuntimeError(f'Terraform template version is invalid: {manifest}')
    import_spec = data.get('import')
    if import_spec is not None:
        if not isinstance(import_spec, dict):
            raise RuntimeError(f'Invalid Terraform import manifest: {manifest}')
        if not isinstance(import_spec.get('resource_address'), str) or not re.fullmatch(r'[A-Za-z0-9_.\[\]"-]+', import_spec['resource_address']):
            raise RuntimeError(f'Invalid Terraform import resource address: {manifest}')
        if not isinstance(import_spec.get('id_format'), str) or len(import_spec['id_format']) > 256:
            raise RuntimeError(f'Invalid Terraform import ID format: {manifest}')
        fields = import_spec.get('fields')
        if not isinstance(fields, list) or not fields or any(not SLUG.fullmatch(str(field)) for field in fields):
            raise RuntimeError(f'Invalid Terraform import fields: {manifest}')
    model_path = data.get('schema_model', '')
    if not isinstance(model_path, str) or not model_path.startswith('app.api.schemas:'):
        raise RuntimeError(f'Unapproved template schema model: {manifest}')
    if not any(folder.glob('*.tf')):
        raise RuntimeError(f'Terraform template contains no HCL: {folder}')
    return data, folder



def template_source_preview(template_id):
    """Return approved Terraform HCL source files for read-only UI preview."""
    data, folder = template_definition(template_id)
    files = []
    total_size = 0
    for path in sorted(folder.glob('*.tf')):
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding='utf-8')
        except (OSError, UnicodeError):
            raise RuntimeError(f'Unable to read Terraform template source: {path}') from None
        size = len(content.encode('utf-8'))
        total_size += size
        if size > 262144 or total_size > 1048576:
            raise HTTPException(413, 'Terraform template source is too large to preview')
        files.append({
            'name': path.name,
            'content': content,
            'size': size,
        })
    return {
        'id': data['id'],
        'name': data['name'],
        'provider': data['provider'],
        'version': data['version'],
        'files': files,
    }


def template_model(template_id):
    data, _ = template_definition(template_id)
    module_name, class_name = data['schema_model'].split(':', 1)
    module = importlib.import_module(module_name)
    model = getattr(module, class_name, None)
    if model is None or not hasattr(model, 'model_validate'):
        raise RuntimeError('Template schema model is unavailable')
    return model


def validate_template_variables(template_id, values):
    model = template_model(template_id)
    try:
        return model.model_validate(values)
    except ValidationError as error:
        raise HTTPException(422, error.errors(include_url=False, include_context=False, include_input=False)) from None


def template_public(template_id):
    data, _ = template_definition(template_id)
    return {
        'id': data['id'],
        'name': data['name'],
        'provider': data['provider'],
        'version': data['version'],
        'importable': bool(data.get('import')),
        'variables_schema': template_model(template_id).model_json_schema(),
    }


def list_templates():
    root = settings().source_dir / 'terraform' / 'templates'
    if not root.is_dir():
        return []
    result = []
    for folder in sorted(root.iterdir()):
        if folder.is_dir() and SLUG.fullmatch(folder.name) and (folder / 'template.json').is_file():
            result.append(template_public(folder.name))
    return result


def resolve_template_source(template_id):
    _, folder = template_definition(template_id)
    return folder


@lru_cache
def _playbook_catalog(source_dir):
    root = Path(source_dir) / 'ansible' / 'playbooks'
    document = _read_json(root / 'catalog.json')
    if not isinstance(document.get('version'), int) or document['version'] < 1:
        raise RuntimeError('Ansible catalog version is invalid')
    items = document.get('playbooks')
    if not isinstance(items, list):
        raise RuntimeError('Ansible catalog must contain a playbooks list')
    result = {}
    for item in items:
        identifier = str(item.get('id', ''))
        filename = str(item.get('file', ''))
        transport = item.get('transport')
        version = item.get('version')
        if not isinstance(version, int) or version < 1:
            raise RuntimeError('Invalid Ansible playbook version')
        if not SLUG.fullmatch(identifier) or not PLAYBOOK_FILE.fullmatch(filename):
            raise RuntimeError('Invalid Ansible catalog identifier or filename')
        if transport not in {'ssh', 'winrm'} or identifier in result:
            raise RuntimeError('Invalid Ansible catalog entry')
        if not (root / filename).is_file():
            raise RuntimeError(f'Ansible playbook is missing: {filename}')
        variables = item.get('variables', {})
        if not isinstance(variables, dict) or any(not SLUG.fullmatch(str(name)) for name in variables):
            raise RuntimeError('Invalid Ansible variable catalog')
        for definition in variables.values():
            if set(definition) - {'pattern', 'required'} or not isinstance(definition.get('pattern', ''), str):
                raise RuntimeError('Invalid Ansible variable definition')
            re.compile(definition['pattern'])
        for key in ('wait', 'validate'):
            extra = item.get(key)
            if extra is not None and (not PLAYBOOK_FILE.fullmatch(str(extra)) or not (root / extra).is_file()):
                raise RuntimeError(f'Invalid Ansible {key} playbook')
        result[identifier] = item
    return result


def builtin_playbook_definition(playbook_id):
    _safe_slug(playbook_id)
    item = _playbook_catalog(str(settings().source_dir)).get(playbook_id)
    return dict(item) if item is not None else None


def playbook_definition(playbook_id, db=None):
    _safe_slug(playbook_id)
    builtin = builtin_playbook_definition(playbook_id)
    if builtin is not None:
        return {**builtin, 'custom': False}
    if db is not None:
        item = custom_playbook_definition(db, playbook_id)
    else:
        with session() as local_db:
            item = custom_playbook_definition(local_db, playbook_id)
    if item is None:
        raise HTTPException(422, 'Unapproved playbook')
    return item


def _playbook_public_item(item):
    return {
        'id': item['id'],
        'name': item['name'],
        'description': item.get('description'),
        'category': item.get('category', 'Inne'),
        'version': item['version'],
        'variables': list(item.get('variables', {}).keys()),
        'required_variables': [
            name for name, definition in item.get('variables', {}).items()
            if definition.get('required')
        ],
        'transport': item['transport'],
        'custom': bool(item.get('custom')),
    }


def playbook_public(playbook_id, db=None):
    return _playbook_public_item(playbook_definition(playbook_id, db=db))


def playbook_source_preview(playbook_id, db=None):
    """Return Ansible playbook YAML files for read-only UI preview."""
    item = playbook_definition(playbook_id, db=db)
    root = (settings().source_dir / 'ansible' / 'playbooks').resolve()
    names = []
    for key in ('file', 'wait', 'validate'):
        filename = item.get(key)
        if filename and filename not in names:
            names.append(filename)

    files = []
    total_size = 0
    for filename in names:
        role = 'main' if filename == item.get('file') else ('wait' if filename == item.get('wait') else 'validate')
        if item.get('custom') and role == 'main':
            content = str(item.get('content') or '')
        else:
            path = (root / filename).resolve()
            if path.parent != root or not PLAYBOOK_FILE.fullmatch(filename) or not path.is_file():
                raise RuntimeError(f'Invalid Ansible playbook source: {filename}')
            try:
                content = path.read_text(encoding='utf-8')
            except (OSError, UnicodeError):
                raise RuntimeError(f'Unable to read Ansible playbook source: {path}') from None
        size = len(content.encode('utf-8'))
        total_size += size
        if size > 262144 or total_size > 1048576:
            raise HTTPException(413, 'Ansible playbook source is too large to preview')
        files.append({
            'name': filename,
            'role': role,
            'content': content,
            'size': size,
        })

    return {
        'id': item['id'],
        'name': item['name'],
        'version': item['version'],
        'transport': item['transport'],
        'custom': bool(item.get('custom')),
        'files': files,
    }


def list_playbooks(db=None):
    builtin_items = [
        _playbook_public_item({**item, 'custom': False})
        for _, item in sorted(_playbook_catalog(str(settings().source_dir)).items())
    ]
    if db is not None:
        custom_items = list_custom_playbook_definitions(db)
    else:
        with session() as local_db:
            custom_items = list_custom_playbook_definitions(local_db)
    return builtin_items + [_playbook_public_item(item) for item in custom_items]


def validate_playbook_variables_definition(item, variables):
    definitions = item.get('variables', {})
    unknown = set(variables) - set(definitions)
    if unknown:
        raise HTTPException(422, 'Unsupported playbook variables: ' + ', '.join(sorted(unknown)))
    result = {}
    for name, definition in definitions.items():
        value = variables.get(name)
        if value is None:
            if definition.get('required'):
                raise HTTPException(422, f'Missing playbook variable: {name}')
            continue
        if not isinstance(value, str) or len(value) > 8192 or not re.fullmatch(definition['pattern'], value):
            raise HTTPException(422, f'Invalid playbook variable: {name}')
        result[name] = value
    return result


def validate_playbook_variables(playbook_id, variables, db=None):
    return validate_playbook_variables_definition(
        playbook_definition(playbook_id, db=db),
        variables,
    )



def template_import_target(template_id, values):
    data, _ = template_definition(template_id)
    spec = data.get('import')
    if not spec:
        raise HTTPException(422, 'Terraform template does not support import')
    fields = spec['fields']
    if set(values) != set(fields):
        raise HTTPException(422, 'Terraform import identity does not match the approved template manifest')
    safe = {}
    for field in fields:
        value = values[field]
        if isinstance(value, int):
            safe[field] = str(value)
        elif isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:@/-]{1,128}', value):
            safe[field] = value
        else:
            raise HTTPException(422, 'Terraform import identity contains an invalid value')
    try:
        import_id = spec['id_format'].format(**safe)
    except (KeyError, ValueError):
        raise RuntimeError('Invalid Terraform import ID format') from None
    return spec['resource_address'], import_id
