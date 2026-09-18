#!/usr/bin/env python3
"""Restore a Cloudportal-backed backup into an explicitly confirmed isolated PostgreSQL database."""
import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def read_env(path):
    values = {}
    for raw in Path(path).read_text().splitlines():
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
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def target_guard(target_url, production_url, confirmed_name):
    target = make_url(target_url)
    if not target.drivername.startswith('postgresql'):
        raise SystemExit('DR drill supports PostgreSQL only')
    if not target.database or target.database != confirmed_name:
        raise SystemExit('--confirm-isolated-database must exactly match the target database name')
    if production_url:
        production = make_url(production_url)
        same_host = (target.host or '') == (production.host or '')
        same_port = (target.port or 5432) == (production.port or 5432)
        same_database = target.database == production.database
        same_socket = target.query.get('host') == production.query.get('host')
        if same_database and same_port and (same_host or same_socket):
            raise SystemExit('Refusing to restore over the configured production database')
    return target


def pg_restore_command(url, dump):
    parsed = make_url(url)
    host = parsed.query.get('host') or parsed.host or ''
    command = [
        'pg_restore', '--clean', '--if-exists', '--no-owner', '--no-privileges',
        '--exit-on-error', '--username', parsed.username or 'cloudportal',
        '--dbname', parsed.database,
    ]
    if host:
        command += ['--host', host]
    command += ['--port', str(parsed.port or 5432), str(dump)]
    env = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8'}
    if parsed.password:
        env['PGPASSWORD'] = parsed.password
    return command, env


def verify_restored_database(target_url, runtime_env):
    for key, value in runtime_env.items():
        if key.startswith('CP_'):
            os.environ[key] = value
    os.environ['CP_DATABASE_URL'] = target_url

    from app.config import settings
    from app.database import engine, session
    from app.models import Credential
    from app.security.core import decrypt_secret

    settings.cache_clear()
    engine.cache_clear()
    result = {'alembic_version': None, 'users': 0, 'credentials': 0, 'credential_decryption': 'not-applicable'}
    with create_engine(target_url, pool_pre_ping=True).connect() as connection:
        result['alembic_version'] = connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
        result['users'] = int(connection.execute(text('SELECT COUNT(*) FROM users')).scalar_one())
        result['credentials'] = int(connection.execute(text('SELECT COUNT(*) FROM credentials')).scalar_one())

    if result['credentials']:
        with session() as db:
            credential = db.query(Credential).order_by(Credential.id).first()
            decrypt_secret(credential)
            result['credential_decryption'] = 'ok'
    return result


def main():
    parser = argparse.ArgumentParser(description='Destructive restore drill into an isolated PostgreSQL database.')
    parser.add_argument('--backup', required=True, type=Path)
    parser.add_argument('--target-database-url', required=True)
    parser.add_argument('--source-env-file', default='/etc/cloudportal-backed/backend.env', type=Path)
    parser.add_argument('--confirm-isolated-database', required=True)
    parser.add_argument('--allow-destructive-isolated-restore', action='store_true', required=True)
    args = parser.parse_args()

    backup = args.backup.resolve()
    metadata_file = backup / 'metadata.json'
    dump = backup / 'database.dump'
    if not metadata_file.is_file() or not dump.is_file():
        raise SystemExit('Backup directory is incomplete')
    metadata = json.loads(metadata_file.read_text())
    if metadata.get('format') != 1 or metadata.get('database_sha256') != sha256(dump):
        raise SystemExit('Backup integrity validation failed')

    runtime = read_env(args.source_env_file)
    production_url = runtime.get('CP_DATABASE_URL', '')
    target_guard(args.target_database_url, production_url, args.confirm_isolated_database)

    master_key = Path(runtime.get('CP_MASTER_KEY_FILE', '/etc/cloudportal-backed/master.key'))
    if not master_key.is_file() or metadata.get('master_key_sha256') != sha256(master_key):
        raise SystemExit('Master key fingerprint does not match backup metadata')

    command, process_env = pg_restore_command(args.target_database_url, dump)
    subprocess.run(command, env=process_env, check=True)
    verification = verify_restored_database(args.target_database_url, runtime)
    print(json.dumps({
        'status': 'ok',
        'backup': str(backup),
        'isolated_database': args.confirm_isolated_database,
        'verification': verification,
    }, indent=2, default=str))


if __name__ == '__main__':
    main()
