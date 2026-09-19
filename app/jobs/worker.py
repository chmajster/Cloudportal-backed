import uuid
import json
import os
import time
from datetime import timedelta
from types import SimpleNamespace
from sqlalchemy import select, update
from app.api.schemas import AnsibleInput, Inventory
from app.config import settings
from app.database import session
from app.executors.ansible import AnsibleExecutor
from app.executors.base import Cancelled, ExecutionFailed
from app.executors.terraform import OpenTofuExecutor, TerraformExecutor
from app.models import Audit, Credential, Deployment, HostnameReservation, IPAllocation, Job, JobLog, ManagedResource, ManagedVM, Token, now
from app.operations.service import queue_job_webhooks, queue_webhook_event, scheduler_user_permissions
from app.providers.registry import provider_for
from app.security.core import effective_permissions


class Context:
    def __init__(self, job):
        self.job = job
        self.started = time.monotonic()
        self.last_check = 0
        self.deployment = self.credential = self.ansible = self.ansible_credential = None

    def check(self):
        if time.monotonic() - self.started > settings().execution_timeout:
            raise ExecutionFailed('Execution timeout')
        if time.monotonic() - self.last_check < 1:
            return
        self.last_check = time.monotonic()
        with session() as db:
            job = db.get(Job, self.job.id)
            if not job or job.cancel_requested:
                raise Cancelled('Cancellation requested')
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


def validate_authorization(db, job):
    if job.source in {'Scheduler', 'Recovery'} and job.token_id is None:
        permissions = scheduler_user_permissions(db, job.created_by)
        if permissions is None:
            raise ExecutionFailed('Scheduled job owner is disabled or locked')
    else:
        token = db.get(Token, job.token_id)
        if not token or not token.user.is_active or token.user.is_locked or (token.user.locked_until and token.user.locked_until > now()):
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
        permissions = effective_permissions(token.user)
        if token.kind == 'api':
            if token.expires_at and token.expires_at <= now():
                raise ExecutionFailed('Job API token expired')
            permissions &= set(token.scopes)
    needed = {'jobs.execute', 'ansible.execute' if job.operation == 'ansible.execute' else 'terraform.execute'}
    if job.operation == 'terraform.apply':
        needed.add('deployments.create')
        blueprint = job.payload.get('blueprint') or {}
        if blueprint.get('recovery_policy') == 'destroy_on_failure':
            needed.add('deployments.destroy')
    if job.operation == 'terraform.destroy':
        needed.add('deployments.destroy')
    if job.operation == 'terraform.import':
        needed.add('deployments.adopt')
    if job.payload.get('ansible'):
        needed.add('ansible.execute')
    if not needed <= permissions:
        raise ExecutionFailed('Job permissions have been revoked')


def ensure_runtime_credential(credential):
    if credential is None:
        raise ExecutionFailed('Required credential is missing')
    if credential.expires_at is not None and credential.expires_at <= now():
        raise ExecutionFailed('Infrastructure credential expired before job execution')
    return credential


def vm_id_from_state(workspace):
    # State is internal, never sent to PHP or returned by API.
    try:
        state = json.loads((workspace / 'terraform.tfstate').read_text())
        vm_id = state.get('outputs', {}).get('vm_id', {}).get('value')
    except (OSError, ValueError, TypeError):
        raise ExecutionFailed('Terraform state could not be read') from None
    if not vm_id:
        raise ExecutionFailed('VM ID missing from Terraform state')
    return int(vm_id)


def register_managed_vm(context, workspace):
    vm_id = vm_id_from_state(workspace)
    with session() as db:
        deployment = db.get(Deployment, context.deployment.id)
        existing = db.scalar(select(ManagedVM).where(
            ManagedVM.provider_id == deployment.provider_id,
            ManagedVM.vm_id == vm_id,
        ))
        if existing and existing.deployment_id not in {None, deployment.id}:
            raise ExecutionFailed('VM identity is already linked to another deployment')
        if existing is None:
            existing = ManagedVM(
                provider_id=deployment.provider_id,
                deployment_id=deployment.id,
                node=deployment.variables['node'],
                vm_id=vm_id,
                name=deployment.name,
                management_mode='terraform',
                lifecycle_status='active',
                created_by=deployment.created_by,
            )
            db.add(existing)
        else:
            existing.deployment_id = deployment.id
            existing.node = deployment.variables['node']
            existing.name = deployment.name
            existing.management_mode = 'terraform'
            existing.lifecycle_status = 'active'
            existing.destroyed_at = None
        db.commit()
    return vm_id



