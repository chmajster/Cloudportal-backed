import uuid
import json
import hashlib
import os
import socket
import time
import ipaddress
from datetime import timedelta
from types import SimpleNamespace
from pathlib import Path
from sqlalchemy import select, update
from fastapi import HTTPException
from app.api.schemas import AnsibleInput, Inventory
from app.automation.guest_bootstrap import decrypt_guest_bootstrap_secret, provision_guest
from app.blueprint_settings import blueprint_execution_settings
from app.config import settings
from app.database import session
from app.executors.ansible import AnsibleExecutor
from app.executors.base import Cancelled, ExecutionFailed
from app.executors.terraform import OpenTofuExecutor, TerraformExecutor
from app.inventory_sync import state_outputs, sync_deployment_inventory
from app.jobs.lifecycle import has_released_allocations
from app.quotas.service import (account_confirmed_absent, commit_job_reservation,
                                mark_job_reservation_uncertain, prepare_job_reservation,
                                release_job_reservation)
from app.resource_scope.authorization import Scope
from app.models import (Audit, Blueprint, Credential, Deployment, HostnameReservation, IPAllocation, Job, JobLog,
                        ManagedResource, ManagedVM, Token, User, now)
from app.operations.service import queue_job_webhooks, queue_webhook_event, scheduler_user_permissions
from app.providers.registry import provider_for
from app.security.core import effective_permissions
from app.terraform.state import delete_plan, persist_plan, restore_plan, restore_state


class ApprovalPending(Exception):
    pass


class Context:
    def __init__(self, job):
        self.job = job
        self.started = time.monotonic()
        self.last_check = 0
        self.step_deadline = None
        self.blueprint_workflow_completed = False
        self.rollback_destroyed = False
        self.quota_provider_submitted = False
        self.deployment = self.credential = self.ansible = self.ansible_credential = None

    def check(self):
        if time.monotonic() - self.started > settings().execution_timeout:
            raise ExecutionFailed('Execution timeout')
        if self.step_deadline is not None and time.monotonic() > self.step_deadline:
            raise ExecutionFailed('Workflow step timeout')
        if time.monotonic() - self.last_check < 1:
            return
        self.last_check = time.monotonic()
        with session() as db:
            job = db.get(Job, self.job.id)
            if not job or job.cancel_requested:
                raise Cancelled('Cancellation requested')
            validate_authorization(db, job)
            job.heartbeat_at = now()
            db.commit()

    def log(self, message):
        if not message.strip():
            return
        with session() as db:
            db.add(JobLog(job_id=self.job.id, message=message[:8192]))
            db.commit()

    def stage(self, action):
        self.check()
        self.log(action)
        with session() as db:
            db.add(Audit(user_id=self.job.created_by, token_id=self.job.token_id, ip=self.job.ip,
                         source=self.job.source,
                         action=action, resource='jobs', resource_id=self.job.id, request_id=self.job.request_id))
            db.commit()


def blueprint_execution_channel(job):
    authorization_source = (
        ((job.payload or {}).get('_auto_resume') or {}).get('authorization_source')
        or job.source
    )
    return {
        'CloudPortal': 'cloudportal',
        'Cloudportal-backed': 'backend',
        'API': 'api',
        'Scheduler': 'backend',
    }.get(authorization_source, 'api')


def _validate_blueprint_authorization(db, job, user, permissions):
    blueprint_snapshot = (job.payload or {}).get('blueprint') or {}
    if job.operation != 'terraform.apply' or not blueprint_snapshot:
        return
    if 'blueprints.execute' not in permissions:
        raise ExecutionFailed('Blueprint execution permission has been revoked')

    blueprint_id = blueprint_snapshot.get('id')
    if blueprint_id is None:
        return
    blueprint = db.get(Blueprint, int(blueprint_id))
    if blueprint is None or not blueprint.is_active:
        raise ExecutionFailed('Blueprint is no longer active or available')

    role_ids = {role.id for role in user.roles}
    if (
        blueprint.allowed_role_ids or blueprint.allowed_user_ids
    ) and user.id not in blueprint.allowed_user_ids and not (role_ids & set(blueprint.allowed_role_ids)):
        raise ExecutionFailed('Blueprint access has been revoked')

    source = blueprint_execution_channel(job)
    if not (blueprint.visibility or {}).get(source, False):
        raise ExecutionFailed('Blueprint is no longer visible to this execution source')


def validate_authorization(db, job):
    if job.source in {'Scheduler', 'Recovery'} and job.token_id is None:
        permissions = scheduler_user_permissions(db, job.created_by)
        user = db.get(User, job.created_by)
        if permissions is None or user is None:
            raise ExecutionFailed('Scheduled job owner is disabled or locked')
    else:
        token = db.get(Token, job.token_id)
        if not token or token.user_id != job.created_by or not token.user.is_active or token.user.is_locked or (token.user.locked_until and token.user.locked_until > now()):
            raise ExecutionFailed('Job authorization has been revoked')
        if token.kind == 'session':
            # Normal refresh rotates the access token. Authorize the surviving session family,
            # while logout, replay detection and password changes revoke the whole family.
            active_family = db.scalar(select(Token.id).where(
                Token.family == token.family, Token.kind == 'refresh', Token.revoked_at.is_(None),
                Token.expires_at > now()).limit(1))
            if not active_family:
                raise ExecutionFailed('Job session has ended')
        elif token.kind != 'api' or token.revoked_at:
            raise ExecutionFailed('Job authorization has been revoked')
        user = token.user
        permissions = effective_permissions(token.user)
        if token.kind == 'api':
            if token.expires_at and token.expires_at <= now():
                raise ExecutionFailed('Job API token expired')
            permissions &= set(token.scopes)
    # Preserve the original API-token/session-family semantics above, then
    # reauthorize the persisted project rather than any later browser selection.
    from app.resource_scope.authorization import Scope, permissions_for_identity, ensure_execution_ready
    from app.resource_scope.database import bind_scope, reference_visible
    from app.tenancy.authorization import Identity
    scope = Scope(job.tenant_id, job.project_id)
    ceiling = frozenset(token.scopes or []) if job.token_id is not None and token.kind == 'api' else None
    identity = Identity(user.id, job.token_id or 0, frozenset(permissions), ceiling)
    try:
        permissions = set(permissions_for_identity(db, identity, scope, write=True))
        bind_scope(db, scope)
        ensure_execution_ready(db, identity, scope)
        target = None
        if job.deployment_id:
            target = db.get(Deployment, job.deployment_id)
            if target is None or (target.tenant_id, target.project_id) != (scope.tenant_id, scope.project_id):
                raise ExecutionFailed('Job and deployment scope do not match')
            if not reference_visible(db, 'provider', target.provider_id, scope):
                raise ExecutionFailed('Provider access has been revoked')
            if not reference_visible(db, 'credential', target.credentials_id, scope):
                raise ExecutionFailed('Credential access has been revoked')
        ansible = (job.payload or {}).get('ansible') or {}
        if ansible and not reference_visible(db, 'credential', ansible.get('credentials_id'), scope):
            raise ExecutionFailed('Ansible credential access has been revoked')
        if job.operation in {'terraform.plan', 'terraform.apply'}:
            blueprint = (job.payload or {}).get('blueprint') or {}
            guest_credential_id = blueprint.get('guest_credential_id')
            if not guest_credential_id and target is not None:
                guest_credential_id = (((target.workflow or {}).get('blueprint') or {}).get('guest_credential_id'))
            if guest_credential_id and not reference_visible(db, 'credential', guest_credential_id, scope):
                raise ExecutionFailed('Guest VM credential access has been revoked')
    except HTTPException:
        raise ExecutionFailed('Job project authorization has been revoked') from None
    needed = {'jobs.execute', 'ansible.execute' if job.operation == 'ansible.execute' else 'terraform.execute'}
    if job.operation == 'terraform.apply':
        needed.add('deployments.create')
        blueprint = job.payload.get('blueprint') or {}
        if blueprint.get('recovery_policy') == 'destroy_on_failure':
            needed.add('deployments.destroy')
        workflow_types = {str(step.get('type')) for step in (blueprint.get('steps') or [])}
        if 'create_snapshot' in workflow_types:
            needed.add('snapshots.create')
        if 'release_ip' in workflow_types:
            needed.add('ipam.release')
        if 'terraform_destroy' in workflow_types:
            needed.add('deployments.destroy')
    if job.operation == 'terraform.destroy':
        needed.add('deployments.destroy')
    if job.operation == 'terraform.import':
        needed.add('deployments.adopt')
    if job.payload.get('ansible'):
        needed.add('ansible.execute')
    if not needed <= permissions:
        raise ExecutionFailed('Job permissions have been revoked')
    _validate_blueprint_authorization(db, job, user, permissions)


