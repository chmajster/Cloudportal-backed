from urllib.parse import parse_qs, urlparse

from app.database import session
from app.models import Setting, User


def sso_payload():
    return {
        'enabled': True,
        'provider_name': 'OpenSSO',
        'issuer': 'http://sso.example.test',
        'client_id': 'cloudportal',
        'client_secret': 'top-secret-client-value',
        'redirect_uri': 'http://testserver/api/v1/auth/sso/callback',
        'scopes': ['openid', 'profile', 'email', 'groups'],
        'token_endpoint_auth_method': 'client_secret_post',
        'verify_tls': False,
        'allow_insecure_http': True,
        'username_claim': 'preferred_username',
        'email_claim': 'email',
        'first_name_claim': 'given_name',
        'last_name_claim': 'family_name',
    }


def test_sso_settings_encrypt_secret_and_public_config(client, headers):
    saved = client.put('/api/v1/settings/sso', headers=headers, json=sso_payload())
    assert saved.status_code == 200, saved.text
    data = saved.json()
    assert data['enabled'] is True
    assert data['provider_name'] == 'OpenSSO'
    assert data['client_secret_configured'] is True
    assert 'client_secret' not in data

    public = client.get('/api/v1/auth/sso/config')
    assert public.status_code == 200
    assert public.json() == {'enabled': True, 'provider_name': 'OpenSSO'}

    with session() as db:
        stored = db.get(Setting, 'sso').value
        assert stored['client_secret_encrypted']
        assert 'top-secret-client-value' not in str(stored)

    update = sso_payload()
    update.pop('client_secret')
    update['provider_name'] = 'OpenSSO Lab'
    preserved = client.put('/api/v1/settings/sso', headers=headers, json=update)
    assert preserved.status_code == 200, preserved.text
    assert preserved.json()['client_secret_configured'] is True


def test_sso_authorization_code_pkce_jit_and_one_time_handoff(client, headers, monkeypatch):
    from app.auth import oidc

    saved = client.put('/api/v1/settings/sso', headers=headers, json=sso_payload())
    assert saved.status_code == 200, saved.text

    metadata = {
        'issuer': 'http://sso.example.test',
        'authorization_endpoint': 'http://sso.example.test/oauth2/authorize',
        'token_endpoint': 'http://sso.example.test/oauth2/token',
        'userinfo_endpoint': 'http://sso.example.test/userinfo',
        'jwks_uri': 'http://sso.example.test/.well-known/jwks.json',
        'code_challenge_methods_supported': ['S256'],
        'token_endpoint_auth_methods_supported': ['client_secret_post', 'client_secret_basic', 'none'],
        'id_token_signing_alg_values_supported': ['RS256'],
    }
    monkeypatch.setattr(oidc, 'oidc_discovery', lambda _config: metadata)
    monkeypatch.setattr(
        oidc,
        '_exchange_code',
        lambda _config, _metadata, _transaction, code: {
            'access_token': 'opensso-access-' + code,
            'id_token': 'signed-id-token',
        },
    )
    monkeypatch.setattr(
        oidc,
        '_validate_id_token',
        lambda _config, _metadata, _transaction, _token: {
            'sub': 'opensso-user-123',
            'preferred_username': 'sso-user',
            'email': 'sso-user@example.com',
            'name': 'SSO User',
        },
    )
    monkeypatch.setattr(
        oidc,
        '_userinfo',
        lambda _config, _metadata, _access, subject: {
            'sub': subject,
            'preferred_username': 'sso-user',
            'email': 'sso-user@example.com',
            'name': 'SSO User',
        },
    )

    started = client.get('/api/v1/auth/sso/login', follow_redirects=False)
    assert started.status_code == 302, started.text
    location = started.headers['location']
    parsed = urlparse(location)
    query = parse_qs(parsed.query)
    assert parsed.path == '/oauth2/authorize'
    assert query['response_type'] == ['code']
    assert query['client_id'] == ['cloudportal']
    assert query['redirect_uri'] == ['http://testserver/api/v1/auth/sso/callback']
    assert query['code_challenge_method'] == ['S256']
    assert len(query['code_challenge'][0]) >= 40
    assert query['scope'] == ['openid profile email groups']

    state = query['state'][0]
    callback = client.get(
        '/api/v1/auth/sso/callback',
        params={'state': state, 'code': 'authorization-code'},
        follow_redirects=False,
    )
    assert callback.status_code == 303, callback.text
    callback_location = callback.headers['location']
    assert callback_location.startswith('/ui/?sso_handoff=')
    handoff = parse_qs(urlparse(callback_location).query)['sso_handoff'][0]

    exchanged = client.post('/api/v1/auth/sso/exchange', json={'token': handoff})
    assert exchanged.status_code == 200, exchanged.text
    pair = exchanged.json()
    assert pair['user']['username'] == 'sso-user'
    assert pair['user']['email'] == 'sso-user@example.com'
    assert pair['user']['auth_source'] == 'oidc'
    assert pair['roles'] == []
    assert pair['permissions'] == []

    me = client.get('/api/v1/auth/me', headers={'Authorization': 'Bearer ' + pair['access_token']})
    assert me.status_code == 200, me.text
    assert me.json()['user']['auth_source'] == 'oidc'

    reused = client.post('/api/v1/auth/sso/exchange', json={'token': handoff})
    assert reused.status_code == 401

    replay = client.get(
        '/api/v1/auth/sso/callback',
        params={'state': state, 'code': 'authorization-code'},
        follow_redirects=False,
    )
    assert replay.status_code == 303
    assert 'sso_error=authentication_failed' in replay.headers['location']

    password_login = client.post('/api/v1/auth/login', json={
        'username': 'sso-user',
        'password': 'not-an-sso-password',
    })
    assert password_login.status_code == 401

    with session() as db:
        user = db.query(User).filter(User.username == 'sso-user').one()
        assert user.external_id.startswith('oidc:')
        assert user.must_change_password is False
        assert user.roles == []


