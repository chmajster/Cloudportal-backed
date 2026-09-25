import uuid
import json
import hashlib
import io
import os
import re
import shlex
import socket
import time
import ipaddress

WAIT_FOR_IP_MAX_SECONDS = 180
WAIT_FOR_IP_POLL_SECONDS = 10
WAIT_FOR_AGENT_PROVIDER_RETRIES = 3
WAIT_FOR_AGENT_PROVIDER_RETRY_SECONDS = 10
WAIT_FOR_AGENT_POLL_SECONDS = 2

import paramiko
from datetime import timedelta
from types import SimpleNamespace
from pathlib import Path
from sqlalchemy import select, update
from fastapi import HTTPException
from app.api.schemas import AnsibleInput, Inventory
from app.awx import AwxClient, AwxError
from app.config import settings
from app.database import session
from app.instance_operation import normal_instance_operation
from app.executors.ansible import AnsibleExecutor
from app.executors.base import Cancelled, ExecutionFailed
from app.executors.terraform import (OpenTofuExecutor, TerraformExecutor, cleanup_qemu_bootstrap,
                                     load_qemu_bootstrap)
from app.inventory_sync import state_outputs, sync_deployment_inventory
from app.jobs.approval import approval_policy_for_job
from app.jobs.lifecycle import has_released_allocations
from app.quotas.service import (account_confirmed_absent, commit_job_reservation,
                                mark_job_reservation_uncertain, prepare_job_reservation,
                                release_job_reservation)
from app.resource_scope.authorization import Scope
from app.models import (Audit, Blueprint, Credential, Deployment, HostnameReservation, IPAllocation, Job, JobLog,
                        ManagedResource, ManagedVM, Token, User, now)
from app.operations.service import queue_job_webhooks, queue_webhook_event, scheduler_user_permissions
from app.providers.registry import provider_for
from app.credentials.ssh import public_key_from_private_key
from app.security.core import decrypt_secret, effective_permissions
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
        self.ansible_runs = []

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
            current = db.get(Job, self.job.id)
            if current is not None:
                payload = dict(current.payload or {})
                payload['_current_stage'] = action
                current.payload = payload
                self.job.payload = dict(payload)
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
        for ansible_run in ((job.payload or {}).get('ansible_runs') or []):
            if not isinstance(ansible_run, dict):
                raise ExecutionFailed('Ansible runbook configuration is invalid')
            credential_id = ansible_run.get('credentials_id')
            if not credential_id or not reference_visible(db, 'credential', credential_id, scope):
                raise ExecutionFailed('Ansible runbook credential access has been revoked')
        if job.operation in {'terraform.plan', 'terraform.apply'}:
            blueprint = (job.payload or {}).get('blueprint') or {}
            guest_credential_id = blueprint.get('guest_credential_id')
            if not guest_credential_id and target is not None:
                guest_credential_id = (((target.workflow or {}).get('blueprint') or {}).get('guest_credential_id'))
            if guest_credential_id and not reference_visible(db, 'credential', guest_credential_id, scope):
                raise ExecutionFailed('Guest VM credential access has been revoked')
            template_guest_credential_id = blueprint.get('template_guest_credential_id')
            if not template_guest_credential_id and target is not None:
                template_guest_credential_id = (((target.workflow or {}).get('blueprint') or {}).get(
                    'template_guest_credential_id'
                ))
            if template_guest_credential_id and not reference_visible(
                db, 'credential', template_guest_credential_id, scope
            ):
                raise ExecutionFailed('Template VM credential access has been revoked')
            awx_config = blueprint.get('awx') or {}
            if not awx_config and target is not None:
                awx_config = ((target.workflow or {}).get('awx') or {})
            awx_credential_id = awx_config.get('credential_id') if isinstance(awx_config, dict) else None
            if awx_credential_id and not reference_visible(
                db, 'credential', awx_credential_id, scope
            ):
                raise ExecutionFailed('AWX credential access has been revoked')
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
    if job.payload.get('ansible') or job.payload.get('ansible_runs'):
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
    provider_retries = 0
    while time.monotonic() < deadline:
        context.check()
        try:
            if provider.guest_agent_ready(node, vm_id):
                return True
            # A successful API request means Proxmox is reachable. Retry limits below
            # apply only to consecutive provider/API failures, not to a guest agent
            # which is still starting inside the VM.
            provider_retries = 0
        except Exception as error:
            last_error = _safe_wait_error(error)
            if provider_retries >= WAIT_FOR_AGENT_PROVIDER_RETRIES:
                raise ExecutionFailed(
                    'QEMU Guest Agent check failed after '
                    f'{WAIT_FOR_AGENT_PROVIDER_RETRIES} retries; '
                    f'last provider error: {last_error}'
                ) from None
            provider_retries += 1
            remaining = max(0.0, deadline - time.monotonic())
            delay = min(WAIT_FOR_AGENT_PROVIDER_RETRY_SECONDS, remaining)
            context.log(
                'workflow.wait_for_agent.retry: '
                f'attempt={provider_retries}/{WAIT_FOR_AGENT_PROVIDER_RETRIES} '
                f'delay={int(delay)}s error={last_error}'
            )
            if delay:
                time.sleep(delay)
            continue

        remaining = max(0.0, deadline - time.monotonic())
        if remaining:
            time.sleep(min(WAIT_FOR_AGENT_POLL_SECONDS, remaining))
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


