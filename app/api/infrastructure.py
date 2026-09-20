import uuid
from datetime import datetime, timedelta
from typing import Annotated, Literal
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, or_, update
from app.api.common import Limit, Offset, find, idempotent, paginate, public
from app.api.outputs import (Items, CredentialOutput, ProviderOutput, DeploymentOutput, CreatedDeploymentOutput,
                             JobOutput, JobLogsOutput, TemplateOutput, PlaybookOutput, DeletedOutput, CredentialTestOutput,
                             SSHHostKeyOutput, SSHKeyBootstrapOutput)
from app.api.schemas import (CatalogItemStateInput, CredentialInput, DeploymentInput, JobInput, ProviderInput,
                             ProxmoxTokenBootstrapInput, SSHHostKeyInput, SSHKeyBootstrapInput)
from app.catalog import (list_playbooks, list_templates, playbook_definition, playbook_public, template_definition,
                         template_public, template_source_preview, playbook_source_preview, validate_template_variables)
from app.catalog_control import catalog_item_public, require_catalog_item_enabled, set_catalog_item_enabled
from app.blueprint_settings import blueprint_execution_settings
from app.credentials.service import credential_public, save_secret
from app.credentials.testing import test_connection
from app.credentials.ssh import install_generated_key, scan_ssh_host_key
from app.database import get_db
from app.models import Blueprint, Credential, Deployment, HostnameReservation, IPAllocation, Job, JobLog, Provider, now
from app.providers.registry import provider_for
from app.providers.proxmox import create_api_token, resolve_proxmox_endpoint, test_proxmox_connection
from app.security.core import audit, require
from app.terraform.state import delete_plan

router = APIRouter(tags=['infrastructure'])
DEPLOYMENT_FIELDS = 'id name provider_id provider template credentials_id workspace state_location variables workflow status created_by created_at updated_at destroyed_at active_job_id executor'
JOB_FIELDS = 'id deployment_id operation status created_by request_id source created_at updated_at cancel_requested error retry_of attempt'


def deployment_public(d):
    return public(d, DEPLOYMENT_FIELDS)


def job_public(j):
    result = public(j, JOB_FIELDS)
    wait = (j.payload or {}).get('_provider_wait') or {}
    result['provider_waiting'] = bool(wait)
    result['provider_retry_attempts'] = int(wait.get('attempts') or 0)
    next_retry = wait.get('next_attempt_at')
    try:
        result['provider_next_retry_at'] = datetime.fromisoformat(next_retry) if next_retry else None
    except (TypeError, ValueError):
        result['provider_next_retry_at'] = None
    return result


def provider_public(p):
    return public(p, 'id name type credentials_id created_at updated_at')


def locked_credential(db, id):
    credential = db.scalar(select(Credential).where(Credential.id == id).with_for_update())
    if credential is None:
        raise HTTPException(404, 'Credential not found')
    return credential


def ensure_credential_usable(credential):
    if credential.expires_at is not None and credential.expires_at <= now():
        raise HTTPException(409, 'Credential is expired and cannot be used for new infrastructure execution')
    return credential


def credential_in_use(db, id, *, pending_only=False):
    deployments = select(Deployment.id).where(
        or_(Deployment.credentials_id == id, Deployment.workflow['ansible']['credentials_id'].as_integer() == id))
    if pending_only:
        deployments = deployments.where(Deployment.active_job_id.is_not(None))
    else:
        deployments = deployments.where(Deployment.status != 'destroyed')
    if db.scalar(deployments.limit(1)) or db.scalar(select(Job.id).where(
        Job.status.in_(['queued', 'running', 'cancelling']), Job.payload['ansible']['credentials_id'].as_integer() == id).limit(1)):
        return True
    if pending_only:
        return False
    blueprint_ref = select(Blueprint.id).where(or_(
        Blueprint.deployment['guest_credential_id'].as_integer() == id,
        Blueprint.deployment['ansible']['credentials_id'].as_integer() == id,
    ))
    return bool(db.scalar(blueprint_ref.limit(1)))


