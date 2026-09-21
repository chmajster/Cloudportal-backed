"""Regression coverage for reconciling #120 with the already merged #119."""
from dataclasses import FrozenInstanceError
from uuid import uuid4
import pytest
from sqlalchemy.exc import IntegrityError
from app.projects import context
from app.projects.context_models import UserProjectContext
from app.projects.context_routes import SelectionInput
from app.projects.models import ProjectMembership
from app.projects.permissions import DEFAULT_PROJECT_ID
from test_projects_unit import domain, error, setup, project_member


def selection(t, p, version=0):
    return SelectionInput(tenant_id=t['id'], project_id=p['id'], expected_version=version)


def test_selection_default_explicit_preference_and_immutable_scope(domain):
    d = domain; t, p = setup(d); project_member(d, t, p, d.alice)
    assert context.context_get(d.db, d.admin) == {'selected': None, 'version': 0}
    assert context.resolve_scope(d.db, d.admin).project_id == DEFAULT_PROJECT_ID
    assert context.resolve_scope(d.db, d.admin).source == 'default'
    chosen = context.context_set(d.db, d.alice, selection(t, p))
    assert chosen['version'] == 1 and chosen['selected']['id'] == p['id']
    scope = context.resolve_scope(d.db, d.alice)
    assert scope.source == 'preference' and scope.user_id == d.alice.user_id
    assert context.resolve_scope(d.db, d.alice, project_id=p['id']).source == 'explicit'
    with pytest.raises(FrozenInstanceError): scope.project_id = 'forged'
    assert context.context_get(d.db, d.bob) == {'selected': None, 'version': 0}
    error('PROJECT_REQUIRED', lambda: context.resolve_scope(d.db, d.alice, tenant_id=t['id']), 422)


def test_selection_clear_preserves_revision_and_prevents_aba(domain):
    d = domain; t, p = setup(d); project_member(d, t, p, d.alice)
    assert context.context_clear(d.db, d.alice, 0)['version'] == 0
    context.context_set(d.db, d.alice, selection(t, p))
    error('VERSION_CONFLICT', lambda: context.context_set(d.db, d.alice, selection(t, p)), 409)
    assert context.context_clear(d.db, d.alice, 1) == {'selected': None, 'version': 2}
    error('VERSION_CONFLICT', lambda: context.context_set(d.db, d.alice, selection(t, p, 1)), 409)
    assert context.context_set(d.db, d.alice, selection(t, p, 2))['version'] == 3
    error('VERSION_CONFLICT', lambda: context.context_clear(d.db, d.alice, 1), 409)


def test_selection_reauthorizes_revocation_and_allows_authenticated_recovery(domain):
    d = domain; t, p = setup(d); project_member(d, t, p, d.alice)
    context.context_set(d.db, d.alice, selection(t, p))
    d.db.get(ProjectMembership, (p['id'], d.alice.user_id)).status = 'disabled'; d.db.flush()
    for call in (lambda: context.context_get(d.db, d.alice), lambda: context.resolve_scope(d.db, d.alice)):
        error('PROJECT_NOT_FOUND', call, 404)
    assert context.context_clear(d.db, d.alice, 1)['version'] == 2
    # Default is not a fallback grant for a revoked or never-joined identity.
    error('PROJECT_NOT_FOUND', lambda: context.resolve_scope(d.db, d.alice), 404)


def test_selection_rejects_foreign_ids_and_token_without_select(domain):
    d = domain; t, p = setup(d); project_member(d, t, p, d.alice)
    error('PROJECT_NOT_FOUND', lambda: context.context_set(d.db, d.eve, selection(t, p)), 404)
    forged = SelectionInput(tenant_id=uuid4(), project_id=p['id'], expected_version=0)
    error('PROJECT_NOT_FOUND', lambda: context.context_set(d.db, d.alice, forged), 404)
    d.tokens[1].kind = 'api'; d.tokens[1].scopes = ['projects.read']; d.db.flush()
    error('SCOPED_PERMISSION_REQUIRED', lambda: context.context_set(d.db, d.alice, selection(t, p)), 403)
    assert d.db.get(UserProjectContext, d.alice.user_id) is None


@pytest.mark.parametrize('pair', ['wrong_tenant', 'partial'])
def test_selection_database_pair_constraint(domain, pair):
    d = domain; t, p = setup(d); d.db.commit()
    d.db.add(UserProjectContext(user_id=d.alice.user_id,
        tenant_id=str(uuid4()) if pair == 'wrong_tenant' else None, project_id=p['id']))
    with pytest.raises(IntegrityError): d.db.flush()


def test_revoked_token_cannot_clear_selection(domain):
    from app.models import now
    d = domain; t, p = setup(d); project_member(d, t, p, d.alice)
    context.context_set(d.db, d.alice, selection(t, p))
    d.tokens[1].revoked_at = now(); d.db.flush()
    error('AUTHENTICATION_REQUIRED', lambda: context.context_clear(d.db, d.alice), 401)
    assert d.db.get(UserProjectContext, d.alice.user_id).project_id == p['id']
