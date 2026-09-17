#!/usr/bin/env python3
"""Explicit opt-in live infrastructure acceptance test, using backend-managed credentials."""
import argparse
import os
import time
import uuid
import httpx

p=argparse.ArgumentParser()
p.add_argument('--url',required=True)
p.add_argument('--token-file',required=True)
p.add_argument('--provider-id',required=True,type=int)
p.add_argument('--credential-id',required=True,type=int)
p.add_argument('--template-id',required=True,type=int)
p.add_argument('--node',required=True)
p.add_argument('--storage',required=True)
p.add_argument('--network',default='vmbr0')
p.add_argument('--ssh-credential-id',required=True,type=int)
p.add_argument('--ssh-user',default='clouduser')
p.add_argument('--ssh-public-key-file',required=True)
p.add_argument('--ca-file')
p.add_argument('--allow-create-and-destroy',action='store_true',required=True)
a=p.parse_args()
from pathlib import Path
if not a.url.startswith('https://'):
    p.error('HTTPS is required')
if (Path(a.token_file).stat().st_mode & 0o077):
    p.error('Token file must have mode 600')
client=httpx.Client(base_url=a.url.rstrip('/')+'/api/v1/',headers={'Authorization':'Bearer '+Path(a.token_file).read_text().strip()},
                    verify=a.ca_file or True,timeout=30)

def request(method,path,payload=None):
    r=client.request(method,path,json=payload,headers={'Idempotency-Key':str(uuid.uuid4()),'X-Request-ID':str(uuid.uuid4())})
    if not r.is_success:
        raise RuntimeError(f'{method} {path}: HTTP {r.status_code}')
    return r.json()

def wait(job):
    deadline=time.monotonic()+4200
    while time.monotonic()<deadline:
        item=request('GET','jobs/'+job)
        if item['status'] in {'successful','failed','cancelled'}:
            logs=request('GET','jobs/'+job+'/logs')
            print('Job',job,'status',item['status'],'request_id',item['request_id'],'log_entries',len(logs['items']))
            if item['status']!='successful':raise RuntimeError('Workflow did not succeed; inspect backend job log')
            return
        time.sleep(5)
    raise RuntimeError('Workflow timeout')

request('GET','auth/me')
request('POST',f'credentials/{a.credential_id}/test')
for kind in ('nodes','storages','networks','templates'):
    request('GET',f'providers/{a.provider_id}/{kind}')
payload={'name':'acceptance-'+uuid.uuid4().hex[:8], 'provider_id':a.provider_id,'credentials_id':a.credential_id,
         'template':'proxmox-vm','variables':{'name':'cp-e2e-'+uuid.uuid4().hex[:8],'node':a.node,
          'template_id':a.template_id,'storage':a.storage,'network':a.network,'ssh_username':a.ssh_user,
          'ssh_public_key':Path(a.ssh_public_key_file).read_text().strip()},
         'ansible':{'playbook':'validate-linux','credentials_id':a.ssh_credential_id,'variables':{}}}
deployment=request('POST','deployments',payload)
print('Created deployment',deployment['id'])
try:
    wait(deployment['job']['id'])
finally:
    # Never force-unlock a still-running operation. Preserve state if a timeout prevents cleanup.
    current=request('GET','deployments/'+deployment['id'])
    if not current['active_job_id']:
        destroyed=request('POST','deployments/'+deployment['id']+'/destroy')
        wait(destroyed['id'])
    else:
        print('Deployment still running; manual cleanup required:',deployment['id'])
client.close()
