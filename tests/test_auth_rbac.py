from datetime import timedelta
from sqlalchemy import select
from app.database import session
from app.models import Setting, Token, User, now
from app.security.core import verify_password
from conftest import new_user


def test_bootstrap_is_once_and_hashes(system):
    from app.bootstrap import bootstrap
    client, headers, admin = system
    with session() as db:
        token = db.scalar(select(Token).where(Token.kind == 'api'))
        user = db.scalar(select(User).where(User.username == 'admin'))
        assert admin['username'] == 'admin' and admin['password'] == 'admin'
        assert user.must_change_password is True
        assert user.password_hash != 'admin' and verify_password('admin', user.password_hash)
        assert token.token_hash != admin['token'] and len(token.token_hash) == 64
        assert bootstrap(db) is None
    response = client.get('/api/v1/auth/me', headers=headers)
    assert response.status_code == 200
    assert 'users.delete' in response.json()['permissions']
    assert 'password_hash' not in response.text and admin['token'] not in response.text


def test_existing_administrator_recovers_all_permissions_on_bootstrap(system):
    from app.bootstrap import bootstrap
    from app.models import Role
    from app.rbac.service import ALL_PERMISSIONS

    client, headers, _admin = system
    with session() as db:
        role = db.scalar(select(Role).where(Role.name == 'Administrator'))
        role.permissions = [permission for permission in role.permissions
                            if permission.name not in {'settings.read', 'settings.update', 'updates.read',
                                                       'updates.execute', 'updates.update'}]
        db.commit()

    before = client.get('/api/v1/auth/me', headers=headers)
    assert before.status_code == 200
    assert 'settings.read' not in before.json()['permissions']
    assert 'updates.read' not in before.json()['permissions']

    with session() as db:
        assert bootstrap(db) is None
        role = db.scalar(select(Role).where(Role.name == 'Administrator'))
        assert {permission.name for permission in role.permissions} == ALL_PERMISSIONS

    after = client.get('/api/v1/auth/me', headers=headers)
    assert after.status_code == 200
    assert {'settings.read', 'settings.update', 'updates.read', 'updates.execute', 'updates.update'} <= set(after.json()['permissions'])



def test_runtime_rbac_sync_restores_admin_ldap_settings_access(system):
    from app.bootstrap import sync_existing_rbac
    from app.models import Role

    client, headers, _admin = system
    with session() as db:
        role = db.scalar(select(Role).where(Role.name == 'Administrator'))
        role.permissions = [
            permission for permission in role.permissions
            if permission.name not in {'settings.read', 'settings.update'}
        ]
        db.commit()

    before = client.get('/api/v1/settings/ldap', headers=headers)
    assert before.status_code == 403

    assert sync_existing_rbac() is True

    after = client.get('/api/v1/settings/ldap', headers=headers)
    assert after.status_code == 200, after.text

    payload = {
        'enabled': False,
        'url': 'ldap://localhost:389',
        'start_tls': False,
        'verify_tls': True,
        'bind_dn': '',
        'base_dn': '',
        'user_filter': '(uid={username})',
        'username_attribute': 'uid',
        'email_attribute': 'mail',
        'first_name_attribute': 'givenName',
        'last_name_attribute': 'sn',
    }
    saved = client.put('/api/v1/settings/ldap', headers=headers, json=payload)
    assert saved.status_code == 200, saved.text


def test_default_admin_session_requires_password_change(system):
    client, bootstrap_headers, admin = system
    login = client.post('/api/v1/auth/login', json={'username': 'admin', 'password': 'admin'})
    assert login.status_code == 200, login.text
    assert login.json()['user']['must_change_password'] is True
    session_headers = {'Authorization': 'Bearer ' + login.json()['access_token']}

    blocked = client.get('/api/v1/users', headers=session_headers)
    assert blocked.status_code == 403
    assert blocked.json()['detail'] == 'Password change required'
    assert client.get('/api/v1/users', headers=bootstrap_headers).status_code == 200

    wrong = client.post('/api/v1/auth/change-password', headers=session_headers,
                        json={'current_password': 'wrong-password', 'password': 'secure-admin-password-1234'})
    assert wrong.status_code == 403
    assert wrong.json()['detail'] == 'Current password is incorrect'

    api_token_change = client.post('/api/v1/auth/change-password', headers=bootstrap_headers,
                                   json={'current_password': 'admin', 'password': 'secure-admin-password-1234'})
    assert api_token_change.status_code == 403
    assert api_token_change.json()['detail'] == 'Browser session required'

    changed = client.post('/api/v1/auth/change-password', headers=session_headers,
                          json={'current_password': 'admin', 'password': 'secure-admin-password-1234'})
    assert changed.status_code == 200, changed.text
    assert client.post('/api/v1/auth/login', json={'username': 'admin', 'password': 'admin'}).status_code == 401
    replacement = client.post('/api/v1/auth/login', json={
        'username': 'admin', 'password': 'secure-admin-password-1234'})
    assert replacement.status_code == 200, replacement.text
    assert replacement.json()['user']['must_change_password'] is False


