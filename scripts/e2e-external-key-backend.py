#!/usr/bin/env python3
"""Explicit live smoke test for the configured AWS KMS or Vault Transit envelope backend."""
import argparse
import base64
import os
import secrets

from app.config import settings
from app.security.core import ENVELOPE_MAGIC, decrypt_blob, encrypt_blob


def main():
    parser = argparse.ArgumentParser(description='Live round-trip test for external Cloudportal-backed KEK.')
    parser.add_argument('--aad', default='live-key-backend-smoke')
    args = parser.parse_args()

    backend = settings().secret_backend
    if backend not in {'aws-kms', 'vault-transit'}:
        parser.error('Set CP_SECRET_BACKEND=aws-kms or vault-transit before running this test')

    plaintext = secrets.token_bytes(64)
    encrypted = encrypt_blob(plaintext, args.aad)
    if not encrypted.startswith(ENVELOPE_MAGIC):
        raise RuntimeError('External backend did not produce a versioned envelope')
    if plaintext in encrypted:
        raise RuntimeError('Plaintext unexpectedly appears in encrypted envelope')
    restored = decrypt_blob(encrypted, args.aad)
    if restored != plaintext:
        raise RuntimeError('External key backend round-trip failed')

    print('External key backend live smoke passed:', backend)
    print('Envelope bytes:', len(encrypted))
    print('Plaintext fingerprint:', base64.urlsafe_b64encode(plaintext[:6]).decode())


if __name__ == '__main__':
    main()
