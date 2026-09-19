import re
from typing import Annotated, Literal
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy import select
from app.api.common import Limit, Offset, find, idempotent, paginate
from app.catalog import template_definition
from app.api.outputs import (BlueprintOutput, CreatedDeploymentOutput, DeletedOutput, GeneratedHostnameOutput,
                             HostnameReservationOutput, HostnameSchemeOutput, Items)
from app.api.schemas import BlueprintExecuteInput, BlueprintInput, DeploymentInput, HostnameGenerateInput, HostnameSchemeInput
from app.automation.service import (available_to, blueprint_public, compile_blueprint, generate_hostname,
                                    hostname_public)
from app.database import get_db
from app.models import Blueprint, Deployment, HostnameReservation, HostnameScheme, IPPool, Provider, Role, User, now
from app.security.core import audit, require


router = APIRouter(tags=['automation'])


def scheme_public(row):
    fields = 'id name pattern next_number padding is_active created_by created_at updated_at'
    return {field: getattr(row, field) for field in fields.split()}


def portal_source(value):
    return {'CloudPortal': 'cloudportal', 'Cloudportal-backed': 'backend', 'API': 'api'}.get(value, 'api')


@router.get('/hostname-schemes', response_model=Items[HostnameSchemeOutput])
def hostname_schemes(limit: Limit = 100, offset: Offset = 0, actor=Depends(require('hostnames.read')), db=Depends(get_db, scope='function')):
    return {'items': [scheme_public(row) for row in paginate(db, HostnameScheme, offset, limit)]}


@router.get('/hostname-schemes/{id}', response_model=HostnameSchemeOutput)
def hostname_scheme(id: int, actor=Depends(require('hostnames.read')), db=Depends(get_db, scope='function')):
    return scheme_public(find(db, HostnameScheme, id))


@router.post('/hostname-schemes', status_code=201, response_model=HostnameSchemeOutput)
def create_hostname_scheme(data: HostnameSchemeInput, request: Request, actor=Depends(require('hostnames.create')), db=Depends(get_db, scope='function')):
    def create():
        row = HostnameScheme(**data.model_dump(), created_by=actor.user_id)
        db.add(row)
        db.flush()
        audit(db, request, 'hostname_scheme.created', 'hostname_schemes', row.id)
        return scheme_public(row)
    return idempotent(db, request, actor, data.model_dump(), create)


@router.put('/hostname-schemes/{id}', response_model=HostnameSchemeOutput)
def update_hostname_scheme(id: int, data: HostnameSchemeInput, request: Request, actor=Depends(require('hostnames.update')), db=Depends(get_db, scope='function')):
    row = find(db, HostnameScheme, id)
    if data.next_number < row.next_number:
        raise HTTPException(409, f'Hostname sequence cannot be moved backwards; next number is {row.next_number}')
    for key, value in data.model_dump().items():
        setattr(row, key, value)
    audit(db, request, 'hostname_scheme.updated', 'hostname_schemes', id)
    db.flush()
    return scheme_public(row)


@router.delete('/hostname-schemes/{id}', response_model=DeletedOutput)
def delete_hostname_scheme(id: int, request: Request, actor=Depends(require('hostnames.delete')), db=Depends(get_db, scope='function')):
    row = find(db, HostnameScheme, id)
    if db.scalar(select(HostnameReservation.id).where(HostnameReservation.scheme_id == id).limit(1)):
        raise HTTPException(409, 'Hostname scheme has reservation history; disable it instead')
    db.delete(row)
    audit(db, request, 'hostname_scheme.deleted', 'hostname_schemes', id)
    return {'deleted': True}


@router.get('/hostnames', response_model=Items[HostnameReservationOutput])
def hostname_reservations(status: Annotated[Literal['reserved', 'assigned', 'released'] | None, Query()] = None,
                          limit: Limit = 100, offset: Offset = 0, actor=Depends(require('hostnames.read')),
                          db=Depends(get_db, scope='function')):
    where = HostnameReservation.status == status if status else None
    return {'items': [hostname_public(row) for row in paginate(db, HostnameReservation, offset, limit, where)]}


@router.post('/hostnames/generate', response_model=GeneratedHostnameOutput)
def generate(data: HostnameGenerateInput, request: Request, actor=Depends(require('hostnames.reserve')), db=Depends(get_db, scope='function')):
    hostname, reservation = generate_hostname(db, data.scheme_id, data.values, actor.user_id, data.reserve)
    if reservation:
        audit(db, request, 'hostname.reserved', 'hostnames', reservation.id)
    return {'hostname': hostname, 'reservation': hostname_public(reservation) if reservation else None}