def ensure_runtime_credential(credential):
    if credential is None:
        raise ExecutionFailed('Required credential is missing')
    if credential.expires_at is not None and credential.expires_at <= now():
        raise ExecutionFailed('Infrastructure credential expired before job execution')
    return credential


def terraform_outputs(workspace):
    try:
        state = json.loads((workspace / 'terraform.tfstate').read_text())
    except (OSError, ValueError, TypeError):
        raise ExecutionFailed('Terraform state could not be read for inventory synchronization') from None
    try:
        return state_outputs(state)
    except RuntimeError as exc:
        raise ExecutionFailed(str(exc)) from None


def vm_id_from_state(workspace):
    vm_id = terraform_outputs(workspace).get('vm_id', {}).get('value')
    if not vm_id:
        raise ExecutionFailed('VM ID missing from Terraform state')
    return int(vm_id)


def register_managed_inventory(context, workspace):
    outputs = terraform_outputs(workspace)
    with session() as db:
        deployment = db.get(Deployment, context.deployment.id)
        if deployment is None:
            raise ExecutionFailed('Deployment disappeared before inventory synchronization')
        try:
            result = sync_deployment_inventory(db, deployment, outputs)
        except RuntimeError as exc:
            raise ExecutionFailed(str(exc)) from None
        db.commit()
        return {
            'external_id': result['external_id'],
            'vm_id': result['vm_id'],
            'node': result['node'],
        }



def register_adopted_resource(context):
    values = (context.job.payload or {}).get('import_values') or {}
    node = values.get('node')
    vm_id = values.get('vm_id')
    if not node or not vm_id:
        raise ExecutionFailed('Adoption identity is missing')
    with session() as db:
        deployment = db.get(Deployment, context.deployment.id)
        managed_vm = db.scalar(select(ManagedVM).where(
            ManagedVM.provider_id == deployment.provider_id,
            ManagedVM.vm_id == int(vm_id),
        ))
        if managed_vm is None:
            raise ExecutionFailed('Imported VM is missing from managed inventory')
        if (managed_vm.tenant_id, managed_vm.project_id) != (deployment.tenant_id, deployment.project_id):
            raise ExecutionFailed('Imported VM belongs to another project')
        if managed_vm.deployment_id not in {None, deployment.id}:
            raise ExecutionFailed('Imported VM is already linked to another deployment')
        managed_vm.deployment_id = deployment.id
        managed_vm.node = str(node)
        managed_vm.name = deployment.name
        managed_vm.management_mode = 'terraform'
        managed_vm.lifecycle_status = 'active'
        managed_vm.destroyed_at = None

        resource = db.scalar(select(ManagedResource).where(
            ManagedResource.deployment_id == deployment.id
        ))
        if resource is None:
            resource = ManagedResource(
                deployment_id=deployment.id,
                provider_id=deployment.provider_id,
                provider=deployment.provider,
                resource_type='vm',
                external_id=str(vm_id),
                name=deployment.name,
                primary_ip=None,
                lifecycle_status='active',
                metadata_json={'adopted': True, 'node': str(node)},
                created_by=deployment.created_by,
            )
            db.add(resource)
        else:
            resource.external_id = str(vm_id)
            resource.name = deployment.name
            resource.lifecycle_status = 'active'
            resource.metadata_json = {'adopted': True, 'node': str(node)}
            resource.destroyed_at = None
        db.commit()

def _workflow_vm_identity(context, workspace):
    node = str((context.deployment.variables or {}).get('node') or '')
    if not node:
        raise ExecutionFailed('Proxmox node missing from deployment variables')
    return node, vm_id_from_state(workspace), provider_for(context.credential)


def _safe_wait_error(error):
    if isinstance(error, HTTPException):
        detail = error.detail
        if isinstance(detail, str):
            return detail[:240]
        return f'HTTP {error.status_code}'
    return error.__class__.__name__


def _log_wait_error(context, stage, error, previous):
    summary = _safe_wait_error(error)
    if summary != previous:
        context.log(f'{stage}.retry: {summary}')
    return summary


def wait_for_vm(context, workspace, timeout=600):
    node, vm_id, provider = _workflow_vm_identity(context, workspace)
    context.stage('workflow.wait_for_vm')
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        context.check()
        try:
            status = provider.vm_status(node, vm_id)
            if str((status or {}).get('status') or '').lower() == 'running':
                return True
        except Exception as error:
            last_error = _log_wait_error(context, 'workflow.wait_for_vm', error, last_error)
        time.sleep(2)
    suffix = f'; last provider error: {last_error}' if last_error else ''
    raise ExecutionFailed('Timed out waiting for VM running state' + suffix)


def wait_for_agent(context, workspace, timeout=600):
    node, vm_id, provider = _workflow_vm_identity(context, workspace)
    context.stage('workflow.wait_for_agent')
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        context.check()
        try:
            if provider.guest_agent_ready(node, vm_id):
                return True
        except Exception as error:
            last_error = _log_wait_error(context, 'workflow.wait_for_agent', error, last_error)
        time.sleep(2)
    suffix = f'; last provider error: {last_error}' if last_error else ''
    raise ExecutionFailed('Timed out waiting for QEMU Guest Agent' + suffix)


def update_inventory_primary_ip(context, address):
    if not address:
        return
    with session() as db:
        resource = db.scalar(select(ManagedResource).where(
            ManagedResource.deployment_id == context.deployment.id
        ).with_for_update())
        if resource is not None:
            resource.primary_ip = str(address)
            db.commit()


def configured_deployment_ip(context):
    raw = str((context.deployment.variables or {}).get('ipv4_address') or '').strip()
    if not raw or raw.lower() == 'dhcp':
        return None
    try:
        return str(ipaddress.ip_interface(raw).ip)
    except ValueError:
        return None


