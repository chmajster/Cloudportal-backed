import re
import uuid
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.access import ensure_inventory_resource_access, ensure_inventory_vm_access
from app.catalog import playbook_definition
from app.day2.diff import configuration_diff, redact
from app.day2.errors import Day2Failure, failure
from app.day2.models import BulkDay2ActionRequest, Day2ActionRequest, Day2ResourceLock, Day2ResourceState
from app.day2.providers import Day2Target, day2_provider
from app.day2.registry import all_actions, get_action
from app.models import Credential, Deployment, Job, JobLog, ManagedResource, ManagedVM, Provider, Setting, now
from app.operations.service import queue_webhook_event
from app.security.core import audit


DEFAULT_SETTINGS = {
    'enable_day2_actions': True,
    'default_approval_policy': 'destructive',
    'action_approval': {
        'delete_vm': True,
        'rebuild_vm': True,
        'restore_snapshot': True,
        'delete_disk': True,
    },
    'environment_policies': {},
    'approval_bypass_permissions': [],
    'allow_force_power_off': True,
    'allow_delete': True,
    'allow_rebuild': False,
    'allow_direct_provider_changes_for_terraform': False,
    'resource_lock_timeout': 3600,
    'action_timeout': 3600,
    'max_bulk_action_size': 25,
    'require_reason_for_destructive_actions': True,
    'require_confirmation_for_destructive_actions': True,
}

AUTOMATION_ACTIONS = {
    'run_ansible', 'update_credentials', 'install_package', 'remove_package', 'update_packages', 'patch_system',
}
PLATFORM_ACTIONS = {'update_metadata'}
PROTECTION_ACTIONS = {'delete_vm', 'rebuild_vm', 'delete_disk'}
TERRAFORM_LIFECYCLE_ACTIONS = {'delete_vm', 'rebuild_vm'}


def day2_settings(db):
    row = db.get(Setting, 'day2')
    value = dict(DEFAULT_SETTINGS)
    if row and isinstance(row.value, dict):
        value.update(row.value)
    value['resource_lock_timeout'] = max(60, min(86400, int(value.get('resource_lock_timeout', 3600))))
    value['action_timeout'] = max(60, min(86400, int(value.get('action_timeout', 3600))))
    value['max_bulk_action_size'] = max(1, min(100, int(value.get('max_bulk_action_size', 25))))
    return value


def save_day2_settings(db, value):
    row = db.get(Setting, 'day2')
    if row is None:
        row = Setting(key='day2', value=dict(value))
        db.add(row)
    else:
        row.value = dict(value)
    db.flush()
    return day2_settings(db)


def _provider_and_credential(db, provider_id):
    provider = db.get(Provider, int(provider_id))
    if provider is None:
        raise failure('RESOURCE_NOT_FOUND', message='Provider for the resource no longer exists', status_code=404)
    credential = db.get(Credential, provider.credentials_id)
    if credential is None:
        raise failure('PROVIDER_UNAVAILABLE', message='Provider credential no longer exists', status_code=503)
    return provider, credential


def load_target(db, resource_id: str):
    vm = db.get(ManagedVM, resource_id)
    if vm is not None:
        provider, credential = _provider_and_credential(db, vm.provider_id)
        target = Day2Target(
            resource_id=vm.id,
            provider_id=vm.provider_id,
            provider_type=provider.type,
            resource_type='vm',
            name=vm.name or f'vm-{vm.vm_id}',
            deployment_id=vm.deployment_id,
            management_mode='TERRAFORM_MANAGED' if vm.management_mode == 'terraform' else 'PROVIDER_MANAGED',
            node=vm.node,
            vm_id=vm.vm_id,
        )
        return target, credential, vm

    resource = db.get(ManagedResource, resource_id)
    if resource is None:
        raise failure('RESOURCE_NOT_FOUND', status_code=404)
    provider, credential = _provider_and_credential(db, resource.provider_id)
    deployment = db.get(Deployment, resource.deployment_id) if resource.deployment_id else None
    managed = bool(deployment and deployment.executor in {'terraform', 'opentofu'})
    target = Day2Target(
        resource_id=resource.id,
        provider_id=resource.provider_id,
        provider_type=provider.type,
        resource_type=resource.resource_type,
        name=resource.name,
        deployment_id=resource.deployment_id,
        management_mode='TERRAFORM_MANAGED' if managed else 'EXTERNAL',
        external_id=resource.external_id,
        primary_ip=resource.primary_ip,
    )
    return target, credential, resource


