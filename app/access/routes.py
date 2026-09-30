from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import and_, func, or_, select

from app.access import service
from app.access.schemas import OrganizationAPMIDCreate, OrganizationAPMIDUpdate, ProjectAPMIDRestrictionInput
from app.database import get_db
from app.iam import routes as iam_routes
from app.iam.models import Group, GroupMember, RoleAssignment
from app.iam.schemas import AssignmentCreate, AssignmentUpdate, AuthorizationInput
from app.iam.service import ALL_PERMISSIONS, authorize, is_system_administrator, normalize_scope
from app.models import Role, Token, User
from app.projects.models import Project
from app.security.core import audit, authenticate
from app.tenancy.models import Tenant, TenantMembership

router = APIRouter(tags=['access'])


ROLE_LABELS = {
    'Global Administrator': ('Administrator', 'Pełne zarządzanie całą platformą.'),
    'Organization Administrator': ('Administrator', 'Pełne zarządzanie w wybranej organizacji.'),
    'Project Administrator': ('Administrator', 'Pełne zarządzanie w wybranym projekcie.'),
    'Project Operator': ('Operator', 'Codzienne operacje na zasobach bez administracji IAM.'),
    'Project Viewer': ('Podgląd', 'Tylko odczyt.'),
    'Organization Auditor': ('Audytor', 'Odczyt, audyt i wyjaśnienie decyzji dostępu.'),
    'Self Service User': ('Self Service', 'Uruchamianie dozwolonych produktów i blueprintów.'),
}


def _public_binding(value):
    if not isinstance(value, dict):
        return value
    result = dict(value)
    if 'tenant_id' in result:
        tenant_id = result.pop('tenant_id')
        result.setdefault('organization_id', tenant_id)
    scope = result.get('scope')
    if isinstance(scope, dict):
        result['scope'] = _public_scope(scope)
    return result


def _public_scope(scope: dict) -> dict:
    result = dict(scope)
    if 'tenant_id' in result:
        tenant_id = result.pop('tenant_id')
        result.setdefault('organization_id', tenant_id)
    return result


def _allowed(db, actor, action: str, scope: dict) -> bool:
    if action not in ALL_PERMISSIONS:
        return False
    return authorize(db, actor, action, scope=scope).allowed


def _require_any_scope(db, actor, actions: tuple[str, ...], scope: dict, *, write=False):
    if is_system_administrator(db, actor):
        return
    for action in actions:
        if action not in ALL_PERMISSIONS:
            continue
        decision = authorize(db, actor, action, scope=scope, write=write)
        if decision.allowed:
            return
    raise HTTPException(403, {'error': 'permission_denied'})


def _organization(db, organization_id: str) -> Tenant:
    row = db.scalar(select(Tenant).where(
        Tenant.id == str(organization_id), Tenant.deleted_at.is_(None),
    ))
    if row is None:
        raise HTTPException(404, {'error': 'organization_not_found'})
    return row


def _organization_read(db, actor, organization_id: str):
    scope = {'scope_type': 'ORGANIZATION', 'scope_id': str(organization_id), 'organization_id': str(organization_id)}
    _require_any_scope(db, actor, ('organizations.read', 'tenants.read', 'iam.assign'), scope)
    return scope


def _organization_write(db, actor, organization_id: str):
    scope = {'scope_type': 'ORGANIZATION', 'scope_id': str(organization_id), 'organization_id': str(organization_id)}
    _require_any_scope(
        db, actor,
        ('organizations.update', 'tenants.update', 'iam.binding.create', 'iam.assign'),
        scope, write=True,
    )
    return scope


@router.get('/access/organizations')
def access_organizations(
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    actor=Depends(authenticate),
    db=Depends(get_db, scope='function'),
):
    candidates = db.scalars(
        select(Tenant).where(Tenant.deleted_at.is_(None)).order_by(Tenant.name, Tenant.id)
    ).all()
    visible = []
    admin = is_system_administrator(db, actor)
    for row in candidates:
        scope = {'scope_type': 'ORGANIZATION', 'scope_id': row.id, 'organization_id': row.id}
        if admin or any(_allowed(db, actor, action, scope) for action in ('organizations.read', 'tenants.read', 'iam.assign')):
            visible.append(row)
    page = visible[offset:offset + limit]
    return {
        'items': [{'id': row.id, 'name': row.name, 'slug': row.slug, 'status': row.status} for row in page],
        'total': len(visible), 'limit': limit, 'offset': offset,
    }


