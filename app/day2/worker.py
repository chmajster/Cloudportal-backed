import time
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import select, update

from app.api.schemas import AnsibleInput, Inventory
from app.database import session
from app.day2.errors import Day2Failure, failure
from app.day2.models import Day2ActionRequest
from app.day2.providers import day2_provider
from app.day2.registry import get_action
from app.day2.service import (
    day2_settings,
    load_target,
    mark_resource_deleted,
    reconcile_resource,
    refresh_resource_lock,
    release_resource_lock,
    update_platform_metadata,
    validate_action,
)
from app.executors.ansible import AnsibleExecutor
from app.executors.base import Cancelled, ExecutionFailed
from app.models import Audit, Credential, Deployment, Job, JobLog, ManagedVM, Token, User, now
from app.operations.service import queue_job_webhooks, queue_webhook_event
from app.security.core import effective_permissions


class Day2Context:
    def __init__(self, job, action_request, timeout):
        self.job = job
        self.action_request = action_request
        self.started = time.monotonic()
        self.timeout = timeout
        self.last_check = 0.0
        self.ansible = None
        self.ansible_credential = None
        self.ansible_tags = []
        self.ansible_skip_tags = []
        self.ansible_timeout = None

    def check(self):
        if time.monotonic() - self.started > self.timeout:
            raise failure('TIMEOUT')
        if time.monotonic() - self.last_check < 1:
            return
        self.last_check = time.monotonic()
        with session() as db:
            job = db.get(Job, self.job.id)
            request = db.get(Day2ActionRequest, self.action_request.id)
            if job is None or request is None or job.cancel_requested or request.status == 'CANCEL_REQUESTED':
                raise Cancelled('Cancellation requested')
            job.heartbeat_at = now()
            refresh_resource_lock(
                db,
                request.resource_id,
                request.id,
                day2_settings(db)['resource_lock_timeout'],
            )
            db.commit()

    def log(self, message):
        if not str(message).strip():
            return
        with session() as db:
            db.add(JobLog(job_id=self.job.id, message=str(message)[:8192]))
            db.commit()

    def stage(self, action):
        self.check()
        self.log(action)
        with session() as db:
            db.add(Audit(
                user_id=self.job.created_by,
                token_id=self.job.token_id,
                ip=self.job.ip,
                source=self.job.source,
                action=action,
                resource='day2_actions',
                resource_id=self.action_request.id,
                request_id=self.job.request_id,
            ))
            db.commit()


def _execution_permissions(db, job, request):
    token = db.get(Token, job.token_id)
    if token is None or token.user is None:
        raise failure('PERMISSION_DENIED', message='Day-2 job authorization no longer exists', status_code=403)
    user = token.user
    if not user.is_active or user.is_locked or (user.locked_until is not None and user.locked_until > now()):
        raise failure('PERMISSION_DENIED', message='Day-2 job owner is disabled or locked', status_code=403)
    if token.kind == 'session':
        active_family = db.scalar(select(Token.id).where(
            Token.family == token.family,
            Token.kind == 'refresh',
            Token.revoked_at.is_(None),
            Token.expires_at > now(),
        ).limit(1))
        if not active_family:
            raise failure('PERMISSION_DENIED', message='Day-2 session has ended', status_code=403)
    elif token.kind != 'api' or token.revoked_at is not None or (token.expires_at and token.expires_at <= now()):
        raise failure('PERMISSION_DENIED', message='Day-2 API authorization has been revoked', status_code=403)
    permissions = effective_permissions(user)
    if token.kind == 'api':
        permissions &= set(token.scopes)
    required = get_action(request.action).permission
    if required not in permissions:
        raise failure('PERMISSION_DENIED', message='Day-2 permission has been revoked', status_code=403, details={'permission': required})
    return user, permissions


