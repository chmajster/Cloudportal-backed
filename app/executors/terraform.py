import fcntl
import hashlib
import json
import os
import shutil
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit
from app.catalog import resolve_template_source, template_definition, template_import_target
from app.config import settings
from app.credentials.ssh import generate_ed25519_key_pair, public_key_from_private_key
from app.database import session
from app.deployments.recreate import recreate_resource_address
from app.executors.base import Executor, ExecutionFailed, execution_environment, run_process
from app.executors.cloud_init import blueprint_snapshot, native_cloud_init_requested, prepare_native_seed
from app.models import Credential, now
from app.security.core import decrypt_secret
from app.terraform.identity import bind_proxmox_vm_id, reserve_proxmox_vm_id
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
def terraform_plugin_cache_lock(cache_dir, context=None):
    cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (cache_dir / '.init.lock').open('a') as lock:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if context is not None:
                    context.check()
                time.sleep(0.5)
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


def guest_credential_runtime_variables(deployment, *, blueprint=None):
    """Resolve guest-login secrets only for the active Terraform execution.

    The Blueprint/deployment stores only the credential id. A password is never
    copied into deployment.variables or the job payload. A private key is never
    passed to Terraform or the VM; only its derived public key is used.
    """
    if blueprint is None:
        blueprint = ((deployment.workflow or {}).get('blueprint') or {})
    credential_id = blueprint.get('guest_credential_id')
    if not credential_id:
        return {}, None

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

    variables = {
        'ssh_username': credential.username,
        # Preserve a Blueprint-provided key for password-only credentials while
        # keeping the explicit null shape expected by Terraform/API callers.
        'ssh_public_key': (deployment.variables or {}).get('ssh_public_key'),
    }
    # Only override the Blueprint key when the credential owns a private key.
    if private_key:
        variables['ssh_public_key'] = public_key_from_private_key(private_key)
    return variables, password


QEMU_BOOTSTRAP_MARKER = '.cloudportal-qemu-bootstrap.json'
QEMU_BOOTSTRAP_KEY = '.cloudportal-qemu-bootstrap-key'


def qemu_bootstrap_paths(workspace):
    workspace = Path(workspace)
    return workspace / QEMU_BOOTSTRAP_MARKER, workspace / QEMU_BOOTSTRAP_KEY


def load_qemu_bootstrap(workspace):
    marker_path, key_path = qemu_bootstrap_paths(workspace)
    if not marker_path.exists():
        return None
    if not key_path.exists():
        raise ExecutionFailed('QEMU Guest Agent bootstrap key is missing from the Terraform workspace')
    try:
        marker = json.loads(marker_path.read_text())
    except (OSError, ValueError, TypeError):
        raise ExecutionFailed('QEMU Guest Agent bootstrap metadata is invalid') from None
    username = str(marker.get('username') or '').strip()
    public_key = str(marker.get('public_key') or '').strip()
    if not username or not public_key:
        raise ExecutionFailed('QEMU Guest Agent bootstrap metadata is incomplete')
    return {
        'username': username,
        'public_key': public_key,
        'private_key_path': key_path,
        'reason': str(marker.get('reason') or 'guest_bootstrap'),
    }


def prepare_qemu_bootstrap(workspace, job_id, reason):
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    existing = load_qemu_bootstrap(workspace)
    if existing:
        return existing
    private_key, public_key = generate_ed25519_key_pair()
    marker_path, key_path = qemu_bootstrap_paths(workspace)
    username = 'cpbootstrap' + str(job_id).replace('-', '')[:8].lower()
    key_path.write_text(private_key, encoding='utf-8')
    os.chmod(key_path, 0o600)
    marker_path.write_text(json.dumps({
        'username': username,
        'public_key': public_key,
        'reason': reason,
    }, sort_keys=True), encoding='utf-8')
    os.chmod(marker_path, 0o600)
    return {
        'username': username,
        'public_key': public_key,
        'private_key_path': key_path,
        'reason': reason,
    }


def cleanup_qemu_bootstrap(workspace):
    for path in qemu_bootstrap_paths(workspace):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


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


