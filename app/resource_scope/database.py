"""Bound-session query isolation and immutable ownership on persisted resource rows.

Only an authenticated HTTP request or a reauthorized worker may bind a session.
Unbound maintenance sessions must use explicit resource identities, never user input.
Core/SQL text is forbidden in a bound session; ORM bulk writes receive the same
criteria as reads. Direct engine access is not a substitute for this API.
"""
from sqlalchemy import and_, event, exists, inspect, select, update
from sqlalchemy.orm import Session, with_loader_criteria
from app import models as m
from app.projects.models import Project, ProjectMembership
from app.tenancy.models import TenantMembership
from app.tenancy.authorization import fail
from app.resource_scope.columns import ResourceScope, DEFAULT_PROJECT_ID, DEFAULT_TENANT_ID
from app.resource_scope.authorization import DEFAULT_SCOPE, Scope
from app.resource_scope.models import ProjectCredentialAccess, ProjectProviderAccess

OWNED_MODELS = (m.Deployment, m.Job, m.ManagedVM, m.ManagedResource, m.Blueprint,
                m.IPPool, m.IPAllocation, m.HostnameScheme, m.HostnameReservation, m.ScheduledOperation)


class ScopedSession(Session):
    """Application sessions; request/worker composition explicitly binds the scope."""


def row_scope(row):
    return Scope(row.tenant_id, row.project_id)


def bind_scope(db, scope):
    previous = db.info.get('resource_scope')
    if previous is not None and previous != scope:
        fail(409, 'SCOPE_IMMUTABLE', 'A database transaction cannot change project scope')
    for row in tuple(db.identity_map.values()):
        if isinstance(row, ResourceScope) and row_scope(row) != scope:
            fail(409, 'SCOPE_SESSION_REUSE', 'Resource session contains a different project')
        indirect_parent = {m.JobLog: ('job_id', m.Job), m.TerraformState: ('deployment_id', m.Deployment),
                           m.TerraformPlan: ('deployment_id', m.Deployment)}.get(type(row))
        if indirect_parent is not None:
            key, model = indirect_parent
            parent = db.get(model, getattr(row, key))
            if parent is None or row_scope(parent) != scope:
                fail(409, 'SCOPE_SESSION_REUSE', 'Resource session contains inaccessible child data')
        if isinstance(row, (m.Provider, m.Credential)):
            kind = 'provider' if isinstance(row, m.Provider) else 'credential'
            if not reference_visible(db, kind, row.id, scope):
                fail(409, 'SCOPE_SESSION_REUSE', 'Resource session contains an inaccessible reference')
    db.info['resource_scope'] = scope


def reference_visible(db, kind, key, scope):
    model, field = (ProjectProviderAccess, ProjectProviderAccess.provider_id) if kind == 'provider' else (
        ProjectCredentialAccess, ProjectCredentialAccess.credential_id)
    return db.scalar(select(field).where(field == key, model.tenant_id == scope.tenant_id,
                                         model.project_id == scope.project_id)) is not None


@event.listens_for(ScopedSession, 'do_orm_execute')
def filter_queries(state):
    scope = state.session.info.get('resource_scope')
    if scope is None:
        return
    if not state.is_orm_statement:
        fail(403, 'UNSCOPED_SQL_FORBIDDEN', 'Raw SQL is not allowed in a resource-scoped session')
    if state.is_insert:
        fail(403, 'UNSCOPED_INSERT_FORBIDDEN', 'Use validated resource objects for writes')
    if state.is_update:
        keys = {getattr(key, 'key', key) for key in (getattr(state.statement, '_values', None) or {})}
        if keys & {'tenant_id', 'project_id'}:
            fail(409, 'SCOPE_IMMUTABLE', 'Resource ownership cannot be changed by bulk update')
    tenant_id, project_id = scope.tenant_id, scope.project_id
    options = [with_loader_criteria(ResourceScope,
        lambda cls: and_(cls.tenant_id == tenant_id, cls.project_id == project_id), include_aliases=True)]
    for reference in (ProjectProviderAccess, ProjectCredentialAccess):
        options.append(with_loader_criteria(reference, and_(reference.tenant_id == tenant_id,
            reference.project_id == project_id), include_aliases=True))
    options += [
        with_loader_criteria(m.Provider, exists(select(ProjectProviderAccess.provider_id).where(
            ProjectProviderAccess.provider_id == m.Provider.id, ProjectProviderAccess.tenant_id == tenant_id,
            ProjectProviderAccess.project_id == project_id)), include_aliases=True),
        with_loader_criteria(m.Credential, exists(select(ProjectCredentialAccess.credential_id).where(
            ProjectCredentialAccess.credential_id == m.Credential.id, ProjectCredentialAccess.tenant_id == tenant_id,
            ProjectCredentialAccess.project_id == project_id)), include_aliases=True),
    ]
    # Indirectly owned rows must not leak through a direct ID, join or log query.
    for model, key, parent in ((m.JobLog, m.JobLog.job_id, m.Job),
                               (m.TerraformState, m.TerraformState.deployment_id, m.Deployment),
                               (m.TerraformPlan, m.TerraformPlan.deployment_id, m.Deployment)):
        options.append(with_loader_criteria(model, exists(select(parent.id).where(
            parent.id == key, parent.tenant_id == tenant_id, parent.project_id == project_id)), include_aliases=True))
    state.statement = state.statement.options(*options)


