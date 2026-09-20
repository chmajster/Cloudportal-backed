import re
import secrets
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from fastapi import HTTPException
from sqlalchemy import select
from app.api.schemas import AnsibleInput
from app.catalog import validate_template_variables
from app.ipam.service import allocate_address
from app.models import Blueprint, HostnameReservation, HostnameScheme, now
from app.vm_classification import vm_classification_settings


HOSTNAME_FIELDS = 'id scheme_id hostname values status resource_id created_by created_at updated_at released_at'
BLUEPRINT_FIELDS = ('id slug name description version is_active visibility allowed_role_ids allowed_user_ids '
                    'variables_schema deployment workflow requires_approval recovery_policy created_by created_at updated_at')


def as_public(row, fields):
    return {name: getattr(row, name) for name in fields.split()}


def hostname_public(row):
    return as_public(row, HOSTNAME_FIELDS)


def blueprint_public(row):
    result = as_public(row, BLUEPRINT_FIELDS)
    result['manager_role_ids'] = sorted(role.id for role in row.manager_roles)
    return result


def can_manage_blueprint(blueprint: Blueprint, actor) -> bool:
    required = {role.id for role in blueprint.manager_roles}
    if not required:
        return True
    actor_roles = {role.id for role in actor.user.roles}
    return bool(required & actor_roles)


def available_to(blueprint: Blueprint, actor, source: str = 'api') -> bool:
    if not blueprint.is_active or not blueprint.visibility.get(source, False):
        return False
    role_ids = {role.id for role in actor.user.roles}
    return (not blueprint.allowed_role_ids and not blueprint.allowed_user_ids
            or actor.user_id in blueprint.allowed_user_ids
            or bool(role_ids & set(blueprint.allowed_role_ids)))


def _hostname(scheme, values, number):
    values = {**values, 'year': str(datetime.now(timezone.utc).year),
              'number': str(number).zfill(scheme.padding), 'random': secrets.token_hex(3)}
    required = set(re.findall(r'{([a-z]+)}', scheme.pattern))
    missing = required - values.keys()
    if missing:
        raise HTTPException(422, 'Missing hostname values: ' + ', '.join(sorted(missing)))
    hostname = scheme.pattern
    for key in required:
        hostname = hostname.replace('{' + key + '}', str(values[key]).lower())
    if len(hostname) > 253 or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                                  for label in hostname.split('.')):
        raise HTTPException(422, 'Generated hostname is not a valid DNS hostname')
    return hostname


def generate_hostname(db, scheme_id, values, actor_id, reserve=True):
    query = select(HostnameScheme).where(HostnameScheme.id == scheme_id)
    if reserve:
        query = query.with_for_update()
    scheme = db.scalar(query)
    if not scheme or not scheme.is_active:
        raise HTTPException(404, 'Active hostname scheme not found')
    values = {**vm_classification_settings(db)['hostname_defaults'], **values}
    number = scheme.next_number
    for _ in range(1000):
        hostname = _hostname(scheme, values, number)
        exists = db.scalar(select(HostnameReservation.id).where(
            HostnameReservation.hostname == hostname, HostnameReservation.status != 'released'))
        if not exists:
            if not reserve:
                return hostname, None
            reservation = HostnameReservation(scheme_id=scheme.id, hostname=hostname, values=values,
                                              created_by=actor_id)
            db.add(reservation)
            scheme.next_number = number + 1
            db.flush()
            return hostname, reservation
        number += 1
    raise HTTPException(409, 'Unable to generate a unique hostname')