@router.get('/credentials', response_model=Items[CredentialOutput])
def credentials(limit: Limit = 100, offset: Offset = 0, actor=Depends(require('credentials.read')), db=Depends(get_db, scope='function')):
    return {'items': [credential_public(c) for c in paginate(db, Credential, offset, limit)]}


@router.post('/credentials/ssh/host-key', response_model=SSHHostKeyOutput)
def ssh_host_key(data: SSHHostKeyInput, request: Request,
                 actor=Depends(require('credentials.create')), db=Depends(get_db, scope='function')):
    try:
        result = scan_ssh_host_key(data.endpoint)
    except HTTPException:
        audit(db, request, 'credential.ssh_host_key_scanned', 'credentials', None, 'failure')
        db.commit()
        raise
    audit(db, request, 'credential.ssh_host_key_scanned', 'credentials', None)
    return result


@router.post('/credentials/ssh/bootstrap', status_code=201, response_model=SSHKeyBootstrapOutput)
def bootstrap_ssh_credential(data: SSHKeyBootstrapInput, request: Request,
                             actor=Depends(require('credentials.create')), db=Depends(get_db, scope='function')):
    try:
        generated = install_generated_key(data.endpoint, data.username, data.password, data.known_hosts)
    except HTTPException:
        audit(db, request, 'credential.ssh_key_bootstrapped', 'credentials', None, 'failure')
        db.commit()
        raise
    credential = Credential(
        name=data.name, type='ssh', endpoint=data.endpoint, username=data.username, verify_ssl=True,
        expires_at=data.expires_at, rotation_due_at=data.rotation_due_at, encrypted_secret=b'',
    )
    db.add(credential)
    db.flush()
    save_secret(db, credential, generated['secret'])
    audit(db, request, 'credential.ssh_key_bootstrapped', 'credentials', credential.id)
    return {
        'credential': credential_public(credential),
        'public_key': generated['public_key'],
        'fingerprint': generated['fingerprint'],
    }


@router.post('/credentials/proxmox/bootstrap', status_code=201, response_model=CredentialOutput)
def bootstrap_proxmox_credential(data: ProxmoxTokenBootstrapInput, request: Request,
                                 actor=Depends(require('credentials.create')), db=Depends(get_db, scope='function')):
    endpoint = resolve_proxmox_endpoint(data.endpoint, verify_ssl=data.verify_ssl)
    secret = create_api_token(
        endpoint,
        data.username,
        data.password,
        data.token_name,
        verify_ssl=data.verify_ssl,
        privilege_separation=data.privilege_separation,
    )
    credential = Credential(
        name=data.name,
        type='proxmox',
        endpoint=endpoint,
        username=data.username,
        verify_ssl=data.verify_ssl,
        expires_at=data.expires_at,
        rotation_due_at=data.rotation_due_at,
        encrypted_secret=b'',
    )
    db.add(credential)
    db.flush()
    save_secret(db, credential, secret)
    audit(db, request, 'credential.proxmox_token_bootstrapped', 'credentials', credential.id)
    return credential_public(credential)


@router.post('/credentials/proxmox/test', response_model=CredentialTestOutput, response_model_exclude_unset=True)
def test_proxmox_draft_credential(data: CredentialInput, request: Request,
                                  actor=Depends(require('credentials.test')), db=Depends(get_db, scope='function')):
    if data.type != 'proxmox':
        raise HTTPException(422, 'Ten test połączenia obsługuje wyłącznie dane dostępowe Proxmox VE.')
    if not data.secrets:
        raise HTTPException(422, 'Podaj hasło Proxmox albo komplet danych tokenu API.')
    endpoint = resolve_proxmox_endpoint(data.endpoint, verify_ssl=data.verify_ssl)
    try:
        result = test_proxmox_connection(
            endpoint,
            data.username,
            data.secrets,
            verify_ssl=data.verify_ssl,
        )
    except HTTPException:
        audit(db, request, 'credential.proxmox_connection_tested', 'credentials', None, 'failure')
        db.commit()
        raise
    audit(db, request, 'credential.proxmox_connection_tested', 'credentials', None)
    return result


