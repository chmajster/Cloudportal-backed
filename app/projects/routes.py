"""Thin project HTTP adapters using the existing transaction/audit machinery."""
from uuid import UUID
from fastapi import APIRouter, Depends, Query, Request
from app.api.common import Limit, Offset, idempotent
from app.api.outputs import DeletedOutput
from app.database import get_db
from app.security.core import audit, authenticate
from app.projects import service
from app.projects.schemas import (ProjectCreate, ProjectUpdate, ProjectOutput, ProjectPage,
    ProjectPermissions, ProjectMemberOutput, ProjectMemberPage, ContextInput, ContextOutput, EligibleUserPage)
from app.tenancy.authorization import Principal
from app.tenancy.schemas import (MemberCreate, MemberRoles, MemberUpdate, MemberStatus,
                                 TenantStatus, RolePage, TenantAuditPage, TenantPage)

router = APIRouter(tags=['projects'])


@router.get('/projects', response_model=ProjectPage)
def projects(limit: Limit = 100, offset: Offset = 0, status: TenantStatus | None = None,
             tenant_id: UUID | None = None, actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    return service.project_list(db, Principal.from_token(actor), limit=limit, offset=offset,
                                status=status, tenant_id=tenant_id)


@router.get('/tenants/{tenant_id}/projects', response_model=ProjectPage)
def tenant_projects(tenant_id: UUID, limit: Limit = 100, offset: Offset = 0,
                    status: TenantStatus | None = None, actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    return service.project_list(db, Principal.from_token(actor), limit=limit, offset=offset,
                                status=status, tenant_id=tenant_id)


@router.post('/projects', status_code=201, response_model=ProjectOutput)
def create_project(data: ProjectCreate, request: Request, actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    principal = Principal.from_token(actor)
    service.authorize_creation(db, principal, data.tenant_id)
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


@router.get('/projects/{project_id}/members', response_model=ProjectMemberPage)
def members(project_id: UUID, limit: Limit = 100, offset: Offset = 0, status: MemberStatus | None = None,
            actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    return service.member_list(db, Principal.from_token(actor), project_id, limit=limit, offset=offset, status=status)


@router.get('/projects/{project_id}/eligible-members', response_model=EligibleUserPage)
def eligible(project_id: UUID, limit: Limit = 100, offset: Offset = 0,
             actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    return service.eligible_members(db, Principal.from_token(actor), project_id, limit=limit, offset=offset)


@router.post('/projects/{project_id}/members', status_code=201, response_model=ProjectMemberOutput)
def create_member(project_id: UUID, data: MemberCreate, request: Request,
                  actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    principal = Principal.from_token(actor)
    service.authorize_member_creation(db, principal, project_id, data)
    def create():
        result = service.member_create(db, principal, project_id, data)
        audit(db, request, 'project.member.added', 'project_memberships', f'{project_id}:{data.user_id}')
        return result
    return idempotent(db, request, actor, data.model_dump(), create)


@router.put('/projects/{project_id}/members/{user_id}', response_model=ProjectMemberOutput)
def update_member(project_id: UUID, user_id: int, data: MemberUpdate, request: Request,
                  actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    result = service.member_update(db, Principal.from_token(actor), project_id, user_id, data)
    audit(db, request, 'project.member.updated', 'project_memberships', f'{project_id}:{user_id}')
    return result


@router.put('/projects/{project_id}/members/{user_id}/roles', response_model=ProjectMemberOutput)
def set_roles(project_id: UUID, user_id: int, data: MemberRoles, request: Request,
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


@router.get('/projects/{project_id}/audit', response_model=TenantAuditPage)
def history(project_id: UUID, limit: Limit = 100, offset: Offset = 0,
            actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    return service.audit_list(db, Principal.from_token(actor), project_id, limit=limit, offset=offset)


@router.get('/project-context', response_model=ContextOutput)
def context(actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    return service.context_get(db, Principal.from_token(actor))


@router.put('/project-context', response_model=ContextOutput)
def select_context(data: ContextInput, request: Request, actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    result = service.context_set(db, Principal.from_token(actor), data)
    audit(db, request, 'project.context.selected', 'projects', data.project_id)
    return result


@router.delete('/project-context', response_model=ContextOutput)
def clear_context(request: Request, expected_version: int | None = Query(default=None, ge=1),
                  actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    result = service.context_clear(db, Principal.from_token(actor), expected_version)
    audit(db, request, 'project.context.cleared', 'users', actor.user_id)
    return result


@router.get('/project-creation-tenants', response_model=TenantPage)
def creation_tenants(limit: Limit = 100, offset: Offset = 0,
                     actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    return service.creation_tenants(db, Principal.from_token(actor), limit=limit, offset=offset)
