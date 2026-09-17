import base64
import hashlib
import json
import os
import secrets
import stat
from datetime import timedelta
from functools import lru_cache
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis import Redis
from sqlalchemy import select, update
from app.config import settings
from app.database import get_db
from app.models import Audit, Token, User, now

password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
dummy_hash = password_hasher.hash(secrets.token_urlsafe(32))
bearer = HTTPBearer(auto_error=False)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def verify_password(value, hashed):
    try:
        return password_hasher.verify(hashed, value)
    except (VerificationError, InvalidHashError):
        return False


@lru_cache
def redis_client():
    return Redis.from_url(settings().redis_url, socket_connect_timeout=2, socket_timeout=3)


def throttle(key, limit, seconds):
    try:
        count = redis_client().eval(
            "local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],ARGV[1]) end; return n",
            1, 'cp:limit:' + digest(key), seconds,
        )
    except Exception:
        raise HTTPException(503, "Rate limiter unavailable") from None
    if count > limit:
        raise HTTPException(429, "Too many requests", headers={"Retry-After": str(seconds)})


def encryption_key():
    path = settings().master_key_file
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.geteuid():
            raise RuntimeError("Master key must be owned by the runtime user with mode 0600")
        raw = os.read(descriptor, 128).strip()
    finally:
        os.close(descriptor)
    key = base64.b64decode(raw, validate=True)
    if len(key) != 32:
        raise RuntimeError("Invalid master key")
    return key


def encrypt_secret(value, credential_id):
    nonce = os.urandom(12)
    return nonce + AESGCM(encryption_key()).encrypt(nonce, json.dumps(value).encode(), f'credential:{credential_id}'.encode())


def decrypt_secret(credential):
    raw = credential.encrypted_secret
    return json.loads(AESGCM(encryption_key()).decrypt(raw[:12], raw[12:], f'credential:{credential.id}'.encode()))


def effective_permissions(user):
    return {p.name for role in user.roles for p in role.permissions}


def issue_token(db, user, name, scopes, kind='api', seconds=None, family=None):
    plain = 'cp_' + secrets.token_urlsafe(48)
    token = Token(name=name, token_hash=digest(plain), token_prefix=plain[:10], user_id=user.id,
                  scopes=sorted(scopes), kind=kind, expires_at=now() + timedelta(seconds=seconds) if seconds else None)
    if family:
        token.family = family
    db.add(token)
    db.flush()
    return token, plain


def revoke_user(db, user_id):
    db.execute(update(Token).where(Token.user_id == user_id, Token.revoked_at.is_(None)).values(revoked_at=now()))


def authenticate(request: Request, auth: HTTPAuthorizationCredentials | None = Depends(bearer), db=Depends(get_db, scope='function')):
    if auth is None or len(auth.credentials) > 256:
        raise HTTPException(401, "Authentication required")
    token = db.scalar(select(Token).where(Token.token_hash == digest(auth.credentials)))
    if (token is None or token.kind == 'refresh' or token.revoked_at is not None
        or (token.expires_at is not None and token.expires_at <= now())
        or not token.user.is_active or token.user.is_locked
        or (token.user.locked_until is not None and token.user.locked_until > now())):
        raise HTTPException(401, "Invalid, expired or revoked token")
    token.last_used_at = now()
    permissions = effective_permissions(token.user)
    if token.kind == 'api':
        permissions &= set(token.scopes)
    request.state.actor = token
    request.state.permissions = permissions
    if (token.kind == 'session' and token.user.must_change_password
            and request.url.path not in {'/api/v1/auth/me', '/api/v1/auth/logout', '/api/v1/auth/change-password'}):
        raise HTTPException(403, 'Password change required')
    return token


def require(permission):
    def dependency(request: Request, actor=Depends(authenticate), db=Depends(get_db, scope='function')):
        if permission not in request.state.permissions:
            audit(db, request, 'authorization.denied', permission, result='denied')
            db.commit()
            raise HTTPException(403, f"Permission required: {permission}")
        return actor
    return dependency


def audit(db, request, action, resource='', resource_id=None, result='success', user_id=None):
    actor = getattr(request.state, 'actor', None)
    db.add(Audit(user_id=user_id if user_id is not None else actor.user_id if actor else None,
                 token_id=actor.id if actor else None, ip=request.client.host if request.client else '',
                 action=action, resource=resource, resource_id=str(resource_id) if resource_id is not None else None,
                 result=result, request_id=request.state.request_id))
