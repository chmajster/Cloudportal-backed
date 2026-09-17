from datetime import timedelta
from sqlalchemy import select
from app.database import session
from app.models import Token, now
from conftest import new_user


def test_bootstrap_is_once_and_hashes(system):
    from app.bootstrap import bootstrap
    client, headers, admin = system
    with session() as db:
        token = db.scalar(select(Token).where(Token.kind == 'api'))
        assert token.token_hash != admin['token'] and len(token.token_hash) == 64
        assert bootstrap(db) is None
    response = client.get('/api/v1/auth/me', headers=headers)
    assert response.status_code == 200
    assert 'users.delete' in response.json()['permissions']
    assert 'password_hash' not in response.text and admin['token'] not in response.text


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
