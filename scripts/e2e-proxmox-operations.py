#!/usr/bin/env python3
"""Explicit opt-in live Proxmox lifecycle acceptance test for console/backup/restore."""
import argparse
import time
import uuid
from pathlib import Path
from urllib.parse import quote

import httpx


parser = argparse.ArgumentParser()
parser.add_argument('--url', required=True)
parser.add_argument('--token-file', required=True)
parser.add_argument('--provider-id', required=True, type=int)
parser.add_argument('--node', required=True)
parser.add_argument('--vmid', required=True, type=int)
parser.add_argument('--backup-storage', required=True)
parser.add_argument('--restore-vmid', required=True, type=int)
parser.add_argument('--restore-storage')
parser.add_argument('--ca-file')
parser.add_argument('--timeout', type=int, default=7200)
parser.add_argument('--allow-backup-restore-and-delete', action='store_true', required=True)
args = parser.parse_args()

if not args.url.startswith('https://'):
    parser.error('HTTPS is required')
if args.restore_vmid == args.vmid:
    parser.error('restore-vmid must differ from source vmid')
token_path = Path(args.token_file)
if token_path.stat().st_mode & 0o077:
    parser.error('Token file must have mode 600')

client = httpx.Client(
    base_url=args.url.rstrip('/') + '/api/v1/',
    headers={'Authorization': 'Bearer ' + token_path.read_text().strip()},
    verify=args.ca_file or True,
    timeout=30,
)


def request(method, path, payload=None):
    response = client.request(
        method,
        path,
        json=payload,
        headers={
            'X-Request-ID': str(uuid.uuid4()),
            'Idempotency-Key': str(uuid.uuid4()),
        },
    )
    if not response.is_success:
        raise RuntimeError(f'{method} {path}: HTTP {response.status_code}')
    return response.json()


def wait_task(node, upid):
    deadline = time.monotonic() + args.timeout
    encoded = quote(upid, safe='')
    while time.monotonic() < deadline:
        status = request('GET', f'providers/{args.provider_id}/tasks/{node}/{encoded}')
        if status.get('status') == 'stopped':
            if status.get('exitstatus') != 'OK':
                raise RuntimeError('Proxmox task failed; inspect PVE task log')
            return status
        time.sleep(3)
    raise RuntimeError('Proxmox task timeout')


request('GET', 'auth/me')
provider = request('GET', f'providers/{args.provider_id}')
if provider['type'] != 'proxmox':
    raise RuntimeError('Selected provider is not Proxmox')

vms = request('GET', f'providers/{args.provider_id}/vms?node={args.node}')['items']
if not any(int(vm.get('vmid', -1)) == args.vmid for vm in vms):
    raise RuntimeError('Source VM does not exist on selected node')
if any(int(vm.get('vmid', -1)) == args.restore_vmid for vm in vms):
    raise RuntimeError('restore-vmid already exists; refusing destructive test')

console = request('POST', f'providers/{args.provider_id}/vms/{args.node}/{args.vmid}/console')
if not console.get('ticket') or not console.get('port'):
    raise RuntimeError('Console endpoint did not return an ephemeral ticket and port')
print('Console ticket issued successfully; secret value was not printed.')

before = {
    row['volid']
    for row in request(
        'GET',
        f'providers/{args.provider_id}/vms/{args.node}/{args.vmid}/backups?storage={args.backup_storage}',
    )['items']
    if row.get('volid')
}
backup = request(
    'POST',
    f'providers/{args.provider_id}/vms/{args.node}/{args.vmid}/backups',
    {
        'storage': args.backup_storage,
        'mode': 'snapshot',
        'compress': 'zstd',
        'notes': 'Cloudportal-backed live acceptance test',
    },
)
if not backup.get('task'):
    raise RuntimeError('Backup did not return a Proxmox task')
wait_task(args.node, backup['task'])

after_rows = request(
    'GET',
    f'providers/{args.provider_id}/vms/{args.node}/{args.vmid}/backups?storage={args.backup_storage}',
)['items']
new_backups = [row for row in after_rows if row.get('volid') and row['volid'] not in before]
if not new_backups:
    raise RuntimeError('Backup completed but no new backup volume was discovered')
archive = sorted(new_backups, key=lambda row: row.get('ctime') or 0)[-1]['volid']
print('Backup created:', archive)

restore_payload = {
    'vm_id': args.restore_vmid,
    'archive': archive,
    'unique': True,
}
if args.restore_storage:
    restore_payload['storage'] = args.restore_storage
restored = request(
    'POST',
    f'providers/{args.provider_id}/restore/{args.node}',
    restore_payload,
)
if not restored.get('task'):
    raise RuntimeError('Restore did not return a Proxmox task')

try:
    wait_task(args.node, restored['task'])
    restored_status = request(
        'GET',
        f'providers/{args.provider_id}/vms/{args.node}/{args.restore_vmid}/status',
    )
    if int(restored_status.get('vmid', -1)) != args.restore_vmid:
        raise RuntimeError('Restored VM was not found')
    print('Restore verified for VMID', args.restore_vmid)
finally:
    current = request('GET', f'providers/{args.provider_id}/vms?node={args.node}')['items']
    if any(int(vm.get('vmid', -1)) == args.restore_vmid for vm in current):
        deleted = request(
            'DELETE',
            f'providers/{args.provider_id}/vms/{args.node}/{args.restore_vmid}?purge=true&destroy_unreferenced_disks=true',
        )
        if deleted.get('task'):
            wait_task(args.node, deleted['task'])
        print('Restored acceptance-test VM cleaned up:', args.restore_vmid)

client.close()
