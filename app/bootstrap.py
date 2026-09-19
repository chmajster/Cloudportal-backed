"""Run as the unprivileged cloudportal user; no bootstrap HTTP endpoint."""
import argparse
import base64
import os
from pathlib import Path
from sqlalchemy import select, text, inspect
from app.config import settings
from app.database import session, engine
from app.models import Role, Setting, User
from app.rbac.service import ALL_PERMISSIONS, seed
from app.security.core import encryption_key, issue_token, password_hasher


def generate_key():
    path = settings().master_key_file
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.exists():
        # Losing a key must never silently create an incompatible replacement.
        with session() as db:
            if inspect(engine()).has_table('settings') and db.get(Setting, 'bootstrapped'):
                raise RuntimeError('Master key missing on an existing installation; restore it from a secure backup')
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        encryption_key()
        return False
    with os.fdopen(fd, 'wb') as file:
        file.write(base64.b64encode(os.urandom(32)) + b'\n')
        file.flush()
        os.fsync(file.fileno())
    return True


def bootstrap(db):
    if db.bind.dialect.name == 'postgresql':
        db.execute(text('SELECT pg_advisory_xact_lock(613040621)'))
    if db.get(Setting, 'bootstrapped'):
        # Existing installations still need idempotent RBAC synchronization after
        # upgrades so the built-in Administrator receives newly added permissions.
        seed(db)
        db.commit()
        return None
    seed(db)
    if db.scalar(select(User.id).limit(1)):
        raise RuntimeError('Existing users without bootstrap marker; refusing automatic privilege changes')
    password = 'admin'
    user = User(username='admin', email='admin@localhost.example', password_hash=password_hasher.hash(password),
                must_change_password=True,
                roles=[db.scalar(select(Role).where(Role.name == 'Administrator'))])
    db.add(user)
    db.flush()
    _, token = issue_token(db, user, 'Initial Administrator Token', ALL_PERMISSIONS)
    db.add(Setting(key='bootstrapped', value={'version': 1}))
    db.commit()
    return {'username': 'admin', 'password': password, 'token': token}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--key-only', action='store_true')
    parser.add_argument('--url', default='https://localhost:8443')
    args = parser.parse_args()
    generate_key()
    if args.key_only:
        return
    with session() as db:
        result = bootstrap(db)
    if result is None:
        print('Existing installation preserved. Bootstrap credentials are never displayed again.')
        return
    print('====================================================\nCloudportal-backed installed successfully\n====================================================')
    print('Backend URL: ' + args.url)
    print('Administrator: ' + result['username'])
    print('Initial password: ' + result['password'])
    print('Initial API Token: ' + result['token'])
    print('The Initial API Token is displayed ONLY ONCE. Save it now.')
    print('The default admin/admin password must be changed at the first login.')
    print('Create a dedicated Portal Service account/token; retire the bootstrap token after setup.')
    print('Health: ' + args.url + '/api/v1/health\nSwagger: ' + args.url + '/docs')
    print('====================================================')


if __name__ == '__main__':
    main()
