"""Validated server-side preference, compatible with the existing project domain.

The saved pair is never a grant. Resolve and reauthorize it for every operation;
background work must persist its own immutable scope, not consult this preference.
"""
from dataclasses import dataclass
from sqlalchemy import select
from app.projects.authorization import authorize
from app.projects.context_models import UserProjectContext
from app.projects.permissions import DEFAULT_PROJECT_ID
from app.projects.service import project_output
from app.tenancy.authorization import assert_version, fail, identity, lock_authorization
from app.tenancy.permissions import DEFAULT_TENANT_ID


@dataclass(frozen=True, slots=True)
class ProjectScope:
    tenant_id: str
    project_id: str
    user_id: int
    token_id: int
    permissions: frozenset[str]
    source: str


def _selection(db, user_id, *, lock=False):
    query = select(UserProjectContext).where(UserProjectContext.user_id == user_id)
    if lock:
        query = query.with_for_update()
    return db.scalar(query.execution_options(populate_existing=True))


def resolve_scope(db, principal, *, tenant_id=None, project_id=None,
                  permission='projects.read', write=False, use_preference=True):
    """Explicit IDs > live server preference > Default/Default, with no denied fallback."""
    actor = identity(db, principal)
    source = 'explicit'
    if tenant_id is not None and project_id is None:
        fail(422, 'PROJECT_REQUIRED', 'A project is required with an explicit tenant')
    if project_id is None:
        preference = _selection(db, actor.user_id) if use_preference else None
        if preference is not None and preference.project_id is not None:
            tenant_id, project_id, source = preference.tenant_id, preference.project_id, 'preference'
        else:
            tenant_id, project_id, source = DEFAULT_TENANT_ID, DEFAULT_PROJECT_ID, 'default'
    access = authorize(db, principal, project_id, permission, tenant_id=tenant_id, write=write)
    return ProjectScope(access.tenant.id, access.project.id, access.identity.user_id,
                        access.identity.token_id, access.permissions, source)


def context_get(db, principal):
    actor = identity(db, principal)
    row = _selection(db, actor.user_id)
    if row is None or row.project_id is None:
        return {'selected': None, 'version': row.version if row else 0}
    access = authorize(db, principal, row.project_id, 'projects.read', tenant_id=row.tenant_id)
    return {'selected': project_output(access.project), 'version': row.version}


def context_set(db, principal, data):
    access = authorize(db, principal, data.project_id, 'projects.select', tenant_id=data.tenant_id, lock=True)
    # Selection returns a project representation, so read permission is required too.
    authorize(db, principal, data.project_id, 'projects.read', tenant_id=data.tenant_id)
    row = _selection(db, access.identity.user_id, lock=True)
    if data.expected_version != (row.version if row else 0):
        fail(409, 'VERSION_CONFLICT', 'Project selection changed; reload it before retrying')
    if row is None:
        row = UserProjectContext(user_id=access.identity.user_id, tenant_id=access.tenant.id,
                                 project_id=access.project.id)
        db.add(row)
    else:
        row.tenant_id, row.project_id = access.tenant.id, access.project.id
        row.version += 1
    db.flush()
    return {'selected': project_output(access.project), 'version': row.version}


def context_clear(db, principal, expected_version=None):
    lock_authorization(db)
    actor = identity(db, principal)
    row = _selection(db, actor.user_id, lock=True)
    if row is None:
        if expected_version not in (None, 0):
            fail(409, 'VERSION_CONFLICT', 'Project selection changed; reload it before retrying')
        return {'selected': None, 'version': 0}
    if expected_version is not None:
        assert_version(row, expected_version)
    # Keep the revision, even after clearing, to prevent select/clear/select ABA.
    # Recovery from a revoked project never requires access to that old project.
    row.tenant_id = row.project_id = None
    row.version += 1
    db.flush()
    return {'selected': None, 'version': row.version}