def wait_for_ip(context, workspace, timeout=600):
    configured = configured_deployment_ip(context)
    if configured:
        wait_for_vm(context, workspace, timeout=timeout)
        context.stage('workflow.wait_for_ip')
        context.log(f'workflow.wait_for_ip.configured: {configured}')
        update_inventory_primary_ip(context, configured)
        return [configured]

    node, vm_id, provider = _workflow_vm_identity(context, workspace)
    context.stage('workflow.wait_for_ip')
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        context.check()
        try:
            addresses = provider.guest_addresses(node, vm_id)
            ipv4 = [address for address in addresses if ':' not in address]
            selected = (ipv4 or addresses)[:1]
            if selected:
                update_inventory_primary_ip(context, selected[0])
                return selected
        except Exception as error:
            last_error = _log_wait_error(context, 'workflow.wait_for_ip', error, last_error)
        time.sleep(2)
    suffix = f'; last provider error: {last_error}' if last_error else ''
    raise ExecutionFailed(
        'Timed out waiting for VM IP address. DHCP discovery requires a working QEMU Guest Agent'
        + suffix
    )


def _provider_retry_delay(attempt):
    cfg = settings()
    return min(cfg.provider_retry_max_seconds, cfg.provider_retry_base_seconds * (2 ** max(0, attempt - 1)))


def defer_for_provider(job_id, reason='unreachable'):
    with session() as db:
        current = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if current is None or current.cancel_requested:
            return
        payload = dict(current.payload or {})
        wait = dict(payload.get('_provider_wait') or {})
        attempt = int(wait.get('attempts') or 0) + 1
        if attempt > settings().provider_retry_max_attempts:
            payload.pop('_provider_wait', None)
            current.payload = payload
            if current.deployment_id:
                deployment = db.get(Deployment, current.deployment_id)
                if deployment is not None and deployment.status == 'waiting_provider':
                    deployment.status = 'running'
            db.commit()
            raise ExecutionFailed(
                f'Provider remained unavailable after {settings().provider_retry_max_attempts} retry attempts'
            )
        delay = _provider_retry_delay(attempt)
        next_attempt = now() + timedelta(seconds=delay)
        payload['_provider_wait'] = {
            'attempts': attempt,
            'reason': reason,
            'next_attempt_at': next_attempt.isoformat(),
        }
        current.payload = payload
        current.status = 'queued'
        current.error = None
        current.heartbeat_at = None
        if current.deployment_id:
            deployment = db.get(Deployment, current.deployment_id)
            if deployment is not None:
                deployment.status = 'waiting_provider'
                deployment.active_job_id = current.id
        db.add(JobLog(
            job_id=current.id,
            message=f'provider.waiting: Proxmox niedostępny; ponowna próba za {delay} s',
        ))
        db.add(Audit(
            user_id=current.created_by,
            token_id=current.token_id,
            ip=current.ip,
            source=current.source,
            action='provider.waiting',
            resource='jobs',
            resource_id=current.id,
            result='queued',
            request_id=current.request_id,
        ))
        db.commit()


def clear_provider_wait(job_id):
    with session() as db:
        current = db.get(Job, job_id)
        if current is None:
            return
        payload = dict(current.payload or {})
        if '_provider_wait' in payload:
            payload.pop('_provider_wait', None)
            current.payload = payload
        if current.deployment_id:
            deployment = db.get(Deployment, current.deployment_id)
            if deployment is not None and deployment.status == 'waiting_provider':
                deployment.status = 'running'
        db.commit()


def restore_recovery_workspace(context):
    workspace = settings().data_dir / 'workspaces' / context.deployment.workspace
    workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        restored = restore_state(context.deployment.id, workspace)
    except RuntimeError as exc:
        raise ExecutionFailed(str(exc)) from None
    if not restored:
        raise ExecutionFailed(
            'Automatic resume refused: persisted Terraform state is unavailable'
        )
    context.stage('recovery.terraform_state.restored')
    return workspace


def persist_workflow_runtime(context, runtime):
    """Checkpoint enough workflow state to resume without repeating Terraform apply."""
    job_id = getattr(context.job, 'id', None)
    if not job_id:
        return
    with session() as db:
        current = db.get(Job, job_id)
        if current is None:
            return
        payload = dict(current.payload or {})
        saved = dict(payload.get('_workflow_runtime') or {})
        saved.update({
            'completed_steps': sorted(
                key for key, value in runtime['step_states'].items()
                if value == 'completed'
            ),
            'plan_ready': bool(runtime.get('plan_ready')),
            'plan_sha256': runtime.get('plan_sha256'),
            'provider_applied': bool(runtime.get('applied')),
            'inventory_synced': bool(runtime.get('inventory_synced')),
            'guest_bootstrapped': bool(runtime.get('guest_bootstrapped')),
            'ansible_ran': bool(runtime.get('ansible_ran')),
        })
        payload['_workflow_runtime'] = saved
        current.payload = payload
        db.commit()
        context.job.payload = dict(payload)


BLUEPRINT_DECLARATIVE_STEPS = {
    'create_vm', 'clone_vm', 'configure_vm', 'cloud_init', 'start_vm',
    'set_hostname', 'set_tags',
}
BLUEPRINT_PRECOMPILED_STEPS = {'generate_hostname', 'allocate_ip'}
BLUEPRINT_POST_APPLY_STEPS = {
    'wait_for_vm', 'wait_for_agent', 'wait_for_ip', 'wait_for_ssh',
    'run_ansible_playbook', 'create_snapshot', 'health_check',
}
BLUEPRINT_SUPPORTED_STEPS = (
    BLUEPRINT_DECLARATIVE_STEPS
    | BLUEPRINT_PRECOMPILED_STEPS
    | BLUEPRINT_POST_APPLY_STEPS
    | {'release_ip', 'terraform_plan', 'terraform_apply', 'terraform_destroy', 'condition', 'approval', 'delay', 'notification'}
)


def workflow_step_timeout(step_type, configured=600):
    timeout = int(configured or 600)
    if step_type in {'terraform_plan', 'terraform_apply', 'terraform_destroy'}:
        return max(timeout, settings().execution_timeout)
    return timeout


def blueprint_workflow_order(steps):
    rows = [dict(step or {}) for step in (steps or [])]
    ids = [row.get('id') for row in rows]
    if not rows:
        return []
    if any(not value for value in ids) or len(ids) != len(set(ids)):
        raise ExecutionFailed('Blueprint workflow contains missing or duplicate step IDs')
    by_id = {row['id']: row for row in rows}
    known = set(by_id)
    for row in rows:
        dependencies = list(row.get('depends_on') or [])
        if row['id'] in dependencies or set(dependencies) - known:
            raise ExecutionFailed('Blueprint workflow contains an invalid dependency')
    pending = list(rows)
    completed = set()
    ordered = []
    while pending:
        ready = [row for row in pending if set(row.get('depends_on') or []) <= completed]
        if not ready:
            raise ExecutionFailed('Blueprint workflow contains a dependency cycle')
        for row in ready:
            ordered.append(row)
            completed.add(row['id'])
            pending.remove(row)
    return ordered


