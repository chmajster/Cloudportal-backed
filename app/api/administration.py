import secrets
from datetime import timedelta, timezone
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, update
from app.api.common import find, idempotent, paginate, public
from app.api.outputs import (Items, UserOutput, RoleOutput, TokenOutput, IssuedTokenOutput,
                             IssuedResetOutput, DeletedOutput, AuditOutput)
from app.api.schemas import AssignRoles, RoleInput, TokenInput, UserCreate, UserUpdate
from app.auth.routes import user_public
from app.database import get_db
from app.models import Audit, PasswordReset, Role, Token, User, UserRole, now
from app.rbac.service import ALL_PERMISSIONS, ensure_admin_remains, governance_lock, permissions_from_names
from app.security.core import audit, digest, effective_permissions, issue_token, password_hasher, require, revoke_user

router = APIRouter(tags=['administration'])
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]


def role_public(role):
    return {'id': role.id, 'name': role.name, 'permissions': sorted(p.name for p in role.permissions)}


def token_public(token):
    return public(token, 'id name token_prefix user_id scopes kind created_at expires_at last_used_at revoked_at')


def can_grant(request, permissions):
    if not set(permissions) <= request.state.permissions:
        raise HTTPException(403, 'Cannot grant permissions outside your effective permissions')


@router.get('/users', response_model=Items[UserOutput])
def users(limit: Limit = 100, offset: Offset = 0, actor=Depends(require('users.read')), db=Depends(get_db, scope='function')):
    return {'items': [user_public(u) for u in paginate(db, User, offset, limit)]}


@router.get('/users/{id}', response_model=UserOutput)
def user(id: int, actor=Depends(require('users.read')), db=Depends(get_db, scope='function')):
    return user_public(find(db, User, id))


@router.post('/users', status_code=201, response_model=UserOutput)
def user_create(data: UserCreate, request: Request, actor=Depends(require('users.create')), db=Depends(get_db, scope='function')):
    def create():
        values = data.model_dump(exclude={'password'})
        u = User(**values, password_hash=password_hasher.hash(data.password))
        db.add(u)
        db.flush()
        audit(db, request, 'user.created', 'users', u.id)
        return user_public(u)
    return idempotent(db, request, actor, data.model_dump(), create)


@router.put('/users/{id}', response_model=UserOutput)
def user_update(id: int, data: UserUpdate, request: Request, actor=Depends(require('users.update')), db=Depends(get_db, scope='function')):
    u = find(db, User, id)
    can_grant(request, effective_permissions(u))
    for key, value in data.model_dump(exclude_unset=True).items():
        if value is None:
            raise HTTPException(422, 'User fields cannot be null')
        setattr(u, key, value.lower() if key == 'email' else value)
    audit(db, request, 'user.updated', 'users', u.id)
    db.flush()
    return user_public(u)


def user_action(db, request, id, action):
    governance_lock(db)
    u = find(db, User, id)
    can_grant(request, effective_permissions(u))
    if action == 'enable':
        u.is_active = True
    elif action == 'disable':
        u.is_active = False
        revoke_user(db, id)
    elif action == 'unlock':
        u.is_locked = False
        u.locked_until = None
        u.failed_login_attempts = 0
    ensure_admin_remains(db)
    audit(db, request, 'user.' + action, 'users', id)
    return user_public(u)


@router.post('/users/{id}/enable', response_model=UserOutput)
def enable(id: int, request: Request, actor=Depends(require('users.update')), db=Depends(get_db, scope='function')):
    return user_action(db, request, id, 'enable')


@router.post('/users/{id}/disable', response_model=UserOutput)
def disable(id: int, request: Request, actor=Depends(require('users.update')), db=Depends(get_db, scope='function')):
    return user_action(db, request, id, 'disable')


@router.post('/users/{id}/unlock', response_model=UserOutput)
def unlock(id: int, request: Request, actor=Depends(require('users.update')), db=Depends(get_db, scope='function')):
    return user_action(db, request, id, 'unlock')