@router.get('/credentials/{id}', response_model=CredentialOutput)
def credential(id: int, actor=Depends(require('credentials.read')), db=Depends(get_db, scope='function')):
    return credential_public(find(db, Credential, id))


@router.post('/credentials', status_code=201, response_model=CredentialOutput)
def create_credential(data: CredentialInput, request: Request, actor=Depends(require('credentials.create')), db=Depends(get_db, scope='function')):
    if data.type == 'proxmox':
        data.endpoint = resolve_proxmox_endpoint(data.endpoint, verify_ssl=data.verify_ssl)

    def create():
        c = Credential(**data.model_dump(exclude={'secrets'}), encrypted_secret=b'')
        db.add(c)
        db.flush()
        save_secret(db, c, data.secrets)
        audit(db, request, 'credential.created', 'credentials', c.id)
        return credential_public(c)
    return idempotent(db, request, actor, data.model_dump(), create)


@router.put('/credentials/{id}', response_model=CredentialOutput)
def update_credential(id: int, data: CredentialInput, request: Request, actor=Depends(require('credentials.update')), db=Depends(get_db, scope='function')):
    if data.type == 'proxmox':
        data.endpoint = resolve_proxmox_endpoint(data.endpoint, verify_ssl=data.verify_ssl)

    c = locked_credential(db, id)
    if credential_in_use(db, id, pending_only=True):
        raise HTTPException(409, 'Credential is used by a queued or running job; finish or cancel the job first')
    # A credential's destination/identity cannot be redirected while reusing a stored secret.
    changed_identity = any(getattr(c, k) != getattr(data, k) for k in ('type', 'endpoint', 'username', 'verify_ssl'))
    if changed_identity and data.secrets is None:
        raise HTTPException(422, 'Changing endpoint, identity or TLS requires submitting a new secret')
    if c.type != data.type and db.scalar(select(Provider.id).where(Provider.credentials_id == id)):
        raise HTTPException(409, 'Credential type is referenced by a provider')
    if changed_identity and credential_in_use(db, id):
        raise HTTPException(409, 'Credential destination is used by active deployments; create another credential')
    for key, value in data.model_dump(exclude={'secrets'}).items():
        setattr(c, key, value)
    save_secret(db, c, data.secrets)
    audit(db, request, 'credential.updated', 'credentials', c.id)
    db.flush()
    return credential_public(c)


@router.delete('/credentials/{id}', response_model=DeletedOutput)
def delete_credential(id: int, request: Request, actor=Depends(require('credentials.delete')), db=Depends(get_db, scope='function')):
    c = locked_credential(db, id)
    if credential_in_use(db, id) or db.scalar(select(Provider.id).where(Provider.credentials_id == id)) or db.scalar(select(Deployment.id).where(Deployment.credentials_id == id)):
        raise HTTPException(409, 'Credential is referenced by a provider, deployment workflow or pending job')
    db.delete(c)
    audit(db, request, 'credential.deleted', 'credentials', id)
    return {'deleted': True}


@router.post('/credentials/{id}/test', response_model=CredentialTestOutput, response_model_exclude_unset=True)
def test_credential(id: int, request: Request, actor=Depends(require('credentials.test')), db=Depends(get_db, scope='function')):
    c = find(db, Credential, id)
    try:
        result = test_connection(c)
    except HTTPException:
        audit(db, request, 'credential.tested', 'credentials', id, 'failure')
        db.commit()
        raise
    audit(db, request, 'credential.tested', 'credentials', id)
    return result