def test_sso_jit_does_not_auto_link_existing_local_account(client, headers, monkeypatch):
    from app.auth import oidc

    created = client.post('/api/v1/users', headers=headers, json={
        'username': 'collision',
        'email': 'collision@example.com',
        'password': 'strong-password-1234',
    })
    assert created.status_code == 201, created.text
    assert client.put('/api/v1/settings/sso', headers=headers, json=sso_payload()).status_code == 200

    metadata = {
        'issuer': 'http://sso.example.test',
        'authorization_endpoint': 'http://sso.example.test/oauth2/authorize',
        'token_endpoint': 'http://sso.example.test/oauth2/token',
        'userinfo_endpoint': 'http://sso.example.test/userinfo',
        'jwks_uri': 'http://sso.example.test/.well-known/jwks.json',
    }
    monkeypatch.setattr(oidc, 'oidc_discovery', lambda _config: metadata)
    monkeypatch.setattr(
        oidc,
        '_exchange_code',
        lambda *_args, **_kwargs: {'access_token': 'access', 'id_token': 'id'},
    )
    monkeypatch.setattr(
        oidc,
        '_validate_id_token',
        lambda *_args, **_kwargs: {
            'sub': 'external-collision-subject',
            'preferred_username': 'collision',
            'email': 'collision@example.com',
        },
    )
    monkeypatch.setattr(
        oidc,
        '_userinfo',
        lambda _config, _metadata, _access, subject: {
            'sub': subject,
            'preferred_username': 'collision',
            'email': 'collision@example.com',
        },
    )

    started = client.get('/api/v1/auth/sso/login', follow_redirects=False)
    state = parse_qs(urlparse(started.headers['location']).query)['state'][0]
    callback = client.get(
        '/api/v1/auth/sso/callback',
        params={'state': state, 'code': 'code'},
        follow_redirects=False,
    )
    assert callback.status_code == 303
    assert 'sso_error=identity_collision' in callback.headers['location']

    with session() as db:
        user = db.query(User).filter(User.username == 'collision').one()
        assert user.auth_source == 'local'
        assert db.query(User).filter(User.auth_source == 'oidc').count() == 0