def resolve_target(db, resource_id, request, actor):
    target, credential, row = load_target(db, resource_id)
    if isinstance(row, ManagedVM):
        ensure_inventory_vm_access(db, request, actor, row)
    else:
        ensure_inventory_resource_access(db, request, actor, row)
    return target, credential, row


def get_resource_state(db, resource_id, *, create=False):
    state = db.get(Day2ResourceState, resource_id)
    if state is None and create:
        state = Day2ResourceState(resource_id=resource_id)
        db.add(state)
        db.flush()
    return state


def resource_state_public(state):
    if state is None:
        return {
            'protected': False,
            'platform_metadata': {},
            'provider_metadata': {},
            'desired_configuration': {},
            'actual_configuration': {},
            'last_synced_at': None,
        }
    return {
        'protected': state.protected,
        'platform_metadata': redact(state.platform_metadata or {}),
        'provider_metadata': redact(state.provider_metadata or {}),
        'desired_configuration': redact(state.desired_configuration or {}),
        'actual_configuration': redact(state.actual_configuration or {}),
        'last_synced_at': state.last_synced_at.isoformat() + 'Z' if state.last_synced_at else None,
    }


def _type_matches(value, expected):
    if isinstance(expected, list):
        return any(_type_matches(value, item) for item in expected)
    if expected == 'null':
        return value is None
    if expected == 'object':
        return isinstance(value, dict)
    if expected == 'array':
        return isinstance(value, list)
    if expected == 'string':
        return isinstance(value, str)
    if expected == 'boolean':
        return isinstance(value, bool)
    if expected == 'integer':
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == 'number':
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return True


def _validate_value(spec, value, path):
    expected = spec.get('type')
    if expected is not None and not _type_matches(value, expected):
        raise failure('VALIDATION_FAILED', message=f'Invalid type for {path}', status_code=422, details={'field': path})
    if value is None:
        return
    if 'enum' in spec and value not in spec['enum']:
        raise failure('VALIDATION_FAILED', message=f'Unsupported value for {path}', status_code=422, details={'field': path})
    if isinstance(value, str):
        if len(value) < int(spec.get('minLength', 0)) or len(value) > int(spec.get('maxLength', 10**9)):
            raise failure('VALIDATION_FAILED', message=f'Invalid length for {path}', status_code=422, details={'field': path})
        if spec.get('pattern') and not re.fullmatch(spec['pattern'], value):
            raise failure('VALIDATION_FAILED', message=f'Invalid format for {path}', status_code=422, details={'field': path})
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if 'minimum' in spec and value < spec['minimum']:
            raise failure('VALIDATION_FAILED', message=f'{path} is below the minimum', status_code=422, details={'field': path})
        if 'maximum' in spec and value > spec['maximum']:
            raise failure('VALIDATION_FAILED', message=f'{path} exceeds the maximum', status_code=422, details={'field': path})
    if isinstance(value, list):
        if len(value) > int(spec.get('maxItems', 10**9)):
            raise failure('VALIDATION_FAILED', message=f'Too many values for {path}', status_code=422, details={'field': path})
        item_spec = spec.get('items') or {}
        for index, item in enumerate(value):
            _validate_value(item_spec, item, f'{path}[{index}]')
    if isinstance(value, dict) and spec.get('properties') is not None:
        _validate_schema(spec, value, path)


def _validate_schema(schema, values, path='parameters'):
    if not isinstance(values, dict):
        raise failure('VALIDATION_FAILED', message='Action parameters must be an object', status_code=422)
    properties = schema.get('properties') or {}
    required = set(schema.get('required') or [])
    missing = sorted(key for key in required if key not in values)
    if missing:
        raise failure('VALIDATION_FAILED', message='Missing required action parameters', status_code=422, details={'fields': missing})
    if schema.get('additionalProperties') is False:
        extras = sorted(set(values) - set(properties))
        if extras:
            raise failure('VALIDATION_FAILED', message='Unknown action parameters', status_code=422, details={'fields': extras})
    for key, value in values.items():
        if key in properties:
            _validate_value(properties[key], value, f'{path}.{key}')


