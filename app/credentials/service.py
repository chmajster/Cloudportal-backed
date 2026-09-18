from fastapi import HTTPException
from app.api.common import public
from app.models import now
from app.security.core import decrypt_secret, encrypt_secret


def credential_public(c):
    return {**public(c, 'id name type endpoint username verify_ssl expires_at rotation_due_at secret_updated_at created_at updated_at'),
            'configured': bool(c.encrypted_secret), 'secret': '********'}


def save_secret(db, c, value):
    if value is None:
        if not c.encrypted_secret:
            raise HTTPException(422, 'Secret is required')
        return
    if c.type == 'proxmox' and not (value.get('password') or (value.get('token_id') and value.get('token_secret'))):
        raise HTTPException(422, 'Proxmox requires password or token_id and token_secret')
    if c.type == 'ssh' and not (value.get('password') or value.get('private_key')):
        raise HTTPException(422, 'SSH requires password or private_key')
    if c.type == 'ssh' and not value.get('known_hosts'):
        raise HTTPException(422, 'SSH requires known_hosts for host identity verification')
    if c.type in {'proxmox', 'vmware', 'winrm', 'openstack'} and not c.endpoint:
        raise HTTPException(422, 'This credential type requires an endpoint')
    if c.type == 'winrm' and not value.get('password'):
        raise HTTPException(422, 'WinRM requires password')
    c.encrypted_secret = encrypt_secret(value, c.id)
    c.secret_updated_at = now()
