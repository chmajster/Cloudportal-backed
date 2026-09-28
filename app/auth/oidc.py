import base64
import hashlib
import json
import re
import secrets
from urllib.parse import urlencode, urlsplit

import httpx
import jwt
from fastapi import HTTPException
from sqlalchemy import or_, select

from app.models import Setting, User
from app.security.core import decrypt_blob, encrypt_blob, password_hasher, redis_client


SSO_KEY = 'sso'
SSO_SECRET_AAD = 'setting:sso:client_secret'
SSO_TRANSACTION_TTL = 300
SSO_HANDOFF_TTL = 60
SSO_TRANSACTION_PREFIX = 'cp:sso:transaction:'
SSO_HANDOFF_PREFIX = 'cp:sso:handoff:'

SSO_DEFAULTS = {
    'enabled': False,
    'provider_name': 'OpenSSO',
    'issuer': '',
    'client_id': '',
    'redirect_uri': '',
    'scopes': ['openid', 'profile', 'email', 'groups'],
    'token_endpoint_auth_method': 'client_secret_post',
    'verify_tls': True,
    'allow_insecure_http': False,
    'username_claim': 'preferred_username',
    'email_claim': 'email',
    'first_name_claim': 'given_name',
    'last_name_claim': 'family_name',
}


def sso_settings(db, *, include_secret=False):
    row = db.get(Setting, SSO_KEY)
    raw = dict(row.value) if row and isinstance(row.value, dict) else {}
    result = {**SSO_DEFAULTS, **{key: value for key, value in raw.items() if key != 'client_secret_encrypted'}}
    secret = raw.get('client_secret_encrypted')
    result['client_secret_configured'] = bool(secret)
    if include_secret:
        result['client_secret'] = ''
        if secret:
            try:
                result['client_secret'] = decrypt_blob(
                    base64.b64decode(secret, validate=True),
                    SSO_SECRET_AAD,
                ).decode()
            except Exception:
                raise HTTPException(500, 'SSO client secret cannot be decrypted') from None
    return result


def save_sso_settings(db, data):
    current = db.get(Setting, SSO_KEY)
    current_value = dict(current.value) if current and isinstance(current.value, dict) else {}
    values = data.model_dump(exclude={'client_secret'})
    if data.client_secret is not None:
        if data.client_secret:
            values['client_secret_encrypted'] = base64.b64encode(
                encrypt_blob(data.client_secret.encode(), SSO_SECRET_AAD)
            ).decode()
        else:
            values.pop('client_secret_encrypted', None)
    elif current_value.get('client_secret_encrypted'):
        values['client_secret_encrypted'] = current_value['client_secret_encrypted']

    if current is None:
        current = Setting(key=SSO_KEY, value=values)
        db.add(current)
    else:
        current.value = values
    db.flush()
    return sso_settings(db)


def _validate_runtime_url(value, *, allow_http=False, label='OIDC endpoint'):
    parsed = urlsplit(str(value or '').strip())
    allowed = {'https'}
    if allow_http:
        allowed.add('http')
    if parsed.scheme not in allowed or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise HTTPException(502, f'{label} returned by provider is invalid')
    return str(value).strip()


def _provider_client(config):
    return httpx.Client(
        timeout=10,
        verify=bool(config.get('verify_tls', True)),
        follow_redirects=False,
        trust_env=False,
        headers={'Accept': 'application/json'},
    )


