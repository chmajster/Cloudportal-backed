#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import subprocess
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.engine import make_url


DEFAULT_ENV = Path('/etc/cloudportal-backed/backend.env')


def read_env(path):
    values = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if '=' not in line:
            raise SystemExit(f'Invalid environment entry in {path}')
        key, value = line.split('=', 1)
        values[key] = value
    return values


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def postgres_command(binary, url, destination):
    parsed = make_url(url)
    if not parsed.drivername.startswith('postgresql'):
        raise SystemExit('Backup supports PostgreSQL only')
    host = parsed.query.get('host') or parsed.host or ''
    port = str(parsed.port or 5432)
    database = parsed.database or 'cloudportal'
    username = parsed.username or 'cloudportal'
    command = [
        binary,
        '--format=custom',
        '--no-owner',
        '--no-privileges',
        '--file',
        str(destination),
        '--username',
        username,
        '--dbname',
        database,
    ]
    if host:
        command += ['--host', host]
    if port:
        command += ['--port', port]
    env = {'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8'}
    if parsed.password:
        env['PGPASSWORD'] = parsed.password
    if not parsed.password and host.startswith('/') and os.geteuid() == 0:
        runuser = shutil.which('runuser', path=env['PATH'])
        if not runuser:
            raise SystemExit('runuser is required for local PostgreSQL peer authentication but was not found in PATH')
        command = [runuser, '-u', username, '--', *command]
    return command, env


def main():
    parser = argparse.ArgumentParser(description='Create a Cloudportal-backed database backup.')
    parser.add_argument('--output', default='/var/backups/cloudportal-backed')
    parser.add_argument('--env-file', default=str(DEFAULT_ENV))
    parser.add_argument('--retention-days', type=int, default=14)
    args = parser.parse_args()
    if args.retention_days < 1 or args.retention_days > 3650:
        raise SystemExit('--retention-days must be between 1 and 3650')

    env_file = Path(args.env_file)
    config = read_env(env_file)
    master_key = Path(config.get('CP_MASTER_KEY_FILE', '/etc/cloudportal-backed/master.key'))
    if not master_key.is_file():
        raise SystemExit('Master key is missing; refusing to create an unusable backup')

    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    destination = Path(args.output) / stamp
    destination.mkdir(parents=True, mode=0o700, exist_ok=False)
    os.chmod(destination, 0o700)
    dump = destination / 'database.dump'

    command, process_env = postgres_command(
        'pg_dump',
        config.get('CP_DATABASE_URL', 'postgresql+psycopg:///cloudportal?host=/var/run/postgresql'),
        dump,
    )
    subprocess.run(command, env=process_env, check=True)
    os.chmod(dump, 0o600)

    release = ''
    current = Path('/opt/cloudportal-backed/current')
    try:
        release = str(current.resolve(strict=True))
    except OSError:
        pass
    metadata = {
        'format': 1,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'database_sha256': sha256(dump),
        'master_key_sha256': sha256(master_key),
        'release': release,
        'master_key_included': False,
        'restore_requires_matching_master_key': True,
    }
    metadata_file = destination / 'metadata.json'
    metadata_file.write_text(json.dumps(metadata, indent=2, sort_keys=True) + '\n')
    os.chmod(metadata_file, 0o600)

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.retention_days)
    root = Path(args.output)
    for entry in root.iterdir():
        if entry == destination or not entry.is_dir():
            continue
        try:
            created = datetime.strptime(entry.name, '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if created < cutoff:
            shutil.rmtree(entry)

    print(destination)


if __name__ == '__main__':
    main()
