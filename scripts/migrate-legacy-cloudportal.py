#!/usr/bin/env python3
import argparse
import base64
import json
import os
import stat
from pathlib import Path

from sqlalchemy import create_engine, select

from app.database import session
from app.migration.legacy import LegacyCloudPortalMigrator
from app.models import User


def read_key(path):
    file = Path(path)
    info = file.stat()
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
        raise SystemExit('Legacy key file must be a regular file with mode 0600')
    raw = file.read_text().strip()
    key = base64.b64decode(raw, validate=True)
    if len(key) != 32:
        raise SystemExit('Legacy encryption key must be base64 encoded 32 bytes')
    return key


def main():
    parser = argparse.ArgumentParser(description='Migrate supported data from legacy HomeLAB-Proxmox-CloudPortal')
    parser.add_argument('--source-dsn', default=os.environ.get('LEGACY_DATABASE_URL'))
    parser.add_argument('--source-key-file', default=os.environ.get('LEGACY_ENCRYPTION_KEY_FILE'))
    parser.add_argument('--actor-username', default='admin')
    parser.add_argument('--apply', action='store_true', help='Commit changes. Without this flag the migration is a dry-run.')
    parser.add_argument('--grant-legacy-admin', action='store_true',
                        help='Map legacy admin role to backend Administrator. Default imports it as a permissionless Legacy role.')
    args = parser.parse_args()
    if not args.source_dsn or not args.source_key_file:
        raise SystemExit('Set LEGACY_DATABASE_URL and LEGACY_ENCRYPTION_KEY_FILE or pass both arguments')

    source = create_engine(args.source_dsn, pool_pre_ping=True)
    key = read_key(args.source_key_file)
    with session() as db:
        actor = db.scalar(select(User).where(User.username == args.actor_username))
        if actor is None:
            raise SystemExit('Target actor user not found')
        migrator = LegacyCloudPortalMigrator(
            source, db, key, actor.id,
            apply=args.apply,
            grant_legacy_admin=args.grant_legacy_admin,
        )
        report = migrator.run()
        if args.apply:
            db.commit()
        print(json.dumps({
            'mode': 'apply' if args.apply else 'dry-run',
            **report.as_dict(),
        }, indent=2, default=str))


if __name__ == '__main__':
    main()