@router.get('/providers', response_model=Items[ProviderOutput])
def providers(limit: Limit = 100, offset: Offset = 0, actor=Depends(require('providers.read')), db=Depends(get_db, scope='function')):
    return {'items': [provider_public(p) for p in paginate(db, Provider, offset, limit)]}


@router.get('/providers/{id}', response_model=ProviderOutput)
def provider(id: int, actor=Depends(require('providers.read')), db=Depends(get_db, scope='function')):
    return provider_public(find(db, Provider, id))


def check_provider_credential(db, data):
    c = locked_credential(db, data.credentials_id)
    if c.type != data.type:
        raise HTTPException(422, 'Provider and credential types must match')


@router.post('/providers', status_code=201, response_model=ProviderOutput)
def create_provider(data: ProviderInput, request: Request, actor=Depends(require('providers.create')), db=Depends(get_db, scope='function')):
    check_provider_credential(db, data)
    def create():
        p = Provider(**data.model_dump())
        db.add(p)
        db.flush()
        audit(db, request, 'provider.created', 'providers', p.id)
        return provider_public(p)
    return idempotent(db, request, actor, data.model_dump(), create)


@router.put('/providers/{id}', response_model=ProviderOutput)
def update_provider(id: int, data: ProviderInput, request: Request, actor=Depends(require('providers.update')), db=Depends(get_db, scope='function')):
    p = find(db, Provider, id)
    check_provider_credential(db, data)
    if p.credentials_id != data.credentials_id and db.scalar(select(Deployment.id).where(Deployment.provider_id == id, Deployment.status != 'destroyed')):
        raise HTTPException(409, 'Provider has active deployments; create another provider')
    for key, value in data.model_dump().items():
        setattr(p, key, value)
    audit(db, request, 'provider.updated', 'providers', id)
    db.flush()
    return provider_public(p)


@router.delete('/providers/{id}', response_model=DeletedOutput)
def delete_provider(id: int, request: Request, actor=Depends(require('providers.delete')), db=Depends(get_db, scope='function')):
    p = find(db, Provider, id)
    if db.scalar(select(Deployment.id).where(Deployment.provider_id == id)):
        raise HTTPException(409, 'Provider has deployment history')
    db.delete(p)
    audit(db, request, 'provider.deleted', 'providers', id)
    return {'deleted': True}


@router.get('/providers/{id}/qemu-agent-readiness')
def qemu_agent_readiness(id: int, actor=Depends(require('providers.read')), db=Depends(get_db, scope='function')):
    provider = find(db, Provider, id)
    if provider.type != 'proxmox':
        raise HTTPException(422, 'QEMU Guest Agent readiness is available only for Proxmox providers')
    credential = find(db, Credential, provider.credentials_id)
    return provider_for(credential).ssh_preflight()


@router.get('/providers/{id}/{resource}', response_model=Items[dict])
def discover(id: int, resource: Literal['nodes', 'storages', 'networks', 'templates', 'vms', 'pools'],
             node: Annotated[str | None, Query(pattern=r'^[A-Za-z0-9_.-]{1,63}$')] = None,
             actor=Depends(require('providers.read')), db=Depends(get_db, scope='function')):
    p = find(db, Provider, id)
    rows = provider_for(find(db, Credential, p.credentials_id)).discover(resource, node)
    # Providers can expose storage passwords or plugin configuration; publish only discovery metadata.
    safe_fields = {
        'nodes': 'node status cpu maxcpu mem maxmem disk maxdisk uptime',
        'storages': 'storage type content nodes shared disable enabled active total used avail',
        'networks': 'node iface type bridge_ports vlan-id address cidr gateway active autostart comments',
        'templates': 'vmid name node status template type tags mem maxmem cpu maxcpu disk maxdisk uptime id',
        'vms': 'vmid name node status template type tags mem maxmem cpu maxcpu disk maxdisk uptime id',
        'pools': 'poolid comment',
    }[resource].split()
    return {'items': [{k: v for k, v in row.items() if k in safe_fields} for row in rows]}