def oidc_discovery(config):
    issuer = str(config.get('issuer') or '').rstrip('/')
    if not issuer:
        raise HTTPException(422, 'SSO issuer is not configured')
    allow_http = bool(config.get('allow_insecure_http'))
    _validate_runtime_url(issuer, allow_http=allow_http, label='SSO issuer')
    try:
        with _provider_client(config) as client:
            response = client.get(issuer + '/.well-known/openid-configuration')
            response.raise_for_status()
            metadata = response.json()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, 'OIDC discovery request failed') from None
    if not isinstance(metadata, dict) or metadata.get('issuer') != issuer:
        raise HTTPException(502, 'OIDC discovery issuer does not match configured issuer')
    for key in ('authorization_endpoint', 'token_endpoint', 'userinfo_endpoint', 'jwks_uri'):
        metadata[key] = _validate_runtime_url(
            metadata.get(key),
            allow_http=allow_http,
            label=key,
        )
    methods = metadata.get('code_challenge_methods_supported')
    if isinstance(methods, list) and methods and 'S256' not in methods:
        raise HTTPException(502, 'OIDC provider does not support PKCE S256')
    supported_auth = metadata.get('token_endpoint_auth_methods_supported')
    configured_auth = config.get('token_endpoint_auth_method')
    if isinstance(supported_auth, list) and supported_auth and configured_auth not in supported_auth:
        raise HTTPException(502, 'Configured OIDC token authentication method is not supported')
    return metadata


def _jwks(config, metadata):
    try:
        with _provider_client(config) as client:
            response = client.get(metadata['jwks_uri'])
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, 'OIDC JWKS request failed') from None
    keys = payload.get('keys') if isinstance(payload, dict) else None
    if not isinstance(keys, list) or not keys:
        raise HTTPException(502, 'OIDC provider returned an empty JWKS')
    return keys


def test_sso_connection(db):
    config = sso_settings(db, include_secret=True)
    if not config.get('issuer') or not config.get('client_id') or not config.get('redirect_uri'):
        raise HTTPException(422, 'SSO issuer, client ID and redirect URI are required')
    if config.get('token_endpoint_auth_method') != 'none' and not config.get('client_secret'):
        raise HTTPException(422, 'SSO client secret is required for the configured token authentication method')
    metadata = oidc_discovery(config)
    keys = _jwks(config, metadata)
    return {
        'ok': True,
        'message': 'OIDC discovery, endpoint validation and JWKS verification succeeded.',
        'issuer': metadata['issuer'],
        'authorization_endpoint': metadata['authorization_endpoint'],
        'token_endpoint': metadata['token_endpoint'],
        'userinfo_endpoint': metadata['userinfo_endpoint'],
        'jwks_uri': metadata['jwks_uri'],
        'signing_keys': len(keys),
    }


def _redis_setex(key, ttl, payload):
    try:
        redis_client().setex(key, ttl, json.dumps(payload, separators=(',', ':')))
    except Exception:
        raise HTTPException(503, 'SSO transaction store is unavailable') from None


def _redis_consume(key):
    try:
        raw = redis_client().eval(
            "local v=redis.call('GET',KEYS[1]); if v then redis.call('DEL',KEYS[1]) end; return v",
            1,
            key,
        )
    except Exception:
        raise HTTPException(503, 'SSO transaction store is unavailable') from None
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode()
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def begin_sso(db):
    config = sso_settings(db, include_secret=True)
    if not config.get('enabled'):
        raise HTTPException(404, 'SSO is disabled')
    if not config.get('client_id') or not config.get('redirect_uri'):
        raise HTTPException(503, 'SSO is not fully configured')
    if config.get('token_endpoint_auth_method') != 'none' and not config.get('client_secret'):
        raise HTTPException(503, 'SSO client secret is not configured')

    metadata = oidc_discovery(config)
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    transaction = {
        'nonce': nonce,
        'verifier': verifier,
        'issuer': config['issuer'],
        'client_id': config['client_id'],
        'redirect_uri': config['redirect_uri'],
    }
    _redis_setex(SSO_TRANSACTION_PREFIX + state, SSO_TRANSACTION_TTL, transaction)
    params = {
        'response_type': 'code',
        'client_id': config['client_id'],
        'redirect_uri': config['redirect_uri'],
        'scope': ' '.join(config.get('scopes') or SSO_DEFAULTS['scopes']),
        'state': state,
        'nonce': nonce,
        'code_challenge': challenge,
        'code_challenge_method': 'S256',
    }
    return metadata['authorization_endpoint'] + '?' + urlencode(params)