def blueprint_runtime_facts(context):
    deployment = context.deployment
    blueprint = (context.job.payload or {}).get('blueprint') or {}
    variables = deployment.variables or {}
    blueprint_variables = blueprint.get('variables') or {}
    tags = set(str(value).lower() for value in (variables.get('tags') or []))
    return {
        'provider': deployment.provider,
        'executor': deployment.executor,
        'has_ansible': bool(context.ansible),
        'hostname': deployment.name,
        'environment': next(
            (tag.removeprefix('env-') for tag in tags if tag.startswith('env-')),
            blueprint_variables.get('environment'),
        ),
        'apmid': next(
            (tag.removeprefix('apmid-').upper() for tag in tags if tag.startswith('apmid-')),
            blueprint_variables.get('apmid'),
        ),
    }


def blueprint_conditions_match(step, context):
    conditions = dict(step.get('conditions') or {})
    if not conditions:
        return True
    step_type = step.get('type')
    ignored = {'seconds', 'message'} if step_type in {'delay', 'notification'} else set()
    facts = blueprint_runtime_facts(context)
    for key, expected in conditions.items():
        if key in ignored:
            continue
        if key not in facts:
            raise ExecutionFailed(f'Unsupported Blueprint workflow condition: {key}')
        actual = facts[key]
        if isinstance(expected, list):
            if actual not in expected:
                return False
        elif isinstance(expected, bool):
            if bool(actual) != expected:
                return False
        elif str(actual).lower() != str(expected).lower():
            return False
    return True


def wait_for_ssh(context, workspace, timeout):
    addresses = wait_for_ip(context, workspace, timeout=timeout)
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        context.check()
        for address in addresses:
            try:
                with socket.create_connection((address, 22), timeout=2):
                    return address
            except OSError as exc:
                last_error = exc
        time.sleep(2)
    raise ExecutionFailed('Timed out waiting for SSH' + (f': {last_error}' if last_error else ''))


def wait_for_tcp_addresses(context, addresses, port, timeout, label):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        context.check()
        for address in addresses:
            try:
                with socket.create_connection((address, port), timeout=2):
                    return address
            except OSError as exc:
                last_error = exc
        time.sleep(2)
    raise ExecutionFailed(
        f'Timed out waiting for {label}'
        + (f': {last_error}' if last_error else '')
    )


def wait_for_ansible_transport(context, workspace, timeout=600, addresses=None):
    addresses = list(addresses or wait_for_ip(context, workspace, timeout=timeout))
    credential_type = getattr(context.ansible_credential, 'type', None)
    if credential_type == 'ssh':
        address = wait_for_tcp_addresses(context, addresses, 22, timeout, 'SSH')
        return [address]
    if credential_type == 'winrm':
        address = wait_for_tcp_addresses(context, addresses, 5986, timeout, 'WinRM HTTPS')
        return [address]
    raise ExecutionFailed('Unsupported Ansible transport credential')


def release_blueprint_ip(context):
    with session() as db:
        active_vm = db.scalar(select(ManagedVM.id).where(
            ManagedVM.deployment_id == context.deployment.id,
            ManagedVM.lifecycle_status == 'active',
        ).limit(1))
        active_resource = db.scalar(select(ManagedResource.id).where(
            ManagedResource.deployment_id == context.deployment.id,
            ManagedResource.lifecycle_status == 'active',
        ).limit(1))
        if active_vm or active_resource:
            raise ExecutionFailed('Refusing to release IP while deployment still has an active VM/resource')
        allocation = db.scalar(select(IPAllocation).where(
            IPAllocation.resource_id == context.deployment.id,
            IPAllocation.status != 'released',
        ).with_for_update())
        if allocation is None:
            context.log('workflow.release_ip: no active allocation')
            return
        allocation.status = 'released'
        allocation.released_at = now()
        db.commit()


def wait_for_proxmox_task(context, provider, node, upid, timeout=600):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        context.check()
        status = provider.task_status(node, upid) or {}
        if str(status.get('status') or '').lower() == 'stopped':
            exit_status = str(status.get('exitstatus') or '')
            if exit_status == 'OK':
                return status
            raise ExecutionFailed(f'Proxmox task failed: {exit_status or "unknown exit status"}')
        time.sleep(2)
    raise ExecutionFailed('Timed out waiting for Proxmox task completion')


def health_check_vm(context, workspace):
    if context.deployment.provider != 'proxmox':
        with session() as db:
            resource = db.scalar(select(ManagedResource).where(
                ManagedResource.deployment_id == context.deployment.id,
                ManagedResource.lifecycle_status == 'active',
            ))
            if resource is None or not resource.external_id:
                raise ExecutionFailed(
                    'Blueprint health_check failed: managed resource was not synchronized'
                )
        return True

    node, vm_id, provider = _workflow_vm_identity(context, workspace)
    status = provider.vm_status(node, vm_id) or {}
    if str(status.get('status') or '').lower() != 'running':
        raise ExecutionFailed(
            'Blueprint health_check failed: VM is not running '
            f"(status={status.get('status') or 'unknown'})"
        )
    blueprint_snapshot = ((context.deployment.workflow or {}).get('blueprint') or {})
    qemu_agent_requested = bool(
        (context.deployment.variables or {}).get('install_qemu_guest_agent')
        or blueprint_snapshot.get('bootstrap_install_qemu_guest_agent')
    )
    if qemu_agent_requested:
        try:
            if not provider.guest_agent_ready(node, vm_id):
                raise ExecutionFailed('Blueprint health_check failed: QEMU Guest Agent is not ready')
        except HTTPException as exc:
            raise ExecutionFailed(
                'Blueprint health_check failed: QEMU Guest Agent check failed'
            ) from exc
    return True


def create_blueprint_snapshot(context, workspace, step):
    if context.deployment.provider != 'proxmox':
        raise ExecutionFailed('create_snapshot is supported only for Proxmox deployments')
    vm_id = vm_id_from_state(workspace)
    node = str((context.deployment.variables or {}).get('node') or '')
    if not node:
        raise ExecutionFailed('Proxmox node missing from deployment variables')
    snapname = ('bp-' + context.job.id[:8] + '-' + str(step.get('id') or 'snapshot'))[:40]
    provider = provider_for(context.credential)
    upid = provider.create_snapshot(
        node,
        vm_id,
        snapname,
        'Cloudportal Blueprint workflow snapshot',
        False,
    )
    if not isinstance(upid, str) or not upid:
        raise ExecutionFailed('Proxmox did not return a snapshot task ID')
    wait_for_proxmox_task(context, provider, node, upid, timeout=int(step.get('timeout') or 600))
    context.log(f'workflow.snapshot.created: {snapname} task={upid}')