@router.get('/templates', response_model=Items[TemplateOutput])
@router.get('/terraform/templates', response_model=Items[TemplateOutput])
def templates(actor=Depends(require('terraform.read')), db=Depends(get_db, scope='function')):
    return {'items': [catalog_item_public(db, 'templates', item) for item in list_templates()]}


@router.get('/templates/{id}', response_model=TemplateOutput)
def template(id: str, actor=Depends(require('terraform.read')), db=Depends(get_db, scope='function')):
    return catalog_item_public(db, 'templates', template_public(id))


@router.put('/catalog/templates/{id}/enabled', response_model=TemplateOutput)
def set_template_enabled(id: str, data: CatalogItemStateInput, request: Request,
                         actor=Depends(require('settings.update')), db=Depends(get_db, scope='function')):
    item = template_public(id)
    set_catalog_item_enabled(db, 'templates', id, data.enabled)
    audit(db, request, 'catalog.template_enabled' if data.enabled else 'catalog.template_disabled', 'catalog', id)
    return catalog_item_public(db, 'templates', item)


@router.get('/templates/{id}/source')
def template_source(id: str, actor=Depends(require('terraform.read'))):
    return template_source_preview(id)


@router.get('/ansible/playbooks', response_model=Items[PlaybookOutput])
def playbooks(actor=Depends(require('ansible.read')), db=Depends(get_db, scope='function')):
    return {'items': [catalog_item_public(db, 'playbooks', item) for item in list_playbooks()]}


@router.put('/catalog/playbooks/{id}/enabled', response_model=PlaybookOutput)
def set_playbook_enabled(id: str, data: CatalogItemStateInput, request: Request,
                         actor=Depends(require('settings.update')), db=Depends(get_db, scope='function')):
    item = playbook_public(id)
    set_catalog_item_enabled(db, 'playbooks', id, data.enabled)
    audit(db, request, 'catalog.playbook_enabled' if data.enabled else 'catalog.playbook_disabled', 'catalog', id)
    return catalog_item_public(db, 'playbooks', item)


@router.get('/ansible/playbooks/{id}/source')
def playbook_source(id: str, actor=Depends(require('ansible.read'))):
    return playbook_source_preview(id)


def check_job_permissions(request, operation):
    required = {'jobs.execute', 'ansible.execute' if operation == 'ansible.execute' else 'terraform.execute'}
    if operation == 'terraform.destroy':
        required.add('deployments.destroy')
    if operation == 'terraform.import':
        required.add('deployments.adopt')
    if operation == 'terraform.apply':
        required.add('deployments.create')
    if not required <= request.state.permissions:
        raise HTTPException(403, 'Missing execution or deployment permissions')


def validate_ansible(db, data):
    c = ensure_credential_usable(locked_credential(db, data.credentials_id))
    require_catalog_item_enabled(db, 'playbooks', data.playbook)
    expected = playbook_definition(data.playbook)['transport']
    if c.type != expected:
        raise HTTPException(422, f'Playbook requires {expected} credential')


def new_job(db, request, actor, operation, deployment=None, payload=None, *, retry_of=None, attempt=1):
    check_job_permissions(request, operation)
    if deployment and deployment.workflow.get('ansible') and operation == 'terraform.apply' and 'ansible.execute' not in request.state.permissions:
        raise HTTPException(403, 'ansible.execute required by the deployment workflow')
    if deployment and (deployment.active_job_id or deployment.status == 'destroyed'):
        raise HTTPException(409, 'Deployment is busy or destroyed')
    job_payload = dict(payload or (deployment.workflow if deployment and operation == 'terraform.apply' else {}))
    if deployment:
        job_payload['previous_status'] = deployment.status
    job = Job(id=str(uuid.uuid4()), operation=operation, deployment_id=deployment.id if deployment else None,
              payload=job_payload, created_by=actor.user_id, token_id=actor.id,
              request_id=request.state.request_id, ip=request.client.host if request.client else '',
              source=getattr(request.state, 'source', 'API'), retry_of=retry_of, attempt=attempt)
    db.add(job)
    db.flush()
    if deployment:
        deployment.active_job_id = job.id
        deployment.status = 'queued'
    audit(db, request, 'job.created', 'jobs', job.id)
    return job


