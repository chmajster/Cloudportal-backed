"""HTTP contracts only; state changes use the existing DB/audit/event transaction."""
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.api.common import Limit, Offset, idempotent
from app.api.outputs import DeletedOutput
from app.database import get_db
from app.security.core import audit, authenticate
from app.projects import service
from app.tenancy.authorization import Principal
from app.projects.authorization import authorize_creation, resolve_context
from app.tenancy.schemas import MemberCreate, MemberRoles, MemberUpdate, RolePage, TenantStatus
from app.projects.schemas import (ProjectMemberOutput as MemberOutput, ProjectMemberPage as MemberPage,
    ProjectAuditPage, ProjectCreate, ProjectOutput, ProjectPage, ProjectPermissions, ProjectUpdate,
    EligibleMemberPage, ContextInput, ContextOutput, CreationScopePage)

router = APIRouter(tags=['projects'])


@router.get('/projects', response_model=ProjectPage)
def projects(limit: Limit = 100, offset: Offset = 0, status: TenantStatus | None = None, tenant_id: UUID | None = None,
            actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    return service.project_list(db, Principal.from_token(actor), tenant_id=tenant_id, limit=limit, offset=offset, status=status)


@router.post('/projects', status_code=201, response_model=ProjectOutput)
def create_project(data: ProjectCreate, request: Request, actor=Depends(authenticate),
                  db=Depends(get_db, scope='function')):
    principal = Principal.from_token(actor)
    # Reauthorize before idempotency replay too; a cached success is not a grant.
    authorize_creation(db, principal, data.tenant_id)
    def create():
        result = service.project_create(db, principal, data)
        audit(db, request, 'project.created', 'projects', result['id'])
        return result
    return idempotent(db, request, actor, data.model_dump(), create)


@router.get('/projects/{project_id}', response_model=ProjectOutput)
def project(project_id: UUID, actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    return service.project_get(db, Principal.from_token(actor), project_id)


@router.get('/projects/{project_id}/permissions', response_model=ProjectPermissions)
def permissions(project_id: UUID, actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    return service.project_permissions(db, Principal.from_token(actor), project_id)


@router.put('/projects/{project_id}', response_model=ProjectOutput)
def update_project(project_id: UUID, data: ProjectUpdate, request: Request,
                  actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    result = service.project_update(db, Principal.from_token(actor), project_id, data)
    audit(db, request, 'project.updated', 'projects', project_id)
    return result


@router.delete('/projects/{project_id}', response_model=DeletedOutput)
def delete_project(project_id: UUID, request: Request, expected_version: int = Query(ge=1),
                  actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    result = service.project_delete(db, Principal.from_token(actor), project_id, expected_version)
    audit(db, request, 'project.deleted', 'projects', project_id)
    return result


@router.get('/projects/{project_id}/members', response_model=MemberPage)
def members(project_id: UUID, limit: Limit = 100, offset: Offset = 0,
            actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    return service.member_list(db, Principal.from_token(actor), project_id, limit=limit, offset=offset)


@router.post('/projects/{project_id}/members', status_code=201, response_model=MemberOutput)
def create_member(project_id: UUID, data: MemberCreate, request: Request,
                  actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    principal = Principal.from_token(actor)
    # Directory and role-grant checks also apply to idempotency replay.
    service.authorize_member_creation(db, principal, project_id, data)
    def create():
        result = service.member_create(db, principal, project_id, data)
        audit(db, request, 'project.member.added', 'project_memberships', f'{project_id}:{data.user_id}')
        return result
    return idempotent(db, request, actor, data.model_dump(), create)


@router.put('/projects/{project_id}/members/{user_id}', response_model=MemberOutput)
def update_member(project_id: UUID, user_id: int, data: MemberUpdate, request: Request,
                  actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    result = service.member_update(db, Principal.from_token(actor), project_id, user_id, data)
    audit(db, request, 'project.member.updated', 'project_memberships', f'{project_id}:{user_id}')
    return result


@router.put('/projects/{project_id}/members/{user_id}/roles', response_model=MemberOutput)
def set_member_roles(project_id: UUID, user_id: int, data: MemberRoles, request: Request,
                     actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    result = service.member_roles(db, Principal.from_token(actor), project_id, user_id, data)
    audit(db, request, 'project.member.roles.changed', 'project_memberships', f'{project_id}:{user_id}')
    return result


@router.delete('/projects/{project_id}/members/{user_id}', response_model=DeletedOutput)
def delete_member(project_id: UUID, user_id: int, request: Request, expected_version: int = Query(ge=1),
                  actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    result = service.member_delete(db, Principal.from_token(actor), project_id, user_id, expected_version)
    audit(db, request, 'project.member.removed', 'project_memberships', f'{project_id}:{user_id}')
    return result


@router.get('/projects/{project_id}/assignable-roles', response_model=RolePage)
def roles(project_id: UUID, limit: Limit = 100, offset: Offset = 0,
          actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    return service.assignable_roles(db, Principal.from_token(actor), project_id, limit=limit, offset=offset)


@router.get('/projects/{project_id}/audit', response_model=ProjectAuditPage)
def history(project_id: UUID, limit: Limit = 100, offset: Offset = 0,
            actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    return service.audit_list(db, Principal.from_token(actor), project_id, limit=limit, offset=offset)


@router.get('/tenants/{tenant_id}/projects', response_model=ProjectPage)
def tenant_projects(tenant_id: UUID, limit: Limit = 100, offset: Offset = 0,
                    actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    return service.project_list(db, Principal.from_token(actor), tenant_id=tenant_id, limit=limit, offset=offset)


@router.get('/projects/{project_id}/eligible-members', response_model=EligibleMemberPage)
def eligible_members(project_id: UUID, limit: Limit = 100, offset: Offset = 0,
                     actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    return service.eligible_members(db, Principal.from_token(actor), project_id, limit=limit, offset=offset)


@router.post('/project-context/resolve', response_model=ContextOutput)
def project_context(data: ContextInput, actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    context = resolve_context(db, Principal.from_token(actor), data.project_id, tenant_id=data.tenant_id, permission='projects.read')
    return {'tenant_id': context.tenant_id, 'project_id': context.project_id,
            'actor_id': context.actor_id, 'permissions': sorted(context.permissions)}



@router.get('/project-context/creation-scopes', response_model=CreationScopePage)
def creation_scopes(limit: Limit = 100, offset: Offset = 0,
                    actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    return service.creation_scopes(db, Principal.from_token(actor), limit=limit, offset=offset)
