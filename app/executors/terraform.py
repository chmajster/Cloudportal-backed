import fcntl
import hashlib
import json
import os
import shutil
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit
from app.catalog import resolve_template_source, template_definition, template_import_target
from app.config import settings
from app.credentials.ssh import public_key_from_private_key
from app.database import session
from app.executors.base import Executor, ExecutionFailed, execution_environment, run_process
from app.models import Credential, now
from app.security.core import decrypt_secret
from app.terraform.state import distributed_deployment_lock, persist_state, restore_state


@contextmanager
def workspace_lock(workspace):
    workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (workspace / '.execution.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ExecutionFailed('Another executor holds the deployment lock') from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


@contextmanager
def terraform_plugin_cache_lock(cache_dir):
    cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (cache_dir / '.init.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def terraform_init_fingerprint(source, binary):
    digest = hashlib.sha256()
    digest.update(binary.encode())
    candidates = sorted(source.glob('*.tf'), key=lambda path: path.name)
    lock_file = source / '.terraform.lock.hcl'
    if lock_file.exists():
        candidates.append(lock_file)
    for path in candidates:
        digest.update(path.name.encode())
        digest.update(b'\0')
        digest.update(path.read_bytes())
        digest.update(b'\0')
    return digest.hexdigest()


def terraform_init_ready(workspace, fingerprint, binary):
    if not (workspace / '.terraform' / 'providers').exists():
        return False
    marker = workspace / '.cloudportal-terraform-init.json'
    try:
        data = json.loads(marker.read_text())
    except (OSError, ValueError, TypeError):
        return False
    return data.get('fingerprint') == fingerprint and data.get('binary') == binary


def mark_terraform_initialized(workspace, fingerprint, binary):
    marker = workspace / '.cloudportal-terraform-init.json'
    temporary = workspace / '.cloudportal-terraform-init.tmp'
    temporary.write_text(json.dumps({'fingerprint': fingerprint, 'binary': binary}, sort_keys=True))
    os.chmod(temporary, 0o600)
    os.replace(temporary, marker)


def guest_credential_runtime_variables(deployment):
    """Resolve guest-login secrets only for the active Terraform execution.

    The Blueprint/deployment stores only the credential id. A password is never
    copied into deployment.variables or the job payload. A private key is never
    passed to Terraform or the VM; only its derived public key is used.
    """
    blueprint = ((deployment.workflow or {}).get('blueprint') or {})
    credential_id = blueprint.get('guest_credential_id')
    if not credential_id:
        return {}, []

    with session() as db:
        credential = db.get(Credential, int(credential_id))
        if credential is None:
            raise ExecutionFailed('Guest SSH credential is missing')
        if credential.type != 'ssh':
            raise ExecutionFailed('Guest credential must be an SSH credential')
        if credential.expires_at is not None and credential.expires_at <= now():
            raise ExecutionFailed('Guest SSH credential expired before VM provisioning')
        if not credential.username:
            raise ExecutionFailed('Guest SSH credential must define a username')
        secret = decrypt_secret(credential)

    private_key = secret.get('private_key')
    password = secret.get('password')
    if not private_key and not password:
        raise ExecutionFailed('Guest SSH credential has no password or private key')

    variables = {'ssh_username': credential.username}
    if private_key:
        variables['ssh_public_key'] = public_key_from_private_key(private_key)
    if password:
        variables['ssh_password'] = password
    return variables, ([password] if password else [])


def proxmox_ssh_preflight(credential, env):
    from app.providers.proxmox import ProxmoxProvider
    readiness = ProxmoxProvider(credential).ssh_preflight(env)
    if readiness.get('ok'):
        return readiness
    reason = readiness.get('reason') or 'ssh_not_ready'
    host = readiness.get('host') or 'Proxmox'
    port = readiness.get('port') or 22
    raise ExecutionFailed(
        f'Automatic qemu-guest-agent cloud-init SSH preflight failed ({reason}) for {host}:{port}'
    )


class TerraformExecutor(Executor):
    binary = 'terraform'

    def execute(self, operation, context):
        deployment, credential = context.deployment, context.credential
        workspace = settings().data_dir / 'workspaces' / deployment.workspace
        try:
            definition, source = template_definition(deployment.template)
        except Exception:
            raise ExecutionFailed('Unapproved Terraform template') from None
        secret = decrypt_secret(credential)
        env = execution_environment(workspace)
        plugin_cache = settings().data_dir / (self.binary + '-plugin-cache')
        plugin_cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        env['TF_PLUGIN_CACHE_DIR'] = str(plugin_cache)
        provider_type = definition['provider']
        if credential.type != provider_type:
            raise ExecutionFailed('Credential type does not match Terraform template provider')
        if provider_type == 'proxmox':
            env['PROXMOX_VE_ENDPOINT'] = credential.endpoint.rstrip('/') + '/'
            env['PROXMOX_VE_INSECURE'] = 'false' if credential.verify_ssl else 'true'
            if secret.get('token_secret'):
                token_id = secret['token_id']
                if '!' not in token_id:
                    token_id = credential.username + '!' + token_id
                env['PROXMOX_VE_API_TOKEN'] = token_id + '=' + secret['token_secret']
            else:
                env['PROXMOX_VE_USERNAME'] = credential.username
                env['PROXMOX_VE_PASSWORD'] = secret['password']

            if deployment.variables.get('install_qemu_guest_agent'):
                ssh_names = (
                    'PROXMOX_VE_SSH_USERNAME',
                    'PROXMOX_VE_SSH_PASSWORD',
                    'PROXMOX_VE_SSH_PRIVATE_KEY',
                    'PROXMOX_VE_SSH_AGENT',
                    'PROXMOX_VE_SSH_AUTH_SOCK',
                )
                for name in ssh_names:
                    value = os.environ.get(name)
                    if value:
                        env[name] = value

                if secret.get('password') and not env.get('PROXMOX_VE_SSH_PASSWORD'):
                    env['PROXMOX_VE_SSH_USERNAME'] = credential.username.split('@', 1)[0]
                    env['PROXMOX_VE_SSH_PASSWORD'] = secret['password']

                has_ssh_auth = bool(
                    env.get('PROXMOX_VE_SSH_PASSWORD')
                    or env.get('PROXMOX_VE_SSH_PRIVATE_KEY')
                    or env.get('PROXMOX_VE_SSH_AGENT', '').lower() == 'true'
                )
                if not has_ssh_auth:
                    raise ExecutionFailed(
                        'Automatic qemu-guest-agent cloud-init requires SSH access to the Proxmox node '
                        'for snippet upload. Use a password-based Proxmox credential or configure '
                        'PROXMOX_VE_SSH_* for the worker.'
                    )
        elif provider_type == 'aws':
            env['AWS_ACCESS_KEY_ID'] = secret['access_key_id']
            env['AWS_SECRET_ACCESS_KEY'] = secret['secret_access_key']
            if secret.get('session_token'):
                env['AWS_SESSION_TOKEN'] = secret['session_token']
        elif provider_type == 'azure':
            env['ARM_TENANT_ID'] = secret['tenant_id']
            env['ARM_CLIENT_ID'] = secret['client_id']
            env['ARM_CLIENT_SECRET'] = secret['client_secret']
            env['ARM_SUBSCRIPTION_ID'] = secret['subscription_id']
        elif provider_type == 'openstack':
            env['OS_AUTH_URL'] = credential.endpoint.rstrip('/') + ('' if credential.endpoint.rstrip('/').endswith('/v3') else '/v3')
            env['OS_USERNAME'] = credential.username
            env['OS_PASSWORD'] = secret['password']
            env['OS_PROJECT_NAME'] = secret['project_name']
            env['OS_USER_DOMAIN_NAME'] = secret.get('domain_name', 'Default')
            env['OS_PROJECT_DOMAIN_NAME'] = secret.get('domain_name', 'Default')
            env['OS_INSECURE'] = 'false' if credential.verify_ssl else 'true'
        elif provider_type == 'vmware':
            endpoint = urlsplit(credential.endpoint)
            if not endpoint.hostname:
                raise ExecutionFailed('VMware endpoint is invalid')
            env['VSPHERE_SERVER'] = endpoint.hostname
            env['VSPHERE_USER'] = credential.username
            env['VSPHERE_PASSWORD'] = secret['password']
            env['VSPHERE_ALLOW_UNVERIFIED_SSL'] = 'false' if credential.verify_ssl else 'true'
        else:
            raise ExecutionFailed('Unsupported Terraform provider')
        sensitive_values = list(secret.values())
        sensitive_values.extend(
            value for name, value in env.items()
            if name in {'PROXMOX_VE_SSH_PASSWORD', 'PROXMOX_VE_SSH_PRIVATE_KEY'} and value
        )
        runtime_variables = dict(deployment.variables or {})
        guest_variables, guest_sensitive_values = guest_credential_runtime_variables(deployment)
        runtime_variables.update(guest_variables)
        sensitive_values.extend(guest_sensitive_values)
        with distributed_deployment_lock(deployment.id):
            with workspace_lock(workspace):
                context.stage('terraform.state.restore')
                restore_state(deployment.id, workspace)
                # Code is root-owned and approved; keep an existing provider lock on updates.
                for path in source.glob('*.tf'):
                    shutil.copyfile(path, workspace / path.name)
                lock_source = source / '.terraform.lock.hcl'
                if lock_source.exists() and not (workspace / '.terraform.lock.hcl').exists():
                    shutil.copyfile(lock_source, workspace / '.terraform.lock.hcl')
                variables_path = workspace / 'terraform.tfvars.json'
                variables_path.write_text(json.dumps(runtime_variables))
                os.chmod(variables_path, 0o600)
                init_fingerprint = terraform_init_fingerprint(source, self.binary)
                if terraform_init_ready(workspace, init_fingerprint, self.binary):
                    context.log('terraform.init.cached: pominięto ponowną inicjalizację; provider i workspace są już gotowe')
                else:
                    init_command = [self.binary, 'init', '-input=false', '-no-color']
                    if (workspace / '.terraform.lock.hcl').exists():
                        init_command.append('-lockfile=readonly')
                    context.stage('terraform.init')
                    with terraform_plugin_cache_lock(plugin_cache):
                        if terraform_init_ready(workspace, init_fingerprint, self.binary):
                            context.log('terraform.init.cached: inicjalizacja została wykonana przez inny worker')
                        else:
                            run_process(init_command, workspace, env, context, sensitive_values)
                            mark_terraform_initialized(workspace, init_fingerprint, self.binary)
                try:
                    if operation == 'terraform.import':
                        import_values = (context.job.payload or {}).get('import_values') or {}
                        try:
                            resource_address, import_id = template_import_target(deployment.template, import_values)
                        except Exception:
                            raise ExecutionFailed('Approved Terraform import identity is invalid') from None
                        context.stage('terraform.import')
                        run_process(
                            [self.binary, 'import', '-input=false', '-no-color', '-lock-timeout=30s', resource_address, import_id],
                            workspace, env, context, sensitive_values,
                        )
                        context.stage('terraform.plan')
                        run_process(
                            [self.binary, 'plan', '-input=false', '-no-color', '-lock-timeout=30s', '-out=execution.tfplan'],
                            workspace, env, context, sensitive_values,
                        )
                    else:
                        saved_plan = (
                            operation == 'terraform.apply'
                            and bool(getattr(context, 'apply_saved_terraform_plan', False))
                            and (workspace / 'execution.tfplan').exists()
                        )
                        if saved_plan:
                            context.stage('terraform.plan.reuse')
                        else:
                            context.stage('terraform.plan')
                            plan = [self.binary, 'plan', '-input=false', '-no-color', '-lock-timeout=30s', '-out=execution.tfplan']
                            if operation == 'terraform.destroy':
                                plan.append('-destroy')
                            run_process(plan, workspace, env, context, sensitive_values)
                        if operation != 'terraform.plan':
                            context.stage(operation)
                            # Quota enters uncertain state only once the mutating
                            # Terraform subprocess is actually about to be submitted.
                            # Init/plan/preflight failures are safe to retry.
                            context.quota_provider_submitted = True
                            run_process([self.binary, 'apply', '-input=false', '-no-color', '-lock-timeout=30s', 'execution.tfplan'], workspace, env, context, sensitive_values)
                finally:
                    keep_plan = (
                        operation == 'terraform.plan'
                        and bool(getattr(context, 'keep_terraform_plan', False))
                    )
                    if not keep_plan:
                        (workspace / 'execution.tfplan').unlink(missing_ok=True)
                    if (workspace / 'terraform.tfstate').exists():
                        context.stage('terraform.state.persist')
                        persist_state(deployment.id, workspace)
                    # Runtime guest passwords are written only to this temporary
                    # 0600 tfvars file. Remove it after every execution path.
                    variables_path.unlink(missing_ok=True)
        return workspace


class OpenTofuExecutor(TerraformExecutor):
    binary = 'tofu'
