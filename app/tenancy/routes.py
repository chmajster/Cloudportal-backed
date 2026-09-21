"""HTTP contracts only; state changes use the existing DB/audit/event transaction."""
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.api.common import Limit, Offset, idempotent
from app.api.outputs import DeletedOutput
from app.database import get_db
from app.security.core import audit, authenticate
from app.tenancy import service
from app.tenancy.authorization import Principal, lock_authorization, require_global
from app.tenancy.schemas import (MemberCreate, MemberOutput, MemberPage, MemberRoles, MemberStatus,
                                MemberUpdate, RolePage, TenantAuditPage, TenantCreate, TenantOutput,
                                TenantPage, TenantPermissions, TenantStatus, TenantUpdate)

router = APIRouter(tags=['tenancy'])


@router.get('/tenants', response_model=TenantPage)
def tenants(limit: Limit = 100, offset: Offset = 0, status: TenantStatus | None = None,
            actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    return service.tenant_list(db, Principal.from_token(actor), limit=limit, offset=offset, status=status)


@router.post('/tenants', status_code=201, response_model=TenantOutput)
def create_tenant(data: TenantCreate, request: Request, actor=Depends(authenticate),
                  db=Depends(get_db, scope='function')):
    principal = Principal.from_token(actor)
    # Reauthorize before idempotency replay too; a cached success is not a grant.
    lock_authorization(db)
    require_global(db, principal, 'tenants.create')
    def create():
        result = service.tenant_create(db, principal, data)
        audit(db, request, 'tenant.created', 'tenants', result['id'])
        return result
    return idempotent(db, request, actor, data.model_dump(), create)


@router.get('/tenants/{tenant_id}', response_model=TenantOutput)
def tenant(tenant_id: UUID, actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    return service.tenant_get(db, Principal.from_token(actor), tenant_id)


@router.get('/tenants/{tenant_id}/permissions', response_model=TenantPermissions)
def permissions(tenant_id: UUID, actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    return service.tenant_permissions(db, Principal.from_token(actor), tenant_id)


@router.put('/tenants/{tenant_id}', response_model=TenantOutput)
def update_tenant(tenant_id: UUID, data: TenantUpdate, request: Request,
                  actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    result = service.tenant_update(db, Principal.from_token(actor), tenant_id, data)
    audit(db, request, 'tenant.updated', 'tenants', tenant_id)
    return result


@router.delete('/tenants/{tenant_id}', response_model=DeletedOutput)
def delete_tenant(tenant_id: UUID, request: Request, expected_version: int = Query(ge=1),
                  actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    result = service.tenant_delete(db, Principal.from_token(actor), tenant_id, expected_version)
    audit(db, request, 'tenant.deleted', 'tenants', tenant_id)
    return result


@router.get('/tenants/{tenant_id}/members', response_model=MemberPage)
def members(tenant_id: UUID, limit: Limit = 100, offset: Offset = 0, status: MemberStatus | None = None,
            actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    return service.member_list(db, Principal.from_token(actor), tenant_id, limit=limit, offset=offset, status=status)


@router.post('/tenants/{tenant_id}/members', status_code=201, response_model=MemberOutput)
def create_member(tenant_id: UUID, data: MemberCreate, request: Request,
                  actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    principal = Principal.from_token(actor)
    # Directory and role-grant checks also apply to idempotency replay.
    service.authorize_member_creation(db, principal, tenant_id, data)
    def create():
        result = service.member_create(db, principal, tenant_id, data)
        audit(db, request, 'tenant.member.added', 'tenant_memberships', f'{tenant_id}:{data.user_id}')
        return result
    return idempotent(db, request, actor, data.model_dump(), create)


@router.put('/tenants/{tenant_id}/members/{user_id}', response_model=MemberOutput)
def update_member(tenant_id: UUID, user_id: int, data: MemberUpdate, request: Request,
                  actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    result = service.member_update(db, Principal.from_token(actor), tenant_id, user_id, data)
    audit(db, request, 'tenant.member.updated', 'tenant_memberships', f'{tenant_id}:{user_id}')
    return result


@router.put('/tenants/{tenant_id}/members/{user_id}/roles', response_model=MemberOutput)
def set_member_roles(tenant_id: UUID, user_id: int, data: MemberRoles, request: Request,
                     actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    result = service.member_roles(db, Principal.from_token(actor), tenant_id, user_id, data)
    audit(db, request, 'tenant.member.roles.changed', 'tenant_memberships', f'{tenant_id}:{user_id}')
    return result


@router.delete('/tenants/{tenant_id}/members/{user_id}', response_model=DeletedOutput)
def delete_member(tenant_id: UUID, user_id: int, request: Request, expected_version: int = Query(ge=1),
                  actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    result = service.member_delete(db, Principal.from_token(actor), tenant_id, user_id, expected_version)
    audit(db, request, 'tenant.member.removed', 'tenant_memberships', f'{tenant_id}:{user_id}')
    return result


@router.get('/tenants/{tenant_id}/assignable-roles', response_model=RolePage)
def roles(tenant_id: UUID, limit: Limit = 100, offset: Offset = 0,
          actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    return service.assignable_roles(db, Principal.from_token(actor), tenant_id, limit=limit, offset=offset)


@router.get('/tenants/{tenant_id}/audit', response_model=TenantAuditPage)
def history(tenant_id: UUID, limit: Limit = 100, offset: Offset = 0,
            actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    return service.audit_list(db, Principal.from_token(actor), tenant_id, limit=limit, offset=offset)