def run_blueprint_workflow(context, executor):
    blueprint = (context.job.payload or {}).get('blueprint') or {}
    steps = blueprint_workflow_order(blueprint.get('steps') or [])
    if not steps:
        return None

    unsupported = sorted({str(step.get('type')) for step in steps} - BLUEPRINT_SUPPORTED_STEPS)
    if unsupported:
        raise ExecutionFailed('Unsupported Blueprint workflow steps: ' + ', '.join(unsupported))

    saved_runtime = dict((context.job.payload or {}).get('_workflow_runtime') or {})
    completed_steps = {
        str(value) for value in (saved_runtime.get('completed_steps') or [])
    }
    saved_plan_ready = bool(saved_runtime.get('plan_ready'))
    provider_applied = bool(saved_runtime.get('provider_applied'))
    if provider_applied:
        saved_workspace = restore_recovery_workspace(context)
    else:
        saved_workspace = (
            settings().data_dir / 'workspaces' / context.deployment.workspace
            if saved_plan_ready else None
        )
    explicit_ansible_completed = any(
        str(step.get('type')) == 'run_ansible_playbook'
        and str(step.get('id')) in completed_steps
        for step in steps
    )
    runtime = {
        'workspace': saved_workspace,
        'inventory_synced': bool(saved_runtime.get('inventory_synced')),
        'guest_bootstrapped': bool(saved_runtime.get('guest_bootstrapped')),
        'addresses': None,
        'applied': provider_applied,
        'ansible_ran': bool(
            saved_runtime.get('ansible_ran')
            or explicit_ansible_completed
        ),
        'prepared': [],
        'step_states': {step_id: 'completed' for step_id in completed_steps},
        'plan_ready': saved_plan_ready,
        'plan_sha256': saved_runtime.get('plan_sha256'),
    }

    by_id = {str(step.get('id')): step for step in steps}
    rollback_targets = {str(step.get('rollback')) for step in steps if step.get('rollback')}
    explicit_apply_ids = {
        str(step.get('id')) for step in steps if str(step.get('type')) == 'terraform_apply'
    }
    legacy_provisioning = any(
        str(step.get('type')) in BLUEPRINT_DECLARATIVE_STEPS for step in steps
    )

    def mark_destroyed_after_rollback():
        released_at = now()
        with session() as db:
            deployment = db.get(Deployment, context.deployment.id)
            if deployment is not None:
                deployment.destroyed_at = released_at
            db.execute(update(HostnameReservation).where(
                HostnameReservation.resource_id == context.deployment.id,
                HostnameReservation.status != 'released',
            ).values(status='released', released_at=released_at))
            db.execute(update(IPAllocation).where(
                IPAllocation.resource_id == context.deployment.id,
                IPAllocation.status != 'released',
            ).values(status='released', released_at=released_at))
            db.execute(update(ManagedVM).where(
                ManagedVM.deployment_id == context.deployment.id,
            ).values(lifecycle_status='destroyed', destroyed_at=released_at))
            db.execute(update(ManagedResource).where(
                ManagedResource.deployment_id == context.deployment.id,
            ).values(lifecycle_status='destroyed', destroyed_at=released_at))
            db.commit()

    def execute_rollback(target_id, failed_step_id):
        rollback_step = by_id.get(str(target_id))
        if rollback_step is None:
            raise ExecutionFailed(f'Rollback target {target_id} does not exist')
        rollback_type = str(rollback_step.get('type'))
        rollback_timeout = workflow_step_timeout(
            rollback_type, rollback_step.get('timeout') or 600
        )
        previous_deadline = context.step_deadline
        context.step_deadline = time.monotonic() + rollback_timeout
        try:
            context.stage(f'workflow.rollback.start:{failed_step_id}:{target_id}:{rollback_type}')
            if rollback_type == 'terraform_destroy':
                executor.execute('terraform.destroy', context)
                mark_destroyed_after_rollback()
                context.rollback_destroyed = True
            elif rollback_type == 'notification':
                message = str((rollback_step.get('conditions') or {}).get('message') or target_id)
                context.log('workflow.rollback.notification: ' + message[:1000])
            elif rollback_type == 'delay':
                seconds = float((rollback_step.get('conditions') or {}).get('seconds', 1))
                if seconds < 0 or seconds > rollback_timeout:
                    raise ExecutionFailed('Rollback delay seconds must be between 0 and rollback timeout')
                deadline = time.monotonic() + seconds
                while time.monotonic() < deadline:
                    context.check()
                    time.sleep(min(1, max(0, deadline - time.monotonic())))
            else:
                raise ExecutionFailed(f'Unsupported rollback step type: {rollback_type}')
            context.stage(f'workflow.rollback.completed:{failed_step_id}:{target_id}:{rollback_type}')
        finally:
            context.step_deadline = previous_deadline

    def verify_saved_plan():
        if not runtime['plan_ready']:
            return
        workspace = runtime['workspace']
        if workspace is None:
            raise ExecutionFailed('Approved Terraform plan workspace is unavailable')
        expected = runtime.get('plan_sha256')
        try:
            restored = restore_plan(context.deployment.id, workspace, expected_sha256=expected)
        except RuntimeError as exc:
            raise ExecutionFailed(str(exc)) from None
        if not restored:
            raise ExecutionFailed('Approved Terraform plan is unavailable; generate and approve a new plan')
        plan_path = workspace / 'execution.tfplan'
        actual = hashlib.sha256(plan_path.read_bytes()).hexdigest()
        if not expected or actual != expected:
            raise ExecutionFailed('Approved Terraform plan checksum mismatch; generate and approve a new plan')

    def ensure_guest_bootstrap():
        blueprint_snapshot = (context.job.payload or {}).get('blueprint') or {}
        encrypted = blueprint_snapshot.get('guest_bootstrap_secret')
        credential_id = blueprint_snapshot.get('guest_credential_id')
        if not encrypted or not credential_id or runtime.get('guest_bootstrapped'):
            return
        workspace = runtime.get('workspace')
        if workspace is None:
            raise ExecutionFailed('Guest bootstrap requires completed Terraform apply')
        context.stage('workflow.guest_bootstrap.wait_for_ssh')
        address = wait_for_ssh(context, workspace, timeout=600)
        bootstrap = decrypt_guest_bootstrap_secret(encrypted)
        context.stage('workflow.guest_bootstrap.configure')
        with session() as bootstrap_db:
            final_guest = provision_guest(
                context,
                address,
                bootstrap,
                bootstrap_db,
                int(credential_id),
                bool(blueprint_snapshot.get('bootstrap_install_qemu_guest_agent')),
            )
            deployment_row = bootstrap_db.get(Deployment, context.deployment.id)
            if deployment_row is None:
                raise ExecutionFailed('Deployment disappeared during guest bootstrap finalization')
            deployment_variables = dict(deployment_row.variables or {})
            deployment_variables.update(final_guest)
            deployment_row.variables = deployment_variables
            deployment_workflow = dict(deployment_row.workflow or {})
            deployment_blueprint = dict(deployment_workflow.get('blueprint') or {})
            deployment_blueprint['guest_bootstrap_enabled'] = False
            deployment_workflow['blueprint'] = deployment_blueprint
            deployment_row.workflow = deployment_workflow

            current_job = bootstrap_db.get(Job, context.job.id)
            if current_job is None:
                raise ExecutionFailed('Job disappeared during guest bootstrap finalization')
            payload = dict(current_job.payload or {})
            job_blueprint = dict(payload.get('blueprint') or {})
            job_blueprint.pop('guest_bootstrap_secret', None)
            job_blueprint['guest_bootstrap_enabled'] = False
            payload['blueprint'] = job_blueprint
            current_job.payload = payload
            bootstrap_db.commit()

            context.deployment.variables = deployment_variables
            context.deployment.workflow = deployment_workflow
            context.job.payload = payload
        runtime['addresses'] = [address]
        runtime['guest_bootstrapped'] = True
        context.stage('workflow.guest_bootstrap.completed')
        persist_workflow_runtime(context, runtime)

    def apply_and_sync(reason='explicit'):
        context.stage('workflow.terraform_apply')
        if reason != 'explicit':
            context.log(f'workflow.compatibility: implicit terraform_apply before {reason}')
        verify_saved_plan()
        context.apply_saved_terraform_plan = bool(runtime['plan_ready'])
        try:
            workspace = executor.execute('terraform.apply', context)
        finally:
            context.apply_saved_terraform_plan = False
        runtime['plan_ready'] = False
        runtime['plan_sha256'] = None
        delete_plan(context.deployment.id)
        runtime['workspace'] = workspace
        runtime['applied'] = True
        context.stage('inventory.synchronizing')
        inventory = register_managed_inventory(context, workspace)
        with session() as quota_db:
            quota_job = quota_db.get(Job, context.job.id)
            if quota_job is not None:
                commit_job_reservation(quota_db, quota_job)
                quota_db.commit()
        runtime['inventory_synced'] = True
        persist_workflow_runtime(context, runtime)
        ensure_guest_bootstrap()
        if inventory['vm_id'] is not None:
            context.log(f"inventory.vm.registered: {inventory['node']} / VMID {inventory['vm_id']}")
        else:
            context.log(f"inventory.resource.registered: {inventory['external_id']}")
        for prepared in runtime['prepared']:
            context.log(f"workflow.step.materialized: {prepared}")
        runtime['prepared'].clear()
        persist_workflow_runtime(context, runtime)
        return workspace

    def workspace_for(step_type):
        if runtime['workspace'] is not None and runtime['applied']:
            return runtime['workspace']
        if explicit_apply_ids:
            raise ExecutionFailed(
                f'Workflow step {step_type} requires a completed terraform_apply dependency'
            )
        if legacy_provisioning:
            return apply_and_sync(step_type)
        raise ExecutionFailed(f'Workflow step {step_type} requires terraform_apply')

    def pause_for_approval(step_id):
        workspace = runtime.get('workspace')
        plan_sha256 = None
        if runtime['plan_ready']:
            plan_path = workspace / 'execution.tfplan' if workspace else None
            if not plan_path or not plan_path.exists():
                raise ExecutionFailed('Terraform plan disappeared before approval')
            plan_sha256 = hashlib.sha256(plan_path.read_bytes()).hexdigest()

        with session() as db:
            current = db.scalar(select(Job).where(Job.id == context.job.id).with_for_update())
            if current is None:
                raise ExecutionFailed('Job disappeared before approval pause')
            config = blueprint_execution_settings(db)
            expires_at = now() + timedelta(hours=config['approval_timeout_hours'])
            payload = dict(current.payload or {})
            payload['_approval'] = {
                'status': 'pending',
                'step_id': step_id,
                'requested_at': now().isoformat(),
                'expires_at': expires_at.isoformat(),
            }
            payload['_workflow_runtime'] = {
                'completed_steps': sorted(
                    key for key, value in runtime['step_states'].items()
                    if value == 'completed'
                ),
                'plan_ready': bool(runtime['plan_ready']),
                'plan_sha256': plan_sha256,
                'provider_applied': bool(runtime['applied']),
                'inventory_synced': bool(runtime['inventory_synced']),
                'guest_bootstrapped': bool(runtime['guest_bootstrapped']),
                'ansible_ran': bool(runtime['ansible_ran']),
                'approval_step': step_id,
            }
            current.payload = payload
            current.status = 'waiting_approval'
            current.error = None
            current.heartbeat_at = None
            if current.deployment_id:
                deployment = db.get(Deployment, current.deployment_id)
                if deployment is not None:
                    deployment.status = 'waiting_approval'
                    deployment.active_job_id = current.id
            db.add(JobLog(
                job_id=current.id,
                message=f'workflow.approval.pending: step={step_id}; expires_at={expires_at.isoformat()}',
            ))
            db.add(Audit(
                user_id=current.created_by,
                token_id=current.token_id,
                ip=current.ip,
                source=current.source,
                action='workflow.approval.pending',
                resource='jobs',
                resource_id=current.id,
                result='waiting_approval',
                request_id=current.request_id,
            ))
            db.commit()
        raise ApprovalPending(step_id)

    for step in steps:
        step_id = str(step.get('id'))
        step_type = str(step.get('type'))

        if runtime['step_states'].get(step_id) == 'completed':
            context.log(f'workflow.step.resumed: {step_id}:{step_type}: already completed')
            continue

        if step_id in rollback_targets:
            runtime['step_states'][step_id] = 'rollback_only'
            context.log(f'workflow.step.rollback_only: {step_id}:{step_type}')
            continue

        dependencies = [str(value) for value in (step.get('depends_on') or [])]
        blocked_dependencies = [
            dependency for dependency in dependencies
            if runtime['step_states'].get(dependency) != 'completed'
        ]
        if blocked_dependencies:
            runtime['step_states'][step_id] = 'blocked'
            context.log(
                f'workflow.step.blocked: {step_id}:{step_type}: '
                'dependency not executed: ' + ', '.join(blocked_dependencies)
            )
            continue

        retry = int(step.get('retry') or 0)
        timeout = workflow_step_timeout(step_type, step.get('timeout') or 600)

        if not blueprint_conditions_match(step, context):
            runtime['step_states'][step_id] = 'skipped'
            context.log(f'workflow.step.skipped: {step_id}:{step_type}: condition=false')
            continue

        attempts = retry + 1
        for attempt in range(1, attempts + 1):
            previous_deadline = context.step_deadline
            context.step_deadline = time.monotonic() + timeout
            try:
                context.stage(f'workflow.step.start:{step_id}:{step_type}')
                if step_type in BLUEPRINT_PRECOMPILED_STEPS:
                    context.log(
                        f'workflow.step.legacy_marker: {step_id}:{step_type}; '
                        'value was resolved before the job was queued'
                    )
                elif step_type in BLUEPRINT_DECLARATIVE_STEPS:
                    if runtime['applied']:
                        raise ExecutionFailed('Declarative VM step cannot run after terraform_apply')
                    runtime['prepared'].append(step_id + ':' + step_type)
                    context.log(
                        f'workflow.step.legacy_marker: {step_id}:{step_type}; '
                        'desired state is owned by terraform_apply'
                    )
                elif step_type == 'terraform_plan':
                    context.keep_terraform_plan = True
                    try:
                        runtime['workspace'] = executor.execute('terraform.plan', context)
                    finally:
                        context.keep_terraform_plan = False
                    runtime['plan_ready'] = True
                    try:
                        runtime['plan_sha256'] = persist_plan(
                            context.deployment.id,
                            runtime['workspace'],
                        )
                    except RuntimeError as exc:
                        raise ExecutionFailed(str(exc)) from None
                elif step_type == 'terraform_apply':
                    if runtime['applied']:
                        context.log(
                            f'workflow.step.resumed: {step_id}:terraform_apply: '
                            'provider apply checkpoint already completed'
                        )
                    else:
                        apply_and_sync()
                elif step_type == 'terraform_destroy':
                    raise ExecutionFailed('terraform_destroy is rollback-only')
                elif step_type == 'wait_for_vm':
                    wait_for_vm(context, workspace_for(step_type), timeout=timeout)
                elif step_type == 'wait_for_agent':
                    wait_for_agent(context, workspace_for(step_type), timeout=timeout)
                elif step_type == 'wait_for_ip':
                    runtime['addresses'] = wait_for_ip(
                        context, workspace_for(step_type), timeout=timeout
                    )
                elif step_type == 'wait_for_ssh':
                    address = wait_for_ssh(context, workspace_for(step_type), timeout)
                    runtime['addresses'] = [address]
                elif step_type == 'run_ansible_playbook':
                    if not context.ansible:
                        raise ExecutionFailed(
                            'Workflow requests Ansible but deployment has no Ansible configuration'
                        )
                    runtime['addresses'] = wait_for_ansible_transport(
                        context,
                        workspace_for(step_type),
                        timeout=timeout,
                        addresses=runtime['addresses'],
                    )
                    context.ansible.inventory = Inventory(hosts=runtime['addresses'])
                    AnsibleExecutor().execute('ansible.execute', context)
                    runtime['ansible_ran'] = True
                elif step_type == 'create_snapshot':
                    create_blueprint_snapshot(context, workspace_for(step_type), step)
                elif step_type == 'release_ip':
                    release_blueprint_ip(context)
                elif step_type == 'health_check':
                    health_check_vm(context, workspace_for(step_type))
                elif step_type == 'condition':
                    context.log(f'workflow.condition.passed: {step_id}')
                elif step_type == 'approval':
                    if not blueprint.get('requires_approval'):
                        raise ExecutionFailed(
                            'Workflow approval step requires Blueprint requires_approval=true'
                        )
                    approval = (context.job.payload or {}).get('_approval') or {}
                    if approval.get('status') != 'approved':
                        pause_for_approval(step_id)
                    approved_step = approval.get('step_id')
                    if approved_step and approved_step != step_id:
                        raise ExecutionFailed('Blueprint approval belongs to a different workflow step')
                    context.log(
                        f"workflow.approval.satisfied: {step_id} "
                        f"approved_by={approval.get('approved_by')}"
                    )
                elif step_type == 'delay':
                    seconds = float((step.get('conditions') or {}).get('seconds', 1))
                    if seconds < 0 or seconds > timeout:
                        raise ExecutionFailed(
                            'Workflow delay seconds must be between 0 and step timeout'
                        )
                    deadline = time.monotonic() + seconds
                    while time.monotonic() < deadline:
                        context.check()
                        time.sleep(min(1, max(0, deadline - time.monotonic())))
                elif step_type == 'notification':
                    message = str((step.get('conditions') or {}).get('message') or step_id)
                    context.log('workflow.notification: ' + message[:1000])

                runtime['step_states'][step_id] = 'completed'
                context.stage(f'workflow.step.completed:{step_id}:{step_type}')
                persist_workflow_runtime(context, runtime)
                break
            except ApprovalPending:
                raise
            except Cancelled:
                raise
            except Exception as exc:
                if attempt >= attempts:
                    terminal = exc if isinstance(exc, ExecutionFailed) else ExecutionFailed(
                        f'Workflow step {step_id} ({step_type}) failed'
                    )
                    rollback_id = step.get('rollback')
                    if rollback_id:
                        try:
                            execute_rollback(rollback_id, step_id)
                        except Exception as rollback_exc:
                            raise ExecutionFailed(
                                f'{terminal}; rollback {rollback_id} failed: '
                                f'{str(rollback_exc)[:200]}'
                            ) from None
                    raise terminal
                context.log(
                    f'workflow.step.retry: {step_id}:{step_type} '
                    f'attempt={attempt}/{attempts} error={str(exc)[:500]}'
                )
            finally:
                context.step_deadline = previous_deadline

    if not runtime['applied']:
        completed_explicit_apply = any(
            runtime['step_states'].get(step_id) == 'completed'
            for step_id in explicit_apply_ids
        )
        if explicit_apply_ids and not completed_explicit_apply:
            raise ExecutionFailed('terraform_apply was skipped or blocked; VM was not provisioned')
        if legacy_provisioning and not explicit_apply_ids:
            apply_and_sync('workflow completion')

    if runtime['applied'] and not runtime['inventory_synced']:
        register_managed_inventory(context, runtime['workspace'])
        runtime['inventory_synced'] = True
        persist_workflow_runtime(context, runtime)

    if runtime['applied']:
        ensure_guest_bootstrap()

    if context.ansible and not runtime['ansible_ran']:
        context.log('workflow.compatibility: running configured Ansible after Blueprint workflow')
        runtime['addresses'] = wait_for_ansible_transport(
            context,
            workspace_for('ansible compatibility'),
            addresses=runtime['addresses'],
        )
        context.ansible.inventory = Inventory(hosts=runtime['addresses'])
        AnsibleExecutor().execute('ansible.execute', context)
        runtime['ansible_ran'] = True
        persist_workflow_runtime(context, runtime)

    context.blueprint_workflow_completed = True
    persist_workflow_runtime(context, runtime)
    context.stage('workflow.completed')
    return runtime['workspace']

