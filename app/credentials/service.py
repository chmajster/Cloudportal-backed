from fastapi import HTTPException
from sqlalchemy import or_, select

from app.api.common import public
from app.models import Blueprint, Deployment, Job, now
from app.security.core import decrypt_secret, encrypt_secret


def credential_public(c):
    supports_cloud_init_ssh_key = False
    supports_cloud_init_password = False
    if c.type == 'ssh' and c.encrypted_secret:
        try:
            secret = decrypt_secret(c)
            supports_cloud_init_ssh_key = bool(secret.get('private_key'))
            supports_cloud_init_password = bool(secret.get('password'))
        except Exception:
            # Capability metadata must never expose secret material or make credential listing fail.
            supports_cloud_init_ssh_key = False
            supports_cloud_init_password = False
    return {
        **public(c, 'id name type endpoint username verify_ssl expires_at rotation_due_at secret_updated_at created_at updated_at'),
        'configured': bool(c.encrypted_secret),
        'supports_cloud_init_ssh_key': supports_cloud_init_ssh_key,
        'supports_cloud_init_password': supports_cloud_init_password,
        'secret': '********',
    }


def save_secret(db, c, value):
    if value is None:
        if not c.encrypted_secret:
            raise HTTPException(422, 'Secret is required')
        return

    endpoint_types = {'proxmox', 'vmware', 'winrm', 'awx', 'openstack'}
    identity_types = {'proxmox', 'vmware', 'ssh', 'winrm', 'awx', 'openstack'}
    if c.type in endpoint_types and not c.endpoint:
        raise HTTPException(422, 'This credential type requires an endpoint')
    if c.type in identity_types and not c.username:
        raise HTTPException(422, 'This credential type requires a username')
    if c.type == 'ssh' and c.endpoint and not c.endpoint.startswith('ssh://'):
        raise HTTPException(422, 'SSH credential endpoint must use ssh://')

    if c.type == 'proxmox':
        has_password = bool(value.get('password'))
        has_token = bool(value.get('token_id') and value.get('token_secret'))
        if not (has_password or has_token):
            raise HTTPException(422, 'Proxmox requires password or token_id and token_secret')
    elif c.type == 'vmware':
        if not value.get('password'):
            raise HTTPException(422, 'VMware requires password')
    elif c.type == 'ssh':
        if not (value.get('password') or value.get('private_key')):
            raise HTTPException(422, 'SSH requires password or private_key')
    elif c.type == 'winrm':
        if not value.get('password'):
            raise HTTPException(422, 'WinRM requires password')
    elif c.type == 'awx':
        if not (value.get('token') or value.get('password')):
            raise HTTPException(422, 'AWX requires an OAuth token or password')
    elif c.type == 'aws':
        if not value.get('access_key_id') or not value.get('secret_access_key'):
            raise HTTPException(422, 'AWS requires access_key_id and secret_access_key')
    elif c.type == 'azure':
        if not all(value.get(key) for key in ('tenant_id', 'client_id', 'client_secret', 'subscription_id')):
            raise HTTPException(422, 'Azure requires tenant_id, client_id, client_secret and subscription_id')
    elif c.type == 'openstack':
        if not value.get('password') or not value.get('project_name'):
            raise HTTPException(422, 'OpenStack requires password and project_name')
    elif c.type == 'other':
        if not value.get('secret'):
            raise HTTPException(422, 'Other credential requires secret')

    c.encrypted_secret = encrypt_secret(value, c.id)
    c.secret_updated_at = now()


def _ansible_runs_reference(payload, credential_id):
    if not isinstance(payload, dict):
        return False
    for run in payload.get('ansible_runs') or []:
        if not isinstance(run, dict):
            continue
        try:
            if int(run.get('credentials_id') or 0) == int(credential_id):
                return True
        except (TypeError, ValueError):
            continue
    return False


def credential_in_use(db, id, *, pending_only=False):
    """Return whether a credential is protected by an active or durable reference."""
    deployments = select(Deployment.id).where(or_(
        Deployment.credentials_id == id,
        Deployment.workflow['ansible']['credentials_id'].as_integer() == id,
        Deployment.workflow['blueprint']['guest_credential_id'].as_integer() == id,
        Deployment.workflow['blueprint']['template_guest_credential_id'].as_integer() == id,
        Deployment.workflow['awx']['credential_id'].as_integer() == id,
        Deployment.workflow['blueprint']['awx']['credential_id'].as_integer() == id,
    ))
    if pending_only:
        deployments = deployments.where(Deployment.active_job_id.is_not(None))
    else:
        deployments = deployments.where(Deployment.status != 'destroyed')

    active_job_credential = select(Job.id).where(
        Job.status.in_(['waiting_approval', 'queued', 'running', 'cancelling']),
        or_(
            Job.payload['ansible']['credentials_id'].as_integer() == id,
            Job.payload['blueprint']['guest_credential_id'].as_integer() == id,
            Job.payload['blueprint']['template_guest_credential_id'].as_integer() == id,
            Job.payload['blueprint']['awx']['credential_id'].as_integer() == id,
        ),
    )
    if db.scalar(deployments.limit(1)) or db.scalar(active_job_credential.limit(1)):
        return True

    deployment_runs = select(Deployment.workflow)
    if pending_only:
        deployment_runs = deployment_runs.where(Deployment.active_job_id.is_not(None))
    else:
        deployment_runs = deployment_runs.where(Deployment.status != 'destroyed')
    if any(_ansible_runs_reference(payload, id) for payload in db.scalars(deployment_runs)):
        return True

    active_jobs = select(Job.payload).where(
        Job.status.in_(['waiting_approval', 'queued', 'running', 'cancelling'])
    )
    if any(_ansible_runs_reference(payload, id) for payload in db.scalars(active_jobs)):
        return True

    if pending_only:
        return False

    blueprint_ref = select(Blueprint.id).where(or_(
        Blueprint.deployment['guest_credential_id'].as_integer() == id,
        Blueprint.deployment['template_guest_credential_id'].as_integer() == id,
        Blueprint.deployment['ansible']['credentials_id'].as_integer() == id,
        Blueprint.deployment['awx']['credential_id'].as_integer() == id,
    ))
    if db.scalar(blueprint_ref.limit(1)):
        return True
    return any(
        _ansible_runs_reference(payload, id)
        for payload in db.scalars(select(Blueprint.deployment))
    )