@router.post('/hostnames/{id}/assign', response_model=HostnameReservationOutput)
def assign_hostname(id: str, resource_id: Annotated[str, Query(min_length=1, max_length=100)], request: Request,
                    actor=Depends(require('hostnames.reserve')), db=Depends(get_db, scope='function')):
    row = find(db, HostnameReservation, id)
    if row.status != 'reserved':
        raise HTTPException(409, 'Only a reserved hostname can be assigned')
    row.status, row.resource_id = 'assigned', resource_id
    audit(db, request, 'hostname.assigned', 'hostnames', id)
    return hostname_public(row)


@router.post('/hostnames/{id}/release', response_model=HostnameReservationOutput)
def release_hostname(id: str, request: Request, actor=Depends(require('hostnames.release')), db=Depends(get_db, scope='function')):
    row = find(db, HostnameReservation, id)
    if row.status == 'released':
        raise HTTPException(409, 'Hostname is already released')
    row.status, row.released_at = 'released', now()
    audit(db, request, 'hostname.released', 'hostnames', id)
    return hostname_public(row)


def validate_blueprint_references(db, data):
    provider = find(db, Provider, data.deployment.provider_id)
    template_meta, _ = template_definition(data.deployment.template)
    if provider.type != template_meta['provider']:
        raise HTTPException(422, 'Blueprint provider does not match its Terraform template')
    if provider.credentials_id != data.deployment.credentials_id:
        raise HTTPException(422, 'Blueprint credential does not belong to its provider')
    if data.deployment.hostname_scheme_id:
        scheme = find(db, HostnameScheme, data.deployment.hostname_scheme_id)
        if not scheme.is_active:
            raise HTTPException(422, 'Blueprint hostname scheme must be active')
        pattern_tokens = set(re.findall(r'{([a-z]+)}', scheme.pattern))
        unknown_defaults = set(data.deployment.hostname_values) - pattern_tokens
        if unknown_defaults:
            raise HTTPException(
                422,
                'Blueprint hostname defaults contain tokens not used by the selected pattern: '
                + ', '.join(sorted(unknown_defaults)),
            )
    if data.deployment.ipam_pool_id:
        pool = find(db, IPPool, data.deployment.ipam_pool_id)
        if not pool.is_active:
            raise HTTPException(422, 'Blueprint IPAM pool must be active')
    for role_id in set(data.allowed_role_ids):
        find(db, Role, role_id)
    for user_id in set(data.allowed_user_ids):
        find(db, User, user_id)


@router.get('/blueprints', response_model=Items[BlueprintOutput])
def blueprints(request: Request, available: bool = False, source_header: Annotated[str | None, Header(alias='X-Portal-Source')] = None,
               limit: Limit = 100, offset: Offset = 0, actor=Depends(require('blueprints.read')),
               db=Depends(get_db, scope='function')):
    rows = paginate(db, Blueprint, offset, limit)
    source = portal_source(source_header)
    can_manage = bool({'blueprints.create', 'blueprints.update'} & request.state.permissions)
    if available or source != 'backend' and not can_manage:
        rows = [row for row in rows if available_to(row, actor, source)]
    return {'items': [blueprint_public(row) for row in rows]}


@router.get('/blueprints/{id}', response_model=BlueprintOutput)
def blueprint(id: int, request: Request, source_header: Annotated[str | None, Header(alias='X-Portal-Source')] = None,
              actor=Depends(require('blueprints.read')), db=Depends(get_db, scope='function')):
    row = find(db, Blueprint, id)
    source = portal_source(source_header)
    can_manage = bool({'blueprints.create', 'blueprints.update'} & request.state.permissions)
    if source != 'backend' and not can_manage and not available_to(row, actor, source):
        raise HTTPException(404, 'Blueprint not found')
    return blueprint_public(row)


@router.post('/blueprints', status_code=201, response_model=BlueprintOutput)
def create_blueprint(data: BlueprintInput, request: Request, actor=Depends(require('blueprints.create')), db=Depends(get_db, scope='function')):
    validate_blueprint_references(db, data)
    def create():
        row = Blueprint(**data.model_dump(mode='json'), created_by=actor.user_id)
        db.add(row)
        db.flush()
        audit(db, request, 'blueprint.created', 'blueprints', row.id)
        return blueprint_public(row)
    return idempotent(db, request, actor, data.model_dump(mode='json'), create)