def test_login_refresh_logout_and_reuse(system):
    c, h, admin = system
    login = c.post('/api/v1/auth/login', json={'username':'admin','password':admin['password']})
    assert login.status_code == 200, login.text
    first = login.json()
    second = c.post('/api/v1/auth/refresh',json={'refresh_token':first['refresh_token']})
    assert second.status_code == 200, second.text
    assert c.get('/api/v1/auth/me',headers={'Authorization':'Bearer '+first['access_token']}).status_code == 401
    assert c.post('/api/v1/auth/refresh',json={'refresh_token':first['refresh_token']}).status_code == 401
    assert c.get('/api/v1/auth/me',headers={'Authorization':'Bearer '+second.json()['access_token']}).status_code == 401
    login = c.post('/api/v1/auth/login',json={'username':'admin','password':admin['password']}).json()
    auth={'Authorization':'Bearer '+login['access_token']}
    assert c.post('/api/v1/auth/logout',headers=auth).status_code == 200
    assert c.get('/api/v1/auth/me',headers=auth).status_code == 401
    assert c.post('/api/v1/auth/refresh',json={'refresh_token':login['refresh_token']}).status_code == 401


def test_login_lockout_unlock(client, headers):
    u, auth = new_user(client, headers)
    for _ in range(5):
        assert client.post('/api/v1/auth/login',json={'username':'viewer','password':'wrong-password'}).status_code == 401
    assert client.post('/api/v1/auth/login',json={'username':'viewer','password':'strong-password-1234'}).status_code == 401
    assert client.post(f'/api/v1/users/{u["id"]}/unlock',headers=headers).status_code == 200
    assert client.post('/api/v1/auth/login',json={'username':'viewer','password':'strong-password-1234'}).status_code == 200


def test_permissions_enforced_without_ui(client, headers):
    u, auth = new_user(client, headers, permissions=['users.read'])
    assert client.get('/api/v1/users',headers=auth).status_code == 200
    for path, body in [('/credentials',{'name':'x','type':'ssh','secrets':{'password':'x','known_hosts':'key'}}),('/roles',{'name':'Admin','permissions':['users.delete']})]:
        assert client.post('/api/v1'+path,headers=auth,json=body).status_code == 403
    assert client.get('/api/v1/users').status_code == 401


def test_last_admin_and_role_escalation(client, headers):
    assert client.post('/api/v1/users/1/disable',headers=headers).status_code == 409
    assert client.delete('/api/v1/users/1',headers=headers).status_code == 409
    roles=client.get('/api/v1/users/1/roles',headers=headers).json()['items']
    assert client.put('/api/v1/roles/'+str(roles[0]['id']),headers=headers,json={'name':'Administrator','permissions':[]}).status_code == 409
    _, auth=new_user(client,headers,permissions=['roles.create','roles.assign'])
    assert client.post('/api/v1/roles',headers=auth,json={'name':'Escalated','permissions':['users.delete']}).status_code == 403
    assert client.put('/api/v1/users/2/roles',headers=auth,json={'role_ids':[roles[0]['id']]}).status_code == 403


def test_token_scopes_revocation_expiration(client, headers):
    response=client.post('/api/v1/tokens',headers=headers,json={'name':'reader','scopes':['users.read']})
    assert response.status_code==201,response.text
    t=response.json();auth={'Authorization':'Bearer '+t['token']}
    assert client.get('/api/v1/users',headers=auth).status_code==200
    assert client.get('/api/v1/roles',headers=auth).status_code==403
    assert t['token'] not in client.get('/api/v1/tokens',headers=headers).text
    assert client.post(f'/api/v1/tokens/{t["id"]}/revoke',headers=headers).status_code==200
    assert client.get('/api/v1/users',headers=auth).status_code==401
    t=client.post('/api/v1/tokens',headers=headers,json={'name':'expiring','scopes':['users.read']}).json()
    with session() as db:
        db.get(Token,t['id']).expires_at=now()-timedelta(seconds=1);db.commit()
    assert client.get('/api/v1/users',headers={'Authorization':'Bearer '+t['token']}).status_code==401


