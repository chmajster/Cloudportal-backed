"""Real PHP -> HTTPS FastAPI contract test; enable with PORTAL_PATH and PHP_BINARY."""
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid
import httpx
import pytest


@pytest.mark.skipif(not os.environ.get('PORTAL_PATH'), reason='Set PORTAL_PATH for cross-repository PHP HTTP integration')
def test_portal_setup_login_rbac_and_crud(system, tmp_path):
    portal=Path(os.environ['PORTAL_PATH']).resolve()
    config=portal/'config/backend.json'
    setup=portal/'storage/backend-setup.token'
    assert not config.exists() and not setup.exists(), 'Use an isolated portal checkout'
    cert=tmp_path/'server.crt';key=tmp_path/'server.key'
    subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-keyout',str(key),'-out',str(cert),'-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost,IP:127.0.0.1'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    php=[os.environ.get('PHP_BINARY','php')]
    if os.environ.get('PHP_INI'):
        php+=['-c',os.environ['PHP_INI']]
    php+=['-d','session.save_path='+str(tmp_path)]
    env={**os.environ,'CP_BACKEND_CA_FILE':str(cert)}
    with (tmp_path/'backend.log').open('w') as blog, (tmp_path/'php.log').open('w') as plog:
        backend=subprocess.Popen([os.sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port','18443','--ssl-certfile',str(cert),'--ssl-keyfile',str(key),'--no-access-log'],env=env,stdout=blog,stderr=blog)
        frontend=subprocess.Popen(php+['-S','127.0.0.1:18780','-t',str(portal/'public')],env=env,cwd=portal,stdout=plog,stderr=plog)
        try:
            for _ in range(100):
                try:
                    if httpx.get('https://127.0.0.1:18443/api/v1/health',verify=str(cert),timeout=1,trust_env=False).status_code in (200,503):break
                except httpx.HTTPError:time.sleep(.1)
            setup_output=subprocess.check_output(php+[str(portal/'bin/backend-setup.php')],env=env,text=True)
            setup_key=re.search(r'once\): ([a-f0-9]+)',setup_output).group(1)
            with httpx.Client(base_url='http://127.0.0.1:18780',follow_redirects=False,timeout=30,trust_env=False) as browser:
                def csrf():
                    page=browser.get('/settings/infrastructure/backend')
                    assert page.status_code==200,page.text
                    return re.search(r'name="_csrf" value="([^"]+)"',page.text).group(1)
                response=browser.post('/settings/infrastructure/backend',data={'_csrf':csrf(),'setup_key':setup_key,'url':'https://127.0.0.1:18443','token':system[2]['token'],'timeout':'30','verify_tls':'1','action':'save'})
                assert response.status_code==302,response.text
                assert not setup.exists() and config.exists()
                page=browser.get('/login');csrf_token=re.search(r'name="_csrf" value="([^"]+)"',page.text).group(1)
                response=browser.post('/login',data={'_csrf':csrf_token,'username':'admin','password':system[2]['password']})
                assert response.status_code==302,response.text
                dashboard=browser.get('/')
                assert dashboard.status_code==200,dashboard.text
                boot=json.loads(re.search(r'<script id="portal-data" type="application/json">(.*?)</script>',dashboard.text,re.S).group(1))
                auth={'X-CSRF-Token':boot['csrf'],'Accept':'application/json','Idempotency-Key':str(uuid.uuid4())}
                assert system[2]['token'] not in dashboard.text
                # The bootstrap administrator must replace the one-time password
                # before any privileged operation. The password change revokes the
                # current backend session, so authenticate again afterwards.
                changed_password='secure-php-admin-password-1234'
                changed=browser.post('/backend-api/auth/change-password',headers=auth,json={
                    'current_password':system[2]['password'],'password':changed_password})
                assert changed.status_code==200,changed.text
                page=browser.get('/login');csrf_token=re.search(r'name="_csrf" value="([^"]+)"',page.text).group(1)
                response=browser.post('/login',data={'_csrf':csrf_token,'username':'admin','password':changed_password})
                assert response.status_code==302,response.text
                dashboard=browser.get('/')
                boot=json.loads(re.search(r'<script id="portal-data" type="application/json">(.*?)</script>',dashboard.text,re.S).group(1))
                auth={'X-CSRF-Token':boot['csrf'],'Accept':'application/json','Idempotency-Key':str(uuid.uuid4())}
                user=browser.post('/backend-api/users',headers=auth,json={'username':'php-user','email':'php-user@example.com','password':'test-password-1234'})
                assert user.status_code==201,user.text
                role=browser.post('/backend-api/roles',headers={**auth,'Idempotency-Key':str(uuid.uuid4())},json={'name':'PHP Reader','permissions':['users.read']})
                assert role.status_code==201,role.text
                assert browser.put(f'/backend-api/users/{user.json()["id"]}/roles',headers=auth,json={'role_ids':[role.json()['id']]}).status_code==200
                assert browser.get('/api/v1/admin/users',headers={'Accept':'application/json'}).status_code==404
                assert browser.post('/backend-api/users',headers={'Accept':'application/json','Authorization':'Bearer forged'},json={}).status_code==419
                credential=browser.post('/backend-api/credentials',headers={**auth,'Idempotency-Key':str(uuid.uuid4())},json={'name':'PHP PVE','type':'proxmox','endpoint':'https://pve.example.com:8006','username':'root@pam','secrets':{'token_id':'root@pam!test','token_secret':'do-not-expose-this-secret'}})
                assert credential.status_code==201,credential.text
                assert 'do-not-expose-this-secret' not in browser.get('/backend-api/credentials',headers=auth).text
                # The PHP layer forwards jobs, JSON object types, statuses and request IDs.
                ssh=browser.post('/backend-api/credentials',headers={**auth,'Idempotency-Key':str(uuid.uuid4())},json={
                    'name':'PHP SSH','type':'ssh','username':'clouduser',
                    'secrets':{'password':'private-ssh-password','known_hosts':'host ssh-ed25519 test'}})
                assert ssh.status_code==201,ssh.text
                request_id=str(uuid.uuid4())
                job_headers={**auth,'Idempotency-Key':str(uuid.uuid4()),'X-Request-ID':request_id}
                payload={'operation':'ansible.execute','ansible':{'playbook':'validate-linux','credentials_id':ssh.json()['id'],
                         'inventory':{'hosts':['192.0.2.1']},'variables':{}}}
                job=browser.post('/backend-api/jobs',headers=job_headers,json=payload)
                assert job.status_code==202,job.text
                assert job.json()['request_id']==request_id and job.headers['X-Request-ID']==request_id
                repeated=browser.post('/backend-api/jobs',headers=job_headers,json=payload)
                assert repeated.status_code==202 and repeated.json()['id']==job.json()['id']
                assert browser.post('/backend-api/jobs/'+job.json()['id']+'/cancel',headers=auth,json={}).status_code==200
                assert browser.get('/backend-api/jobs/'+job.json()['id'],headers=auth).json()['status']=='cancelled'
                audit=browser.get('/backend-api/audit?request_id='+request_id,headers=auth).json()['items']
                assert any(row['action']=='job.created' for row in audit)
                for path in ['/api/v1/admin/users','/api/v1/vms','/api/v1/jobs','/api/v1/ansible/run']:
                    assert browser.post(path,headers=auth,json={}).status_code==404
                # Existing local executors cannot provide a fallback if the backend is offline.
                for script in ('worker.php','console-gateway.php'):
                    stopped=subprocess.run(php+[str(portal/'bin'/script)],env=env,cwd=portal,capture_output=True,text=True,timeout=10)
                    assert stopped.returncode==1 and 'Cloudportal-backed' in stopped.stderr
                assert browser.get('/settings/infrastructure/backend').status_code==200
                assert system[2]['token'] not in browser.get('/settings/infrastructure/backend').text
                assert browser.post('/logout',data={'_csrf':boot['csrf']}).status_code==302
                page=browser.get('/login');csrf_token=re.search(r'name="_csrf" value="([^"]+)"',page.text).group(1)
                assert browser.post('/login',data={'_csrf':csrf_token,'username':'php-user','password':'test-password-1234'}).status_code==302
                dashboard=browser.get('/')
                assert 'href="/admin/roles"' not in dashboard.text
                assert browser.get('/backend-api/roles',headers={'Accept':'application/json'}).status_code==403
                backend.terminate()
                backend.wait(timeout=15)
                assert browser.get('/backend-api/users',headers={'Accept':'application/json'}).status_code==503
        finally:
            for process in (frontend,backend):
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=15)
            config.unlink(missing_ok=True)
            setup.unlink(missing_ok=True)