def _environment(db, target, state):
    if state and isinstance(state.platform_metadata, dict) and state.platform_metadata.get('environment'):
        return str(state.platform_metadata['environment'])
    if target.deployment_id:
        deployment = db.get(Deployment, target.deployment_id)
        if deployment and isinstance(deployment.variables, dict):
            return deployment.variables.get('environment')
    return None


def approval_required(db, target, action, permissions, config, state=None):
    if 'day2.admin' in permissions or set(config.get('approval_bypass_permissions') or []) & set(permissions):
        return False
    explicit = config.get('action_approval') or {}
    required = bool(explicit.get(action.id, action.approval_default))
    policy = str(config.get('default_approval_policy') or 'destructive')
    if policy == 'all':
        required = True
    elif policy == 'destructive' and action.destructive:
        required = True
    elif policy == 'none' and action.id not in explicit:
        required = False
    environment = _environment(db, target, state)
    if environment:
        env_policy = (config.get('environment_policies') or {}).get(str(environment), {})
        if action.id in set(env_policy.get('approval_required') or []):
            required = True
        if action.id in set(env_policy.get('approval_not_required') or []):
            required = False
    return required


def _provider_supports(adapter, action_id, target):
    if action_id in PLATFORM_ACTIONS:
        return True
    if action_id in AUTOMATION_ACTIONS:
        return target.resource_type == 'vm'
    capabilities = adapter.capabilities(target)
    return action_id in set(capabilities.get('actions') or [])


def _availability_reason(db, target, action, adapter, permissions, config, state, power_state=None):
    if not config['enable_day2_actions']:
        return 'Day-2 Actions are disabled globally'
    if action.permission not in permissions:
        return 'Missing permission: ' + action.permission
    if target.resource_type not in action.resource_types:
        return 'Action does not support this resource type'
    if not _provider_supports(adapter, action.id, target):
        return 'Provider does not support this action'
    if action.id == 'power_off' and not config.get('allow_force_power_off', True):
        return 'Forced power off is disabled by policy'
    if action.id == 'delete_vm' and not config.get('allow_delete', True):
        return 'Resource deletion is disabled by policy'
    if action.id == 'rebuild_vm' and not config.get('allow_rebuild', False):
        return 'Resource rebuild is disabled by policy'
    if state and state.protected and action.id in PROTECTION_ACTIONS and 'day2.override_protection' not in permissions:
        return 'Resource is protected'
    if target.management_mode == 'TERRAFORM_MANAGED':
        if action.id in TERRAFORM_LIFECYCLE_ACTIONS:
            return 'Terraform-managed lifecycle changes must use the deployment workflow'
        if action.mutates_configuration and not config.get('allow_direct_provider_changes_for_terraform', False):
            return 'Direct configuration changes are blocked for Terraform-managed resources'
    if action.supported_states is not None and power_state and str(power_state).lower() not in action.supported_states:
        return 'Current resource state does not allow this action'
    return None


def action_catalog(db, target, credential, permissions):
    config = day2_settings(db)
    adapter = day2_provider(credential)
    state = get_resource_state(db, target.resource_id)
    try:
        live = adapter.snapshot_state(target)
        power_state = live.get('power_state')
        capabilities = adapter.capabilities(target)
    except (HTTPException, Day2Failure):
        live = {'power_state': 'unknown'}
        power_state = None
        capabilities = {'provider': target.provider_type, 'actions': [], 'unavailable': True}
    items = []
    for action in all_actions():
        if action.permission not in permissions:
            continue
        reason = _availability_reason(db, target, action, adapter, permissions, config, state, power_state)
        item = action.public()
        item.update({
            'supported': reason is None,
            'unavailable_reason': reason,
            'approval_required': approval_required(db, target, action, permissions, config, state),
        })
        items.append(item)
    return {
        'resource_id': target.resource_id,
        'resource_type': target.resource_type,
        'management_mode': target.management_mode,
        'power_state': power_state or 'unknown',
        'capabilities': capabilities,
        'actions': items,
    }


