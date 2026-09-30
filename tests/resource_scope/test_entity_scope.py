import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.resource_scope.authorization import requested_scope
from app.resource_scope.columns import DEFAULT_PROJECT_ID, DEFAULT_TENANT_ID


def _request(*, entity=None):
    headers = [
        (b'x-tenant-id', DEFAULT_TENANT_ID.encode()),
        (b'x-project-id', DEFAULT_PROJECT_ID.encode()),
    ]
    if entity is not None:
        headers.append((b'x-entity', entity.encode()))
    return Request({
        'type': 'http',
        'method': 'GET',
        'path': '/api/v1/inventory/vms',
        'query_string': b'',
        'headers': headers,
        'client': ('127.0.0.1', 12345),
        'server': ('testserver', 80),
        'scheme': 'http',
    })


def test_requested_scope_parses_canonical_entity_header():
    scope = requested_scope(_request(entity='entity.leo.PROD.operator'))
    assert scope.tenant_id == DEFAULT_TENANT_ID
    assert scope.project_id == DEFAULT_PROJECT_ID
    assert scope.entity_key == 'entity.LEO.prod.operator'
    assert scope.apmid == 'LEO'
    assert scope.environment == 'prod'
    assert scope.entity_role == 'operator'


def test_requested_scope_rejects_unknown_entity_role():
    with pytest.raises(HTTPException) as exc:
        requested_scope(_request(entity='entity.LEO.prod.root'))
    assert exc.value.status_code == 422
    assert exc.value.detail['error'] == 'INVALID_ENTITY'