@router.delete('/users/{id}', response_model=DeletedOutput)
def delete_user(id: int, request: Request, actor=Depends(require('users.delete')), db=Depends(get_db, scope='function')):
    # Keep the immutable user ID for deployments and audit; remove identity and access.
    governance_lock(db)
    u = find(db, User, id)
    can_grant(request, effective_permissions(u))
    u.is_active = False
    u.is_locked = True
    u.roles = []
    u.username = f'deleted-{u.id}'
    u.email = f'deleted-{u.id}@invalid.example'
    u.first_name = u.last_name = ''
    u.password_hash = password_hasher.hash(secrets.token_urlsafe(64))
    revoke_user(db, id)
    ensure_admin_remains(db)
    audit(db, request, 'user.deleted', 'users', id)
    return {'deleted': True}


@router.post('/users/{id}/reset-password', response_model=IssuedResetOutput, response_model_exclude_unset=True)
def reset_user(id: int, request: Request, actor=Depends(require('users.update')), db=Depends(get_db, scope='function')):
    u = find(db, User, id)
    can_grant(request, effective_permissions(u))
    if u.is_service_account or not u.is_active:
        raise HTTPException(409, 'Password reset requires an active human account')
    def create():
        plain = secrets.token_urlsafe(48)
        db.execute(update(PasswordReset).where(PasswordReset.user_id == id, PasswordReset.consumed_at.is_(None)).values(consumed_at=now()))
        reset = PasswordReset(user_id=id, token_hash=digest(plain), expires_at=now() + timedelta(minutes=15))
        db.add(reset)
        revoke_user(db, id)
        audit(db, request, 'user.password_reset_requested', 'users', id)
        return {'reset_token': plain, 'expires_in': 900, 'user_id': id}
    return idempotent(db, request, actor, {'id': id}, create)


@router.get('/permissions', response_model=Items[str])
def permissions(actor=Depends(require('roles.read'))):
    return {'items': sorted(ALL_PERMISSIONS)}


@router.get('/roles', response_model=Items[RoleOutput])
def roles(limit: Limit = 100, offset: Offset = 0, actor=Depends(require('roles.read')), db=Depends(get_db, scope='function')):
    return {'items': [role_public(r) for r in paginate(db, Role, offset, limit)]}


@router.get('/roles/{id}', response_model=RoleOutput)
def role(id: int, actor=Depends(require('roles.read')), db=Depends(get_db, scope='function')):
    return role_public(find(db, Role, id))


@router.post('/roles', status_code=201, response_model=RoleOutput)
def create_role(data: RoleInput, request: Request, actor=Depends(require('roles.create')), db=Depends(get_db, scope='function')):
    can_grant(request, data.permissions)
    def create():
        r = Role(name=data.name, permissions=permissions_from_names(db, data.permissions))
        db.add(r)
        db.flush()
        audit(db, request, 'role.created', 'roles', r.id)
        return role_public(r)
    return idempotent(db, request, actor, data.model_dump(), create)


@router.put('/roles/{id}', response_model=RoleOutput)
def update_role(id: int, data: RoleInput, request: Request, actor=Depends(require('roles.update')), db=Depends(get_db, scope='function')):
    governance_lock(db)
    r = find(db, Role, id)
    can_grant(request, [p.name for p in r.permissions])
    can_grant(request, data.permissions)
    r.name = data.name
    r.permissions = permissions_from_names(db, data.permissions)
    ensure_admin_remains(db)
    audit(db, request, 'role.updated', 'roles', id)
    return role_public(r)


@router.delete('/roles/{id}', response_model=DeletedOutput)
def delete_role(id: int, request: Request, actor=Depends(require('roles.delete')), db=Depends(get_db, scope='function')):
    governance_lock(db)
    r = find(db, Role, id)
    can_grant(request, [p.name for p in r.permissions])
    if db.scalar(select(UserRole).where(UserRole.role_id == id)):
        raise HTTPException(409, 'Unassign the role before deleting it')
    db.delete(r)
    ensure_admin_remains(db)
    audit(db, request, 'role.deleted', 'roles', id)
    return {'deleted': True}