def test_password_reset_consumed_and_revokes(client,headers):
    u,auth=new_user(client,headers)
    token=client.post(f'/api/v1/users/{u["id"]}/reset-password',headers=headers).json()['reset_token']
    assert client.get('/api/v1/auth/me',headers=auth).status_code==401
    assert client.post('/api/v1/auth/reset-password',json={'token':token,'password':'new-password-1234'}).status_code==200
    assert client.post('/api/v1/auth/reset-password',json={'token':token,'password':'new-password-5678'}).status_code==400
    assert client.post('/api/v1/auth/login',json={'username':'viewer','password':'new-password-1234'}).status_code==200


def test_user_creation_without_initial_password(client, headers):
    response = client.post('/api/v1/users', headers=headers, json={
        'username': 'passwordless',
        'email': 'passwordless@example.com',
    })
    assert response.status_code == 201, response.text
    created = response.json()
    assert created['must_change_password'] is True

    with session() as db:
        user = db.get(User, created['id'])
        assert user is not None
        assert user.password_hash
        assert not verify_password('known-password-1234', user.password_hash)

    assert client.post('/api/v1/auth/login', json={
        'username': 'passwordless',
        'password': 'known-password-1234',
    }).status_code == 401

    reset = client.post(
        f'/api/v1/users/{created["id"]}/reset-password',
        headers=headers,
    )
    assert reset.status_code == 200, reset.text
    changed = client.post('/api/v1/auth/reset-password', json={
        'token': reset.json()['reset_token'],
        'password': 'configured-password-1234',
    })
    assert changed.status_code == 200, changed.text
    login = client.post('/api/v1/auth/login', json={
        'username': 'passwordless',
        'password': 'configured-password-1234',
    })
    assert login.status_code == 200, login.text
    assert login.json()['user']['must_change_password'] is False


def test_empty_password_is_treated_as_omitted(client, headers):
    response = client.post('/api/v1/users', headers=headers, json={
        'username': 'empty-password',
        'email': 'empty-password@example.com',
        'password': '',
    })
    assert response.status_code == 201, response.text
    assert response.json()['must_change_password'] is True


def test_service_account_does_not_require_password(client, headers):
    response = client.post('/api/v1/users', headers=headers, json={
        'username': 'service-no-password',
        'email': 'service-no-password@example.com',
        'is_service_account': True,
    })
    assert response.status_code == 201, response.text
    created = response.json()
    assert created['is_service_account'] is True
    assert created['must_change_password'] is False
    assert client.post('/api/v1/auth/login', json={
        'username': 'service-no-password',
        'password': 'arbitrary-password-1234',
    }).status_code == 401


def test_ldap_settings_secret_and_jit_rbac(client, headers, monkeypatch):
    payload = {
        'enabled': True,
        'url': 'ldaps://ldap.example.com:636',
        'start_tls': False,
        'verify_tls': True,
        'bind_dn': 'cn=cloudportal,dc=example,dc=com',
        'bind_password': 'LDAP_BIND_SECRET_123',
        'base_dn': 'ou=people,dc=example,dc=com',
        'user_filter': '(uid={username})',
        'username_attribute': 'uid',
        'email_attribute': 'mail',
        'first_name_attribute': 'givenName',
        'last_name_attribute': 'sn',
    }
    saved = client.put('/api/v1/settings/ldap', headers=headers, json=payload)
    assert saved.status_code == 200, saved.text
    assert saved.json()['bind_password_configured'] is True
    assert 'LDAP_BIND_SECRET_123' not in saved.text
    with session() as db:
        raw = db.get(Setting, 'ldap').value
        assert raw.get('bind_secret')
        assert 'LDAP_BIND_SECRET_123' not in str(raw)

    profile = {
        'dn': 'uid=alice,ou=people,dc=example,dc=com',
        'username': 'alice',
        'email': 'alice@example.com',
        'first_name': 'Alice',
        'last_name': 'Directory',
    }
    monkeypatch.setattr(
        'app.auth.routes.authenticate_ldap',
        lambda db, identity, password: profile if identity == 'alice' and password == 'directory-secret' else None,
    )
    login = client.post('/api/v1/auth/login', json={'username': 'alice', 'password': 'directory-secret'})
    assert login.status_code == 200, login.text
    assert login.json()['user']['auth_source'] == 'ldap'
    assert login.json()['roles'] == []

    alice = next(row for row in client.get('/api/v1/users', headers=headers).json()['items'] if row['username'] == 'alice')
    role = client.post('/api/v1/roles', headers=headers, json={
        'name': 'LDAP operator',
        'permissions': ['deployments.read'],
    }).json()
    assigned = client.put('/api/v1/users/' + str(alice['id']) + '/roles', headers=headers, json={'role_ids': [role['id']]})
    assert assigned.status_code == 200
    second = client.post('/api/v1/auth/login', json={'username': 'alice', 'password': 'directory-secret'})
    assert second.json()['permissions'] == ['deployments.read']
    assert client.post('/api/v1/users/' + str(alice['id']) + '/reset-password', headers=headers).status_code == 409


