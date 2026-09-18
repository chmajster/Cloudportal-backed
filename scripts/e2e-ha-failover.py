#!/usr/bin/env python3
"""Explicit opt-in multi-host HA readiness and worker failover drill."""
import argparse
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path

import httpx


HOST = re.compile(r'^[A-Za-z0-9_.:-]{1,255}$')
USER = re.compile(r'^[A-Za-z_][A-Za-z0-9_.-]{0,63}$')


def ssh_command(host, user, key_file, known_hosts, remote):
    if not HOST.fullmatch(host) or not USER.fullmatch(user):
        raise ValueError('Invalid SSH host or user')
    return [
        'ssh',
        '-o', 'BatchMode=yes',
        '-o', 'StrictHostKeyChecking=yes',
        '-o', f'UserKnownHostsFile={known_hosts}',
        '-i', str(key_file),
        f'{user}@{host}',
        remote,
    ]


def run_ssh(host, args, remote, *, capture=True, check=True):
    result = subprocess.run(
        ssh_command(host, args.ssh_user, args.ssh_key_file, args.known_hosts_file, remote),
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        timeout=30,
        check=False,
    )
    if check and result.returncode:
        detail = (result.stderr or '').strip().splitlines()[-1:] or ['remote command failed']
        raise RuntimeError(f'{host}: {detail[0]}')
    return (result.stdout or '').strip(), result.returncode


def host_fingerprint(host, args):
    remote = (
        'sudo sha256sum /etc/cloudportal-backed/master.key; '
        'sudo sh -c \'grep -E "^(CP_DATABASE_URL|CP_REDIS_URL|CP_MASTER_KEY_FILE)=" '
        '/etc/cloudportal-backed/backend.env | sort | sha256sum\'; '
        'sudo systemctl is-active --quiet cloudportal-worker@1.service'
    )
    output, _ = run_ssh(host, args, remote)
    lines = [line for line in output.splitlines() if line.strip()]
    if len(lines) < 2:
        raise RuntimeError(f'{host}: incomplete HA fingerprint')
    values = {
        'key': lines[0].split()[0],
        'control': lines[1].split()[0],
    }
    if len(values['key']) != 64 or len(values['control']) != 64:
        raise RuntimeError(f'{host}: invalid HA fingerprint')
    return values


def wait_health(client, minimum_online, timeout):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            response = client.get('/api/v1/health')
            if response.status_code not in {200, 503}:
                response.raise_for_status()
            last = response.json()
            online = int(last['checks']['workers']['online'])
            if online >= minimum_online:
                return last
        except Exception:
            pass
        time.sleep(2)
    raise RuntimeError(f'Health did not reach >= {minimum_online} workers; last={last!r}')


def main():
    parser = argparse.ArgumentParser(description='Validate multi-host Cloudportal-backed HA and optionally exercise one worker failure.')
    parser.add_argument('--url', required=True)
    parser.add_argument('--worker-host', action='append', required=True, dest='worker_hosts')
    parser.add_argument('--ssh-user', required=True)
    parser.add_argument('--ssh-key-file', required=True, type=Path)
    parser.add_argument('--known-hosts-file', required=True, type=Path)
    parser.add_argument('--ca-file', type=Path)
    parser.add_argument('--timeout', type=int, default=120)
    parser.add_argument('--exercise-failover', action='store_true')
    args = parser.parse_args()

    if len(args.worker_hosts) < 2:
        parser.error('At least two --worker-host values are required')
    for path in (args.ssh_key_file, args.known_hosts_file):
        if not path.is_file() or path.stat().st_mode & 0o077:
            parser.error(f'{path} must exist and have mode 600')
    verify = str(args.ca_file) if args.ca_file else True
    client = httpx.Client(base_url=args.url.rstrip('/'), verify=verify, timeout=15, follow_redirects=False)

    baseline = wait_health(client, 2, args.timeout)
    online = int(baseline['checks']['workers']['online'])
    expected = int(baseline['checks']['workers']['expected'])
    if online < 2:
        raise RuntimeError('Backend does not currently expose at least two live workers')

    fingerprints = {host: host_fingerprint(host, args) for host in args.worker_hosts}
    key_hashes = {value['key'] for value in fingerprints.values()}
    control_hashes = {value['control'] for value in fingerprints.values()}
    if len(key_hashes) != 1:
        raise RuntimeError('Workers do not share the same master key fingerprint')
    if len(control_hashes) != 1:
        raise RuntimeError('Workers do not share the same DB/Redis/master-key configuration fingerprint')

    result = {
        'workers_online': online,
        'workers_expected': expected,
        'hosts_checked': len(args.worker_hosts),
        'shared_master_key': True,
        'shared_control_plane_config': True,
        'failover_exercised': False,
    }

    if args.exercise_failover:
        victim = args.worker_hosts[0]
        stopped = False
        try:
            run_ssh(victim, args, 'sudo systemctl stop cloudportal-worker@1.service', capture=False)
            stopped = True
            deadline = time.monotonic() + args.timeout
            observed_drop = False
            while time.monotonic() < deadline:
                response = client.get('/api/v1/health')
                if response.status_code in {200, 503}:
                    current = int(response.json()['checks']['workers']['online'])
                    if current < online:
                        observed_drop = True
                        if current < 1:
                            raise RuntimeError('Stopping one worker left no online worker')
                        break
                time.sleep(2)
            if not observed_drop:
                raise RuntimeError('Worker count did not drop after stopping the selected worker')
        finally:
            if stopped:
                run_ssh(victim, args, 'sudo systemctl start cloudportal-worker@1.service', capture=False, check=False)
        wait_health(client, online, args.timeout)
        result['failover_exercised'] = True
        result['failed_host'] = victim

    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