def validate_blueprint_variables(schema, supplied):
    unknown = set(supplied) - set(schema)
    if unknown:
        raise HTTPException(422, 'Unknown blueprint variables: ' + ', '.join(sorted(unknown)))
    result = {}
    for name, definition in schema.items():
        value = supplied.get(name, definition.get('default'))
        if value is None:
            if definition.get('required'):
                raise HTTPException(422, f'Missing blueprint variable: {name}')
            continue
        kind = definition['type']
        if kind == 'integer':
            if isinstance(value, bool) or not isinstance(value, int):
                raise HTTPException(422, f'{name} must be an integer')
            if definition.get('min') is not None and value < definition['min']:
                raise HTTPException(422, f'{name} is below the minimum')
            if definition.get('max') is not None and value > definition['max']:
                raise HTTPException(422, f'{name} exceeds the maximum')
        elif kind == 'boolean':
            if not isinstance(value, bool):
                raise HTTPException(422, f'{name} must be a boolean')
        else:
            if not isinstance(value, str) or len(value) > 8192:
                raise HTTPException(422, f'{name} must be a string')
            if kind == 'select' and value not in definition.get('options', []):
                raise HTTPException(422, f'{name} is not an allowed option')
        result[name] = value
    return result


EXACT_TEMPLATE = re.compile(r'^{{\s*([a-zA-Z0-9_]+)\s*}}$')
INLINE_TEMPLATE = re.compile(r'{{\s*([a-zA-Z0-9_]+)\s*}}')