def test_ldap_does_not_override_local_account(client, headers, monkeypatch):
    new_user(client, headers, username='localuser')
    called = {'value': False}

    def ldap_attempt(*args, **kwargs):
        called['value'] = True
        return None

    monkeypatch.setattr('app.auth.routes.authenticate_ldap', ldap_attempt)
    denied = client.post('/api/v1/auth/login', json={'username': 'localuser', 'password': 'wrong-local-password'})
    assert denied.status_code == 401
    assert called['value'] is False


def test_service_account_cannot_login_or_impersonate(client,headers):
    user=client.post('/api/v1/users',headers=headers,json={'username':'portal','email':'portal@example.com','password':'service-password-1234','is_service_account':True}).json()
    role=next(r for r in client.get('/api/v1/roles',headers=headers).json()['items'] if r['name']=='Portal Service')
    assert client.put(f'/api/v1/users/{user["id"]}/roles',headers=headers,json={'role_ids':[role['id']]}).status_code==200
    token=client.post('/api/v1/tokens',headers=headers,json={'name':'portal','user_id':user['id'],'scopes':['portal.connect']}).json()['token']
    assert client.get('/api/v1/auth/me',headers={'Authorization':'Bearer '+token}).json()['permissions']==['portal.connect']
    assert client.get('/api/v1/users',headers={'Authorization':'Bearer '+token}).status_code==403
    assert client.post('/api/v1/auth/login',json={'username':'portal','password':'service-password-1234'}).status_code==401


def test_validation_never_echoes_secrets(client,headers):
    secret='SECRET_BAD_PASSWORD'
    response=client.post('/api/v1/users',headers=headers,json={'username':'x','email':'bad-email','password':secret,'extra_secret':secret})
    assert response.status_code==422
    assert secret not in response.text


def test_password_change_invalidates_outstanding_reset_token(client, headers):
    user, _ = new_user(client, headers)
    reset = client.post(f'/api/v1/users/{user["id"]}/reset-password', headers=headers).json()['reset_token']
    pair = client.post('/api/v1/auth/login', json={'username': 'viewer', 'password': 'strong-password-1234'}).json()
    response = client.post('/api/v1/auth/change-password', headers={'Authorization': 'Bearer ' + pair['access_token']},
                           json={'current_password': 'strong-password-1234', 'password': 'changed-password-1234'})
    assert response.status_code == 200, response.text
    assert client.post('/api/v1/auth/reset-password', json={'token': reset, 'password': 'reset-password-5678'}).status_code == 400
    assert client.post('/api/v1/auth/login', json={'username': 'viewer', 'password': 'changed-password-1234'}).status_code == 200


def test_blueprint_execution_global_settings(client, headers):
    defaults = client.get('/api/v1/settings/blueprints', headers=headers)
    assert defaults.status_code == 200, defaults.text
    assert defaults.json() == {'auto_approve_for_executors': True, 'approval_timeout_hours': 48}

    disabled = client.put(
        '/api/v1/settings/blueprints',
        headers=headers,
        json={'auto_approve_for_executors': False, 'approval_timeout_hours': 12},
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json() == {'auto_approve_for_executors': False, 'approval_timeout_hours': 12}

    reloaded = client.get('/api/v1/settings/blueprints', headers=headers)
    assert reloaded.json() == {'auto_approve_for_executors': False, 'approval_timeout_hours': 12}

    with session() as db:
        raw = db.get(Setting, 'blueprint_execution').value
        assert raw == {'auto_approve_for_executors': False, 'approval_timeout_hours': 12}


def test_job_execution_concurrency_settings(client, headers):
    from app.config import settings

    defaults = client.get('/api/v1/settings/execution', headers=headers)
    assert defaults.status_code == 200, defaults.text
    assert settings().worker_count == 10
    assert defaults.json() == {'max_parallel_jobs': 10}

    saved = client.put(
        '/api/v1/settings/execution',
        headers=headers,
        json={'max_parallel_jobs': 3},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json() == {'max_parallel_jobs': 3}

    reloaded = client.get('/api/v1/settings/execution', headers=headers)
    assert reloaded.status_code == 200, reloaded.text
    assert reloaded.json() == {'max_parallel_jobs': 3}

    assert client.put(
        '/api/v1/settings/execution',
        headers=headers,
        json={'max_parallel_jobs': 0},
    ).status_code == 422
    assert client.put(
        '/api/v1/settings/execution',
        headers=headers,
        json={'max_parallel_jobs': 65},
    ).status_code == 422

    with session() as db:
        raw = db.get(Setting, 'job_execution').value
        assert raw == {'max_parallel_jobs': 3}
