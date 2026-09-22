"""Compatibility gate for the independently merged legacy Day-2 subsystem.

Day-2 has global action/history models and native targets that do not yet have
complete project isolation. Keep single-project Default deployments functional,
but never expose this older execution path as a multi-project bypass.
"""
from fastapi import Depends, Request
from sqlalchemy import exists, select

from app.database import get_db
from app.models import Deployment, ManagedResource, ManagedVM
from app.resource_scope.authorization import DEFAULT_SCOPE
from app.resource_scope.http import require as scoped_require
from app.tenancy.authorization import fail


def ensure_legacy_day2_scope(db, scope):
    if scope != DEFAULT_SCOPE:
        fail(409, 'GOVERNED_DAY2_REQUIRED', 'Multi-project Day-2 execution is not enabled')
    # Deliberately inspect across projects, even in a bound session. A native
    # clone/restore can target an identity not yet registered in inventory.
    # Return no identifiers or counts from this internal safety check.
    for model in (Deployment, ManagedVM, ManagedResource):
        table = model.__table__
        foreign = exists().where(
            (table.c.tenant_id != scope.tenant_id) | (table.c.project_id != scope.project_id))
        if db.connection().scalar(select(foreign)):
            fail(409, 'GOVERNED_DAY2_REQUIRED',
                 'Day-2 requires complete native-target isolation in a multi-project installation')


def require(permission):
    """Retain global Day-2 permissions and add validated resource-scope checks."""
    scoped_dependency = scoped_require(permission)

    def dependency(request: Request, actor=Depends(scoped_dependency),
                   db=Depends(get_db, scope='function')):
        ensure_legacy_day2_scope(db, request.state.resource_scope)
        return actor

    return dependency
