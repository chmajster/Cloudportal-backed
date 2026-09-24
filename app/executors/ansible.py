import json
import os
import shutil
import tempfile
import yaml
from pathlib import Path
from app.catalog import playbook_definition
from app.config import settings
from app.executors.base import Executor, ExecutionFailed, execution_environment, run_process
from app.security.core import decrypt_secret

class UnsafeString(str):
    pass


class InventoryDumper(yaml.SafeDumper):
    pass


InventoryDumper.add_representer(UnsafeString, lambda dumper, value: dumper.represent_scalar('!unsafe', value))


class AnsibleExecutor(Executor):
    def execute(self, operation, context):
        spec = context.ansible
        payload = context.job.payload or {}
        snapshots = payload.get('_ansible_playbook_snapshots') or {}
        snapshot = (
            snapshots.get(spec.playbook)
            if isinstance(snapshots, dict)
            else None
        ) or payload.get('_ansible_playbook_snapshot') or {}
        try:
            if (
                isinstance(snapshot, dict)
                and snapshot.get('custom') is True
                and snapshot.get('id') == spec.playbook
                and snapshot.get('content')
            ):
                definition = dict(snapshot)
            else:
                definition = playbook_definition(spec.playbook)
        except Exception:
            raise ExecutionFailed('Unapproved playbook') from None
        filename = definition['file']
        if definition.get('transport') != context.ansible_credential.type:
            raise ExecutionFailed('Playbook transport does not match Ansible credential')
        secret = decrypt_secret(context.ansible_credential)
        root = settings().data_dir / 'runs'
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(prefix='ansible-', dir=root) as folder:
            workspace = Path(folder)
            custom_playbook_path = None
            if definition.get('custom'):
                custom_playbook_path = workspace / filename
                custom_playbook_path.write_text(str(definition.get('content') or ''), encoding='utf-8')
                os.chmod(custom_playbook_path, 0o600)
            env = execution_environment(workspace)
            env.update(ANSIBLE_RETRY_FILES_ENABLED='False', ANSIBLE_NOCOLOR='1',
                       ANSIBLE_LOCAL_TEMP=str(workspace / 'tmp'), ANSIBLE_CONFIG=str(settings().source_dir / 'ansible' / 'ansible.cfg'))
            variables = {'ansible_user': context.ansible_credential.username}
            if context.ansible_credential.type == 'ssh':
                if secret.get('known_hosts'):
                    known_hosts = workspace / 'known_hosts'
                    known_hosts.write_text(secret['known_hosts'])
                    os.chmod(known_hosts, 0o600)
                    env['ANSIBLE_HOST_KEY_CHECKING'] = 'True'
                    variables['ansible_ssh_common_args'] = '-o StrictHostKeyChecking=yes -o UserKnownHostsFile=' + str(known_hosts)
                else:
                    env['ANSIBLE_HOST_KEY_CHECKING'] = 'False'
                    variables['ansible_ssh_common_args'] = '-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null'
                if 'private_key' in secret:
                    key = workspace / 'id_key'
                    key.write_text(secret['private_key'])
                    os.chmod(key, 0o600)
                    variables['ansible_ssh_private_key_file'] = str(key)
                if 'password' in secret:
                    variables['ansible_password'] = secret['password']
            elif context.ansible_credential.type == 'winrm':
                variables.update(ansible_connection='winrm', ansible_port=5986, ansible_winrm_scheme='https',
                                 ansible_winrm_transport='ntlm', ansible_winrm_server_cert_validation='validate',
                                 ansible_password=secret['password'])
            else:
                raise ExecutionFailed('Invalid Ansible credential type')
            variables.update(spec.variables)
            inventory = {'all': {'hosts': {host: {} for host in spec.inventory.hosts}}}
            inventory_path = workspace / 'inventory.json'
            inventory_path.write_text(json.dumps(inventory))
            os.chmod(inventory_path, 0o600)
            variables_path = workspace / 'variables.yml'
            # Values from credentials are data, never Jinja expressions/lookup plugins.
            variables_path.write_text(yaml.dump({key: UnsafeString(v) if isinstance(v, str) else v for key, v in variables.items()}, Dumper=InventoryDumper))
            os.chmod(variables_path, 0o600)
            sequence = [definition.get('wait'), filename, definition.get('validate')]
            for index, playbook in enumerate(item for item in sequence if item):
                context.stage(('ansible.wait_for_connection' if index == 0 and definition.get('wait') else 'ansible.execution') + ':' + playbook)
                playbook_path = (
                    custom_playbook_path
                    if definition.get('custom') and playbook == filename
                    else settings().source_dir / 'ansible' / 'playbooks' / playbook
                )
                run_process(['ansible-playbook', '-i', str(workspace / 'inventory.json'),
                             str(playbook_path),
                             '--extra-vars', '@' + str(workspace / 'variables.yml')], workspace, env, context, secret.values())