def _current_for_diff(action_id, target, adapter, state, params=None):
    if action_id == 'resize_compute':
        config = adapter.configuration(target)
        return {
            'cpu_cores': config.get('cores'),
            'cpu_sockets': config.get('sockets'),
            'memory_mb': config.get('memory'),
        }
    if action_id in {'update_tags', 'add_tag', 'remove_tag'}:
        config = adapter.configuration(target)
        return {'tags': [item for item in str(config.get('tags') or '').split(';') if item]}
    if action_id == 'update_metadata':
        return {'metadata': dict((state.platform_metadata if state else {}) or {})}
    if action_id == 'migrate_vm':
        return {'target_node': target.node}
    if action_id == 'resize_disk':
        rows = adapter.disks(target)
        device = (params or {}).get('device')
        row = next((item for item in rows if item['device'] == device), None)
        return {'new_size_gib': row.get('size_gib')} if row else {}
    return {}


def _validate_action_specific(db, target, credential, adapter, action, params):
    warnings = []
    if action.id == 'resize_compute' and not any(key in params for key in ('cpu_cores', 'cpu_sockets', 'memory_mb')):
        raise failure('VALIDATION_FAILED', message='At least one CPU or memory field must be supplied', status_code=422)
    if action.id == 'migrate_vm':
        nodes = {str(row.get('node')) for row in adapter.provider.discover('nodes') if row.get('node')}
        if params['target_node'] not in nodes:
            raise failure('VALIDATION_FAILED', message='Target node is not available from the provider', status_code=422)
        if params['target_node'] == target.node:
            raise failure('VALIDATION_FAILED', message='Target node must differ from the current node', status_code=422)
    if action.id in {'add_disk', 'move_storage'}:
        node = params.get('target_node') or target.node
        storages = {str(row.get('storage')) for row in adapter.provider.discover('storages', node) if row.get('storage')}
        key = 'storage' if action.id == 'add_disk' else 'target_storage'
        if params[key] not in storages:
            raise failure('VALIDATION_FAILED', message='Selected storage is not available on the provider', status_code=422)
    if action.id == 'update_cloud_init':
        config = adapter.configuration(target)
        if not any('cloudinit' in str(value).lower() for value in config.values()):
            raise failure('ACTION_NOT_SUPPORTED', message='VM does not expose a cloud-init drive', status_code=409)
    if action.id == 'run_ansible':
        try:
            playbook_definition(params['playbook'])
        except Exception:
            raise failure('VALIDATION_FAILED', message='Only playbooks from the approved catalog can be executed', status_code=422) from None
        guest = db.get(Credential, int(params['credential_id']))
        if guest is None or guest.type not in {'ssh', 'winrm'}:
            raise failure('VALIDATION_FAILED', message='RUN_ANSIBLE requires an SSH or WinRM credential', status_code=422)
    if action.id in {'install_package', 'remove_package', 'update_packages', 'patch_system', 'update_credentials'}:
        guest = db.get(Credential, int(params['credential_id']))
        if guest is None or guest.type not in {'ssh', 'winrm'}:
            raise failure('VALIDATION_FAILED', message='Guest operation requires an SSH or WinRM credential', status_code=422)
        if action.id in {'install_package', 'remove_package'} and guest.type != 'ssh':
            raise failure('ACTION_NOT_SUPPORTED', message='Package install/remove is currently provided by the approved Linux catalog', status_code=409)
        if action.id == 'update_credentials' and params.get('operation') != 'create_user':
            raise failure('ACTION_NOT_SUPPORTED', message='This credential operation has no approved catalog playbook yet', status_code=409)
    if action.id == 'create_snapshot' and params.get('quiesce'):
        raise failure('ACTION_NOT_SUPPORTED', message='Explicit filesystem quiesce is not exposed by the Proxmox adapter')
    return warnings