@router.get('/access/organizations/{organization_id}/projects')
def access_projects(
    organization_id: str,
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    actor=Depends(authenticate),
    db=Depends(get_db, scope='function'),
):
    _organization(db, organization_id)
    rows = db.scalars(select(Project).where(
        Project.tenant_id == str(organization_id), Project.deleted_at.is_(None),
    ).order_by(Project.name, Project.id)).all()
    admin = is_system_administrator(db, actor)
    visible = []
    for row in rows:
        scope = {
            'scope_type': 'PROJECT', 'scope_id': row.id,
            'organization_id': str(organization_id), 'project_id': row.id,
        }
        if admin or any(_allowed(db, actor, action, scope) for action in ('projects.read', 'projects.use', 'iam.assign')):
            visible.append(row)
    page = visible[offset:offset + limit]
    return {
        'items': [{
            'id': row.id, 'organization_id': row.tenant_id, 'name': row.name,
            'slug': row.slug, 'status': row.status,
        } for row in page],
        'total': len(visible), 'limit': limit, 'offset': offset,
    }


@router.get('/access/subjects')
def access_subjects(
    request: Request,
    type: str = Query('USER'),
    organization_id: str | None = None,
    project_id: str | None = None,
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    actor=Depends(authenticate),
    db=Depends(get_db, scope='function'),
):
    kind = str(type or 'USER').upper()
    if kind not in {'USER', 'GROUP', 'SERVICE_ACCOUNT', 'API_TOKEN'}:
        raise HTTPException(422, {'error': 'unsupported_subject_type'})
    admin = is_system_administrator(db, actor)

    if organization_id:
        _organization_read(db, actor, organization_id)

    if kind in {'USER', 'SERVICE_ACCOUNT'}:
        filters = [User.is_active.is_(True), User.is_service_account.is_(kind == 'SERVICE_ACCOUNT')]
        query = select(User).where(*filters)
        if project_id:
            project = db.scalar(select(Project).where(
                Project.id == str(project_id),
                Project.tenant_id == str(organization_id) if organization_id else Project.id == str(project_id),
            ))
            if project is None:
                raise HTTPException(404, {'error': 'resource_not_found'})
            from app.projects.models import ProjectMembership
            query = query.join(ProjectMembership, ProjectMembership.user_id == User.id).where(
                ProjectMembership.project_id == str(project_id),
                ProjectMembership.status == 'active',
            )
        elif organization_id:
            query = query.join(TenantMembership, TenantMembership.user_id == User.id).where(
                TenantMembership.tenant_id == str(organization_id),
                TenantMembership.status == 'active',
            )
        elif not admin:
            query = query.where(User.id == actor.user_id)
        rows = db.scalars(query.order_by(User.username, User.id).offset(offset).limit(limit)).all()
        total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
        return {
            'items': [iam_routes._subject_user_public(row) for row in rows],
            'total': int(total), 'limit': limit, 'offset': offset,
        }

    if kind == 'GROUP':
        query = select(Group).where(Group.enabled.is_(True))
        if organization_id and not admin:
            project_ids = list(db.scalars(
                select(Project.id).where(Project.tenant_id == str(organization_id))
            ))
            clauses = [
                Group.system_key.like(f'org:{organization_id}:%'),
                Group.system_key.like(f'apmid:{organization_id}:%'),
            ]
            clauses.extend(Group.system_key.like(f'project:{project_id}:%') for project_id in project_ids)
            query = query.where(or_(*clauses))
        elif not organization_id and not admin:
            query = query.where(False)
        total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
        rows = db.scalars(query.order_by(Group.name, Group.id).offset(offset).limit(limit)).all()
        return {
            'items': [iam_routes._group_public(db, row) for row in rows],
            'total': int(total), 'limit': limit, 'offset': offset,
        }

    if not admin:
        raise HTTPException(403, {'error': 'permission_denied'})
    query = select(Token).where(Token.kind == 'api', Token.revoked_at.is_(None))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(Token.name, Token.id).offset(offset).limit(limit)).all()
    return {
        'items': [{'id': row.id, 'name': row.name, 'token_prefix': row.token_prefix, 'user_id': row.user_id} for row in rows],
        'total': int(total), 'limit': limit, 'offset': offset,
    }


@router.get('/access/subjects/{subject_type}/{subject_id}')
def access_subject(
    subject_type: str, subject_id: str, request: Request,
    actor=Depends(authenticate), db=Depends(get_db, scope='function'),
):
    kind = subject_type.upper()
    result = access_subjects(
        request=request, type=kind, limit=500, offset=0, actor=actor, db=db,
    )
    for item in result['items']:
        if str(item.get('id')) == str(subject_id):
            return item
    raise HTTPException(404, {'error': 'subject_not_found'})