def render_template(value: Any, variables: dict[str, Any]):
    if isinstance(value, dict):
        return {key: render_template(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        return [render_template(item, variables) for item in value]
    if not isinstance(value, str):
        return value
    exact = EXACT_TEMPLATE.fullmatch(value)
    if exact:
        if exact.group(1) not in variables:
            raise HTTPException(422, f'Unresolved blueprint variable: {exact.group(1)}')
        return variables[exact.group(1)]
    def replace(match):
        if match.group(1) not in variables:
            raise HTTPException(422, f'Unresolved blueprint variable: {match.group(1)}')
        return str(variables[match.group(1)])
    return INLINE_TEMPLATE.sub(replace, value)


def compile_blueprint(db, blueprint, supplied, hostname_values, actor_id, apmid=None, environment=None):
    variables = validate_blueprint_variables(blueprint.variables_schema, supplied)
    reservation = None
    ip_allocation = None
    deployment = deepcopy(blueprint.deployment)

    has_apmid_selection_flag = 'select_apmid_on_execute' in deployment
    select_apmid_on_execute = bool(deployment.pop('select_apmid_on_execute', False))
    select_environment_on_execute = bool(deployment.pop('select_environment_on_execute', False))
    fixed_apmid = deployment.pop('apmid', None)
    fixed_environment = deployment.pop('environment', None)
    scheme_id = deployment.pop('hostname_scheme_id', None)
    ipam_pool_id = deployment.pop('ipam_pool_id', None)
    default_hostname_values = deployment.pop('hostname_values', {})

    deployment_variables = deployment.setdefault('variables', {})
    tags = [str(tag).strip().lower() for tag in (deployment_variables.get('tags') or []) if str(tag).strip()]

    tagged_apmid = None
    tagged_environment = None
    for tag in tags:
        apmid_match = re.fullmatch(r'apmid-([a-z0-9][a-z0-9_-]{0,62})', tag)
        if apmid_match and tagged_apmid is None:
            tagged_apmid = apmid_match.group(1).upper()
        env_match = re.fullmatch(r'env-(test|dev|nonprod|prod)', tag)
        if env_match and tagged_environment is None:
            tagged_environment = env_match.group(1)

    fixed_apmid = str(fixed_apmid or tagged_apmid or '').strip().upper() or None
    fixed_environment = str(fixed_environment or tagged_environment or '').strip().lower() or None
    runtime_apmid = str(apmid or '').strip().upper() or None
    runtime_environment = str(environment or '').strip().lower() or None

    # Preserve legacy behavior for old Blueprints created before the explicit
    # runtime APMID switch existed: if they had no fixed APMID, keep asking for one.
    if not has_apmid_selection_flag and not fixed_apmid:
        select_apmid_on_execute = True

    classification = vm_classification_settings(db)
    if select_apmid_on_execute:
        if not runtime_apmid:
            raise HTTPException(422, 'APMID must be selected when this Blueprint is executed')
        if runtime_apmid not in classification['apmids']:
            raise HTTPException(422, 'Selected APMID is not configured or is no longer available')
    elif runtime_apmid and fixed_apmid and runtime_apmid != fixed_apmid:
        raise HTTPException(422, 'Blueprint does not allow changing APMID at runtime')

    enabled_environments = {
        name for name, enabled in classification['environments'].items() if enabled
    }
    if select_environment_on_execute:
        if not runtime_environment:
            raise HTTPException(422, 'Environment must be selected when this Blueprint is executed')
        if runtime_environment not in enabled_environments:
            raise HTTPException(422, 'Selected Environment is disabled or unavailable')
    elif runtime_environment and fixed_environment and runtime_environment != fixed_environment:
        raise HTTPException(422, 'Blueprint does not allow changing Environment at runtime')

    effective_apmid = runtime_apmid if select_apmid_on_execute else (fixed_apmid or runtime_apmid)
    effective_environment = (
        runtime_environment if select_environment_on_execute
        else (fixed_environment or runtime_environment)
    )

    # Classification tags are regenerated from the effective values so a
    # runtime selection never leaves stale Blueprint defaults on the VM.
    def classification_tag(tag):
        return (
            re.fullmatch(r'apmid-[a-z0-9][a-z0-9_-]{0,62}', tag)
            or re.fullmatch(r'env-(test|dev|nonprod|prod)', tag)
            or re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,62}\.(test|dev|nonprod|prod)', tag)
        )

    tags = [tag for tag in tags if not classification_tag(tag)]
    if effective_apmid:
        tags.append('apmid-' + effective_apmid.lower())
    if effective_environment:
        tags.append('env-' + effective_environment)
    if effective_apmid and effective_environment:
        tags.append(effective_apmid.lower() + '.' + effective_environment)
    deployment_variables['tags'] = sorted(set(tags))

    if scheme_id:
        defaults = render_template(default_hostname_values, variables)
        defaults = {key: value for key, value in defaults.items() if key not in {'location', 'role'}}
        merged_hostname_values = {**defaults, **hostname_values}

        scheme = db.get(HostnameScheme, scheme_id)
        if scheme and effective_environment:
            pattern_tokens = set(re.findall(r'{([a-z]+)}', scheme.pattern))
            if 'env' in pattern_tokens:
                merged_hostname_values['env'] = effective_environment
            if 'environment' in pattern_tokens:
                merged_hostname_values['environment'] = effective_environment

        hostname, reservation = generate_hostname(db, scheme_id, merged_hostname_values, actor_id, reserve=True)
        variables['hostname'] = hostname
        # A Blueprint with a hostname scheme uses the generated hostname as the
        # authoritative deployment and VM name. User-supplied/manual names must
        # not override the reserved sequence.
        deployment['name'] = '{{ hostname }}'
        deployment_variables = deployment.setdefault('variables', {})
        if 'name' in deployment_variables:
            deployment_variables['name'] = '{{ hostname }}'

    if ipam_pool_id:
        ip_allocation = allocate_address(
            db, ipam_pool_id, actor_id, hostname=variables.get('hostname')
        )
        variables['ip_address'] = ip_allocation.address
        variables['ip_prefix_length'] = ip_allocation.prefix_length
        variables['ip_address_cidr'] = f'{ip_allocation.address}/{ip_allocation.prefix_length}'
        variables['ip_gateway'] = ip_allocation.gateway
        deployment.setdefault('variables', {})
        deployment['variables'].setdefault('ipv4_address', '{{ ip_address_cidr }}')
        deployment['variables'].setdefault('ipv4_gateway', '{{ ip_gateway }}')

    rendered = render_template(deployment, variables)
    rendered['variables'] = validate_template_variables(
        rendered.get('template', 'proxmox-vm'), rendered['variables']
    ).model_dump(mode='json')
    if rendered.get('ansible'):
        rendered['ansible'] = AnsibleInput.model_validate(rendered['ansible'])
    rendered['blueprint_variables'] = variables
    return rendered, reservation, ip_allocation
