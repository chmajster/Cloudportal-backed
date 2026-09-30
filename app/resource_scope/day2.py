"""Scope gate for Day-2 APIs.

Legacy collection/history surfaces are not project-isolated and therefore stay
closed once multi-project data exists. Resource-addressed Day-2 routes are safe
to use in a selected project because the target is explicit and its persisted
tenant/project ownership is verified before provider access.
"""
from fastapi import Depends, Request
from sqlalchemy import exists, select

from app.database import get_db
from app.models import Deployment, ManagedResource, ManagedVM
from app.resource_scope.authorization import DEFAULT_SCOPE
from app.resource_scope.database import row_scope
from app.resource_scope.http import require as scoped_require
from app.tenancy.authorization import fail


def _ensure_explicit_target_scope(db, scope, resource_id):
    """Fail closed unless the explicit Day-2 target belongs to the selected scope."""
    row = db.get(ManagedVM, resource_id)
    if row is None:
        row = db.get(ManagedResource, resource_id)
    if row is None or row_scope(row) != scope:
        fail(404, 'RESOURCE_NOT_FOUND', 'Resource not found')
    return row


def ensure_legacy_day2_scope(db, scope, *, resource_id=None):
    """Allow explicit scoped targets; retain the legacy fence for unscoped surfaces."""
    if resource_id is not None:
        return _ensure_explicit_target_scope(db, scope, resource_id)

    if scope != DEFAULT_SCOPE:
        fail(409, 'GOVERNED_DAY2_REQUIRED', 'Multi-project Day-2 collection access is not enabled')
    # Deliberately inspect across projects, even in a bound session. Legacy
    # collection/history endpoints do not carry an explicit resource target.
    # Return no identifiers or counts from this internal safety check.
    for model in (Deployment, ManagedVM, ManagedResource):
        table = model.__table__
        foreign = exists().where(
            (table.c.tenant_id != scope.tenant_id) | (table.c.project_id != scope.project_id))
        if db.connection().scalar(select(foreign)):
            fail(409, 'GOVERNED_DAY2_REQUIRED',
                 'Day-2 collection access requires project isolation in a multi-project installation')


def require(permission):
    """Retain Day-2 permissions and add validated resource-scope checks."""
    scoped_dependency = scoped_require(permission)

    def dependency(request: Request, actor=Depends(scoped_dependency),
                   db=Depends(get_db, scope='function')):
        ensure_legacy_day2_scope(
            db,
            request.state.resource_scope,
            resource_id=request.path_params.get('resource_id'),
        )
        return actor

    return dependency
