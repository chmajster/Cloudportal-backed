#!/usr/bin/env python3
import argparse
import json

from sqlalchemy import select

from app.config import settings
from app.database import session
from app.models import Credential, TerraformState, WebhookEndpoint
from app.security.core import decrypt_blob, encrypt_blob


def main():
    parser = argparse.ArgumentParser(description='Re-encrypt backend secrets with the currently configured secret backend')
    parser.add_argument('--apply', action='store_true', help='Commit changes. Default is dry-run.')
    args = parser.parse_args()
    if settings().secret_backend == 'local':
        raise SystemExit('Set CP_SECRET_BACKEND=aws-kms or vault-transit before rewrapping')

    counts = {'credentials': 0, 'terraform_states': 0, 'webhooks': 0}
    with session() as db:
        for row in db.scalars(select(Credential)).all():
            aad = f'credential:{row.id}'
            row.encrypted_secret = encrypt_blob(decrypt_blob(row.encrypted_secret, aad), aad)
            counts['credentials'] += 1
        for row in db.scalars(select(TerraformState)).all():
            aad = f'terraform-state:{row.deployment_id}'
            row.encrypted_state = encrypt_blob(decrypt_blob(row.encrypted_state, aad), aad)
            counts['terraform_states'] += 1
        for row in db.scalars(select(WebhookEndpoint)).all():
            aad = f'webhook:{row.id}'
            row.encrypted_secret = encrypt_blob(decrypt_blob(row.encrypted_secret, aad), aad)
            counts['webhooks'] += 1

        db.flush()
        if args.apply:
            db.commit()
        else:
            db.rollback()
    print(json.dumps({'mode': 'apply' if args.apply else 'dry-run', 'backend': settings().secret_backend, 'rewrapped': counts}, indent=2))


if __name__ == '__main__':
    main()