def wait_for_ip(context, workspace, timeout=WAIT_FOR_IP_MAX_SECONDS):
    effective_timeout = min(max(float(timeout), 1.0), WAIT_FOR_IP_MAX_SECONDS)
    configured = configured_deployment_ip(context)
    if configured:
        wait_for_vm(context, workspace, timeout=timeout)
        context.stage('workflow.wait_for_ip')
        context.log(f'workflow.wait_for_ip.configured: {configured}')
        update_inventory_primary_ip(context, configured)
        return [configured]

    node, vm_id, provider = _workflow_vm_identity(context, workspace)
    context.stage('workflow.wait_for_ip')
    deadline = time.monotonic() + effective_timeout
    max_attempts = max(1, int((effective_timeout + WAIT_FOR_IP_POLL_SECONDS - 1) // WAIT_FOR_IP_POLL_SECONDS))
    context.log(
        'workflow.wait_for_ip.polling: '
        f'interval={WAIT_FOR_IP_POLL_SECONDS}s timeout={int(effective_timeout)}s attempts={max_attempts}'
    )
    last_error = None
    attempt = 0
    while time.monotonic() < deadline:
        context.check()
        attempt += 1
        context.log(f'workflow.wait_for_ip.poll: attempt={attempt}/{max_attempts}')
        try:
            addresses = provider.guest_addresses(node, vm_id)
            ipv4 = [address for address in addresses if ':' not in address]
            selected = (ipv4 or addresses)[:1]
            if selected:
                context.log(f'workflow.wait_for_ip.discovered: {selected[0]} attempt={attempt}')
                update_inventory_primary_ip(context, selected[0])
                return selected
        except Exception as error:
            last_error = _log_wait_error(context, 'workflow.wait_for_ip', error, last_error)
        remaining = max(0.0, deadline - time.monotonic())
        if remaining:
            time.sleep(min(WAIT_FOR_IP_POLL_SECONDS, remaining))
    suffix = f'; last provider error: {last_error}' if last_error else ''
    raise ExecutionFailed(
        f'Timed out after {int(effective_timeout)}s waiting for VM IP address. '
        'DHCP discovery requires a working QEMU Guest Agent'
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
            'ansible_ran': bool(runtime.get('ansible_ran')),
            'ansible_completed_runs': sorted(
                int(value) for value in runtime.get('ansible_completed_runs', set())
            ),
            'ansible_inflight_run': runtime.get('ansible_inflight_run'),
            'awx_launches': dict(runtime.get('awx_launches') or {}),
        })
        payload['_workflow_runtime'] = saved
        current.payload = payload
        db.commit()
        context.job.payload = dict(payload)


def configured_ansible_runs(context):
    if context.ansible_runs:
        return list(context.ansible_runs)
    if context.ansible is not None and context.ansible_credential is not None:
        return [(context.ansible, context.ansible_credential)]
    return []


def execute_configured_ansible(context, runtime, workspace, *, timeout=600, addresses=None):
    runs = configured_ansible_runs(context)
    if not runs:
        raise ExecutionFailed(
            'Workflow requests Ansible but deployment has no Ansible runbook configuration'
        )
    completed = runtime.setdefault('ansible_completed_runs', set())
    addresses = list(addresses or [])
    inflight = runtime.get('ansible_inflight_run')
    if inflight is not None and int(inflight) not in completed:
        raise ExecutionFailed(
            'Automatic resume refused to repeat an Ansible run with an ambiguous result; '
            'inspect the host and retry the deployment explicitly'
        )
    for index, (spec, credential) in enumerate(runs):
        if index in completed:
            context.log(
                f'workflow.ansible.run.resumed: {index + 1}/{len(runs)}:{getattr(spec, 'playbook', 'ansible')}'
            )
            continue
        context.ansible = spec
        context.ansible_credential = credential
        context.stage(
            f'workflow.ansible.run.start:{index + 1}/{len(runs)}:{getattr(spec, 'playbook', 'ansible')}'
        )
        addresses = wait_for_ansible_transport(
            context,
            workspace,
            timeout=timeout,
            addresses=addresses,
        )
        context.ansible.inventory = Inventory(hosts=addresses)
        runtime['ansible_inflight_run'] = index
        persist_workflow_runtime(context, runtime)
        AnsibleExecutor().execute('ansible.execute', context)
        completed.add(index)
        runtime['ansible_inflight_run'] = None
        context.stage(
            f'workflow.ansible.run.completed:{index + 1}/{len(runs)}:{getattr(spec, 'playbook', 'ansible')}'
        )
        persist_workflow_runtime(context, runtime)
    runtime['ansible_ran'] = len(completed) >= len(runs)
    return addresses


BLUEPRINT_DECLARATIVE_STEPS = {
    'create_vm', 'clone_vm', 'configure_vm', 'cloud_init', 'start_vm',
    'set_hostname', 'set_tags',
}
BLUEPRINT_PRECOMPILED_STEPS = {'generate_hostname', 'allocate_ip'}
BLUEPRINT_POST_APPLY_STEPS = {
    'wait_for_vm', 'wait_for_agent', 'wait_for_ip', 'wait_for_ssh',
    'run_ansible_playbook', 'register_awx', 'create_snapshot', 'health_check',
}
BLUEPRINT_SUPPORTED_STEPS = (
    BLUEPRINT_DECLARATIVE_STEPS
    | BLUEPRINT_PRECOMPILED_STEPS
    | BLUEPRINT_POST_APPLY_STEPS
    | {'release_ip', 'terraform_plan', 'terraform_apply', 'terraform_destroy', 'condition', 'approval', 'delay', 'notification'}
)


def workflow_step_timeout(step_type, configured=600):
    timeout = max(1, int(configured or 600))
    # A per-step timeout may be shorter than the job-wide execution timeout, but
    # can never extend the lifetime of the job beyond the global safety bound.
    return min(timeout, settings().execution_timeout)


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


def wait_for_awx_job(context, client, job_id, timeout):
    deadline = time.monotonic() + max(1, float(timeout))
    last_status = 'unknown'
    while time.monotonic() < deadline:
        context.check()
        try:
            payload = client.request('GET', f'jobs/{int(job_id)}/').json()
        except AwxError as exc:
            raise ExecutionFailed('AWX job status check failed: ' + str(exc)[:350]) from None
        last_status = str((payload or {}).get('status') or 'unknown').lower()
        if last_status == 'successful':
            return payload
        if last_status in {'failed', 'error', 'canceled', 'cancelled'}:
            raise ExecutionFailed(
                f'AWX Job Template execution finished with status {last_status}'
            )
        remaining = max(0.0, deadline - time.monotonic())
        if remaining:
            time.sleep(min(2.0, remaining))
    raise ExecutionFailed(
        f'Timed out waiting for AWX Job Template completion (last status: {last_status})'
    )


def register_awx_host(context, runtime, workspace, *, timeout=600, step_id='register_awx'):
    blueprint = (context.job.payload or {}).get('blueprint') or {}
    config = blueprint.get('awx') or {}
    if not config:
        config = ((context.deployment.workflow or {}).get('awx') or {})
    if not isinstance(config, dict) or not config.get('credential_id'):
        raise ExecutionFailed('AWX onboarding configuration is missing')

    credential_id = int(config['credential_id'])
    with session() as db:
        credential = db.get(Credential, credential_id)
        if credential is None:
            raise ExecutionFailed('AWX credential disappeared before workflow execution')
        if credential.type != 'awx':
            raise ExecutionFailed('AWX onboarding requires an AWX credential')
        if credential.expires_at is not None and credential.expires_at <= now():
            raise ExecutionFailed('AWX credential expired before workflow execution')
        secret = decrypt_secret(credential)
        endpoint = credential.endpoint
        username = credential.username
        verify_ssl = credential.verify_ssl

    addresses = list(runtime.get('addresses') or [])
    if not addresses:
        addresses = wait_for_ip(context, workspace, timeout=timeout)
    if not addresses:
        raise ExecutionFailed('AWX onboarding requires a discovered VM address')
    address = next((value for value in addresses if ':' not in value), addresses[0])

    facts = blueprint_runtime_facts(context)
    client = AwxClient(
        endpoint,
        verify_ssl=verify_ssl,
        token=secret.get('token'),
        username=username,
        password=secret.get('password'),
        timeout=min(max(float(timeout), 5.0), 60.0),
    )
    context.check()
    try:
        result = client.register_host(
            hostname=context.deployment.name,
            ansible_host=address,
            deployment_id=context.deployment.id,
            environment=facts.get('environment'),
            apmid=facts.get('apmid'),
            inventory_id=config.get('inventory_id'),
            inventory_name=str(config.get('inventory_name') or 'CloudPortal'),
            organization_id=config.get('organization_id'),
            group_by_environment=bool(config.get('group_by_environment', True)),
            group_by_apmid=bool(config.get('group_by_apmid', True)),
        )
        job_template_id = config.get('job_template_id')
        project_id = config.get('project_id')
        launch = None
        if job_template_id:
            if project_id:
                job_template = client.request(
                    'GET', f'job_templates/{int(job_template_id)}/'
                ).json()
                if (
                    not isinstance(job_template, dict)
                    or int(job_template.get('project') or 0) != int(project_id)
                ):
                    raise AwxError(
                        'Selected AWX Job Template does not belong to the selected project'
                    )
            launch_key = str(step_id)
            launches = runtime.setdefault('awx_launches', {})
            existing_job_id = launches.get(launch_key)
            if existing_job_id:
                launch = {'job': int(existing_job_id), 'resumed': True}
                context.log(
                    f'workflow.awx.job.resumed: template={int(job_template_id)} job={int(existing_job_id)}'
                )
            else:
                launch = client.launch_job_template(
                    int(job_template_id),
                    hostname=context.deployment.name,
                    deployment_id=context.deployment.id,
                    environment=facts.get('environment'),
                    apmid=facts.get('apmid'),
                )
                launched_job_id = launch.get('job') or launch.get('id')
                if not launched_job_id:
                    raise AwxError('AWX launch did not return a job id')
                launches[launch_key] = int(launched_job_id)
                # Persist immediately: after AWX accepts a launch, recovery must
                # poll that job rather than silently launching a duplicate.
                persist_workflow_runtime(context, runtime)
            wait_for_awx_job(
                context,
                client,
                int(launch.get('job') or launch.get('id')),
                timeout,
            )
    except AwxError as exc:
        raise ExecutionFailed('AWX onboarding failed: ' + str(exc)[:350]) from None
    context.check()
    context.log(
        'workflow.awx.registered: '
        f"inventory={result['inventory_id']} host={result['host_id']} "
        f"groups={','.join(result.get('groups') or []) or '-'}"
    )
    if job_template_id:
        context.log(
            'workflow.awx.job.launched: '
            f"template={int(job_template_id)} job={launch.get('job') or launch.get('id') or 'unknown'}"
        )
    runtime['addresses'] = addresses
    return result


def cleanup_awx_after_destroy(context):
    config = ((context.deployment.workflow or {}).get('awx') or {})
    if not isinstance(config, dict) or not config.get('remove_on_destroy', True):
        return False
    credential_id = config.get('credential_id')
    if not credential_id:
        return False

    with session() as db:
        deployment = db.get(Deployment, context.deployment.id)
        if deployment is None:
            context.log('workflow.awx.cleanup.skipped: deployment metadata unavailable')
            return False
        from app.resource_scope.database import reference_visible
        scope = Scope(deployment.tenant_id, deployment.project_id)
        if not reference_visible(db, 'credential', credential_id, scope):
            context.log('workflow.awx.cleanup.skipped: AWX credential access revoked')
            return False
        credential = db.get(Credential, int(credential_id))
        if credential is None or credential.type != 'awx':
            context.log('workflow.awx.cleanup.skipped: AWX credential unavailable')
            return False
        try:
            secret = decrypt_secret(credential)
        except Exception:
            context.log('workflow.awx.cleanup.skipped: AWX credential could not be decrypted')
            return False
        endpoint = credential.endpoint
        username = credential.username
        verify_ssl = credential.verify_ssl

    try:
        result = AwxClient(
            endpoint,
            verify_ssl=verify_ssl,
            token=secret.get('token'),
            username=username,
            password=secret.get('password'),
            timeout=30,
        ).remove_host(
            hostname=context.deployment.name,
            inventory_id=config.get('inventory_id'),
            inventory_name=str(config.get('inventory_name') or 'CloudPortal'),
            organization_id=config.get('organization_id'),
        )
    except AwxError:
        context.log(
            'workflow.awx.cleanup.failed: AWX unavailable; '
            'VM destruction remains successful and stale AWX inventory may require reconciliation'
        )
        return False

    if result.get('removed'):
        context.log(
            'workflow.awx.cleanup.completed: '
            f"inventory={result.get('inventory_id')} hosts={','.join(map(str, result.get('host_ids') or []))}"
        )
        return True
    context.log('workflow.awx.cleanup.completed: host already absent')
    return False


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
            normalized_actual = str(actual).lower()
            if normalized_actual not in {str(value).lower() for value in expected}:
                return False
        elif isinstance(expected, bool):
            if bool(actual) != expected:
                return False
        elif str(actual).lower() != str(expected).lower():
            return False
    return True


def wait_for_ssh(context, workspace, timeout, addresses=None):
    addresses = list(addresses or wait_for_ip(context, workspace, timeout=timeout))
    access = _guest_access_credential(context)
    private_key = _guest_private_key(access['private_key']) if access else None
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        context.check()
        for address in addresses:
            client = None
            try:
                if access is None:
                    with socket.create_connection((address, 22), timeout=2):
                        return address
                client = _guest_ssh_connect(
                    address,
                    access['username'],
                    private_key=private_key,
                    password=access['password'],
                )
                _guest_ssh_run(client, 'true', timeout=min(30, timeout))
                context.log(
                    'workflow.wait_for_ssh.authenticated: username=' + access['username']
                )
                return address
            except (OSError, paramiko.SSHException, ExecutionFailed) as exc:
                last_error = exc
            finally:
                if client is not None:
                    client.close()
        time.sleep(2)
    label = 'authenticated SSH' if access else 'SSH'
    raise ExecutionFailed(
        f'Timed out waiting for {label}'
        + (f': {last_error}' if last_error else '')
    )


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


POSIX_GUEST_USERNAME = re.compile(r'^[a-z_][a-z0-9_-]{0,31}$')


def _guest_private_key(value):
    raw = str(value or '').strip()
    if not raw:
        return None
    for loader in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
        try:
            return loader.from_private_key(io.StringIO(raw))
        except (paramiko.SSHException, ValueError, TypeError):
            continue
    raise ExecutionFailed('Guest SSH credential contains an unsupported private key')


def _guest_ssh_connect(address, username, *, private_key=None, password=None, host_key=None):
    client = paramiko.SSHClient()
    if host_key is None:
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    else:
        client.get_host_keys().add(address, host_key.get_name(), host_key)
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
    client.connect(
        hostname=address,
        port=22,
        username=username,
        password=password,
        pkey=private_key,
        timeout=10,
        auth_timeout=10,
        banner_timeout=10,
        allow_agent=False,
        look_for_keys=False,
    )
    return client


def _guest_ssh_run(client, command, *, stdin_text=None, timeout=300):
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    if stdin_text is not None:
        stdin.write(stdin_text)
        stdin.flush()
        stdin.channel.shutdown_write()
    status = stdout.channel.recv_exit_status()
    error = stderr.read().decode('utf-8', errors='replace').strip()
    if status != 0:
        detail = error[-1200:] if error else f'exit status {status}'
        raise ExecutionFailed('Guest bootstrap command failed: ' + detail)
    return stdout.read().decode('utf-8', errors='replace')


def _blueprint_guest_credential_id(context, field):
    blueprint = (context.job.payload or {}).get('blueprint') or {}
    credential_id = blueprint.get(field)
    if not credential_id:
        credential_id = (((context.deployment.workflow or {}).get('blueprint') or {}).get(field))
    return credential_id


def _guest_access_credential(context):
    credential_id = (
        _blueprint_guest_credential_id(context, 'template_guest_credential_id')
        or _blueprint_guest_credential_id(context, 'guest_credential_id')
    )
    if not credential_id:
        return None
    with session() as db:
        credential = db.get(Credential, int(credential_id))
        if credential is None:
            raise ExecutionFailed('Guest access credential disappeared before workflow execution')
        if credential.type != 'ssh':
            raise ExecutionFailed('Guest access requires an SSH credential')
        if credential.expires_at is not None and credential.expires_at <= now():
            raise ExecutionFailed('Guest access credential expired before workflow execution')
        if not credential.username:
            raise ExecutionFailed('Guest access credential must define a username')
        secret = decrypt_secret(credential)
    if not secret.get('private_key') and not secret.get('password'):
        raise ExecutionFailed('Guest access credential has no password or private key')
    return {
        'credential_id': credential.id,
        'username': str(credential.username).strip(),
        'private_key': secret.get('private_key'),
        'password': secret.get('password'),
    }


def _guest_target_credential(context):
    credential_id = _blueprint_guest_credential_id(context, 'guest_credential_id')
    variables = context.deployment.variables or {}
    if not credential_id:
        return {
            'credential_id': None,
            'username': str(variables.get('ssh_username') or '').strip(),
            'public_key': str(variables.get('ssh_public_key') or '').strip() or None,
            'private_key': None,
            'password': None,
        }

    with session() as db:
        credential = db.get(Credential, int(credential_id))
        if credential is None:
            raise ExecutionFailed('Guest SSH credential disappeared before bootstrap')
        if credential.type != 'ssh':
            raise ExecutionFailed('Guest bootstrap requires an SSH credential')
        if credential.expires_at is not None and credential.expires_at <= now():
            raise ExecutionFailed('Guest SSH credential expired before bootstrap')
        secret = decrypt_secret(credential)
        private_key = secret.get('private_key')
        return {
            'credential_id': credential.id,
            'username': str(credential.username or '').strip(),
            'public_key': public_key_from_private_key(private_key) if private_key else None,
            'private_key': private_key,
            'password': secret.get('password'),
        }


def _verify_guest_ssh_host_key(provider, node, vm_id, host_key):
    key_paths = {
        'ssh-ed25519': '/etc/ssh/ssh_host_ed25519_key.pub',
        'ssh-rsa': '/etc/ssh/ssh_host_rsa_key.pub',
        'ecdsa-sha2-nistp256': '/etc/ssh/ssh_host_ecdsa_key.pub',
        'ecdsa-sha2-nistp384': '/etc/ssh/ssh_host_ecdsa_key.pub',
        'ecdsa-sha2-nistp521': '/etc/ssh/ssh_host_ecdsa_key.pub',
    }
    key_type = host_key.get_name()
    path = key_paths.get(key_type)
    if path is None:
        raise ExecutionFailed(
            'Unsupported SSH host key type during QEMU bootstrap verification: ' + key_type
        )
    try:
        status = provider.guest_exec(node, vm_id, ['/bin/cat', path], timeout=30)
    except HTTPException as exc:
        raise ExecutionFailed(
            'Could not verify VM SSH host key through QEMU Guest Agent'
        ) from exc
    line = str(status.get('out-data') or '').strip()
    parts = line.split()
    if len(parts) < 2 or parts[1] != host_key.get_base64():
        raise ExecutionFailed(
            'VM SSH host key does not match the key observed during bootstrap'
        )


def ensure_qemu_guest_bootstrap(context, workspace, timeout=600):
    bootstrap = load_qemu_bootstrap(workspace)
    if bootstrap is None:
        return False

    target = _guest_target_credential(context)
    install_qemu_agent = bool(
        (context.deployment.variables or {}).get('install_qemu_guest_agent')
    )
    if not install_qemu_agent and not target['credential_id']:
        cleanup_qemu_bootstrap(workspace)
        return False

    bootstrap_user = bootstrap['username']
    target_user = target['username']
    if not POSIX_GUEST_USERNAME.fullmatch(bootstrap_user):
        raise ExecutionFailed('Generated QEMU bootstrap username is invalid')
    if target_user and not POSIX_GUEST_USERNAME.fullmatch(target_user):
        raise ExecutionFailed(
            'Guest SSH credential username must be a Linux account name '
            '(lowercase letters, digits, underscore and hyphen; max 32 characters)'
        )
    if target_user == bootstrap_user:
        raise ExecutionFailed('Guest SSH credential username collides with the temporary bootstrap account')

    configured = configured_deployment_ip(context)
    if configured:
        wait_for_vm(context, workspace, timeout=timeout)
        addresses = [configured]
    else:
        try:
            addresses = wait_for_ip(context, workspace, timeout=min(timeout, 90))
        except ExecutionFailed as exc:
            raise ExecutionFailed(
                'Guest bootstrap cannot discover a DHCP address before SSH provisioning. '
                'Use a static/IPAM address or a template that already contains a working QEMU Guest Agent.'
            ) from exc
    address = wait_for_tcp_addresses(context, addresses, 22, timeout, 'bootstrap SSH')

    try:
        bootstrap_key = _guest_private_key(
            bootstrap['private_key_path'].read_text(encoding='utf-8')
        )
    except OSError:
        raise ExecutionFailed('Guest bootstrap private key could not be read') from None

    context.stage(
        'workflow.qemu_guest_agent.bootstrap'
        if install_qemu_agent
        else 'workflow.guest_credential.bootstrap'
    )
    deadline = time.monotonic() + timeout
    client = None
    verify = None
    last_error = None
    while time.monotonic() < deadline:
        context.check()
        try:
            client = _guest_ssh_connect(
                address,
                bootstrap_user,
                private_key=bootstrap_key,
            )
            break
        except (OSError, paramiko.SSHException) as exc:
            last_error = exc
            time.sleep(2)
    if client is None:
        raise ExecutionFailed(
            'Timed out authenticating temporary guest bootstrap account'
            + (f': {last_error}' if last_error else '')
        )

    host_key = client.get_transport().get_remote_server_key()
    node = vm_id = provider = None
    try:
        if install_qemu_agent:
            install_script = r'''set -eu
if [ "$(id -u)" -eq 0 ]; then
  SUDO=""
elif command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
  SUDO="sudo -n"
else
  echo "bootstrap account has no passwordless sudo" >&2
  exit 42
fi
if command -v apt-get >/dev/null 2>&1; then
  $SUDO env DEBIAN_FRONTEND=noninteractive apt-get update -y
  $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y qemu-guest-agent
elif command -v dnf >/dev/null 2>&1; then
  $SUDO dnf install -y qemu-guest-agent
elif command -v yum >/dev/null 2>&1; then
  $SUDO yum install -y qemu-guest-agent
elif command -v zypper >/dev/null 2>&1; then
  $SUDO zypper --non-interactive install qemu-guest-agent
elif command -v apk >/dev/null 2>&1; then
  $SUDO apk add qemu-guest-agent
else
  echo "unsupported package manager for qemu-guest-agent" >&2
  exit 43
fi
if command -v systemctl >/dev/null 2>&1; then
  $SUDO systemctl enable --now qemu-guest-agent
elif command -v rc-update >/dev/null 2>&1; then
  $SUDO rc-update add qemu-guest-agent default || true
  $SUDO rc-service qemu-guest-agent restart
else
  echo "unsupported service manager for qemu-guest-agent" >&2
  exit 44
fi
'''
            _guest_ssh_run(client, install_script, timeout=min(timeout, 600))

            node, vm_id, provider = _workflow_vm_identity(context, workspace)
            agent_deadline = time.monotonic() + min(timeout, 120)
            while time.monotonic() < agent_deadline:
                context.check()
                try:
                    if provider.guest_agent_ready(node, vm_id):
                        break
                except Exception:
                    pass
                time.sleep(2)
            else:
                raise ExecutionFailed('QEMU Guest Agent was installed but did not become ready')
            _verify_guest_ssh_host_key(provider, node, vm_id, host_key)

        if target_user:
            quoted_user = shlex.quote(target_user)
            quoted_key = shlex.quote(target['public_key'] or '')
            create_user = f'''set -eu
TARGET={quoted_user}
if ! id "$TARGET" >/dev/null 2>&1; then
  if command -v useradd >/dev/null 2>&1; then
    useradd -m -s /bin/sh "$TARGET"
  elif command -v adduser >/dev/null 2>&1; then
    adduser -D "$TARGET"
  else
    echo "no supported user creation command" >&2
    exit 45
  fi
fi
HOME_DIR="$(awk -F: -v user="$TARGET" '$1 == user {{ print $6 }}' /etc/passwd)"
if [ -z "$HOME_DIR" ]; then
  echo "cannot resolve target home directory" >&2
  exit 46
fi
if command -v usermod >/dev/null 2>&1; then
  if getent group sudo >/dev/null 2>&1; then
    usermod -aG sudo "$TARGET"
  elif getent group wheel >/dev/null 2>&1; then
    usermod -aG wheel "$TARGET"
  fi
elif command -v addgroup >/dev/null 2>&1 && grep -q '^wheel:' /etc/group; then
  addgroup "$TARGET" wheel
fi
install -d -m 700 -o "$TARGET" -g "$(id -gn "$TARGET")" "$HOME_DIR/.ssh"
'''
            if target['public_key']:
                create_user += f'''
printf '%s\n' {quoted_key} > "$HOME_DIR/.ssh/authorized_keys"
chown "$TARGET:$(id -gn "$TARGET")" "$HOME_DIR/.ssh/authorized_keys"
chmod 600 "$HOME_DIR/.ssh/authorized_keys"
'''
            create_user += '''
if [ "$TARGET" != "root" ] && [ -d /etc/sudoers.d ]; then
  printf '%s ALL=(ALL) NOPASSWD:ALL\n' "$TARGET" > "/etc/sudoers.d/90-cloudportal-$TARGET"
  chmod 440 "/etc/sudoers.d/90-cloudportal-$TARGET"
fi
'''

            if install_qemu_agent:
                try:
                    provider.guest_exec(
                        node,
                        vm_id,
                        ['/bin/sh', '-c', create_user],
                        timeout=min(timeout, 120),
                    )
                    if target['password']:
                        provider.set_guest_user_password(
                            node,
                            vm_id,
                            target_user,
                            target['password'],
                        )
                except HTTPException as exc:
                    raise ExecutionFailed(
                        'QEMU Guest Agent could not create the target guest credential account'
                    ) from exc
            else:
                _guest_ssh_run(
                    client,
                    'sudo -n /bin/sh -s',
                    stdin_text=create_user,
                    timeout=min(timeout, 120),
                )
                if target['password']:
                    if '\n' in target['password'] or '\r' in target['password']:
                        raise ExecutionFailed(
                            'Guest SSH credential password cannot contain line breaks'
                        )
                    _guest_ssh_run(
                        client,
                        'sudo -n chpasswd',
                        stdin_text=f"{target_user}:{target['password']}\n",
                        timeout=min(timeout, 120),
                    )

        if target['credential_id']:
            target_key = _guest_private_key(target['private_key'])
            try:
                verify = _guest_ssh_connect(
                    address,
                    target_user,
                    private_key=target_key,
                    password=target['password'],
                    host_key=host_key,
                )
                _guest_ssh_run(verify, 'true', timeout=30)
            except (OSError, paramiko.SSHException) as exc:
                raise ExecutionFailed(
                    'Target guest credential was created but SSH verification failed; '
                    'temporary bootstrap account was retained for recovery'
                ) from exc

        quoted_bootstrap = shlex.quote(bootstrap_user)
        cleanup_command = f'''set -eu
BOOTSTRAP={quoted_bootstrap}
HOME_DIR="$(awk -F: -v user="$BOOTSTRAP" '$1 == user {{ print $6 }}' /etc/passwd)"
if [ -n "$HOME_DIR" ]; then
  rm -f "$HOME_DIR/.ssh/authorized_keys"
fi
passwd -l "$BOOTSTRAP" >/dev/null 2>&1 || true
if command -v userdel >/dev/null 2>&1; then
  userdel -r "$BOOTSTRAP" >/dev/null 2>&1 || true
elif command -v deluser >/dev/null 2>&1; then
  deluser --remove-home "$BOOTSTRAP" >/dev/null 2>&1 || true
fi
'''

        if install_qemu_agent:
            try:
                provider.guest_exec(
                    node,
                    vm_id,
                    ['/bin/sh', '-c', cleanup_command],
                    timeout=min(timeout, 120),
                )
            except HTTPException as exc:
                raise ExecutionFailed('Failed to remove temporary QEMU bootstrap account') from exc
        elif verify is not None:
            cleanup_shell = '/bin/sh -s' if target_user == 'root' else 'sudo -n /bin/sh -s'
            _guest_ssh_run(
                verify,
                cleanup_shell,
                stdin_text=cleanup_command,
                timeout=min(timeout, 120),
            )
        else:
            raise ExecutionFailed(
                'Guest credential bootstrap completed without a final SSH session for cleanup'
            )
    finally:
        if verify is not None:
            verify.close()
        client.close()

    cleanup_qemu_bootstrap(workspace)
    context.log(
        'guest-bootstrap.completed: target credential prepared; '
        + ('QEMU Guest Agent ready; ' if install_qemu_agent else '')
        + 'temporary account removed'
    )
    return True


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
    if (context.deployment.variables or {}).get('install_qemu_guest_agent'):
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
    try:
        existing = provider.snapshots(node, vm_id) or []
    except Exception:
        existing = []
    if any(str(item.get('name') or item.get('snapname') or '') == snapname for item in existing):
        context.log(f'workflow.snapshot.reused: {snapname}')
        return
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
        'addresses': None,
        'applied': provider_applied,
        'ansible_ran': bool(
            saved_runtime.get('ansible_ran')
            or explicit_ansible_completed
        ),
        'ansible_completed_runs': {
            int(value) for value in (saved_runtime.get('ansible_completed_runs') or [])
        },
        'ansible_inflight_run': saved_runtime.get('ansible_inflight_run'),
        'awx_launches': dict(saved_runtime.get('awx_launches') or {}),
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
        # TerraformExecutor persists state before returning. Checkpoint the provider
        # mutation immediately, before guest bootstrap or any other post-apply work,
        # so a worker crash cannot cause recovery to submit terraform apply again.
        runtime['applied'] = True
        persist_workflow_runtime(context, runtime)
        ensure_qemu_guest_bootstrap(
            context,
            workspace,
            timeout=min(settings().execution_timeout, 900),
        )
        context.stage('inventory.synchronizing')
        inventory = register_managed_inventory(context, workspace)
        with session() as quota_db:
            quota_job = quota_db.get(Job, context.job.id)
            if quota_job is not None:
                commit_job_reservation(quota_db, quota_job)
                quota_db.commit()
        runtime['inventory_synced'] = True
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
            ensure_qemu_guest_bootstrap(
                context,
                runtime['workspace'],
                timeout=min(settings().execution_timeout, 900),
            )
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
            config = approval_policy_for_job(db, current)
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
                    address = wait_for_ssh(
                        context,
                        workspace_for(step_type),
                        timeout,
                        addresses=runtime['addresses'],
                    )
                    runtime['addresses'] = [address]
                elif step_type == 'run_ansible_playbook':
                    runtime['addresses'] = execute_configured_ansible(
                        context,
                        runtime,
                        workspace_for(step_type),
                        timeout=timeout,
                        addresses=runtime['addresses'],
                    )
                elif step_type == 'register_awx':
                    register_awx_host(
                        context,
                        runtime,
                        workspace_for(step_type),
                        timeout=timeout,
                        step_id=step_id,
                    )
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
        inventory = register_managed_inventory(context, runtime['workspace'])
        runtime['inventory_synced'] = True
        if inventory['vm_id'] is not None:
            context.log(f"inventory.vm.registered: {inventory['node']} / VMID {inventory['vm_id']}")
        else:
            context.log(f"inventory.resource.registered: {inventory['external_id']}")
        persist_workflow_runtime(context, runtime)

    if configured_ansible_runs(context) and not runtime['ansible_ran']:
        context.log('workflow.compatibility: running configured Ansible after Blueprint workflow')
        runtime['addresses'] = execute_configured_ansible(
            context,
            runtime,
            workspace_for('ansible compatibility'),
            addresses=runtime['addresses'],
        )
        persist_workflow_runtime(context, runtime)

    context.blueprint_workflow_completed = True
    persist_workflow_runtime(context, runtime)
    context.stage('workflow.completed')
    return runtime['workspace']

def _execute_unfenced(job_id):
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
            playbook_snapshots = (job.payload or {}).get('_ansible_playbook_snapshots') or {}
            for raw_run in ((job.payload or {}).get('ansible_runs') or []):
                playbook_id = str(raw_run.get('playbook') or '') if isinstance(raw_run, dict) else ''
                snapshot = (
                    playbook_snapshots.get(playbook_id)
                    if isinstance(playbook_snapshots, dict)
                    else None
                )
                spec = AnsibleInput.model_validate(
                    raw_run,
                    context={'playbook_snapshot': snapshot},
                )
                run_credential = ensure_runtime_credential(db.get(Credential, spec.credentials_id))
                context.ansible_runs.append((spec, run_credential))
            if job.payload.get('ansible'):
                raw_ansible = job.payload['ansible']
                playbook_id = str(raw_ansible.get('playbook') or '') if isinstance(raw_ansible, dict) else ''
                snapshot = (
                    playbook_snapshots.get(playbook_id)
                    if isinstance(playbook_snapshots, dict)
                    else None
                ) or (job.payload or {}).get('_ansible_playbook_snapshot')
                context.ansible = AnsibleInput.model_validate(
                    raw_ansible,
                    context={'playbook_snapshot': snapshot},
                )
                context.ansible_credential = ensure_runtime_credential(db.get(Credential, context.ansible.credentials_id))
            elif context.ansible_runs:
                context.ansible, context.ansible_credential = context.ansible_runs[0]
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
                    cleanup_awx_after_destroy(context)
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
            # The mutation already completed successfully. A late cancellation must
            # not rewrite provider truth as "cancelled" (especially after destroy),
            # otherwise deployment/inventory/allocation state diverges from reality.
            db.add(JobLog(
                job_id=current.id,
                message='job.cancel.too_late: operation already completed successfully',
            ))
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
                    previous_status = (current.payload or {}).get(
                        'previous_status',
                        deployment.status or 'failed',
                    )
                    if previous_status == 'reconciliation_required' and status == 'successful':
                        deployment.status = 'successful'
                        db.add(JobLog(
                            job_id=current.id,
                            message='restore.reconciliation.completed: successful terraform.plan',
                        ))
                    else:
                        deployment.status = previous_status
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

def execute(job_id):
    with normal_instance_operation():
        return _execute_unfenced(job_id)

