#!/usr/bin/env python3
"""Run only in an isolated CI VM after native installation; never print bootstrap secrets."""
import json
from pathlib import Path
import re
import ssl
import urllib.request

output=Path('/tmp/cloudportal-install-output').read_text()
token=re.search(r'^Initial API Token: (cp_\S+)$',output,re.M).group(1)
password=re.search(r'^Initial password: (\S+)$',output,re.M).group(1)
context=ssl.create_default_context(cafile='/etc/cloudportal-backed/tls/server.crt')

def request(path,body=None):
    req=urllib.request.Request('https://localhost:8443/api/v1/'+path,data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    with urllib.request.urlopen(req,context=context,timeout=30) as r:return json.load(r)
assert request('health')['status']=='ok'
assert 'users.delete' in request('auth/me')['permissions']
assert request('auth/login',{'username':'admin','password':password})['access_token'].startswith('cp_')
user=request('users',{'username':'installation-test','email':'installation-test@example.com','password':'Testing-password-1234'})
assert user['id']>0
print('Native installer: TLS, database, Redis, worker, bootstrap, login and users verified.')