@router.get('/access/subjects/{subject_type}/{subject_id}/effective')
def access_subject_effective(
    subject_type: str, subject_id: str,
    scope_type: str = Query('GLOBAL'),
    scope_id: str | None = None,
    organization_id: str | None = None,
    project_id: str | None = None,
    apmid: str | None = None,
    environment: str | None = None,
    actor=Depends(authenticate), db=Depends(get_db, scope='function'),
):
    kind = subject_type.upper()
    if kind in {'USER', 'SERVICE_ACCOUNT'}:
        try:
            user_id = int(subject_id)
        except ValueError as exc:
            raise HTTPException(422, {'error': 'invalid_subject_id'}) from exc
        result = iam_routes.user_effective_access(
            user_id=user_id, scope_type=scope_type, scope_id=scope_id,
            tenant_id=organization_id, project_id=project_id, apmid=apmid,
            environment=environment, actor=actor, db=db,
        )
        result['scope'] = _public_scope(result['scope'])
        result['assignments'] = [_public_binding(row) for row in result['assignments']]
        return result

    _require_any_scope(db, actor, ('authorization.explain', 'iam.binding.read'), {'scope_type': 'GLOBAL'})
    rows = db.scalars(select(RoleAssignment).where(
        RoleAssignment.subject_type == kind,
        RoleAssignment.subject_id == str(subject_id),
    ).order_by(RoleAssignment.created_at.desc())).all()
    return {
        'subject_type': kind, 'subject_id': subject_id,
        'scope': _public_scope(normalize_scope({
            'scope_type': scope_type, 'scope_id': scope_id,
            'organization_id': organization_id, 'project_id': project_id,
            'apmid': apmid, 'environment': environment,
        })),
        'assignments': [_public_binding(iam_routes._assignment_public(db, row)) for row in rows],
        'policy_evaluated': False,
    }


@router.get('/access/roles')
def access_roles(
    request: Request,
    assignable: bool = True,
    scope_type: str = Query('GLOBAL'),
    scope_id: str | None = None,
    organization_id: str | None = None,
    project_id: str | None = None,
    apmid: str | None = None,
    environment: str | None = None,
    limit: int = Query(200, ge=1, le=200),
    offset: int = Query(0, ge=0),
    actor=Depends(authenticate), db=Depends(get_db, scope='function'),
):
    page = iam_routes.roles(
        request=request, limit=limit, offset=offset, assignable=assignable,
        scope_type=scope_type, scope_id=scope_id, tenant_id=organization_id,
        project_id=project_id, apmid=apmid, environment=environment,
        actor=actor, db=db,
    )
    items = []
    for row in page['items']:
        label, description = ROLE_LABELS.get(row['name'], (row['name'], row.get('description') or 'Rola niestandardowa.'))
        items.append({
            'id': row['id'], 'name': row['name'], 'label': label,
            'description': description, 'system_role': row.get('system_role', False),
        })
    return {**page, 'items': items}


@router.get('/access/groups')
def access_groups(
    organization_id: str | None = None,
    limit: int = Query(200, ge=1, le=200),
    offset: int = Query(0, ge=0),
    actor=Depends(authenticate), db=Depends(get_db, scope='function'),
):
    if organization_id:
        _organization_read(db, actor, organization_id)
    elif not is_system_administrator(db, actor):
        raise HTTPException(403, {'error': 'organization_required'})
    query = select(Group).where(Group.enabled.is_(True))
    if organization_id:
        project_ids = list(db.scalars(select(Project.id).where(Project.tenant_id == str(organization_id))))
        clauses = [
            Group.system_key.like(f'org:{organization_id}:%'),
            Group.system_key.like(f'apmid:{organization_id}:%'),
        ]
        clauses.extend(Group.system_key.like(f'project:{project_id}:%') for project_id in project_ids)
        query = query.where(or_(*clauses))
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    rows = db.scalars(query.order_by(Group.name, Group.id).offset(offset).limit(limit)).all()
    return {
        'items': [iam_routes._group_public(db, row) for row in rows],
        'total': int(total), 'limit': limit, 'offset': offset,
    }


@router.post('/access/bindings', status_code=201)
def create_binding(
    data: AssignmentCreate, request: Request,
    actor=Depends(authenticate), db=Depends(get_db, scope='function'),
):
    return _public_binding(iam_routes.create_assignment(data=data, request=request, actor=actor, db=db))


