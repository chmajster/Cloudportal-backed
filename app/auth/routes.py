import secrets
from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import or_, select, update
from app.api.schemas import Login, Refresh, ResetPassword, ChangePassword
from app.database import get_db
from app.models import PasswordReset, Token, User, now
from app.config import settings
from app.security.core import (authenticate, audit, digest, dummy_hash, effective_permissions, issue_token,
                               password_hasher, revoke_user, throttle, verify_password)

router = APIRouter(prefix='/auth', tags=['authentication'])
USER_FIELDS = 'id username email first_name last_name is_active is_locked is_service_account created_at updated_at last_login_at failed_login_attempts locked_until'


def user_public(user):
    from app.api.common import public
    return public(user, USER_FIELDS)


def session_pair(db, user, family=None):
    token, access = issue_token(db, user, 'Browser session', [], 'session', settings().access_seconds, family)
    _, refresh = issue_token(db, user, 'Session refresh', [], 'refresh', settings().refresh_seconds, token.family)
    return {'access_token': access, 'refresh_token': refresh, 'token_type': 'bearer', 'expires_in': settings().access_seconds,
            'user': user_public(user), 'roles': [{'id': r.id, 'name': r.name} for r in user.roles],
            'permissions': sorted(effective_permissions(user))}


@router.post('/login')
def login(data: Login, request: Request, db=Depends(get_db, scope='function')):
    identity = data.username.strip().lower()
    throttle('login:ip:' + (request.client.host if request.client else ''), 60, settings().lockout_seconds)
    throttle('login:identity:' + identity, 20, settings().lockout_seconds)
    user = db.scalar(select(User).where(or_(User.username == identity, User.email == identity)).with_for_update())
    valid = verify_password(data.password, user.password_hash if user else dummy_hash)
    locked = user and (user.is_locked or (user.locked_until and user.locked_until > now()))
    if not user or not valid or locked or not user.is_active or user.is_service_account:
        if user and not locked and user.is_active:
            user.failed_login_attempts += 1
            if user.failed_login_attempts >= settings().login_attempts:
                user.locked_until = now() + timedelta(seconds=settings().lockout_seconds)
                user.failed_login_attempts = 0
        audit(db, request, 'auth.login', 'users', user.id if user else None, 'failure', user.id if user else None)
        db.commit()
        raise HTTPException(401, 'Invalid credentials or unavailable account')
    if password_hasher.check_needs_rehash(user.password_hash):
        user.password_hash = password_hasher.hash(data.password)
    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login_at = now()
    audit(db, request, 'auth.login', 'users', user.id, user_id=user.id)
    return session_pair(db, user)


@router.get('/me')
def me(request: Request, actor=Depends(authenticate)):
    return {'user': user_public(actor.user), 'roles': [{'id': r.id, 'name': r.name} for r in actor.user.roles],
            'permissions': sorted(request.state.permissions), 'token_type': actor.kind}


@router.post('/logout')
def logout(request: Request, actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    if actor.kind == 'session':
        db.execute(update(Token).where(Token.family == actor.family).values(revoked_at=now()))
    else:
        actor.revoked_at = now()
    audit(db, request, 'auth.logout', 'tokens', actor.id)
    return {'logged_out': True}


@router.post('/refresh')
def refresh(data: Refresh, request: Request, db=Depends(get_db, scope='function')):
    token = db.scalar(select(Token).where(Token.token_hash == digest(data.refresh_token)).with_for_update(of=Token))
    if not token or token.kind != 'refresh':
        raise HTTPException(401, 'Invalid refresh token')
    if token.revoked_at is not None:
        db.execute(update(Token).where(Token.family == token.family).values(revoked_at=now()))
        audit(db, request, 'auth.refresh_reuse', 'tokens', token.id, 'denied', token.user_id)
        db.commit()
        raise HTTPException(401, 'Refresh token already used; session revoked')
    if token.expires_at <= now() or not token.user.is_active or token.user.is_locked or (token.user.locked_until and token.user.locked_until > now()):
        raise HTTPException(401, 'Session expired or account unavailable')
    db.execute(update(Token).where(Token.family == token.family, Token.revoked_at.is_(None)).values(revoked_at=now()))
    audit(db, request, 'auth.refresh', 'tokens', token.id, user_id=token.user_id)
    return session_pair(db, token.user, token.family)


@router.post('/reset-password')
def reset_password(data: ResetPassword, request: Request, db=Depends(get_db, scope='function')):
    throttle('reset:' + (request.client.host if request.client else ''), 20, 900)
    reset = db.scalar(select(PasswordReset).where(PasswordReset.token_hash == digest(data.token)).with_for_update())
    if not reset or reset.consumed_at or reset.expires_at <= now():
        raise HTTPException(400, 'Invalid or expired reset token')
    user = db.get(User, reset.user_id)
    if not user.is_active or user.is_service_account:
        raise HTTPException(400, 'Account unavailable')
    user.password_hash = password_hasher.hash(data.password)
    reset.consumed_at = now()
    revoke_user(db, user.id)
    audit(db, request, 'auth.password_reset', 'users', user.id, user_id=user.id)
    return {'reset': True}


@router.post('/change-password')
def change_password(data: ChangePassword, request: Request, actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    if actor.kind != 'session' or not verify_password(data.current_password, actor.user.password_hash):
        raise HTTPException(403, 'Current password required')
    actor.user.password_hash = password_hasher.hash(data.password)
    revoke_user(db, actor.user_id)
    audit(db, request, 'auth.password_changed', 'users', actor.user_id)
    return {'changed': True, 'login_required': True}