PARENTS = {
    m.Job: (('deployment_id', m.Deployment), ('retry_of', m.Job)),
    m.ManagedVM: (('deployment_id', m.Deployment),),
    m.ManagedResource: (('deployment_id', m.Deployment),),
    m.ScheduledOperation: (('deployment_id', m.Deployment),),
    m.IPAllocation: (('pool_id', m.IPPool),),
    m.HostnameReservation: (('scheme_id', m.HostnameScheme),),
}


def _parent_scope(db, row):
    result = None
    for key, model in PARENTS.get(type(row), ()):
        parent_id = getattr(row, key)
        if parent_id is None:
            continue
        parent = db.get(model, parent_id)
        if parent is None:
            fail(404, 'PARENT_NOT_FOUND', 'Parent resource not found')
        current = row_scope(parent)
        if result is not None and result != current:
            fail(409, 'SCOPE_MISMATCH', 'Related resources must have the same project')
        result = current
    return result or db.info.get('resource_scope', DEFAULT_SCOPE)


@event.listens_for(ScopedSession, 'before_flush')
def protect_writes(db, _context, _instances):
    scope = db.info.get('resource_scope')
    for row in tuple(db.new | db.dirty | db.deleted):
        if row in db.deleted and isinstance(row, (m.Credential, m.Provider)):
            reference, key = ((ProjectCredentialAccess, ProjectCredentialAccess.credential_id)
                              if isinstance(row, m.Credential) else (ProjectProviderAccess, ProjectProviderAccess.provider_id))
            # Central infrastructure deletion changes each explicit assignment.
            # Only the platform CRUD permission can reach this path.
            table = reference.__table__
            projects = db.connection().scalars(select(table.c.project_id).where(table.c[key.key] == row.id))
            db.info.setdefault('_touch_scope_projects', set()).update(projects)
        if isinstance(row, ResourceScope):
            if row in db.new:
                expected = _parent_scope(db, row)
                if row.tenant_id is None:
                    row.tenant_id = expected.tenant_id
                if row.project_id is None:
                    row.project_id = expected.project_id
                if any(getattr(row, key) is not None for key, _ in PARENTS.get(type(row), ())) and row_scope(row) != expected:
                    fail(409, 'SCOPE_MISMATCH', 'Child and deployment must have the same project')
            else:
                changes = inspect(row).attrs
                if changes.tenant_id.history.has_changes() or changes.project_id.history.has_changes():
                    fail(409, 'SCOPE_IMMUTABLE', 'Resource ownership cannot be changed in place')
            if scope is not None and row_scope(row) != scope:
                fail(404, 'RESOURCE_NOT_FOUND', 'Resource not found')
            if row not in db.deleted:
                attrs = inspect(row).attrs
                if row not in db.new and any(getattr(row, key) is not None for key, _ in PARENTS.get(type(row), ())) and any(attrs[key].history.has_changes()
                        for key, _ in PARENTS.get(type(row), ())):
                    if row_scope(row) != _parent_scope(db, row):
                        fail(409, 'SCOPE_MISMATCH', 'Related resources must have the same project')
                expected = row_scope(row)
                if isinstance(row, (m.Deployment, m.ManagedVM, m.ManagedResource)) and (
                        row in db.new or attrs.provider_id.history.has_changes()):
                    if not reference_visible(db, 'provider', row.provider_id, expected):
                        fail(404, 'PROVIDER_NOT_FOUND', 'Provider is not assigned to this project')
                if isinstance(row, m.Deployment) and (row in db.new or attrs.credentials_id.history.has_changes()):
                    if not reference_visible(db, 'credential', row.credentials_id, expected):
                        fail(404, 'CREDENTIAL_NOT_FOUND', 'Credential is not assigned to this project')
        if row in db.new and isinstance(row, (m.User, m.Provider, m.Credential)):
            db.info.setdefault('_new_scope_references', []).append(row)


@event.listens_for(ScopedSession, 'after_flush_postexec')
def default_references(db, _context):
    new_rows = db.info.pop('_new_scope_references', [])
    touched = db.info.pop('_touch_scope_projects', set())
    scope = db.info.get('resource_scope', DEFAULT_SCOPE)
    # Insert assignments in this flush transaction, not a later ORM flush: a
    # caller may create a VM immediately after flushing its new provider.
    # Registration runs only at creation, never during login. Revoked memberships
    # and allowlist entries cannot be silently recreated by authentication.
    for row in new_rows:
        if isinstance(row, m.User):
            connection = db.connection()
            connection.execute(TenantMembership.__table__.insert().values(
                tenant_id=DEFAULT_TENANT_ID, user_id=row.id, status='active'))
            connection.execute(ProjectMembership.__table__.insert().values(
                tenant_id=DEFAULT_TENANT_ID, project_id=DEFAULT_PROJECT_ID, user_id=row.id, status='active'))
        elif isinstance(row, m.Credential):
            touched.add(scope.project_id)
            db.connection().execute(ProjectCredentialAccess.__table__.insert().values(
                tenant_id=scope.tenant_id, project_id=scope.project_id, credential_id=row.id))
        elif isinstance(row, m.Provider):
            touched.add(scope.project_id)
            db.connection().execute(ProjectProviderAccess.__table__.insert().values(
                tenant_id=scope.tenant_id, project_id=scope.project_id, provider_id=row.id))

    if touched:
        db.execute(update(Project).where(Project.id.in_(touched)).values(
            version=Project.version + 1, updated_at=m.now()).execution_options(synchronize_session='fetch'))


@event.listens_for(ScopedSession, 'after_soft_rollback')
def discard_uncommitted_references(db, _previous_transaction):
    db.info.pop('_new_scope_references', None)
    db.info.pop('_touch_scope_projects', None)
