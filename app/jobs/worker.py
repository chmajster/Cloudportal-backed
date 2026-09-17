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
from app.models import Audit, Credential, Deployment, Job, JobLog, Token, now
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
                         action=action, resource='jobs', resource_id=self.job.id, request_id=self.job.request_id))
            db.commit()


def validate_authorization(db, job):
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
    if job.operation == 'terraform.destroy':
        needed.add('deployments.destroy')
    if job.payload.get('ansible'):
        needed.add('ansible.execute')
    if not needed <= permissions:
        raise ExecutionFailed('Job permissions have been revoked')


def wait_for_vm(context, workspace):
    # State is internal, never sent to PHP or returned by API.
    state = json.loads((workspace / 'terraform.tfstate').read_text())
    vm_id = state.get('outputs', {}).get('vm_id', {}).get('value')
    if not vm_id:
        raise ExecutionFailed('VM ID missing from Terraform state')
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
                context.credential = db.get(Credential, context.deployment.credentials_id)
            if job.payload.get('ansible'):
                context.ansible = AnsibleInput.model_validate(job.payload['ansible'])
                context.ansible_credential = db.get(Credential, context.ansible.credentials_id)
        context.stage('job.running')
        if job.operation.startswith('terraform.'):
            executor = OpenTofuExecutor() if context.deployment.executor == 'opentofu' else TerraformExecutor()
            workspace = executor.execute(job.operation, context)
            if job.operation == 'terraform.apply' and context.ansible:
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
            if deployment.status == 'destroyed':
                deployment.destroyed_at = now()
        db.add(JobLog(job_id=job_id, message='job.' + status + (': ' + error if error else '')))
        db.add(Audit(user_id=job.created_by, token_id=job.token_id, ip=job.ip, action='job.' + status,
                     resource='jobs', resource_id=job.id, result=status, request_id=job.request_id))
        db.commit()
