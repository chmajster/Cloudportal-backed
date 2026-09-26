import re
from typing import Annotated, Literal
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy import select
from app.api.common import Limit, Offset, find, idempotent, paginate
from app.catalog import template_definition, validate_template_variables
from app.catalog_control import require_catalog_item_enabled
from app.api.outputs import (BlueprintAvatarOutput, BlueprintCreationScopeOutput, BlueprintOutput,
                             CreatedDeploymentOutput, DeletedOutput, GeneratedHostnameOutput,
                             HostnameReservationOutput, HostnameSchemeOutput, Items,
                             VMClassificationSettingsOutput)
from app.api.schemas import (BlueprintExecuteInput, BlueprintInput, CatalogItemStateInput,
                             DeploymentInput, HostnameGenerateInput, HostnameSchemeInput)
from app.automation.schemas import BlueprintBundleInput
from app.automation.service import (available_to, blueprint_public, can_manage_blueprint, compile_blueprint,
                                    generate_hostname, guest_credential_cloud_init, hostname_public)
from app.automation.yaml_codec import dump_blueprint_yaml, parse_blueprint_yaml
from app.blueprint_avatars import blueprint_avatar, list_blueprint_avatars
from app.database import get_db
from app.models import (Blueprint, BlueprintManagerRole, Credential, Deployment, HostnameReservation, HostnameScheme,
                        IPPool, Provider, Role, User, now)
from app.providers.registry import provider_for
from app.projects.authorization import effective_permissions as project_effective_permissions, visible_projects
from app.projects.models import Project
from app.security.core import audit, authenticate
from app.tenancy.authorization import Principal, identity as scoped_identity
from app.tenancy.models import Tenant
from app.resource_scope.http import require
from app.vm_classification import vm_classification_settings


router = APIRouter(tags=['automation'])


BlueprintScopePermission = Literal[
    'blueprints.read', 'blueprints.create', 'blueprints.update',
    'blueprints.delete', 'blueprints.execute', 'blueprints.approve',
]


def scheme_public(row):
    fields = 'id name pattern next_number padding is_active created_by created_at updated_at'
    return {field: getattr(row, field) for field in fields.split()}


def portal_source(value):
    return {'CloudPortal': 'cloudportal', 'Cloudportal-backed': 'backend', 'API': 'api'}.get(value, 'api')


