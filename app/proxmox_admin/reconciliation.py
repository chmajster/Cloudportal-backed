from __future__ import annotations


def vm_config_values(params):
    mapping = {
        'cores': 'cores',
        'sockets': 'sockets',
        'memory_mb': 'memory',
        'balloon_mb': 'balloon',
        'tags': 'tags',
        'description': 'description',
        'boot': 'boot',
        'onboot': 'onboot',
        'protection': 'protection',
        'agent': 'agent',
    }
    values = {}
    for key, target in mapping.items():
        if key not in params or params[key] is None:
            continue
        value = params[key]
        if isinstance(value, bool):
            value = int(value)
        values[target] = value
    return values


def lxc_config_values(params):
    mapping = {
        'cores': 'cores',
        'memory_mb': 'memory',
        'swap_mb': 'swap',
        'tags': 'tags',
        'description': 'description',
        'onboot': 'onboot',
        'protection': 'protection',
    }
    values = {}
    for key, target in mapping.items():
        if key not in params or params[key] is None:
            continue
        value = params[key]
        if isinstance(value, bool):
            value = int(value)
        values[target] = value
    return values


def find_native(adapter, object_type, object_id):
    wanted = 'qemu' if object_type == 'vm' else 'lxc'
    for row in adapter.cluster_resources('vm'):
        if row.get('type') == wanted and int(row.get('vmid', -1)) == int(object_id):
            return dict(row)
    return None


def _config_contains(actual, expected):
    for key, value in expected.items():
        if value is None:
            continue
        actual_value = actual.get(key)
        if isinstance(value, bool):
            if int(actual_value or 0) != int(value):
                return False
        elif str(actual_value) != str(value):
            return False
    return True


def reconcile(adapter, meta, params):
    """Read live Proxmox state and prove the requested post-condition.

    The caller may mark a job successful only when this function returns True.
    """
    command = meta['command']
    node = meta.get('node')
    object_id = meta.get('object_id')

    if meta['object_type'] in {'vm', 'container'}:
        native = find_native(adapter, meta['object_type'], object_id)
        if command in {'vm.delete', 'lxc.delete'}:
            return native is None, {'resource': native}
        if native is None:
            return False, {'resource': None}

        current_node = native.get('node') or node
        if command in {'vm.migrate', 'lxc.migrate'}:
            return str(current_node) == str(params['target']), {'resource': native}

        if command in {'vm.clone', 'lxc.clone'}:
            clone = find_native(adapter, meta['object_type'], params['new_vmid'])
            return clone is not None, {'resource': native, 'clone': clone}

        if command == 'vm.template':
            return bool(native.get('template')), {'resource': native}

        if command.endswith('snapshot.create') or command.endswith('snapshot.delete'):
            snapshots = (
                adapter.snapshots(current_node, int(object_id))
                if meta['object_type'] == 'vm'
                else adapter.lxc_snapshots(current_node, int(object_id))
            )
            names = {str(row.get('name')) for row in snapshots}
            expected = params['name'] in names if command.endswith('create') else params['name'] not in names
            return expected, {'resource': native, 'snapshots': snapshots}

        if command.endswith('snapshot.rollback'):
            state = (
                adapter.vm_status(current_node, int(object_id))
                if meta['object_type'] == 'vm'
                else adapter.lxc_status(current_node, int(object_id))
            )
            return bool(state), {'resource': native, 'status': state}

        if command == 'vm.power':
            status = adapter.vm_status(current_node, int(object_id)) or {}
            expected = {
                'start': {'running'}, 'shutdown': {'stopped'}, 'stop': {'stopped'},
                'reboot': {'running'}, 'reset': {'running'},
                'suspend': {'paused', 'suspended'}, 'resume': {'running'},
            }[params['action']]
            return str(status.get('status') or '').lower() in expected, {'resource': native, 'status': status}

        if command == 'lxc.power':
            status = adapter.lxc_status(current_node, int(object_id)) or {}
            expected = {
                'start': {'running'}, 'shutdown': {'stopped'}, 'stop': {'stopped'},
                'reboot': {'running'}, 'suspend': {'paused', 'suspended'}, 'resume': {'running'},
            }[params['action']]
            return str(status.get('status') or '').lower() in expected, {'resource': native, 'status': status}

        if command == 'vm.config':
            config = adapter.vm_config(current_node, int(object_id)) or {}
            expected = vm_config_values(params)
            return _config_contains(config, expected), {'resource': native, 'config': config}

        if command == 'vm.cloudinit':
            config = adapter.vm_config(current_node, int(object_id)) or {}
            expected = {key: value for key, value in params.items() if value is not None}
            return _config_contains(config, expected), {'resource': native, 'config': config}

        if command == 'lxc.config':
            config = adapter.lxc_config(current_node, int(object_id)) or {}
            expected = lxc_config_values(params)
            return _config_contains(config, expected), {'resource': native, 'config': config}

        if command in {'vm.disk.resize', 'vm.disk.add'}:
            config = adapter.vm_config(current_node, int(object_id)) or {}
            return params['disk'] in config, {'resource': native, 'config': config}

        if command == 'vm.disk.remove':
            config = adapter.vm_config(current_node, int(object_id)) or {}
            return params['disk'] not in config, {'resource': native, 'config': config}

        if command == 'vm.disk.move':
            config = adapter.vm_config(current_node, int(object_id)) or {}
            return str(config.get(params['disk']) or '').startswith(params['storage'] + ':'), {
                'resource': native, 'config': config,
            }

        if command == 'vm.nic.set':
            config = adapter.vm_config(current_node, int(object_id)) or {}
            value = str(config.get(params['nic']) or '')
            return params['bridge'] in value, {'resource': native, 'config': config}

        if command == 'vm.nic.remove':
            config = adapter.vm_config(current_node, int(object_id)) or {}
            return params['nic'] not in config, {'resource': native, 'config': config}

        if command == 'backup.run':
            backups = adapter.backups(current_node, params['storage'], int(object_id))
            return bool(backups), {'resource': native, 'backups': backups}

        return True, {'resource': native}

    if command == 'backup.restore':
        restored = find_native(adapter, 'vm', params['vmid'])
        return restored is not None, {'resource': restored}

    if command == 'backup.delete':
        rows = adapter.storage_content(params['node'], params['storage'], content='backup')
        exists = any(str(row.get('volid')) == str(params['volume']) for row in rows)
        return not exists, {'backups': rows}

    if command == 'node.service':
        services = adapter.node_services(node)
        service = next((row for row in services if str(row.get('service')) == str(object_id)), None)
        action = params['action']
        if action == 'stop':
            ok = service is not None and str(service.get('state') or '').lower() not in {'running', 'active'}
        else:
            ok = service is not None and str(service.get('state') or '').lower() in {'running', 'active'}
        return ok, {'service': service}

    if command.startswith('firewall.rule.'):
        level = params.get('_level')
        snapshot = adapter.firewall_snapshot(
            level, node=params.get('_node'), object_id=params.get('_object_id')
        )
        if command == 'firewall.rule.delete':
            pos = int(params['pos'])
            rules = list(snapshot.get('rules') or [])
            return all(int(rule.get('pos', -1)) != pos for rule in rules), {'firewall': snapshot}
        return bool(snapshot.get('rules') is not None), {'firewall': snapshot}

    return False, {}