def validate_action(db, target, credential, action_id, params, reason, permissions):
    action = get_action(action_id)
    config = day2_settings(db)
    if action.permission not in permissions:
        raise failure('PERMISSION_DENIED', status_code=403, details={'permission': action.permission})
    _validate_schema(action.schema, params)
    adapter = day2_provider(credential)
    state = get_resource_state(db, target.resource_id)
    try:
        live = adapter.snapshot_state(target)
    except Day2Failure:
        raise
    except HTTPException:
        raise failure('PROVIDER_UNAVAILABLE', status_code=503) from None
    reason_unavailable = _availability_reason(
        db, target, action, adapter, permissions, config, state, live.get('power_state')
    )
    if reason_unavailable:
        code = 'PROTECTED_RESOURCE' if 'protected' in reason_unavailable.lower() else 'ACTION_NOT_SUPPORTED'
        if 'Terraform' in reason_unavailable:
            code = 'TERRAFORM_OWNED'
        if 'state' in reason_unavailable.lower():
            code = 'INVALID_STATE'
        raise failure(code, message=reason_unavailable, status_code=409)
    if action.destructive and config.get('require_reason_for_destructive_actions', True) and not str(reason or '').strip():
        raise failure('VALIDATION_FAILED', message='Reason is required for destructive Day-2 actions', status_code=422)
    if action.requires_confirmation and config.get('require_confirmation_for_destructive_actions', True):
        if str(params.get('confirmation') or '') != target.name:
            raise failure('VALIDATION_FAILED', message='Confirmation must exactly match the resource name', status_code=422, details={'expected': target.name})
    warnings = _validate_action_specific(db, target, credential, adapter, action, params)
    current = _current_for_diff(action.id, target, adapter, state, params)
    requested = {key: value for key, value in params.items() if key != 'confirmation'}
    changes = configuration_diff(current, requested)
    capabilities = adapter.capabilities(target)
    restart_required = False
    if action.id == 'resize_compute' and str(live.get('power_state')).lower() == 'running':
        if 'cpu_cores' in params or 'cpu_sockets' in params:
            restart_required = restart_required or not capabilities.get('hot_cpu_supported', False)
        if 'memory_mb' in params:
            restart_required = restart_required or not capabilities.get('hot_memory_supported', False)
    return {
        'valid': True,
        'warnings': warnings,
        'errors': [],
        'changes': changes,
        'restart_required': restart_required,
        'approval_required': approval_required(db, target, action, permissions, config, state),
        'quota_checked': False,
        'management_mode': target.management_mode,
    }


def _lock_row(db, resource_id):
    return db.scalar(select(Day2ResourceLock).where(Day2ResourceLock.resource_id == resource_id).with_for_update())


def acquire_resource_lock(db, resource_id, action_request_id, timeout_seconds):
    row = _lock_row(db, resource_id)
    current = now()
    if row is not None and row.expires_at <= current and row.action_request_id != action_request_id:
        previous = db.get(Day2ActionRequest, row.action_request_id)
        if (previous is None or previous.status not in {'SUCCEEDED', 'SUCCEEDED_WITH_WARNING', 'FAILED', 'CANCELLED'}
                or (previous.result or {}).get('reconciliation_required')):
            raise failure('RESOURCE_LOCKED', message='Expired execution lease requires reconciliation before reuse')
        db.delete(row)
        db.flush()
        row = None
    if row is not None and row.action_request_id != action_request_id:
        raise failure('RESOURCE_LOCKED', details={'action_request_id': row.action_request_id})
    if row is None:
        row = Day2ResourceLock(
            resource_id=resource_id,
            action_request_id=action_request_id,
            expires_at=current + timedelta(seconds=timeout_seconds),
        )
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
        except IntegrityError:
            raise failure('RESOURCE_LOCKED') from None
    else:
        row.expires_at = current + timedelta(seconds=timeout_seconds)
    return row


def refresh_resource_lock(db, resource_id, action_request_id, timeout_seconds):
    row = _lock_row(db, resource_id)
    if row is None or row.action_request_id != action_request_id:
        raise failure('RESOURCE_LOCKED', message='Day-2 execution lost its resource lock')
    row.expires_at = now() + timedelta(seconds=timeout_seconds)


def release_resource_lock(db, resource_id, action_request_id):
    row = _lock_row(db, resource_id)
    if row is not None and row.action_request_id == action_request_id:
        db.delete(row)