@router.get('/blueprint-avatars', response_model=Items[BlueprintAvatarOutput])
def blueprint_avatars(actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    return {'items': list_blueprint_avatars(db)}


@router.get('/vm-classification/options', response_model=VMClassificationSettingsOutput)
def vm_classification_options(actor=Depends(require('blueprints.execute')), db=Depends(get_db, scope='function')):
    return vm_classification_settings(db)


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


def _contains_template_placeholder(value):
    if isinstance(value, str):
        return bool(re.search(r'{{\s*[A-Za-z0-9_]+\s*}}', value))
    if isinstance(value, dict):
        return any(_contains_template_placeholder(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_contains_template_placeholder(item) for item in value)
    return False


def validate_blueprint_template_variables(data: BlueprintInput):
    """Validate static IaC variables when they can be checked at Blueprint-save time.

    Proxmox Blueprints created by the step-by-step wizard contain only the generated
    hostname placeholder. Replacing that single placeholder lets the canonical
    template model reject invalid IPv4/DNS/VLAN/CPU/RAM/storage data before a
    Blueprint can be persisted. Legacy self-service Blueprints may template other
    values, including placeholders nested inside arrays or dictionaries; those
    remain execution-time validated because their concrete values do not exist yet.
    """
    variables = dict(data.deployment.variables or {})
    if data.deployment.template != 'proxmox-vm':
        return

    name = variables.get('name')
    if isinstance(name, str) and re.fullmatch(r'{{\s*hostname\s*}}', name):
        variables['name'] = 'blueprint-validation'

    if any(_contains_template_placeholder(value) for value in variables.values()):
        return

    validate_template_variables(data.deployment.template, variables)


def validate_blueprint_references(db, data, blueprint_id=None):
    if data.avatar_id:
        blueprint_avatar(db, data.avatar_id)
    provider = find(db, Provider, data.deployment.provider_id)
    existing = db.get(Blueprint, blueprint_id) if blueprint_id is not None else None
    existing_template = existing.deployment.get('template') if existing else None
    existing_playbooks = set()
    if existing:
        legacy_ansible = existing.deployment.get('ansible') or {}
        if legacy_ansible.get('playbook'):
            existing_playbooks.add(legacy_ansible['playbook'])
        existing_playbooks.update(
            row.get('playbook')
            for row in (existing.deployment.get('ansible_runs') or [])
            if isinstance(row, dict) and row.get('playbook')
        )
    if data.deployment.template != existing_template:
        require_catalog_item_enabled(db, 'templates', data.deployment.template)
    template_meta, _ = template_definition(data.deployment.template)
    requested_ansible = ([data.deployment.ansible] if data.deployment.ansible else []) + list(data.deployment.ansible_runs)
    for ansible_run in requested_ansible:
        if ansible_run.playbook not in existing_playbooks:
            require_catalog_item_enabled(db, 'playbooks', ansible_run.playbook)
    if provider.type != template_meta['provider']:
        raise HTTPException(422, 'Blueprint provider does not match its Terraform template')
    validate_blueprint_template_variables(data)
    if provider.credentials_id != data.deployment.credentials_id:
        raise HTTPException(422, 'Blueprint credential does not belong to its provider')
    workflow_types = {str(step.type) for step in data.workflow}
    if blueprint_id is None and 'terraform_apply' not in workflow_types:
        raise HTTPException(422, 'New Blueprints must contain an explicit terraform_apply step')

    proxmox_only_steps = {
        'cloud_init', 'wait_for_vm', 'wait_for_agent', 'wait_for_ip', 'wait_for_ssh',
        'run_ansible_playbook', 'register_awx', 'create_snapshot', 'health_check',
    }
    invalid_provider_steps = sorted(workflow_types & proxmox_only_steps) if provider.type != 'proxmox' else []
    if invalid_provider_steps:
        raise HTTPException(
            422,
            'Workflow steps supported only for Proxmox: ' + ', '.join(invalid_provider_steps),
        )
    if requested_ansible and provider.type != 'proxmox':
        raise HTTPException(422, 'Blueprint Ansible post-provisioning currently requires Proxmox')
    if data.deployment.awx:
        awx_credential = find(db, Credential, data.deployment.awx.credential_id)
        if awx_credential.type != 'awx':
            raise HTTPException(422, 'Blueprint AWX onboarding requires an AWX credential')
        if 'register_awx' not in workflow_types:
            raise HTTPException(422, 'AWX onboarding configuration requires a register_awx workflow step')
        if 'cloud_init' not in workflow_types:
            raise HTTPException(422, 'AWX onboarding in this Blueprint requires an explicit cloud_init step')
    elif 'register_awx' in workflow_types:
        raise HTTPException(422, 'register_awx workflow step requires AWX onboarding configuration')
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
    if data.deployment.guest_account_mode == 'existing_template':
        if not (data.deployment.template_guest_credential_id or data.deployment.guest_credential_id):
            raise HTTPException(422, 'Existing template account mode requires a guest SSH credential')
        if 'cloud_init' not in workflow_types:
            raise HTTPException(422, 'Existing template account mode requires an explicit cloud_init workflow step')
    if data.deployment.guest_credential_id:
        if provider.type != 'proxmox':
            raise HTTPException(422, 'Guest credential injection is currently supported only for Proxmox templates')
        guest_credential_cloud_init(db, data.deployment.guest_credential_id)
    if data.deployment.template_guest_credential_id:
        if provider.type != 'proxmox':
            raise HTTPException(422, 'Template guest credential is currently supported only for Proxmox templates')
        guest_credential_cloud_init(db, data.deployment.template_guest_credential_id)
    for role_id in set(data.allowed_role_ids):
        find(db, Role, role_id)
    manager_roles = []
    for role_id in sorted(set(data.manager_role_ids)):
        role = db.scalar(select(Role).where(Role.id == role_id).with_for_update())
        if role is None:
            raise HTTPException(404, 'Manager role not found')
        permissions = {permission.name for permission in role.permissions}
        required_permissions = {'blueprints.read', 'blueprints.update', 'blueprints.delete'}
        if not required_permissions <= permissions:
            raise HTTPException(422, f'Role {role.name} must include blueprint read, update and delete permissions')
        assignment = db.scalar(select(BlueprintManagerRole).where(BlueprintManagerRole.role_id == role_id))
        if assignment is not None and assignment.blueprint_id != blueprint_id:
            raise HTTPException(409, f'Role {role.name} is already dedicated to another template')
        manager_roles.append(role)
    for user_id in set(data.allowed_user_ids):
        find(db, User, user_id)
    return manager_roles


def require_blueprint_manager(row, actor):
    if not can_manage_blueprint(row, actor):
        raise HTTPException(403, 'A dedicated manager role for this template is required')


@router.post('/blueprint-designer/yaml/parse')
def blueprint_yaml_parse(payload: dict[str, str], actor=Depends(require('blueprints.read'))):
    model = parse_blueprint_yaml(payload.get('yaml', ''))
    return {
        'blueprint': model.model_dump(mode='json'),
        'yaml': dump_blueprint_yaml(model),
    }


@router.post('/blueprint-designer/yaml/render')
def blueprint_yaml_render(data: BlueprintInput, actor=Depends(require('blueprints.read'))):
    return {
        'blueprint': data.model_dump(mode='json'),
        'yaml': dump_blueprint_yaml(data),
    }


@router.get('/blueprints/creation-scopes', response_model=Items[BlueprintCreationScopeOutput])
def blueprint_creation_scopes(limit: Limit = 200, offset: Offset = 0,
                              permission: BlueprintScopePermission = 'blueprints.create',
                              actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    """Return active Blueprint scopes for one explicitly requested permission.

    The default remains blueprints.create for backward compatibility with the
    creation wizard. The Blueprint list requests blueprints.read so delegated
    project grants work even though resource-scoped permissions intentionally do
    not appear in /auth/me.
    """
    identity = scoped_identity(db, Principal.from_token(actor))
    if identity.platform_admin and permission in identity.global_permissions:
        predicate = Project.deleted_at.is_(None)
    else:
        predicate = visible_projects(identity, permission=permission)
    rows = db.execute(
        select(Project, Tenant)
        .join(Tenant, Tenant.id == Project.tenant_id)
        .where(
            predicate,
            Project.status == 'active',
            Tenant.status == 'active',
            Project.deleted_at.is_(None),
            Tenant.deleted_at.is_(None),
        )
        .order_by(Tenant.name, Tenant.id, Project.name, Project.id)
        .offset(offset)
        .limit(limit)
    ).all()
    return {'items': [{
        'tenant_id': tenant.id,
        'tenant_name': tenant.name,
        'tenant_slug': tenant.slug,
        'project_id': project.id,
        'project_name': project.name,
        'project_slug': project.slug,
        'permissions': sorted(project_effective_permissions(db, identity, project)[0]),
    } for project, tenant in rows]}


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


@router.get('/blueprints/{id}/yaml')
def blueprint_yaml(id: int, request: Request,
                   source_header: Annotated[str | None, Header(alias='X-Portal-Source')] = None,
                   actor=Depends(require('blueprints.read')), db=Depends(get_db, scope='function')):
    row = find(db, Blueprint, id)
    source = portal_source(source_header)
    can_manage = bool({'blueprints.create', 'blueprints.update'} & request.state.permissions)
    if source != 'backend' and not can_manage and not available_to(row, actor, source):
        raise HTTPException(404, 'Blueprint not found')
    public = blueprint_public(row)
    payload = {name: public[name] for name in BlueprintInput.model_fields}
    return {'yaml': dump_blueprint_yaml(payload, version=row.version)}


def blueprint_with_inline_hostname_scheme(db, data: BlueprintInput, hostname_scheme: HostnameSchemeInput | None,
                                          request: Request, actor):
    if hostname_scheme is None:
        return data
    if 'hostnames.create' not in request.state.permissions:
        raise HTTPException(403, 'hostnames.create required to create an inline hostname pattern')
    scheme = HostnameScheme(**hostname_scheme.model_dump(), created_by=actor.user_id)
    db.add(scheme)
    db.flush()
    audit(db, request, 'hostname_scheme.created', 'hostname_schemes', scheme.id)
    deployment = data.deployment.model_copy(update={'hostname_scheme_id': scheme.id})
    return data.model_copy(update={'deployment': deployment})


@router.post('/blueprints', status_code=201, response_model=BlueprintOutput)
def create_blueprint(data: BlueprintInput, request: Request, actor=Depends(require('blueprints.create')), db=Depends(get_db, scope='function')):
    manager_roles = validate_blueprint_references(db, data)
    def create():
        values = data.model_dump(mode='json', exclude={'manager_role_ids'})
        row = Blueprint(**values, created_by=actor.user_id)
        row.manager_roles = manager_roles
        db.add(row)
        db.flush()
        audit(db, request, 'blueprint.created', 'blueprints', row.id)
        return blueprint_public(row)
    return idempotent(db, request, actor, data.model_dump(mode='json'), create)


@router.post('/blueprints/bundle', status_code=201, response_model=BlueprintOutput)
def create_blueprint_bundle(bundle: BlueprintBundleInput, request: Request,
                            actor=Depends(require('blueprints.create')), db=Depends(get_db, scope='function')):
    def create():
        data = blueprint_with_inline_hostname_scheme(db, bundle.blueprint, bundle.hostname_scheme, request, actor)
        manager_roles = validate_blueprint_references(db, data)
        values = data.model_dump(mode='json', exclude={'manager_role_ids'})
        row = Blueprint(**values, created_by=actor.user_id)
        row.manager_roles = manager_roles
        db.add(row)
        db.flush()
        audit(db, request, 'blueprint.created', 'blueprints', row.id)
        return blueprint_public(row)
    return idempotent(db, request, actor, bundle.model_dump(mode='json'), create)


@router.put('/blueprints/{id}', response_model=BlueprintOutput)
def update_blueprint(id: int, data: BlueprintInput, request: Request, actor=Depends(require('blueprints.update')), db=Depends(get_db, scope='function')):
    row = find(db, Blueprint, id)
    require_blueprint_manager(row, actor)
    manager_roles = validate_blueprint_references(db, data, blueprint_id=id)
    for key, value in data.model_dump(mode='json', exclude={'manager_role_ids'}).items():
        setattr(row, key, value)
    row.manager_roles = manager_roles
    row.version += 1
    audit(db, request, 'blueprint.updated', 'blueprints', id)
    db.flush()
    return blueprint_public(row)


@router.put('/blueprints/{id}/bundle', response_model=BlueprintOutput)
def update_blueprint_bundle(id: int, bundle: BlueprintBundleInput, request: Request,
                            actor=Depends(require('blueprints.update')), db=Depends(get_db, scope='function')):
    row = find(db, Blueprint, id)
    require_blueprint_manager(row, actor)
    data = blueprint_with_inline_hostname_scheme(db, bundle.blueprint, bundle.hostname_scheme, request, actor)
    manager_roles = validate_blueprint_references(db, data, blueprint_id=id)
    for key, value in data.model_dump(mode='json', exclude={'manager_role_ids'}).items():
        setattr(row, key, value)
    row.manager_roles = manager_roles
    row.version += 1
    audit(db, request, 'blueprint.updated', 'blueprints', id)
    db.flush()
    return blueprint_public(row)


@router.put('/blueprints/{id}/enabled', response_model=BlueprintOutput)
def set_blueprint_enabled(id: int, data: CatalogItemStateInput, request: Request,
                          actor=Depends(require('blueprints.update')), db=Depends(get_db, scope='function')):
    row = find(db, Blueprint, id)
    require_blueprint_manager(row, actor)
    row.is_active = data.enabled
    row.version += 1
    audit(db, request, 'blueprint.enabled' if data.enabled else 'blueprint.disabled', 'blueprints', id)
    db.flush()
    return blueprint_public(row)


@router.delete('/blueprints/{id}', response_model=DeletedOutput)
def delete_blueprint(id: int, request: Request, actor=Depends(require('blueprints.delete')), db=Depends(get_db, scope='function')):
    row = find(db, Blueprint, id)
    require_blueprint_manager(row, actor)
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
    if row.recovery_policy == 'destroy_on_failure' and 'deployments.destroy' not in request.state.permissions:
        raise HTTPException(403, 'deployments.destroy required by blueprint recovery policy')
    workflow_types = {step.get('type') for step in (row.workflow or [])}
    required_workflow_permissions = set()
    if 'create_snapshot' in workflow_types:
        required_workflow_permissions.add('snapshots.create')
    if 'release_ip' in workflow_types:
        required_workflow_permissions.add('ipam.release')
    if 'terraform_destroy' in workflow_types:
        required_workflow_permissions.add('deployments.destroy')
    missing_workflow_permissions = required_workflow_permissions - request.state.permissions
    if missing_workflow_permissions:
        raise HTTPException(
            403,
            'Missing Blueprint workflow permissions: ' + ', '.join(sorted(missing_workflow_permissions)),
        )
    def create():
        rendered, reservation, ip_allocation, guest_credential_id, template_guest_credential_id, ansible_runs, awx = compile_blueprint(
            db, row, data.variables, data.hostname_values, actor.user_id, data.apmid, data.environment
        )
        from app.policy_engine.integration import enforce_blueprint_execution
        rendered, policy_result = enforce_blueprint_execution(
            db, request, actor, request.state.permissions, row, rendered,
            apmid=data.apmid or (row.deployment or {}).get('apmid'),
            environment=data.environment or (row.deployment or {}).get('environment'),
        )
        blueprint_variables = rendered.pop('blueprint_variables')
        parsed = DeploymentInput.model_validate(rendered)
        require_catalog_item_enabled(db, 'templates', parsed.template)
        parsed.variables = validate_template_variables(
            parsed.template, parsed.variables
        ).model_dump(mode='json')
        provider = find(db, Provider, parsed.provider_id)
        template_meta, _ = template_definition(parsed.template)
        if provider.type != template_meta['provider']:
            raise HTTPException(422, 'Policy-selected provider does not match the Terraform template')
        if provider.credentials_id != parsed.credentials_id:
            raise HTTPException(422, 'Credential does not belong to the selected provider')
        primary_ansible = parsed.ansible or (ansible_runs[0] if ansible_runs else None)
        credential_ids = {parsed.credentials_id}
        if primary_ansible:
            credential_ids.add(primary_ansible.credentials_id)
        credential_ids.update(run.credentials_id for run in ansible_runs)
        if guest_credential_id:
            credential_ids.add(guest_credential_id)
        if template_guest_credential_id:
            credential_ids.add(template_guest_credential_id)
        if awx:
            credential_ids.add(awx.credential_id)
        for credential_id in sorted(credential_ids):
            locked_credential(db, credential_id)
        if awx:
            awx_credential = locked_credential(db, awx.credential_id)
            if awx_credential.type != 'awx':
                raise HTTPException(422, 'AWX onboarding requires an AWX credential')
        configured_ansible = ansible_runs or ([parsed.ansible] if parsed.ansible else [])
        if configured_ansible:
            if provider.type != 'proxmox':
                raise HTTPException(422, 'Blueprint Ansible post-provisioning currently requires Proxmox')
            if 'ansible.execute' not in request.state.permissions:
                raise HTTPException(403, 'ansible.execute required by blueprint')
            for ansible_run in configured_ansible:
                validate_ansible(db, ansible_run)
        deployment = Deployment(name=parsed.name, provider_id=provider.id, provider=provider.type, template=parsed.template,
                                credentials_id=parsed.credentials_id, variables=parsed.variables,
                                workflow={'ansible': primary_ansible.model_dump() if primary_ansible else None,
                                          'ansible_runs': [run.model_dump() for run in configured_ansible],
                                          'awx': awx.model_dump() if awx else None,
                                          'blueprint': {'id': row.id, 'slug': row.slug, 'version': row.version,
                                                        'variables': blueprint_variables, 'steps': row.workflow,
                                                        'guest_credential_id': guest_credential_id,
                                                        'template_guest_credential_id': template_guest_credential_id,
                                                        'guest_account_mode': (row.deployment or {}).get('guest_account_mode', 'cloud_init_managed'),
                                                        'awx': awx.model_dump() if awx else None,
                                                        'requires_approval': (
                                                            row.requires_approval
                                                            or policy_result.get('decision') == 'approval_required'
                                                        ),
                                                        'policy_decision_id': policy_result.get('decision_id'),
                                                        'policy_matched_ids': policy_result.get('matched_policy_ids') or [],
                                                        'policy_approvals': policy_result.get('approvals') or [],
                                                        'policy_obligations': policy_result.get('obligations') or [],
                                                        'policy_warnings': policy_result.get('warnings') or [],
                                                        'auto_approve_for_executors': (
                                                            False
                                                            if policy_result.get('decision') == 'approval_required'
                                                            else row.auto_approve_for_executors
                                                        ),
                                                        'approval_timeout_hours': row.approval_timeout_hours,
                                                        'recovery_policy': row.recovery_policy}},
                                created_by=actor.user_id, executor=parsed.executor)
        db.add(deployment)
        db.flush()
        deployment.state_location = f'database://terraform-states/{deployment.id}'
        job = new_job(db, request, actor, 'terraform.apply', deployment,
                      {'ansible': primary_ansible.model_dump() if primary_ansible else None,
                       'ansible_runs': [run.model_dump() for run in configured_ansible],
                       'blueprint': deployment.workflow['blueprint']})
        if reservation:
            reservation.status, reservation.resource_id = 'assigned', deployment.id
        if ip_allocation:
            ip_allocation.status, ip_allocation.resource_id = 'assigned', deployment.id
        audit(db, request, 'blueprint.executed', 'blueprints', row.id)
        audit(db, request, 'deployment.created', 'deployments', deployment.id)
        return {**deployment_public(deployment), 'job': job_public(job)}
    return idempotent(db, request, actor, {'blueprint_id': id, **data.model_dump(mode='json')}, create, required=True)
