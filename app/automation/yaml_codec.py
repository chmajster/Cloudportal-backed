from __future__ import annotations

from typing import Any

import yaml
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.schemas import BlueprintInput


MAX_BLUEPRINT_YAML_BYTES = 262_144
API_VERSION = 'cloudportal.io/v1'
KIND = 'Blueprint'


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HTTPException(422, f'{label} must be a YAML mapping')
    return value


def _native_payload(document: dict[str, Any]) -> dict[str, Any]:
    if 'spec' not in document and 'kind' not in document and 'apiVersion' not in document:
        return document

    api_version = document.get('apiVersion', API_VERSION)
    kind = document.get('kind', KIND)
    if api_version != API_VERSION:
        raise HTTPException(422, f'Unsupported Blueprint apiVersion: {api_version}')
    if kind != KIND:
        raise HTTPException(422, f'Unsupported YAML kind: {kind}')

    metadata = _require_mapping(document.get('metadata', {}), 'metadata')
    spec = _require_mapping(document.get('spec', {}), 'spec')
    access = _require_mapping(spec.get('access', {}), 'spec.access')
    governance = _require_mapping(spec.get('governance', {}), 'spec.governance')

    return {
        'slug': metadata.get('slug') or metadata.get('name'),
        'name': metadata.get('displayName') or metadata.get('name'),
        'description': metadata.get('description', ''),
        'is_active': spec.get('active', True),
        'visibility': spec.get('visibility', {}),
        'allowed_role_ids': access.get('allowedRoleIds', []),
        'allowed_user_ids': access.get('allowedUserIds', []),
        'manager_role_ids': access.get('managerRoleIds', []),
        'variables_schema': spec.get('variables', {}),
        'deployment': spec.get('deployment', {}),
        'workflow': spec.get('workflow', []),
        'requires_approval': governance.get('requiresApproval', False),
        'recovery_policy': governance.get('recoveryPolicy', 'preserve'),
    }


def parse_blueprint_yaml(content: str) -> BlueprintInput:
    if not isinstance(content, str) or not content.strip():
        raise HTTPException(422, 'Blueprint YAML is empty')
    if len(content.encode('utf-8')) > MAX_BLUEPRINT_YAML_BYTES:
        raise HTTPException(413, 'Blueprint YAML is too large')

    try:
        documents = list(yaml.safe_load_all(content))
    except yaml.YAMLError as exc:
        mark = getattr(exc, 'problem_mark', None)
        suffix = f' at line {mark.line + 1}, column {mark.column + 1}' if mark else ''
        raise HTTPException(422, f'Invalid Blueprint YAML{suffix}') from None

    if len(documents) != 1:
        raise HTTPException(422, 'Blueprint YAML must contain exactly one document')
    document = _require_mapping(documents[0], 'Blueprint YAML')
    payload = _native_payload(document)
    try:
        return BlueprintInput.model_validate(payload)
    except ValidationError as exc:
        errors = []
        for error in exc.errors(include_url=False):
            location = '.'.join(str(part) for part in error.get('loc', [])) or 'document'
            errors.append(f"{location}: {error.get('msg', 'invalid value')}")
        raise HTTPException(422, 'Blueprint YAML validation failed: ' + '; '.join(errors[:12])) from None


def blueprint_yaml_document(blueprint: BlueprintInput | dict[str, Any], *, version: int | None = None) -> dict[str, Any]:
    model = blueprint if isinstance(blueprint, BlueprintInput) else BlueprintInput.model_validate(blueprint)
    data = model.model_dump(mode='json')
    metadata: dict[str, Any] = {
        'name': data['slug'],
        'displayName': data['name'],
    }
    if data.get('description'):
        metadata['description'] = data['description']
    if version is not None:
        metadata['annotations'] = {'cloudportal.io/version': str(version)}

    return {
        'apiVersion': API_VERSION,
        'kind': KIND,
        'metadata': metadata,
        'spec': {
            'active': data['is_active'],
            'visibility': data['visibility'],
            'access': {
                'allowedRoleIds': data['allowed_role_ids'],
                'allowedUserIds': data['allowed_user_ids'],
                'managerRoleIds': data['manager_role_ids'],
            },
            'variables': data['variables_schema'],
            'deployment': data['deployment'],
            'workflow': data['workflow'],
            'governance': {
                'requiresApproval': data['requires_approval'],
                'recoveryPolicy': data['recovery_policy'],
            },
        },
    }


def dump_blueprint_yaml(blueprint: BlueprintInput | dict[str, Any], *, version: int | None = None) -> str:
    return yaml.safe_dump(
        blueprint_yaml_document(blueprint, version=version),
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=120,
    )