def execute(job_id):
    os.umask(0o077)
    with session() as db:
        # Atomic claim prevents duplicate dispatch or RQ retry from executing twice.
        claimed = db.execute(update(Job).where(Job.id == job_id, Job.status == 'queued', Job.cancel_requested.is_(False))
                             .values(status='running', heartbeat_at=now()))
        if claimed.rowcount != 1:
            db.commit()
            return
        job = db.get(Job, job_id)
        if job.deployment_id:
            deployment = db.get(Deployment, job.deployment_id)
            if deployment is not None and deployment.active_job_id == job.id:
                deployment.status = 'running'
        db.commit()
        context = Context(job)
    status, error = 'successful', None
    try:
        with session() as db:
            validate_authorization(db, job)
            if job.deployment_id:
                context.deployment = db.get(Deployment, job.deployment_id)
                context.credential = ensure_runtime_credential(db.get(Credential, context.deployment.credentials_id))
                if (
                    job.operation in {'terraform.apply', 'terraform.import', 'terraform.destroy'}
                    and not (job.payload or {}).get('_quota_checked')
                ):
                    quota_job = db.get(Job, job.id)
                    prepare_job_reservation(db, quota_job, context.deployment)
                    db.commit()
                    job.payload = dict(quota_job.payload or {})
                    context.job.payload = dict(quota_job.payload or {})
                if job.operation == 'terraform.apply':
                    if ((context.deployment.workflow or {}).get('adoption') or {}).get('plan_only'):
                        raise ExecutionFailed('Adopted deployment is plan-only; terraform.apply is disabled')
                    if has_released_allocations(db, context.deployment.id):
                        raise ExecutionFailed('Deployment allocations were released; execute the Blueprint again')
            if job.payload.get('ansible'):
                context.ansible = AnsibleInput.model_validate(job.payload['ansible'])
                context.ansible_credential = ensure_runtime_credential(db.get(Credential, context.ansible.credentials_id))
        if (
            settings().provider_offline_queue_enabled
            and job.operation in {'terraform.apply', 'terraform.destroy'}
            and context.deployment is not None
            and context.deployment.provider == 'proxmox'
        ):
            availability = provider_for(context.credential).execution_availability()
            if not availability.get('ok'):
                if availability.get('retryable'):
                    defer_for_provider(job.id, availability.get('reason') or 'unreachable')
                    return
                reason = availability.get('reason') or 'configuration'
                raise ExecutionFailed(
                    'Proxmox is reachable but cannot be used for Terraform execution; '
                    f'check credentials, TLS and provider configuration ({reason})'
                )
            clear_provider_wait(job.id)

        context.stage('job.running')
        if job.operation.startswith('terraform.'):
            executor = OpenTofuExecutor() if context.deployment.executor == 'opentofu' else TerraformExecutor()
            blueprint = (job.payload or {}).get('blueprint') or {}
            if job.operation == 'terraform.apply' and blueprint.get('steps'):
                run_blueprint_workflow(context, executor)
                if not context.blueprint_workflow_completed:
                    raise ExecutionFailed('Blueprint workflow did not complete')
            else:
                workspace = executor.execute(job.operation, context)
                if job.operation == 'terraform.import':
                    register_adopted_resource(context)
                    with session() as quota_db:
                        quota_job = quota_db.get(Job, job.id)
                        if quota_job is not None:
                            commit_job_reservation(quota_db, quota_job)
                            quota_db.commit()
                if job.operation == 'terraform.apply':
                    context.stage('inventory.synchronizing')
                    inventory = register_managed_inventory(context, workspace)
                    with session() as quota_db:
                        quota_job = quota_db.get(Job, job.id)
                        if quota_job is not None:
                            commit_job_reservation(quota_db, quota_job)
                            quota_db.commit()
                    if inventory['vm_id'] is not None:
                        context.log(
                            f"inventory.vm.registered: {inventory['node']} / VMID {inventory['vm_id']}"
                        )
                    else:
                        context.log(f"inventory.resource.registered: {inventory['external_id']}")
                    if context.ansible:
                        addresses = wait_for_ansible_transport(context, workspace)
                        context.ansible.inventory = Inventory(hosts=addresses)
                        AnsibleExecutor().execute('ansible.execute', context)
                if job.operation == 'terraform.destroy':
                    with session() as quota_db:
                        quota_job = quota_db.get(Job, job.id)
                        if quota_job is not None:
                            commit_job_reservation(quota_db, quota_job)
                            quota_db.commit()
        else:
            AnsibleExecutor().execute(job.operation, context)
        context.check()
    except ApprovalPending:
        if not context.quota_provider_submitted:
            with session() as quota_db:
                quota_job = quota_db.get(Job, job.id)
                if quota_job is not None:
                    release_job_reservation(quota_db, quota_job)
                    quota_db.commit()
        return
    except Cancelled:
        status, error = 'cancelled', 'Cancellation requested; inspect deployment state before retrying'
    except ExecutionFailed as exc:
        status, error = 'failed', str(exc)[:500]
    except Exception:
        # Raw exceptions can contain secrets; detailed process output is separately redacted.
        status, error = 'failed', 'Executor failure; inspect the sanitized job log and backend configuration'
    with session() as db:
        current = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if current.cancel_requested and status == 'successful':
            status, error = 'cancelled', 'Cancellation requested at completion; inspect deployment state'
        if status == 'successful':
            commit_job_reservation(db, current)
        elif context.quota_provider_submitted:
            mark_job_reservation_uncertain(db, current)
        else:
            release_job_reservation(db, current)
        current.status, current.error = status, error
        owns_deployment = False
        if current.deployment_id:
            deployment = db.get(Deployment, current.deployment_id)
            owns_deployment = deployment is not None and deployment.active_job_id == current.id
            if owns_deployment:
                deployment.active_job_id = None
                if current.operation == 'terraform.plan':
                    deployment.status = current.payload.get('previous_status', deployment.status or 'failed')
                elif context.rollback_destroyed:
                    deployment.status = 'destroyed'
                else:
                    deployment.status = 'destroyed' if status == 'successful' and current.operation == 'terraform.destroy' else status
                if current.operation == 'terraform.import' and status == 'successful':
                    deployment.status = 'imported'
            if owns_deployment and deployment.status == 'destroyed':
                if context.rollback_destroyed or current.source == 'Recovery':
                    account_confirmed_absent(
                        db, Scope(deployment.tenant_id, deployment.project_id),
                        'deployment', deployment.id, f'confirmed-absent:{current.id}', current.created_by,
                    )
                deployment.destroyed_at = now()
                released_at = now()
                db.execute(update(HostnameReservation).where(
                    HostnameReservation.resource_id == deployment.id,
                    HostnameReservation.status != 'released',
                ).values(status='released', released_at=released_at))
                db.execute(update(IPAllocation).where(
                    IPAllocation.resource_id == deployment.id,
                    IPAllocation.status != 'released',
                ).values(status='released', released_at=released_at))
                db.execute(update(ManagedVM).where(
                    ManagedVM.deployment_id == deployment.id,
                ).values(lifecycle_status='destroyed', destroyed_at=released_at))
                db.execute(update(ManagedResource).where(
                    ManagedResource.deployment_id == deployment.id,
                ).values(lifecycle_status='destroyed', destroyed_at=released_at))
        recovery = None
        blueprint = (current.payload or {}).get('blueprint') or {}
        if (
            status == 'failed'
            and current.operation == 'terraform.apply'
            and current.source != 'Recovery'
            and current.deployment_id
            and blueprint.get('recovery_policy') == 'destroy_on_failure'
            and not context.rollback_destroyed
        ):
            deployment = db.get(Deployment, current.deployment_id)
            if owns_deployment and deployment is not None and deployment.active_job_id is None:
                recovery = Job(
                    id=str(uuid.uuid4()),
                    operation='terraform.destroy',
                    deployment_id=deployment.id,
                    payload={'previous_status': 'failed', 'recovery_of': current.id},
                    created_by=current.created_by,
                    token_id=current.token_id,
                    request_id=str(uuid.uuid4()),
                    ip=current.ip,
                    source='Recovery',
                )
                db.add(recovery)
                db.flush()
                prepare_job_reservation(db, recovery, deployment)
                deployment.active_job_id = recovery.id
                deployment.status = 'recovery_queued'
                queue_webhook_event(db, 'recovery.queued', recovery.id, {
                    'recovery': {
                        'job_id': recovery.id,
                        'deployment_id': recovery.deployment_id,
                        'status': 'queued',
                        'recovery_of': current.id,
                    }
                })
                db.add(Audit(
                    user_id=current.created_by,
                    token_id=current.token_id,
                    ip=current.ip,
                    source='Recovery',
                    action='recovery.queued',
                    resource='jobs',
                    resource_id=recovery.id,
                    request_id=recovery.request_id,
                ))
        db.add(JobLog(job_id=job_id, message='job.' + status + (': ' + error if error else '')))
        queue_job_webhooks(db, current)
        db.add(Audit(user_id=job.created_by, token_id=job.token_id, ip=job.ip, action='job.' + status,
                     source=job.source,
                     resource='jobs', resource_id=job.id, result=status, request_id=job.request_id))
        db.commit()
