import pytest

from app.policy_engine.engine import evaluate
from app.policy_engine.entities import (
    build_entity_key,
    entity_keys_for_permissions,
    entity_role_catalog,
    normalize_entity_key,
    parse_entity_key,
)


def _policy(scope):
    return {
        'id': 'entity-access',
        'tenant_id': None,
        'project_id': None,
        'priority': 7000,
        'status': 'enforced',
        'enforcement': 'hard',
        'policy_type': 'access',
        'scope': {
            **scope,
            'actions': ['blueprints.execute'],
            'resource_types': ['blueprint'],
        },
        'condition': {},
        'effects': [{'type': 'allow', 'mode': 'whitelist'}],
    }


def _context(entities):
    return {
        'actor': {
            'id': 7,
            'username': 'operator',
            'roles': [],
            'groups': [],
            'entities': list(entities),
        },
        'scope': {
            'tenant_id': 'tenant-1',
            'project_id': 'project-1',
            'apmid': 'LEO-131',
            'environment': 'prod',
        },
        'request': {'action': 'blueprints.execute', 'phase': 'pre_request'},
        'resource': {
            'type': 'blueprint',
            'apmid': 'LEO-131',
            'environment': 'prod',
        },
    }


def test_entity_key_is_canonical_and_roles_are_fixed():
    assert build_entity_key('leo-131', 'PROD', 'Read-Only') == 'entity.LEO-131.prod.read-only'
    assert normalize_entity_key('entity.leo-131.PROD.operator') == 'entity.LEO-131.prod.operator'
    assert parse_entity_key('entity.LEO-131.prod.admin') == {
        'apmid': 'LEO-131',
        'environment': 'prod',
        'role': 'admin',
    }

    assert [row['id'] for row in entity_role_catalog()] == [
        'read-only', 'operator', 'deployer', 'maintainer', 'admin',
    ]

    with pytest.raises(ValueError, match='Unknown entity role'):
        parse_entity_key('entity.LEO-131.prod.root')


def test_entity_roles_are_derived_without_granting_missing_permissions():
    read_only = entity_keys_for_permissions(
        {'blueprints.read'},
        apmid='LEO-131',
        environment='prod',
    )
    assert read_only == ['entity.LEO-131.prod.read-only']

    operator = entity_keys_for_permissions(
        {'blueprints.read', 'blueprints.execute'},
        apmid='LEO-131',
        environment='prod',
    )
    assert operator == [
        'entity.LEO-131.prod.read-only',
        'entity.LEO-131.prod.operator',
    ]

    admin = entity_keys_for_permissions(
        {
            'blueprints.read', 'blueprints.execute', 'blueprints.update',
            'blueprints.delete', 'blueprints.manage_access',
        },
        apmid='LEO-131',
        environment='prod',
    )
    assert 'entity.LEO-131.prod.admin' in admin


def test_policy_engine_matches_entity_as_identity_dimension():
    entity = 'entity.LEO-131.prod.operator'
    rule = _policy({'entities': [entity]})

    assert evaluate([rule], _context([entity])).decision == 'allow'
    assert evaluate([rule], _context(['entity.LEO-131.dev.operator'])).decision == 'deny'
