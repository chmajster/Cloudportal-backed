import pytest
from fastapi import HTTPException

from app.automation.yaml_codec import dump_blueprint_yaml, parse_blueprint_yaml


BASE_BLUEPRINT = {
    'slug': 'web-prod',
    'name': 'Web Production',
    'description': 'Production web VM',
    'is_active': True,
    'visibility': {'backend': True, 'cloudportal': True, 'api': True},
    'allowed_role_ids': [],
    'allowed_user_ids': [],
    'manager_role_ids': [],
    'variables_schema': {
        'cpu': {'type': 'integer', 'label': 'CPU', 'required': False, 'default': 2, 'min': 1, 'max': 16},
    },
    'deployment': {
        'name': '{{ hostname }}',
        'provider_id': 7,
        'credentials_id': 5,
        'template': 'proxmox-vm',
        'variables': {'cpu': '{{ cpu }}'},
        'executor': 'terraform',
    },
    'workflow': [
        {'id': 'apply', 'type': 'terraform_apply', 'depends_on': [], 'conditions': {}, 'retry': 0, 'timeout': 3600, 'rollback': None},
        {'id': 'ip', 'type': 'wait_for_ip', 'depends_on': ['apply'], 'conditions': {}, 'retry': 1, 'timeout': 600, 'rollback': None},
    ],
    'requires_approval': False,
    'recovery_policy': 'preserve',
}


def test_yaml_round_trip_uses_cloudportal_blueprint_document():
    text = dump_blueprint_yaml(BASE_BLUEPRINT, version=4)

    assert 'apiVersion: cloudportal.io/v1' in text
    assert 'kind: Blueprint' in text
    assert 'cloudportal.io/version' in text

    parsed = parse_blueprint_yaml(text)
    assert parsed.slug == 'web-prod'
    assert parsed.deployment.provider_id == 7
    assert [step.id for step in parsed.workflow] == ['apply', 'ip']
    assert parsed.workflow[1].depends_on == ['apply']


def test_yaml_parser_accepts_native_api_payload():
    import yaml

    parsed = parse_blueprint_yaml(yaml.safe_dump(BASE_BLUEPRINT, sort_keys=False))
    assert parsed.name == 'Web Production'
    assert parsed.variables_schema['cpu'].default == 2


def test_yaml_parser_rejects_multiple_documents():
    with pytest.raises(HTTPException) as exc:
        parse_blueprint_yaml('slug: one\n---\nslug: two\n')

    assert exc.value.status_code == 422
    assert 'exactly one' in exc.value.detail


def test_yaml_parser_reports_schema_validation_errors():
    invalid = dump_blueprint_yaml(BASE_BLUEPRINT).replace(
        'depends_on:\n    - apply',
        'depends_on:\n    - missing-step',
    )

    with pytest.raises(HTTPException) as exc:
        parse_blueprint_yaml(invalid)

    assert exc.value.status_code == 422
    assert 'dependency' in exc.value.detail.lower()


def test_yaml_parser_rejects_unknown_security_sensitive_fields():
    bad_access = dump_blueprint_yaml(BASE_BLUEPRINT).replace(
        'allowedRoleIds: []',
        'allowedRoleId: []',
    )
    with pytest.raises(HTTPException) as exc:
        parse_blueprint_yaml(bad_access)
    assert exc.value.status_code == 422
    assert 'unsupported fields' in exc.value.detail
    assert 'allowedRoleId' in exc.value.detail

    bad_spec = dump_blueprint_yaml(BASE_BLUEPRINT).replace(
        'active: true',
        'activ: false',
    )
    with pytest.raises(HTTPException) as exc:
        parse_blueprint_yaml(bad_spec)
    assert exc.value.status_code == 422
    assert 'unsupported fields' in exc.value.detail
    assert 'activ' in exc.value.detail
