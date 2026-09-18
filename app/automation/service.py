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


HOSTNAME_FIELDS = 'id scheme_id hostname values status resource_id created_by created_at updated_at released_at'
BLUEPRINT_FIELDS = ('id slug name description version is_active visibility allowed_role_ids allowed_user_ids '
                    'variables_schema deployment workflow requires_approval recovery_policy created_by created_at updated_at')


def as_public(row, fields):
    return {name: getattr(row, name) for name in fields.split()}


def hostname_public(row):
    return as_public(row, HOSTNAME_FIELDS)


def blueprint_public(row):
    return as_public(row, BLUEPRINT_FIELDS)


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


def compile_blueprint(db, blueprint, supplied, hostname_values, actor_id):
    variables = validate_blueprint_variables(blueprint.variables_schema, supplied)
    reservation = None
    ip_allocation = None
    deployment = deepcopy(blueprint.deployment)
    scheme_id = deployment.pop('hostname_scheme_id', None)
    ipam_pool_id = deployment.pop('ipam_pool_id', None)
    if scheme_id:
        hostname, reservation = generate_hostname(db, scheme_id, hostname_values, actor_id, reserve=True)
        variables['hostname'] = hostname
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
    rendered['variables'] = validate_template_variables(rendered.get('template', 'proxmox-vm'), rendered['variables']).model_dump(mode='json')
    if rendered.get('ansible'):
        rendered['ansible'] = AnsibleInput.model_validate(rendered['ansible'])
    rendered['blueprint_variables'] = variables
    return rendered, reservation, ip_allocation
