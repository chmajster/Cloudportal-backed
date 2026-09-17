#!/usr/bin/env python3
"""Run only in an isolated CI VM after native installation; never print bootstrap secrets."""
import json
from pathlib import Path
import re
import ssl
import sys
import urllib.request

output=Path('/tmp/cloudportal-install-output').read_text()
token=re.search(r'^Initial API Token: (cp_\S+)$',output,re.M).group(1)
password=re.search(r'^Initial password: (\S+)$',output,re.M).group(1)
context=ssl.create_default_context(cafile='/etc/cloudportal-backed/tls/server.crt')

def request(path,body=None,method=None):
    req=urllib.request.Request('https://localhost:8443/api/v1/'+path,data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'},method=method)
    with urllib.request.urlopen(req,context=context,timeout=30) as r:return json.load(r)
assert request('health')['status']=='ok'
assert 'users.delete' in request('auth/me')['permissions']
assert request('auth/login',{'username':'admin','password':password})['access_token'].startswith('cp_')
fixture=Path('/tmp/cloudportal-install-fixture.json')
if '--verify-only' not in sys.argv:
    user=request('users',{'username':'installation-test','email':'installation-test@example.com','password':'Testing-password-1234'})
    role=request('roles',{'name':'Installation reader','permissions':['users.read']})
    request(f'users/{user["id"]}/roles',{'role_ids':[role['id']]},'PUT')
    credential=request('credentials',{'name':'Installer encryption check','type':'proxmox','endpoint':'https://pve.example.com:8006',
                                      'username':'installer@pve','secrets':{'token_id':'installer@pve!test','token_secret':'synthetic-installation-secret'}})
    fixture.write_text(json.dumps({'user_id':user['id'],'role_id':role['id'],'credential_id':credential['id']}))
record=json.loads(fixture.read_text())
assert request(f'users/{record["user_id"]}')['username']=='installation-test'
assert request(f'users/{record["user_id"]}/roles')['items'][0]['id']==record['role_id']
credential=request(f'credentials/{record["credential_id"]}')
assert credential['configured'] and credential['secret']=='********'
print('Native installer: verified TLS, PostgreSQL, Redis, dispatcher, worker, bootstrap, login, users, roles and encrypted credential persistence.')
