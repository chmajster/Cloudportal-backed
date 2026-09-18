import fcntl
import json
import os
import shutil
from contextlib import contextmanager
from pathlib import Path
from app.config import settings
from app.executors.base import Executor, ExecutionFailed, execution_environment, run_process
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


class TerraformExecutor(Executor):
    binary = 'terraform'

    def execute(self, operation, context):
        deployment, credential = context.deployment, context.credential
        workspace = settings().data_dir / 'workspaces' / deployment.workspace
        source = settings().source_dir / 'terraform' / 'templates' / deployment.template
        if deployment.template != 'proxmox-vm' or source.resolve().parent != (settings().source_dir / 'terraform/templates').resolve():
            raise ExecutionFailed('Unapproved Terraform template')
        secret = decrypt_secret(credential)
        env = execution_environment(workspace)
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
                variables_path.write_text(json.dumps(deployment.variables))
                os.chmod(variables_path, 0o600)
                context.stage('terraform.init')
                run_process([self.binary, 'init', '-input=false', '-no-color'], workspace, env, context, secret.values())
                context.stage('terraform.plan')
                plan = [self.binary, 'plan', '-input=false', '-no-color', '-lock-timeout=30s', '-out=execution.tfplan']
                if operation == 'terraform.destroy':
                    plan.append('-destroy')
                try:
                    run_process(plan, workspace, env, context, secret.values())
                    if operation != 'terraform.plan':
                        context.stage(operation)
                        run_process([self.binary, 'apply', '-input=false', '-no-color', '-lock-timeout=30s', 'execution.tfplan'], workspace, env, context, secret.values())
                finally:
                    (workspace / 'execution.tfplan').unlink(missing_ok=True)
                    if (workspace / 'terraform.tfstate').exists():
                        context.stage('terraform.state.persist')
                        persist_state(deployment.id, workspace)
        return workspace


class OpenTofuExecutor(TerraformExecutor):
    binary = 'tofu'