def public_action_request(row):
    return {
        'id': row.id,
        'resource_id': row.resource_id,
        'deployment_id': row.deployment_id,
        'action': row.action,
        'parameters': redact(row.parameters or {}),
        'reason': row.reason,
        'requested_by': row.requested_by,
        'requested_at': row.requested_at.isoformat() + 'Z' if row.requested_at else None,
        'approved_by': row.approved_by,
        'approved_at': row.approved_at.isoformat() + 'Z' if row.approved_at else None,
        'approval_state': row.approval_state,
        'job_id': row.job_id,
        'status': row.status,
        'started_at': row.started_at.isoformat() + 'Z' if row.started_at else None,
        'finished_at': row.finished_at.isoformat() + 'Z' if row.finished_at else None,
        'error_code': row.error_code,
        'error_message': row.error_message,
        'correlation_id': row.correlation_id,
        'request_id': row.request_id,
        'retry_of': row.retry_of,
        'attempt': row.attempt,
        'changes': redact(row.safe_diff or {}),
        'result': redact(row.result or {}),
    }


def _event(db, event, row, extra=None):
    payload = {
        'action_request': {
            'id': row.id,
            'resource_id': row.resource_id,
            'action': row.action,
            'status': row.status,
            'user_id': row.requested_by,
            'correlation_id': row.correlation_id,
            **(extra or {}),
        }
    }
    queue_webhook_event(db, event, row.id, redact(payload))


def create_action(db, request, actor, target, credential, action_id, params, reason, permissions, *, retry_of=None, attempt=1):
    validation = validate_action(db, target, credential, action_id, params, reason, permissions)
    action = get_action(action_id)
    request_id = str(getattr(request.state, 'request_id', '') or uuid.uuid4())
    row = Day2ActionRequest(
        resource_id=target.resource_id,
        deployment_id=target.deployment_id,
        action=action.id,
        parameters=redact(dict(params)),
        reason=str(reason or '').strip(),
        requested_by=actor.user_id,
        approval_state='pending' if validation['approval_required'] else 'not_required',
        status='WAITING_APPROVAL' if validation['approval_required'] else 'QUEUED',
        request_id=request_id,
        retry_of=retry_of,
        attempt=attempt,
        safe_diff=validation['changes'],
    )
    db.add(row)
    db.flush()
    job = Job(
        id=str(uuid.uuid4()),
        deployment_id=None,
        operation='day2.' + action.id,
        payload={'day2_action_request_id': row.id, 'resource_id': target.resource_id},
        status='waiting_approval' if validation['approval_required'] else 'queued',
        created_by=actor.user_id,
        token_id=actor.id,
        request_id=request_id,
        ip=request.client.host if request.client else '',
        source=getattr(request.state, 'source', 'API'),
    )
    db.add(job)
    db.flush()
    row.job_id = job.id
    if not validation['approval_required']:
        acquire_resource_lock(db, target.resource_id, row.id, day2_settings(db)['resource_lock_timeout'])
    db.add(JobLog(job_id=job.id, message='day2.requested: ' + action.id))
    audit(db, request, 'day2.requested', 'day2_actions', row.id)
    _event(db, 'day2.approval_required' if validation['approval_required'] else 'day2.requested', row)
    return row, validation


def _lock_action_job(db, row):
    # Match the worker's job -> request lock order and discard stale API snapshots.
    if row.job_id:
        db.scalar(select(Job).where(Job.id == row.job_id).with_for_update())
    return db.scalar(select(Day2ActionRequest).where(Day2ActionRequest.id == row.id)
                     .execution_options(populate_existing=True).with_for_update())


def approve_action(db, request, actor, row, permissions):
    row = _lock_action_job(db, row)
    if 'day2.approve' not in permissions and 'day2.admin' not in permissions:
        raise failure('PERMISSION_DENIED', status_code=403, details={'permission': 'day2.approve'})
    if row.status != 'WAITING_APPROVAL' or row.approval_state != 'pending':
        raise failure('INVALID_STATE', message='Action is not waiting for approval')
    target, credential, _ = load_target(db, row.resource_id)
    validate_action(db, target, credential, row.action, row.parameters or {}, row.reason, permissions | {get_action(row.action).permission})
    acquire_resource_lock(db, row.resource_id, row.id, day2_settings(db)['resource_lock_timeout'])
    row.approval_state = 'approved'
    row.approved_by = actor.user_id
    row.approved_at = now()
    row.status = 'QUEUED'
    job = db.get(Job, row.job_id)
    if job is None:
        raise failure('RESOURCE_NOT_FOUND', message='Action job no longer exists', status_code=404)
    job.status = 'queued'
    payload = dict(job.payload or {})
    payload['_day2_approval'] = {'approved_by': actor.user_id, 'approved_at': row.approved_at.isoformat()}
    job.payload = payload
    db.add(JobLog(job_id=job.id, message='day2.approved'))
    audit(db, request, 'day2.approved', 'day2_actions', row.id)
    _event(db, 'day2.approved', row)
    return row