@router.patch('/access/bindings/{binding_id}')
def update_binding(
    binding_id: str, data: AssignmentUpdate, request: Request,
    actor=Depends(authenticate), db=Depends(get_db, scope='function'),
):
    return _public_binding(iam_routes.update_assignment(
        assignment_id=binding_id, data=data, request=request, actor=actor, db=db,
    ))


@router.delete('/access/bindings/{binding_id}')
def delete_binding(
    binding_id: str, request: Request,
    actor=Depends(authenticate), db=Depends(get_db, scope='function'),
):
    return iam_routes.delete_assignment(
        assignment_id=binding_id, request=request, actor=actor, db=db,
    )


@router.post('/access/evaluate')
def evaluate_access(
    data: AuthorizationInput, request: Request,
    actor=Depends(authenticate), db=Depends(get_db, scope='function'),
):
    result = iam_routes.authorization_check(data=data, request=request, actor=actor, db=db)
    return _public_binding(result)


@router.get('/access/organizations/{organization_id}/apmids')
@router.get('/organizations/{organization_id}/apmids')
def organization_apmids(
    organization_id: str, include_disabled: bool = False,
    actor=Depends(authenticate), db=Depends(get_db, scope='function'),
):
    _organization_read(db, actor, organization_id)
    _organization(db, organization_id)
    rows = service.list_apmids(db, organization_id, include_disabled=include_disabled)
    return {'items': [service.apmid_public(row) for row in rows], 'total': len(rows)}


@router.post('/organizations/{organization_id}/apmids', status_code=201)
def create_organization_apmid(
    organization_id: str, data: OrganizationAPMIDCreate, request: Request,
    actor=Depends(authenticate), db=Depends(get_db, scope='function'),
):
    _organization_write(db, actor, organization_id)
    organization = _organization(db, organization_id)
    row = service.create_apmid(db, organization, data, created_by=actor.user_id)
    audit(db, request, 'apmid.created', 'organization_apmids', row.id)
    return service.apmid_public(row)


@router.patch('/organizations/{organization_id}/apmids/{apmid}')
def update_organization_apmid(
    organization_id: str, apmid: str, data: OrganizationAPMIDUpdate, request: Request,
    actor=Depends(authenticate), db=Depends(get_db, scope='function'),
):
    _organization_write(db, actor, organization_id)
    organization = _organization(db, organization_id)
    row = service.update_apmid(db, organization, apmid, data)
    audit(db, request, 'apmid.updated', 'organization_apmids', row.id)
    return service.apmid_public(row)


@router.delete('/organizations/{organization_id}/apmids/{apmid}')
def delete_organization_apmid(
    organization_id: str, apmid: str, request: Request,
    actor=Depends(authenticate), db=Depends(get_db, scope='function'),
):
    _organization_write(db, actor, organization_id)
    organization = _organization(db, organization_id)
    row = service.get_apmid(db, organization_id, apmid)
    row_id = row.id
    service.delete_apmid(db, organization, apmid)
    audit(db, request, 'apmid.deleted', 'organization_apmids', row_id)
    return {'deleted': True}


@router.put('/access/organizations/{organization_id}/projects/{project_id}/apmids')
def set_project_apmids(
    organization_id: str, project_id: str, data: ProjectAPMIDRestrictionInput, request: Request,
    actor=Depends(authenticate), db=Depends(get_db, scope='function'),
):
    scope = {
        'scope_type': 'PROJECT', 'scope_id': str(project_id),
        'organization_id': str(organization_id), 'project_id': str(project_id),
    }
    _require_any_scope(db, actor, ('projects.update', 'projects.admin', 'iam.assign'), scope, write=True)
    project = db.scalar(select(Project).where(
        Project.id == str(project_id), Project.tenant_id == str(organization_id), Project.deleted_at.is_(None),
    ))
    if project is None:
        raise HTTPException(404, {'error': 'resource_not_found'})
    allowed = data.apmids
    if allowed is not None:
        active = set(service.effective_project_apmids(db, organization_id))
        invalid = sorted(set(allowed) - active)
        if invalid:
            raise HTTPException(422, {'error': 'invalid_apmids', 'apmids': invalid})
    project.allowed_apmids = allowed
    db.flush()
    audit(db, request, 'project.apmids.updated', 'projects', project.id)
    return {
        'organization_id': str(organization_id), 'project_id': str(project_id),
        'apmids': project.allowed_apmids,
        'effective_apmids': service.effective_project_apmids(db, organization_id, project_id),
    }