@router.put('/blueprints/{id}', response_model=BlueprintOutput)
def update_blueprint(id: int, data: BlueprintInput, request: Request, actor=Depends(require('blueprints.update')), db=Depends(get_db, scope='function')):
    validate_blueprint_references(db, data)
    row = find(db, Blueprint, id)
    for key, value in data.model_dump(mode='json').items():
        setattr(row, key, value)
    row.version += 1
    audit(db, request, 'blueprint.updated', 'blueprints', id)
    db.flush()
    return blueprint_public(row)


@router.delete('/blueprints/{id}', response_model=DeletedOutput)
def delete_blueprint(id: int, request: Request, actor=Depends(require('blueprints.delete')), db=Depends(get_db, scope='function')):
    row = find(db, Blueprint, id)
    db.delete(row)
    audit(db, request, 'blueprint.deleted', 'blueprints', id)
    return {'deleted': True}


@router.post('/blueprints/{id}/execute', status_code=202, response_model=CreatedDeploymentOutput)
def execute_blueprint(id: int, data: BlueprintExecuteInput, request: Request,
                      source_header: Annotated[str | None, Header(alias='X-Portal-Source')] = None,
                      actor=Depends(require('blueprints.execute')), db=Depends(get_db, scope='function')):
    from app.api.infrastructure import deployment_public, job_public, locked_credential, new_job, validate_ansible
    row = find(db, Blueprint, id)
    source = portal_source(source_header)
    if not available_to(row, actor, source):
        raise HTTPException(403, 'Blueprint is not available to this identity and portal')
    if row.requires_approval and 'blueprints.approve' not in request.state.permissions:
        raise HTTPException(403, 'blueprints.approve required by this blueprint')
    if row.recovery_policy == 'destroy_on_failure' and 'deployments.destroy' not in request.state.permissions:
        raise HTTPException(403, 'deployments.destroy required by blueprint recovery policy')
    def create():
        rendered, reservation, ip_allocation = compile_blueprint(db, row, data.variables, data.hostname_values, actor.user_id)
        blueprint_variables = rendered.pop('blueprint_variables')
        parsed = DeploymentInput.model_validate(rendered)
        provider = find(db, Provider, parsed.provider_id)
        if provider.credentials_id != parsed.credentials_id:
            raise HTTPException(422, 'Credential does not belong to the selected provider')
        for credential_id in sorted({parsed.credentials_id} | ({parsed.ansible.credentials_id} if parsed.ansible else set())):
            locked_credential(db, credential_id)
        if parsed.ansible:
            if provider.type != 'proxmox':
                raise HTTPException(422, 'Blueprint Ansible post-provisioning currently requires Proxmox')
            if 'ansible.execute' not in request.state.permissions:
                raise HTTPException(403, 'ansible.execute required by blueprint')
            validate_ansible(db, parsed.ansible)
        deployment = Deployment(name=parsed.name, provider_id=provider.id, provider=provider.type, template=parsed.template,
                                credentials_id=parsed.credentials_id, variables=parsed.variables,
                                workflow={'ansible': parsed.ansible.model_dump() if parsed.ansible else None,
                                          'blueprint': {'id': row.id, 'slug': row.slug, 'version': row.version,
                                                        'variables': blueprint_variables, 'steps': row.workflow,
                                                        'requires_approval': row.requires_approval,
                                                        'recovery_policy': row.recovery_policy}},
                                created_by=actor.user_id, executor=parsed.executor)
        db.add(deployment)
        db.flush()
        deployment.state_location = f'database://terraform-states/{deployment.id}'
        job = new_job(db, request, actor, 'terraform.apply', deployment,
                      {'ansible': parsed.ansible.model_dump() if parsed.ansible else None,
                       'blueprint': deployment.workflow['blueprint']})
        if reservation:
            reservation.status, reservation.resource_id = 'assigned', deployment.id
        if ip_allocation:
            ip_allocation.status, ip_allocation.resource_id = 'assigned', deployment.id
        audit(db, request, 'blueprint.executed', 'blueprints', row.id)
        audit(db, request, 'deployment.created', 'deployments', deployment.id)
        return {**deployment_public(deployment), 'job': job_public(job)}
    return idempotent(db, request, actor, {'blueprint_id': id, **data.model_dump(mode='json')}, create, required=True)