@router.post('/deployments', status_code=202, response_model=CreatedDeploymentOutput)
def create_deployment(data: DeploymentInput, request: Request, actor=Depends(require('deployments.create')), db=Depends(get_db, scope='function')):
    check_job_permissions(request, 'terraform.apply')
    p = find(db, Provider, data.provider_id)
    ensure_credential_usable(find(db, Credential, p.credentials_id))
    require_catalog_item_enabled(db, 'templates', data.template)
    template_meta, _ = template_definition(data.template)
    if p.type != template_meta['provider']:
        raise HTTPException(422, 'Selected infrastructure provider does not match the Terraform template')
    variables = validate_template_variables(data.template, data.variables)
    if p.credentials_id != data.credentials_id:
        raise HTTPException(422, 'Credential does not belong to the selected provider')
    # Share the same row locks with credential mutation/deletion to preserve references.
    for credential_id in sorted({data.credentials_id} | ({data.ansible.credentials_id} if data.ansible else set())):
        locked_credential(db, credential_id)
    if data.ansible:
        if p.type != 'proxmox':
            raise HTTPException(422, 'Ansible post-provisioning currently requires the Proxmox guest-agent workflow')
        if 'ansible.execute' not in request.state.permissions:
            raise HTTPException(403, 'ansible.execute required')
        validate_ansible(db, data.ansible)
    def create():
        d = Deployment(name=data.name, provider_id=p.id, provider=p.type, template=data.template, credentials_id=data.credentials_id,
                       variables=variables.model_dump(mode='json'), workflow={'ansible': data.ansible.model_dump() if data.ansible else None}, created_by=actor.user_id, executor=data.executor)
        db.add(d)
        db.flush()
        d.state_location = f'database://terraform-states/{d.id}'
        job = new_job(db, request, actor, 'terraform.apply', d, {'ansible': data.ansible.model_dump() if data.ansible else None})
        audit(db, request, 'deployment.created', 'deployments', d.id)
        return {**deployment_public(d), 'job': job_public(job)}
    return idempotent(db, request, actor, data.model_dump(), create, required=True)


@router.get('/deployments', response_model=Items[DeploymentOutput])
def deployments(limit: Limit = 100, offset: Offset = 0, actor=Depends(require('deployments.read')), db=Depends(get_db, scope='function')):
    return {'items': [deployment_public(d) for d in paginate(db, Deployment, offset, limit)]}


@router.get('/deployments/{id}', response_model=DeploymentOutput)
def deployment(id: str, actor=Depends(require('deployments.read')), db=Depends(get_db, scope='function')):
    return deployment_public(find(db, Deployment, id))


@router.post('/deployments/{id}/destroy', status_code=202, response_model=JobOutput)
@router.delete('/deployments/{id}', status_code=202, response_model=JobOutput)
def destroy_deployment(id: str, request: Request, actor=Depends(require('deployments.destroy')), db=Depends(get_db, scope='function')):
    def create():
        d = db.scalar(select(Deployment).where(Deployment.id == id).with_for_update())
        if not d:
            raise HTTPException(404, 'Deployment not found')
        return job_public(new_job(db, request, actor, 'terraform.destroy', d))
    return idempotent(db, request, actor, {'id': id}, create, required=True)


@router.post('/jobs', status_code=202, response_model=JobOutput)
def create_job(data: JobInput, request: Request, actor=Depends(require('jobs.execute')), db=Depends(get_db, scope='function')):
    check_job_permissions(request, data.operation)
    if data.ansible:
        validate_ansible(db, data.ansible)
    def create():
        d = None
        if data.deployment_id:
            d = db.scalar(select(Deployment).where(Deployment.id == data.deployment_id).with_for_update())
            if not d:
                raise HTTPException(404, 'Deployment not found')
        return job_public(new_job(db, request, actor, data.operation, d, {'ansible': data.ansible.model_dump()} if data.ansible else None))
    return idempotent(db, request, actor, data.model_dump(), create, required=True)