@router.get('/users/{id}/roles', response_model=Items[RoleOutput])
def user_roles(id: int, actor=Depends(require('roles.read')), db=Depends(get_db, scope='function')):
    return {'items': [role_public(r) for r in find(db, User, id).roles]}


@router.put('/users/{id}/roles', response_model=Items[RoleOutput])
def assign_roles(id: int, data: AssignRoles, request: Request, actor=Depends(require('roles.assign')), db=Depends(get_db, scope='function')):
    governance_lock(db)
    u = find(db, User, id)
    can_grant(request, effective_permissions(u))
    roles = [find(db, Role, rid) for rid in set(data.role_ids)]
    can_grant(request, {p.name for r in [*roles, *u.roles] for p in r.permissions})
    u.roles = roles
    ensure_admin_remains(db)
    audit(db, request, 'role.assigned', 'users', id)
    return {'items': [role_public(r) for r in roles]}


@router.get('/tokens', response_model=Items[TokenOutput])
def tokens(limit: Limit = 100, offset: Offset = 0, actor=Depends(require('tokens.read')), db=Depends(get_db, scope='function')):
    return {'items': [token_public(t) for t in paginate(db, Token, offset, limit, Token.kind == 'api')]}


@router.get('/tokens/{id}', response_model=TokenOutput)
def token(id: int, actor=Depends(require('tokens.read')), db=Depends(get_db, scope='function')):
    t = find(db, Token, id)
    if t.kind != 'api':
        raise HTTPException(404, 'API token not found')
    return token_public(t)


@router.post('/tokens', status_code=201, response_model=IssuedTokenOutput, response_model_exclude_unset=True)
def create_token(data: TokenInput, request: Request, actor=Depends(require('tokens.create')), db=Depends(get_db, scope='function')):
    user_id = data.user_id or actor.user_id
    if user_id != actor.user_id and 'users.update' not in request.state.permissions:
        raise HTTPException(403, 'users.update required for another account')
    user = find(db, User, user_id)
    if not user.is_active or user.is_locked:
        raise HTTPException(409, 'Account unavailable')
    can_grant(request, data.scopes)
    if not set(data.scopes) <= effective_permissions(user):
        raise HTTPException(422, 'Token scopes must be a subset of account permissions')
    seconds = None
    if data.expires_at:
        expiry = data.expires_at.astimezone(timezone.utc).replace(tzinfo=None) if data.expires_at.tzinfo else data.expires_at
        seconds = int((expiry - now()).total_seconds())
        if seconds <= 0:
            raise HTTPException(422, 'Expiration must be in the future')
    def create():
        token, plain = issue_token(db, user, data.name, data.scopes, seconds=seconds)
        audit(db, request, 'token.created', 'tokens', token.id)
        return {**token_public(token), 'token': plain}
    return idempotent(db, request, actor, data.model_dump(mode='json'), create)


@router.post('/tokens/{id}/revoke', response_model=TokenOutput)
@router.delete('/tokens/{id}', response_model=TokenOutput)
def revoke_token(id: int, request: Request, actor=Depends(require('tokens.revoke')), db=Depends(get_db, scope='function')):
    t = find(db, Token, id)
    if t.kind != 'api':
        raise HTTPException(404, 'API token not found')
    t.revoked_at = now()
    audit(db, request, 'token.revoked', 'tokens', id)
    return token_public(t)


@router.get('/audit', response_model=Items[AuditOutput])
def audit_list(limit: Limit = 100, offset: Offset = 0, request_id: str | None = None, actor=Depends(require('audit.read')), db=Depends(get_db, scope='function')):
    return {'items': [public(a, 'id timestamp user_id token_id ip action resource resource_id result request_id')
                      for a in paginate(db, Audit, offset, limit, Audit.request_id == request_id if request_id else None)]}