def register_managed_resource(context, workspace):
    try:
        state = json.loads((workspace / 'terraform.tfstate').read_text())
    except (OSError, ValueError, TypeError):
        raise ExecutionFailed('Terraform state could not be read for resource inventory') from None
    outputs = state.get('outputs', {})
    external_id = outputs.get('resource_id', {}).get('value')
    if external_id is None:
        external_id = outputs.get('vm_id', {}).get('value')
    if external_id is None:
        raise ExecutionFailed('Managed resource identity is missing from Terraform state')
    primary_ip = outputs.get('primary_ip', {}).get('value')
    with session() as db:
        deployment = db.get(Deployment, context.deployment.id)
        row = db.scalar(select(ManagedResource).where(
            ManagedResource.deployment_id == deployment.id
        ))
        if row is None:
            row = ManagedResource(
                deployment_id=deployment.id,
                provider_id=deployment.provider_id,
                provider=deployment.provider,
                resource_type='vm',
                external_id=str(external_id),
                name=deployment.name,
                primary_ip=str(primary_ip) if primary_ip else None,
                lifecycle_status='active',
                metadata_json={},
                created_by=deployment.created_by,
            )
            db.add(row)
        else:
            row.external_id = str(external_id)
            row.name = deployment.name
            row.primary_ip = str(primary_ip) if primary_ip else None
            row.lifecycle_status = 'active'
            row.destroyed_at = None
        db.commit()


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

def wait_for_vm(context, workspace):
    vm_id = vm_id_from_state(workspace)
    provider = provider_for(context.credential)
    context.stage('workflow.wait_for_vm')
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        context.check()
        try:
            addresses = provider.guest_addresses(context.deployment.variables['node'], int(vm_id))
            if addresses:
                return addresses[:1]
        except Exception:
            pass  # Guest agent is expected to be unavailable during boot.
        time.sleep(2)
    raise ExecutionFailed('Timed out waiting for VM guest-agent address')


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


def execute(job_id):
    os.umask(0o077)
    with session() as db:
        # Atomic claim prevents duplicate dispatch or RQ retry from executing twice.
        claimed = db.execute(update(Job).where(Job.id == job_id, Job.status == 'queued', Job.cancel_requested.is_(False))
                             .values(status='running', heartbeat_at=now()))
        db.commit()
        if claimed.rowcount != 1:
            return
        job = db.get(Job, job_id)
        context = Context(job)
    status, error = 'successful', None
    try:
        with session() as db:
            validate_authorization(db, job)
            if job.deployment_id:
                context.deployment = db.get(Deployment, job.deployment_id)
                context.credential = ensure_runtime_credential(db.get(Credential, context.deployment.credentials_id))
            if job.payload.get('ansible'):
                context.ansible = AnsibleInput.model_validate(job.payload['ansible'])
                context.ansible_credential = ensure_runtime_credential(db.get(Credential, context.ansible.credentials_id))
        if (
            settings().provider_offline_queue_enabled
            and job.operation == 'terraform.apply'
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
                    'Proxmox is reachable but cannot be used for provisioning; '
                    f'check credentials, TLS and provider configuration ({reason})'
                )
            clear_provider_wait(job.id)

        context.stage('job.running')
        if job.operation.startswith('terraform.'):
            executor = OpenTofuExecutor() if context.deployment.executor == 'opentofu' else TerraformExecutor()
            workspace = executor.execute(job.operation, context)
            if job.operation == 'terraform.import':
                register_adopted_resource(context)
            if job.operation == 'terraform.apply':
                register_managed_resource(context, workspace)
                if context.deployment.provider == 'proxmox':
                    register_managed_vm(context, workspace)
                if context.ansible:
                    context.ansible.inventory = Inventory(hosts=wait_for_vm(context, workspace))
                    AnsibleExecutor().execute('ansible.execute', context)
        else:
            AnsibleExecutor().execute(job.operation, context)
        context.check()
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
        current.status, current.error = status, error
        if current.deployment_id:
            deployment = db.get(Deployment, current.deployment_id)
            deployment.active_job_id = None
            deployment.status = 'destroyed' if status == 'successful' and current.operation == 'terraform.destroy' else status
            if current.operation == 'terraform.plan' and status == 'successful':
                deployment.status = current.payload.get('previous_status', 'failed')
            if current.operation == 'terraform.import' and status == 'successful':
                deployment.status = 'imported'
            if deployment.status == 'destroyed':
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
        ):
            deployment = db.get(Deployment, current.deployment_id)
            if deployment is not None and deployment.active_job_id is None:
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