@router.get('/jobs', response_model=Items[JobOutput])
def jobs(limit: Limit = 100, offset: Offset = 0, actor=Depends(require('jobs.read')), db=Depends(get_db, scope='function')):
    return {'items': [job_public(j) for j in paginate(db, Job, offset, limit)]}


@router.get('/jobs/{id}', response_model=JobOutput)
def job(id: str, actor=Depends(require('jobs.read')), db=Depends(get_db, scope='function')):
    return job_public(find(db, Job, id))


@router.post('/jobs/{id}/approve', response_model=JobOutput)
def approve_job(id: str, request: Request, actor=Depends(require('blueprints.approve')),
                db=Depends(get_db, scope='function')):
    job = db.scalar(select(Job).where(Job.id == id).with_for_update())
    if job is None:
        raise HTTPException(404, 'Job not found')
    if job.status != 'waiting_approval':
        raise HTTPException(409, 'Job is not waiting for approval')
    blueprint = (job.payload or {}).get('blueprint') or {}
    if not blueprint.get('requires_approval'):
        raise HTTPException(409, 'Job does not require Blueprint approval')

    payload = dict(job.payload or {})
    approval = dict(payload.get('_approval') or {})
    expires_at = approval.get('expires_at')
    if expires_at:
        try:
            if datetime.fromisoformat(expires_at) <= now():
                raise HTTPException(409, 'Approval request has expired')
        except ValueError:
            raise HTTPException(409, 'Approval request expiry is invalid') from None
    approval.update({
        'status': 'approved',
        'approved_by': actor.user_id,
        'approved_at': now().isoformat(),
    })
    payload['_approval'] = approval
    job.payload = payload
    job.status = 'queued'
    if job.deployment_id:
        deployment = db.get(Deployment, job.deployment_id)
        if deployment is not None and deployment.active_job_id == job.id:
            deployment.status = 'queued'
    db.add(JobLog(job_id=job.id, message=f'workflow.approval.approved: user={actor.user_id}'))
    audit(db, request, 'blueprint.execution.approved', 'jobs', job.id)
    db.flush()
    return job_public(job)


