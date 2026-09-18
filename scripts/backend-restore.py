#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

from sqlalchemy.engine import make_url


DEFAULT_ENV = Path('/etc/cloudportal-backed/backend.env')
SERVICES = ['cloudportal-api', 'cloudportal-dispatcher']


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


def postgres_command(url, dump):
    parsed = make_url(url)
    if not parsed.drivername.startswith('postgresql'):
        raise SystemExit('Restore supports PostgreSQL only')
    host = parsed.query.get('host') or parsed.host or ''
    port = str(parsed.port or 5432)
    database = parsed.database or 'cloudportal'
    username = parsed.username or 'cloudportal'
    command = [
        'pg_restore',
        '--clean',
        '--if-exists',
        '--no-owner',
        '--no-privileges',
        '--exit-on-error',
        '--username',
        username,
        '--dbname',
        database,
    ]
    if host:
        command += ['--host', host]
    if port:
        command += ['--port', port]
    command.append(str(dump))
    env = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8'}
    if parsed.password:
        env['PGPASSWORD'] = parsed.password
    if not parsed.password and host.startswith('/') and os.geteuid() == 0:
        command = ['runuser', '-u', username, '--', *command]
    return command, env


def worker_units():
    result = subprocess.run(
        ['systemctl', 'list-units', '--all', '--type=service', '--no-legend', 'cloudportal-worker@*.service'],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return [line.split()[0] for line in result.stdout.splitlines() if line.strip()]


def main():
    parser = argparse.ArgumentParser(description='Restore a Cloudportal-backed database backup.')
    parser.add_argument('--backup', required=True)
    parser.add_argument('--env-file', default=str(DEFAULT_ENV))
    parser.add_argument('--yes-replace-database', action='store_true')
    args = parser.parse_args()

    if os.geteuid() != 0:
        raise SystemExit('Run the restore command as root')
    if not args.yes_replace_database:
        raise SystemExit('Restore is destructive; pass --yes-replace-database')

    backup = Path(args.backup).resolve()
    metadata_file = backup / 'metadata.json'
    dump = backup / 'database.dump'
    if not metadata_file.is_file() or not dump.is_file():
        raise SystemExit('Backup directory is incomplete')
    metadata = json.loads(metadata_file.read_text())
    if metadata.get('format') != 1 or metadata.get('database_sha256') != sha256(dump):
        raise SystemExit('Backup integrity validation failed')

    config = read_env(Path(args.env_file))
    master_key = Path(config.get('CP_MASTER_KEY_FILE', '/etc/cloudportal-backed/master.key'))
    if not master_key.is_file() or metadata.get('master_key_sha256') != sha256(master_key):
        raise SystemExit('Master key fingerprint does not match this backup; restore aborted')

    units = [*SERVICES, *worker_units()]
    subprocess.run(['systemctl', 'stop', *units], check=True)
    try:
        command, process_env = postgres_command(
            config.get('CP_DATABASE_URL', 'postgresql+psycopg:///cloudportal?host=/var/run/postgresql'),
            dump,
        )
        subprocess.run(command, env=process_env, check=True)
    finally:
        subprocess.run(['systemctl', 'start', *units], check=False)
    print('Restore completed; master key fingerprint matched the backup metadata.')


if __name__ == '__main__':
    main()