def discard_sso_state(state):
    if not state:
        return
    _redis_consume(SSO_TRANSACTION_PREFIX + state)


def _exchange_code(config, metadata, transaction, code):
    data = {
        'grant_type': 'authorization_code',
        'client_id': config['client_id'],
        'code': code,
        'redirect_uri': transaction['redirect_uri'],
        'code_verifier': transaction['verifier'],
    }
    method = config.get('token_endpoint_auth_method', 'client_secret_post')
    auth = None
    if method == 'client_secret_post':
        data['client_secret'] = config.get('client_secret', '')
    elif method == 'client_secret_basic':
        auth = httpx.BasicAuth(config['client_id'], config.get('client_secret', ''))
    elif method != 'none':
        raise HTTPException(503, 'Unsupported SSO token authentication method')
    try:
        with _provider_client(config) as client:
            response = client.post(
                metadata['token_endpoint'],
                data=data,
                auth=auth,
                headers={'Accept': 'application/json'},
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(401, 'OIDC authorization code exchange failed') from None
    if not isinstance(payload, dict) or not payload.get('access_token') or not payload.get('id_token'):
        raise HTTPException(401, 'OIDC token response is incomplete')
    return payload


def _validate_id_token(config, metadata, transaction, raw_token):
    try:
        header = jwt.get_unverified_header(raw_token)
    except jwt.PyJWTError:
        raise HTTPException(401, 'OIDC ID token header is invalid') from None
    algorithm = header.get('alg')
    kid = header.get('kid')
    allowed = {'RS256', 'RS384', 'RS512', 'ES256', 'ES384', 'ES512'}
    advertised = metadata.get('id_token_signing_alg_values_supported')
    if algorithm not in allowed or (isinstance(advertised, list) and advertised and algorithm not in advertised):
        raise HTTPException(401, 'OIDC ID token uses an unsupported signing algorithm')
    keys = _jwks(config, metadata)
    candidates = [item for item in keys if not kid or item.get('kid') == kid]
    if len(candidates) != 1:
        raise HTTPException(401, 'OIDC signing key cannot be resolved unambiguously')
    try:
        key = jwt.PyJWK.from_dict(candidates[0]).key
        claims = jwt.decode(
            raw_token,
            key=key,
            algorithms=[algorithm],
            audience=config['client_id'],
            issuer=config['issuer'],
            leeway=30,
            options={'require': ['exp', 'iat', 'iss', 'sub', 'aud']},
        )
    except (jwt.PyJWTError, ValueError, KeyError):
        raise HTTPException(401, 'OIDC ID token validation failed') from None
    if not secrets.compare_digest(str(claims.get('nonce') or ''), str(transaction.get('nonce') or '')):
        raise HTTPException(401, 'OIDC nonce validation failed')
    return claims


def _userinfo(config, metadata, access_token, subject):
    try:
        with _provider_client(config) as client:
            response = client.get(
                metadata['userinfo_endpoint'],
                headers={'Authorization': 'Bearer ' + access_token, 'Accept': 'application/json'},
            )
            response.raise_for_status()
            claims = response.json()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(401, 'OIDC userinfo request failed') from None
    if not isinstance(claims, dict) or not secrets.compare_digest(str(claims.get('sub') or ''), str(subject)):
        raise HTTPException(401, 'OIDC userinfo subject does not match ID token')
    return claims


def _claim(claims, name):
    current = claims
    for part in str(name or '').split('.'):
        if not isinstance(current, dict):
            return ''
        current = current.get(part)
    if isinstance(current, (str, int)):
        return str(current).strip()
    return ''


def _username(claims, config, subject):
    candidate = _claim(claims, config.get('username_claim')) or _claim(claims, config.get('email_claim')).split('@', 1)[0]
    candidate = candidate.lower()
    candidate = re.sub(r'[^a-z0-9_.-]+', '-', candidate).strip('._-')
    if not candidate or not candidate[0].isalnum():
        candidate = 'oidc-' + hashlib.sha256(subject.encode()).hexdigest()[:12]
    return candidate[:63]


def _profile(claims, config, subject):
    username = _username(claims, config, subject)
    email = _claim(claims, config.get('email_claim')).lower()
    if len(email) > 254 or '@' not in email:
        email = f'{username}@oidc.invalid'
    first_name = _claim(claims, config.get('first_name_claim'))
    last_name = _claim(claims, config.get('last_name_claim'))
    if not first_name and not last_name:
        name = _claim(claims, 'name')
        if name:
            first_name, _, last_name = name.partition(' ')
    return {
        'username': username,
        'email': email,
        'first_name': first_name[:100],
        'last_name': last_name[:100],
    }


def authenticate_sso_callback(db, *, state, code):
    transaction = _redis_consume(SSO_TRANSACTION_PREFIX + str(state or ''))
    if not transaction:
        raise HTTPException(400, 'SSO state is invalid, expired or already used')

    config = sso_settings(db, include_secret=True)
    if not config.get('enabled'):
        raise HTTPException(403, 'SSO was disabled during authentication')
    for key in ('issuer', 'client_id', 'redirect_uri'):
        if transaction.get(key) != config.get(key):
            raise HTTPException(400, 'SSO configuration changed during authentication')

    metadata = oidc_discovery(config)
    tokens = _exchange_code(config, metadata, transaction, code)
    id_claims = _validate_id_token(config, metadata, transaction, tokens['id_token'])
    subject = str(id_claims.get('sub') or '')
    user_claims = _userinfo(config, metadata, tokens['access_token'], subject)
    claims = {**id_claims, **user_claims}
    profile = _profile(claims, config, subject)

    issuer_hash = hashlib.sha256(config['issuer'].encode()).hexdigest()[:20]
    subject_hash = hashlib.sha256(subject.encode()).hexdigest()
    external_id = f'oidc:{issuer_hash}:{subject_hash}'

    user = db.scalar(select(User).where(User.external_id == external_id).with_for_update())
    created = False
    if user is None:
        collision = db.scalar(select(User).where(or_(
            User.username == profile['username'],
            User.email == profile['email'],
        )).with_for_update())
        if collision:
            raise HTTPException(409, 'SSO identity collides with an existing Cloudportal account')
        user = User(
            username=profile['username'],
            email=profile['email'],
            password_hash=password_hasher.hash(secrets.token_urlsafe(64)),
            auth_source='oidc',
            external_id=external_id,
            first_name=profile['first_name'],
            last_name=profile['last_name'],
            must_change_password=False,
            is_active=True,
        )
        db.add(user)
        db.flush()
        created = True
    elif user.auth_source != 'oidc':
        raise HTTPException(409, 'External identity is already bound to another authentication source')
    else:
        username_collision = db.scalar(select(User.id).where(User.username == profile['username'], User.id != user.id))
        email_collision = db.scalar(select(User.id).where(User.email == profile['email'], User.id != user.id))
        if username_collision or email_collision:
            raise HTTPException(409, 'SSO profile update collides with an existing Cloudportal account')
        user.username = profile['username']
        user.email = profile['email']
        user.first_name = profile['first_name']
        user.last_name = profile['last_name']

    if not user.is_active or user.is_locked or user.is_service_account:
        raise HTTPException(403, 'SSO account is unavailable')
    return user, created


def issue_sso_handoff(user_id):
    token = secrets.token_urlsafe(48)
    _redis_setex(SSO_HANDOFF_PREFIX + token, SSO_HANDOFF_TTL, {'user_id': int(user_id)})
    return token


def consume_sso_handoff(token):
    payload = _redis_consume(SSO_HANDOFF_PREFIX + str(token or ''))
    if not payload:
        raise HTTPException(401, 'SSO handoff is invalid, expired or already used')
    try:
        return int(payload['user_id'])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(401, 'SSO handoff is invalid') from None