@router.post('/jobs/{id}/retry', status_code=202, response_model=JobOutput)
def retry_job(id: str, request: Request, actor=Depends(require('jobs.execute')), db=Depends(get_db, scope='function')):
    original = db.scalar(select(Job).where(Job.id == id).with_for_update())
    if original is None:
        raise HTTPException(404, 'Job not found')
    if original.status not in {'failed', 'cancelled'}:
        raise HTTPException(409, 'Only failed or cancelled jobs can be retried')
    check_job_permissions(request, original.operation)
    payload = dict(original.payload or {})
    payload.pop('_approval', None)
    payload.pop('_workflow_runtime', None)
    payload.pop('_provider_wait', None)
    if original.operation == 'ansible.execute' and payload.get('ansible'):
        from app.api.schemas import AnsibleInput
        validate_ansible(db, AnsibleInput.model_validate(payload['ansible']))

    def create():
        deployment = None
        if original.deployment_id:
            deployment = db.scalar(select(Deployment).where(Deployment.id == original.deployment_id).with_for_update())
            if deployment is None:
                raise HTTPException(404, 'Deployment not found')
            delete_plan(deployment.id)
        new = new_job(
            db,
            request,
            actor,
            original.operation,
            deployment,
            payload,
            retry_of=original.id,
            attempt=original.attempt + 1,
        )
        blueprint = (new.payload or {}).get('blueprint') or {}
        if original.operation == 'terraform.apply' and blueprint.get('requires_approval'):
            approval_settings = blueprint_execution_settings(db)
            new_payload = dict(new.payload or {})
            if approval_settings['auto_approve_for_executors']:
                new_payload['_approval'] = {
                    'status': 'approved',
                    'approved_by': actor.user_id,
                    'approved_at': now().isoformat(),
                    'automatic': True,
                }
                db.add(JobLog(
                    job_id=new.id,
                    message='workflow.approval.auto: retry zatwierdzony wg aktualnej polityki globalnej',
                ))
            else:
                new_payload['_approval'] = {'status': 'pending'}
                has_runtime_approval = any(
                    str(step.get('type')) == 'approval'
                    for step in (blueprint.get('steps') or [])
                )
                if not has_runtime_approval:
                    expires_at = now() + timedelta(
                        hours=approval_settings['approval_timeout_hours']
                    )
                    new_payload['_approval'].update({
                        'requested_at': now().isoformat(),
                        'expires_at': expires_at.isoformat(),
                    })
                    new.status = 'waiting_approval'
                    if deployment is not None:
                        deployment.status = 'waiting_approval'
                db.add(JobLog(
                    job_id=new.id,
                    message='workflow.approval.pending: retry wymaga ponownej akceptacji',
                ))
            new.payload = new_payload
        audit(db, request, 'job.retried', 'jobs', new.id)
        return job_public(new)

    return idempotent(
        db,
        request,
        actor,
        {'retry_of': original.id, 'attempt': original.attempt + 1},
        create,
        required=True,
    )


@router.get('/jobs/{id}/logs', response_model=JobLogsOutput)
def logs(id: str, after: Annotated[int, Query(ge=0)] = 0, limit: Limit = 100,
         actor=Depends(require('jobs.read')), db=Depends(get_db, scope='function')):
    j = find(db, Job, id)
    rows = db.scalars(select(JobLog).where(JobLog.job_id == id, JobLog.id > after).order_by(JobLog.id).limit(limit)).all()
    return {'request_id': j.request_id, 'status': j.status, 'items': [public(r, 'id timestamp message') for r in rows], 'next_after': rows[-1].id if rows else after}


@router.post('/jobs/{id}/cancel', response_model=JobOutput)
def cancel_job(id: str, request: Request, actor=Depends(require('jobs.cancel')), db=Depends(get_db, scope='function')):
    j = db.scalar(select(Job).where(Job.id == id).with_for_update())
    if j is None:
        raise HTTPException(404, 'Job not found')
    if j.status in {'successful', 'failed', 'cancelled'}:
        raise HTTPException(409, 'Job already finished')
    if j.cancel_requested and j.status == 'cancelling':
        return job_public(j)

    was_waiting_approval = j.status == 'waiting_approval'
    j.cancel_requested = True
    db.add(JobLog(job_id=j.id, message='job.cancel_requested: żądanie anulowania przyjęte'))

    if j.status in {'queued', 'waiting_approval'}:
        j.status = 'cancelled'
        j.error = 'Cancellation requested before execution'
        if j.deployment_id:
            d = find(db, Deployment, j.deployment_id)
            if d.active_job_id == j.id:
                d.active_job_id = None
            d.status = 'cancelled'
            if was_waiting_approval:
                delete_plan(d.id)
                released_at = now()
                db.execute(update(HostnameReservation).where(
                    HostnameReservation.resource_id == d.id,
                    HostnameReservation.status != 'released',
                ).values(status='released', released_at=released_at))
                db.execute(update(IPAllocation).where(
                    IPAllocation.resource_id == d.id,
                    IPAllocation.status != 'released',
                ).values(status='released', released_at=released_at))
    else:
        j.status = 'cancelling'
        if j.deployment_id:
            d = find(db, Deployment, j.deployment_id)
            if d.active_job_id == j.id:
                d.status = 'cancelling'

    audit(db, request, 'job.cancel', 'jobs', id)
    return job_public(j)