def cancel_action(db, request, actor, row):
    row = _lock_action_job(db, row)
    action = get_action(row.action)
    if row.status in {'SUCCEEDED', 'SUCCEEDED_WITH_WARNING', 'FAILED', 'CANCELLED'}:
        raise failure('INVALID_STATE', message='Completed Day-2 action cannot be cancelled')
    job = db.get(Job, row.job_id) if row.job_id else None
    if row.status in {'WAITING_APPROVAL', 'QUEUED'}:
        row.status = 'CANCELLED'
        row.finished_at = now()
        row.approval_state = 'cancelled' if row.approval_state == 'pending' else row.approval_state
        if job:
            job.cancel_requested = True
            job.status = 'cancelled'
            job.error = 'Cancelled before execution'
            db.add(JobLog(job_id=job.id, message='day2.cancelled: before execution'))
        release_resource_lock(db, row.resource_id, row.id)
        _event(db, 'day2.cancelled', row)
    elif action.supports_cancel:
        row.status = 'CANCEL_REQUESTED'
        if job:
            job.cancel_requested = True
            job.status = 'cancelling'
            db.add(JobLog(job_id=job.id, message='day2.cancel_requested'))
    else:
        raise failure('ACTION_NOT_SUPPORTED', message='Provider action does not support cancellation')
    audit(db, request, 'day2.cancel_requested', 'day2_actions', row.id)
    return row


def reconcile_resource(db, target, adapter, *, desired=None):
    snapshot = adapter.snapshot_state(target)
    state = get_resource_state(db, target.resource_id, create=True)
    state.actual_configuration = redact(snapshot)
    state.provider_metadata = {'provider': target.provider_type, 'node': snapshot.get('node')}
    state.last_synced_at = now()
    if desired:
        merged = dict(state.desired_configuration or {})
        merged.update(redact(desired))
        state.desired_configuration = merged
    vm = db.get(ManagedVM, target.resource_id)
    if vm is not None:
        if snapshot.get('node'):
            vm.node = str(snapshot['node'])
        if snapshot.get('name'):
            vm.name = str(snapshot['name'])[:100]
        if vm.lifecycle_status != 'destroyed':
            vm.lifecycle_status = 'active'
    resource = db.get(ManagedResource, target.resource_id)
    if resource is not None:
        if snapshot.get('name'):
            resource.name = str(snapshot['name'])[:100]
        if snapshot.get('primary_ip'):
            resource.primary_ip = str(snapshot['primary_ip'])[:64]
        if resource.lifecycle_status != 'destroyed':
            resource.lifecycle_status = 'active'
    desired_state = state.desired_configuration or {}
    actual = state.actual_configuration or {}
    drift = {
        key: {'desired': value, 'actual': actual.get(key)}
        for key, value in desired_state.items()
        if key in actual and actual.get(key) != value
    }
    return snapshot, drift


def mark_resource_deleted(db, target):
    when = now()
    vm = db.get(ManagedVM, target.resource_id)
    if vm is not None:
        vm.lifecycle_status = 'destroyed'
        vm.destroyed_at = when
    resource = db.get(ManagedResource, target.resource_id)
    if resource is not None:
        resource.lifecycle_status = 'destroyed'
        resource.destroyed_at = when
    state = get_resource_state(db, target.resource_id, create=True)
    actual = dict(state.actual_configuration or {})
    actual['lifecycle_status'] = 'destroyed'
    state.actual_configuration = actual
    state.last_synced_at = when


def update_platform_metadata(db, target, metadata):
    state = get_resource_state(db, target.resource_id, create=True)
    current = dict(state.platform_metadata or {})
    current.update(redact(metadata or {}))
    state.platform_metadata = current
    return current


def list_action_requests(db, resource_id=None, limit=100, offset=0):
    query = select(Day2ActionRequest)
    if resource_id:
        query = query.where(Day2ActionRequest.resource_id == resource_id)
    return db.scalars(query.order_by(Day2ActionRequest.requested_at.desc()).offset(offset).limit(limit)).all()
