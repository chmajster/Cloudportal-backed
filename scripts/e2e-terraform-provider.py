#!/usr/bin/env python3
"""Explicit opt-in live Terraform acceptance test for any approved provider template."""
import argparse
import json
import time
import uuid
from pathlib import Path

import httpx


parser = argparse.ArgumentParser()
parser.add_argument('--url', required=True)
parser.add_argument('--token-file', required=True)
parser.add_argument('--provider-id', required=True, type=int)
parser.add_argument('--credential-id', required=True, type=int)
parser.add_argument('--template', required=True)
parser.add_argument('--variables-file', required=True)
parser.add_argument('--executor', choices=['terraform', 'opentofu'], default='terraform')
parser.add_argument('--timeout', type=int, default=4200)
parser.add_argument('--ca-file')
parser.add_argument('--allow-create-and-destroy', action='store_true', required=True)
args = parser.parse_args()

if not args.url.startswith('https://'):
    parser.error('HTTPS is required')
token_path = Path(args.token_file)
if token_path.stat().st_mode & 0o077:
    parser.error('Token file must have mode 600')
variables_path = Path(args.variables_file)
variables = json.loads(variables_path.read_text())
if not isinstance(variables, dict):
    parser.error('variables-file must contain one JSON object')

headers = {'Authorization': 'Bearer ' + token_path.read_text().strip()}
client = httpx.Client(
    base_url=args.url.rstrip('/') + '/api/v1/',
    headers=headers,
    verify=args.ca_file or True,
    timeout=30,
)


def request(method, path, payload=None):
    request_headers = {
        'X-Request-ID': str(uuid.uuid4()),
        'Idempotency-Key': str(uuid.uuid4()),
    }
    response = client.request(method, path, json=payload, headers=request_headers)
    if not response.is_success:
        raise RuntimeError(f'{method} {path}: HTTP {response.status_code}')
    return response.json()


def wait(job_id):
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        item = request('GET', 'jobs/' + job_id)
        if item['status'] in {'successful', 'failed', 'cancelled'}:
            logs = request('GET', 'jobs/' + job_id + '/logs')
            print(
                'Job', job_id,
                'status', item['status'],
                'request_id', item['request_id'],
                'log_entries', len(logs['items']),
            )
            if item['status'] != 'successful':
                raise RuntimeError('Workflow failed; inspect sanitized backend job logs')
            return item
        time.sleep(5)
    raise RuntimeError('Workflow timeout')


request('GET', 'auth/me')
credential = request('GET', f'credentials/{args.credential_id}')
provider = request('GET', f'providers/{args.provider_id}')
if provider['credentials_id'] != credential['id'] or provider['type'] != credential['type']:
    raise RuntimeError('Provider and credential do not match')
request('POST', f'credentials/{args.credential_id}/test')

templates = request('GET', 'templates')['items']
template = next((item for item in templates if item['id'] == args.template), None)
if template is None:
    raise RuntimeError('Requested template is not published by backend')
if template['provider'] != provider['type']:
    raise RuntimeError('Template provider does not match selected provider')

deployment = request('POST', 'deployments', {
    'name': 'acceptance-' + uuid.uuid4().hex[:8],
    'provider_id': args.provider_id,
    'credentials_id': args.credential_id,
    'template': args.template,
    'executor': args.executor,
    'variables': variables,
})
print('Created deployment', deployment['id'], 'provider', provider['type'], 'template', args.template)

try:
    wait(deployment['job']['id'])
    resources = request('GET', 'inventory/resources?limit=500')['items']
    managed = next((row for row in resources if row.get('deployment_id') == deployment['id']), None)
    if managed is None or managed.get('lifecycle_status') != 'active':
        raise RuntimeError('Successful apply did not register an active ManagedResource')
    print('Managed resource registered:', managed['resource_type'], managed['external_id'])
finally:
    current = request('GET', 'deployments/' + deployment['id'])
    if not current['active_job_id'] and current['status'] != 'destroyed':
        destroyed = request('POST', 'deployments/' + deployment['id'] + '/destroy')
        wait(destroyed['id'])
    elif current['active_job_id']:
        print('Deployment still running; manual cleanup required:', deployment['id'])

client.close()