def terraform_state_vm_id(workspace):
    path = Path(workspace) / 'terraform.tfstate'
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
        value = ((payload.get('outputs') or {}).get('vm_id') or {}).get('value')
        return int(value) if value is not None else None
    except (OSError, TypeError, ValueError):
        return None


def terraform_plan_command(binary, operation, recreate_address=None):
    command = [binary, 'plan', '-input=false', '-no-color', '-lock-timeout=30s', '-out=execution.tfplan']
    if operation == 'terraform.destroy':
        command.append('-destroy')
    elif recreate_address:
        command.append('-replace=' + recreate_address)
    return command


class TerraformExecutor(Executor):
    binary = 'terraform'

    def execute(self, operation, context):
        deployment, credential = context.deployment, context.credential
        workspace = settings().data_dir / 'workspaces' / deployment.workspace
        try:
            definition, source = template_definition(deployment.template)
        except Exception:
            raise ExecutionFailed('Unapproved Terraform template') from None
        recreate_address = None
        if operation in {'terraform.plan', 'terraform.apply'} and bool((context.job.payload or {}).get('_recreate')):
            try:
                recreate_address = recreate_resource_address(deployment.template)
            except Exception:
                raise ExecutionFailed('Approved Terraform template does not support full resource recreation') from None
            context.log('terraform.recreate.target: ' + recreate_address)
        secret = decrypt_secret(credential)
        env = execution_environment(workspace)
        plugin_cache = settings().data_dir / (self.binary + '-plugin-cache')
        plugin_cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        env['TF_PLUGIN_CACHE_DIR'] = str(plugin_cache)
        provider_type = definition['provider']
        if credential.type != provider_type:
            raise ExecutionFailed('Credential type does not match Terraform template provider')
        native_seed = operation in {'terraform.plan', 'terraform.apply'} and native_cloud_init_requested(context)
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

            qemu_bootstrap = None
            qemu_install = bool(deployment.variables.get('install_qemu_guest_agent'))
            job_blueprint = (context.job.payload or {}).get('blueprint') or {}
            guest_credential_id = job_blueprint.get('guest_credential_id')
            bootstrap_required = bool(qemu_install or guest_credential_id)
            if bootstrap_required and not native_seed and operation in {'terraform.plan', 'terraform.apply'}:
                force_guest_bootstrap = bool(guest_credential_id)

                # A selected guest credential must never depend on SSH to the
                # Proxmox node. The temporary account is created through native
                # cloud-init and the requested account is finalized from inside
                # the guest after apply.
                if qemu_install and not force_guest_bootstrap:
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

                marker_path, _ = qemu_bootstrap_paths(workspace)
                saved_plan_apply = operation == 'terraform.apply' and bool(
                    getattr(context, 'apply_saved_terraform_plan', False)
                )
                if saved_plan_apply:
                    qemu_bootstrap = load_qemu_bootstrap(workspace) if marker_path.exists() else None
                    if force_guest_bootstrap and qemu_bootstrap is None:
                        raise ExecutionFailed(
                            'Saved Terraform plan is missing the temporary guest bootstrap key'
                        )
                    use_snippet = qemu_bootstrap is None
                    readiness = {
                        'ok': use_snippet,
                        'reason': 'saved_plan_guest_bootstrap' if qemu_bootstrap else 'saved_plan_snippet',
                    }
                elif force_guest_bootstrap:
                    readiness = {'ok': False, 'reason': 'guest_credential_bootstrap'}
                    use_snippet = False
                    qemu_bootstrap = prepare_qemu_bootstrap(
                        workspace,
                        context.job.id,
                        readiness['reason'],
                    )
                else:
                    snippet_storage = deployment.variables.get('cloud_init_snippet_storage')
                    if snippet_storage:
                        from app.providers.proxmox import ProxmoxProvider
                        readiness = ProxmoxProvider(credential).ssh_preflight(env)
                    else:
                        readiness = {'ok': False, 'reason': 'snippet_storage_missing'}
                    use_snippet = bool(snippet_storage and readiness.get('ok'))
                    if use_snippet:
                        cleanup_qemu_bootstrap(workspace)
                    else:
                        qemu_bootstrap = prepare_qemu_bootstrap(
                            workspace,
                            context.job.id,
                            readiness.get('reason') or 'ssh_not_ready',
                        )
                mode = 'snippet' if use_snippet else 'guest-bootstrap'
                subject = 'guest-credential' if force_guest_bootstrap else 'qemu-guest-agent'
                context.log(
                    subject
                    + '.provisioning-mode: '
                    + mode
                    + '; reason='
                    + str(readiness.get('reason') or 'ready')
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
        if provider_type == 'proxmox' and operation in {'terraform.plan', 'terraform.apply'}:
            runtime_variables['qemu_guest_agent_bootstrap'] = bool(qemu_bootstrap)
            if qemu_bootstrap:
                runtime_variables['bootstrap_username'] = qemu_bootstrap['username']
                runtime_variables['bootstrap_public_key'] = qemu_bootstrap['public_key']
        if operation in {'terraform.plan', 'terraform.apply'} and not native_seed and not (
            provider_type == 'proxmox' and qemu_bootstrap
        ):
            guest_variables, guest_password = guest_credential_runtime_variables(deployment)
            runtime_variables.update(guest_variables)
            if guest_password:
                # Keep the plaintext password out of deployment variables, job
                # payloads and terraform.tfvars.json. Terraform reads it only from
                # the process environment for this execution.
                env['TF_VAR_ssh_password'] = guest_password
                sensitive_values.append(guest_password)
        with distributed_deployment_lock(deployment.id):
            with workspace_lock(workspace):
                context.stage('terraform.state.restore')
                restore_state(deployment.id, workspace)

                if (
                    provider_type == 'proxmox'
                    and deployment.template in {'proxmox-vm', 'proxmox-appliance'}
                    and operation in {'terraform.plan', 'terraform.apply'}
                ):
                    existing_vm_id = terraform_state_vm_id(workspace)
                    try:
                        if existing_vm_id is not None:
                            reserved_vm_id = bind_proxmox_vm_id(deployment.id, existing_vm_id)
                            context.log(f'proxmox.vmid.bound_from_state: {reserved_vm_id}')
                        else:
                            reserved_vm_id = reserve_proxmox_vm_id(
                                deployment.id,
                                credential,
                                context=context,
                            )
                    except Exception:
                        raise ExecutionFailed(
                            'Could not reserve a unique Proxmox VMID before Terraform execution'
                        ) from None
                    runtime_variables['vm_id'] = reserved_vm_id
                    deployment.variables = {
                        **(deployment.variables or {}),
                        'vm_id': reserved_vm_id,
                    }

                if native_seed:
                    if any(path.exists() for path in qemu_bootstrap_paths(workspace)):
                        raise ExecutionFailed(
                            'Legacy guest bootstrap is still pending; preserve its workspace and '
                            'finish recovery before starting a new Cloud-init deployment'
                        )
                    context.stage('cloud-init.preparing')
                    guest_variables, guest_password = guest_credential_runtime_variables(
                        deployment, blueprint=blueprint_snapshot(context),
                    )
                    runtime_variables.update(prepare_native_seed(
                        context, workspace, guest_variables, guest_password,
                    ))
                    # Native media contains a password hash; never place the
                    # cleartext password in Terraform env, tfvars, or state.
                    guest_password = None
                # Mirror the approved template source into the persistent workspace.
                # Leaving removed *.tf files behind can silently keep obsolete
                # resources/providers in later plans.
                source_tf_names = {path.name for path in source.glob('*.tf')}
                for stale in workspace.glob('*.tf'):
                    if stale.name not in source_tf_names:
                        stale.unlink()
                for path in source.glob('*.tf'):
                    shutil.copyfile(path, workspace / path.name)
                lock_source = source / '.terraform.lock.hcl'
                workspace_lockfile = workspace / '.terraform.lock.hcl'
                if lock_source.exists():
                    shutil.copyfile(lock_source, workspace_lockfile)
                else:
                    workspace_lockfile.unlink(missing_ok=True)
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
                    with terraform_plugin_cache_lock(plugin_cache, context):
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
                            plan = terraform_plan_command(self.binary, operation, recreate_address)
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
                    variables_path.unlink(missing_ok=True)
        return workspace


class OpenTofuExecutor(TerraformExecutor):
    binary = 'tofu'