def _automation_spec(db, target, adapter, action, params):
    addresses = adapter.guest_addresses(target)
    if not addresses:
        raise failure('VALIDATION_FAILED', message='Guest IP address is unavailable; QEMU Guest Agent or synchronized inventory is required', status_code=422)
    credential = db.get(Credential, int(params['credential_id']))
    if credential is None or credential.type not in {'ssh', 'winrm'}:
        raise failure('VALIDATION_FAILED', message='Guest credential is unavailable', status_code=422)
    if credential.expires_at is not None and credential.expires_at <= now():
        raise failure('VALIDATION_FAILED', message='Guest credential is expired', status_code=409)

    if action == 'run_ansible':
        playbook = params['playbook']
        variables = dict(params.get('variables') or {})
    elif action == 'install_package':
        playbook = 'linux-install-packages'
        variables = {'packages': params['package']}
    elif action == 'remove_package':
        playbook = 'linux-remove-packages'
        variables = {'packages': params['package']}
    elif action in {'update_packages', 'patch_system'}:
        playbook = 'windows-update' if credential.type == 'winrm' else 'linux-system-update'
        variables = {}
    elif action == 'update_credentials' and params.get('operation') == 'create_user':
        if credential.type != 'ssh':
            raise failure('ACTION_NOT_SUPPORTED', message='Linux user creation requires an SSH credential')
        playbook = 'linux-create-user'
        variables = {'username': params['username'], 'user_shell': '/bin/bash', 'user_groups': ''}
    else:
        raise failure('ACTION_NOT_SUPPORTED', message='No approved automation implementation exists for this action')

    spec = AnsibleInput(
        playbook=playbook,
        credentials_id=credential.id,
        inventory=Inventory(hosts=addresses),
        variables=variables,
    )
    return spec, credential


def _sync_terraform_desired(db, target, action, params):
    if target.management_mode != 'TERRAFORM_MANAGED' or not target.deployment_id:
        return
    if not day2_settings(db).get('allow_direct_provider_changes_for_terraform', False):
        return
    deployment = db.get(Deployment, target.deployment_id)
    if deployment is None:
        return
    variables = dict(deployment.variables or {})
    if action == 'resize_compute':
        if 'cpu_cores' in params:
            variables['cpu'] = int(params['cpu_cores'])
        if 'memory_mb' in params:
            variables['memory'] = int(params['memory_mb'])
    elif action == 'migrate_vm':
        variables['node'] = params['target_node']
    deployment.variables = variables


def _emit(db, event, request, extra=None):
    queue_webhook_event(db, event, request.id, {
        'action_request': {
            'id': request.id,
            'resource_id': request.resource_id,
            'action': request.action,
            'status': request.status,
            'user_id': request.requested_by,
            'correlation_id': request.correlation_id,
            **(extra or {}),
        }
    })


def execute(job_id):
    task = None
    target = None
    adapter = None
    context = None
    started = time.monotonic()
    final_status = 'FAILED'
    error_code = None
    error_message = None
    result = {}
    warnings = []

    with session() as db:
        claimed = db.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == 'queued', Job.cancel_requested.is_(False))
            .values(status='running', heartbeat_at=now())
        )
        if claimed.rowcount != 1:
            db.commit()
            return
        job = db.get(Job, job_id)
        request = db.get(Day2ActionRequest, (job.payload or {}).get('day2_action_request_id'))
        if request is None:
            job.status = 'failed'
            job.error = 'Day-2 action request is missing'
            db.commit()
            return
        request.status = 'RUNNING'
        request.started_at = now()
        user, permissions = _execution_permissions(db, job, request)
        target, credential, _ = load_target(db, request.resource_id)
        refresh_resource_lock(db, target.resource_id, request.id, day2_settings(db)['resource_lock_timeout'])
        adapter = day2_provider(credential)
        validate_action(db, target, credential, request.action, request.parameters or {}, request.reason, permissions)
        context = Day2Context(job, request, day2_settings(db)['action_timeout'])
        db.add(JobLog(job_id=job.id, message='day2.started: ' + request.action))
        db.add(Audit(
            user_id=job.created_by, token_id=job.token_id, ip=job.ip, source=job.source,
            action='day2.started', resource='day2_actions', resource_id=request.id, request_id=job.request_id,
        ))
        _emit(db, 'day2.started', request)
        db.commit()

    try:
        context.stage('day2.execution.start:' + context.action_request.action)
        action = context.action_request.action
        params = dict(context.action_request.parameters or {})

        if action in {'run_ansible', 'install_package', 'remove_package', 'update_packages', 'patch_system', 'update_credentials'}:
            with session() as db:
                spec, guest_credential = _automation_spec(db, target, adapter, action, params)
            context.ansible = spec
            context.ansible_credential = guest_credential
            context.ansible_tags = list(params.get('tags') or [])
            context.ansible_skip_tags = list(params.get('skip_tags') or [])
            context.ansible_timeout = params.get('timeout')
            AnsibleExecutor().execute('ansible.execute', context)
        elif action == 'update_metadata':
            with session() as db:
                metadata = update_platform_metadata(db, target, params.get('metadata') or {})
                db.commit()
            result['platform_metadata'] = metadata
        else:
            context.stage('day2.provider.request:' + action)
            task = adapter.execute(target, action, params)
            task_result = adapter.wait_task(target, task, context.check, timeout=context.timeout)
            result.update(task_result)
            if action == 'migrate_vm':
                target.node = params['target_node']
                with session() as db:
                    vm = db.get(ManagedVM, target.resource_id)
                    if vm is not None:
                        vm.node = target.node
                    db.commit()

        context.check()
        with session() as db:
            if action == 'delete_vm':
                mark_resource_deleted(db, target)
            else:
                desired = {key: value for key, value in params.items() if key != 'confirmation'} if get_action(action).mutates_configuration else None
                try:
                    snapshot, drift = reconcile_resource(db, target, adapter, desired=desired)
                    result['actual'] = snapshot
                    result['drift'] = drift
                except Exception:
                    warnings.append('Provider action completed but state reconciliation failed; refresh the resource before another configuration change')
                _sync_terraform_desired(db, target, action, params)
            db.commit()
        final_status = 'SUCCEEDED_WITH_WARNING' if warnings else 'SUCCEEDED'
    except Cancelled:
        final_status = 'CANCELLED'
        if adapter is not None and target is not None and task is not None:
            if not adapter.cancel_task(target, task):
                warnings.append('Provider did not confirm cancellation of its asynchronous task')
        error_code = 'CANCELLED'
        error_message = 'Cancellation requested'
    except Day2Failure as exc:
        final_status = 'FAILED'
        error_code = exc.code
        error_message = exc.message[:500]
    except HTTPException:
        final_status = 'FAILED'
        error_code = 'PROVIDER_ERROR'
        error_message = 'Provider operation failed; inspect sanitized job logs and provider health'
    except ExecutionFailed as exc:
        final_status = 'FAILED'
        error_code = 'EXECUTION_FAILED'
        error_message = str(exc)[:500]
    except Exception:
        final_status = 'FAILED'
        error_code = 'INTERNAL_ERROR'
        error_message = 'Day-2 executor failed; inspect sanitized job logs and backend configuration'

    duration_ms = max(0, round((time.monotonic() - started) * 1000))
    with session() as db:
        job = db.get(Job, job_id)
        request = db.get(Day2ActionRequest, (job.payload or {}).get('day2_action_request_id')) if job else None
        if job is None or request is None:
            return
        request.status = final_status
        request.finished_at = now()
        request.error_code = error_code
        request.error_message = error_message
        request.result = {
            **result,
            'status': final_status,
            'resource_id': request.resource_id,
            'action': request.action,
            'changes': request.safe_diff or {},
            'warnings': warnings,
            'duration_ms': duration_ms,
        }
        job.status = 'successful' if final_status in {'SUCCEEDED', 'SUCCEEDED_WITH_WARNING'} else ('cancelled' if final_status == 'CANCELLED' else 'failed')
        job.error = error_message
        job.heartbeat_at = now()
        release_resource_lock(db, request.resource_id, request.id)
        db.add(JobLog(job_id=job.id, message='day2.' + final_status.lower() + (': ' + error_message if error_message else '')))
        db.add(Audit(
            user_id=job.created_by, token_id=job.token_id, ip=job.ip, source=job.source,
            action='day2.' + final_status.lower(), resource='day2_actions', resource_id=request.id,
            result=job.status, request_id=job.request_id,
        ))
        event = 'day2.completed' if final_status in {'SUCCEEDED', 'SUCCEEDED_WITH_WARNING'} else ('day2.cancelled' if final_status == 'CANCELLED' else 'day2.failed')
        _emit(db, event, request, {'error_code': error_code, 'warnings': warnings})
        queue_job_webhooks(db, job)
        db.commit()
