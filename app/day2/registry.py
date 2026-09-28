from dataclasses import dataclass

from app.day2.errors import failure


EMPTY_SCHEMA = {'type': 'object', 'properties': {}, 'additionalProperties': False}


def object_schema(properties: dict, required=()):
    return {
        'type': 'object',
        'properties': properties,
        'required': list(required),
        'additionalProperties': False,
    }


@dataclass(frozen=True, slots=True)
class ActionDefinition:
    id: str
    label: str
    category: str
    description: str
    permission: str
    schema: dict
    resource_types: frozenset[str] = frozenset({'vm'})
    destructive: bool = False
    requires_confirmation: bool = False
    supports_cancel: bool = False
    supported_states: frozenset[str] | None = None
    mutates_configuration: bool = False
    approval_default: bool = False

    def public(self):
        return {
            'id': self.id,
            'label': self.label,
            'category': self.category,
            'description': self.description,
            'required_permission': self.permission,
            'destructive': self.destructive,
            'requires_confirmation': self.requires_confirmation,
            'supports_cancel': self.supports_cancel,
            'parameter_schema': self.schema,
        }


TEXT = {'type': 'string', 'minLength': 1, 'maxLength': 253}
SLUG = {'type': 'string', 'pattern': r'^[A-Za-z0-9_.-]+$', 'minLength': 1, 'maxLength': 63}
POSITIVE_INT = {'type': 'integer', 'minimum': 1}


ACTIONS = (
    ActionDefinition('power_on', 'Uruchom', 'power', 'Uruchamia zatrzymaną maszynę.', 'machines.power.on', EMPTY_SCHEMA,
                     supported_states=frozenset({'stopped'})),
    ActionDefinition('power_off', 'Wymuś wyłączenie', 'power', 'Wykonuje wymuszone zatrzymanie VM.', 'machines.power.hard_stop', EMPTY_SCHEMA,
                     destructive=True, supported_states=frozenset({'running', 'paused'})),
    ActionDefinition('shutdown', 'Wyłącz system', 'power', 'Wysyła kontrolowane wyłączenie systemu.', 'machines.power.off', EMPTY_SCHEMA,
                     supported_states=frozenset({'running'})),
    ActionDefinition('reboot', 'Restart', 'power', 'Wykonuje kontrolowany restart VM.', 'machines.power.reset', EMPTY_SCHEMA,
                     supported_states=frozenset({'running'})),
    ActionDefinition('reset', 'Twardy reset', 'power', 'Odpowiednik sprzętowego resetu VM.', 'machines.power.reset', EMPTY_SCHEMA,
                     destructive=True, supported_states=frozenset({'running'})),
    ActionDefinition('suspend', 'Wstrzymaj', 'power', 'Wstrzymuje działanie VM.', 'machines.power.suspend', EMPTY_SCHEMA,
                     supported_states=frozenset({'running'})),
    ActionDefinition('resume', 'Wznów', 'power', 'Wznawia wstrzymaną VM.', 'machines.power.resume', EMPTY_SCHEMA,
                     supported_states=frozenset({'paused', 'suspended'})),
    ActionDefinition(
        'create_snapshot', 'Utwórz snapshot', 'snapshots', 'Tworzy snapshot VM.', 'machines.snapshot.create',
        object_schema({
            'name': SLUG,
            'description': {'type': 'string', 'maxLength': 1000},
            'include_memory': {'type': 'boolean', 'default': False},
            'quiesce': {'type': 'boolean', 'default': False},
        }, ('name',)), supports_cancel=True,
    ),
    ActionDefinition(
        'delete_snapshot', 'Usuń snapshot', 'snapshots', 'Usuwa wskazany snapshot.', 'machines.snapshot.delete',
        object_schema({'name': SLUG, 'confirmation': TEXT}, ('name', 'confirmation')),
        destructive=True, requires_confirmation=True, approval_default=True, supports_cancel=True,
    ),
    ActionDefinition(
        'restore_snapshot', 'Przywróć snapshot', 'snapshots', 'Przywraca VM do wskazanego snapshotu.', 'machines.snapshot.restore',
        object_schema({'name': SLUG, 'confirmation': TEXT}, ('name', 'confirmation')),
        destructive=True, requires_confirmation=True, approval_default=True, supports_cancel=True,
    ),
    ActionDefinition(
        'resize_compute', 'Zmień CPU / RAM', 'compute', 'Zmienia zasoby obliczeniowe VM.', 'machines.compute.resize',
        object_schema({
            'cpu_cores': {'type': 'integer', 'minimum': 1, 'maximum': 128},
            'cpu_sockets': {'type': 'integer', 'minimum': 1, 'maximum': 8},
            'memory_mb': {'type': 'integer', 'minimum': 512, 'maximum': 1048576},
        }), mutates_configuration=True, supports_cancel=True,
    ),
    ActionDefinition(
        'add_disk', 'Dodaj dysk', 'storage', 'Dodaje dysk do VM.', 'machines.disk.add',
        object_schema({
            'device': {'type': 'string', 'pattern': r'^(?:scsi|virtio|sata|ide)\d{1,2}$'},
            'size_gib': {'type': 'integer', 'minimum': 1, 'maximum': 65536},
            'storage': SLUG,
            'format': {'type': 'string', 'enum': ['raw', 'qcow2']},
            'cache': {'type': 'string', 'enum': ['none', 'writethrough', 'writeback', 'unsafe', 'directsync']},
            'discard': {'type': 'boolean'},
            'ssd_emulation': {'type': 'boolean'},
        }, ('device', 'size_gib', 'storage')), mutates_configuration=True, supports_cancel=True,
    ),
    ActionDefinition(
        'resize_disk', 'Powiększ dysk', 'storage', 'Powiększa istniejący dysk; zmniejszanie jest zabronione.', 'machines.disk.resize',
        object_schema({
            'device': {'type': 'string', 'pattern': r'^(?:scsi|virtio|sata|ide)\d{1,2}$'},
            'new_size_gib': {'type': 'integer', 'minimum': 1, 'maximum': 65536},
        }, ('device', 'new_size_gib')), mutates_configuration=True, supports_cancel=True,
    ),
    ActionDefinition(
        'detach_disk', 'Odłącz dysk', 'storage', 'Odłącza dysk bez deklarowania jego usunięcia.', 'machines.disk.delete',
        object_schema({'device': {'type': 'string', 'pattern': r'^(?:scsi|virtio|sata|ide)\d{1,2}$'}}, ('device',)),
        destructive=True, mutates_configuration=True,
    ),
    ActionDefinition(
        'delete_disk', 'Usuń dysk', 'storage', 'Trwale usuwa wskazany odłączony dysk.', 'day2.disk.delete',
        object_schema({'device': {'type': 'string', 'pattern': r'^unused\d{1,2}$'}, 'confirmation': TEXT}, ('device', 'confirmation')),
        destructive=True, requires_confirmation=True, approval_default=True, mutates_configuration=True, supports_cancel=True,
    ),
    ActionDefinition(
        'add_nic', 'Dodaj interfejs', 'network', 'Dodaje interfejs sieciowy.', 'machines.network.add',
        object_schema({
            'device': {'type': 'string', 'pattern': r'^net\d{1,2}$'},
            'bridge': SLUG,
            'vlan': {'type': ['integer', 'null'], 'minimum': 1, 'maximum': 4094},
            'model': {'type': 'string', 'enum': ['virtio', 'e1000', 'e1000e', 'rtl8139', 'vmxnet3']},
            'mac': {'type': ['string', 'null'], 'pattern': r'^[0-9A-Fa-f:]{17}$'},
            'firewall': {'type': 'boolean'},
            'link_up': {'type': 'boolean'},
        }, ('device', 'bridge')), mutates_configuration=True,
    ),
    ActionDefinition(
        'edit_nic', 'Edytuj interfejs', 'network', 'Aktualizuje konfigurację interfejsu.', 'machines.network.update',
        object_schema({
            'device': {'type': 'string', 'pattern': r'^net\d{1,2}$'},
            'bridge': SLUG,
            'vlan': {'type': ['integer', 'null'], 'minimum': 1, 'maximum': 4094},
            'model': {'type': 'string', 'enum': ['virtio', 'e1000', 'e1000e', 'rtl8139', 'vmxnet3']},
            'mac': {'type': ['string', 'null'], 'pattern': r'^[0-9A-Fa-f:]{17}$'},
            'firewall': {'type': 'boolean'},
            'link_up': {'type': 'boolean'},
        }, ('device', 'bridge')), mutates_configuration=True,
    ),
    ActionDefinition(
        'detach_nic', 'Odłącz interfejs', 'network', 'Usuwa konfigurację wskazanego NIC z VM.', 'machines.network.delete',
        object_schema({'device': {'type': 'string', 'pattern': r'^net\d{1,2}$'}}, ('device',)),
        destructive=True, mutates_configuration=True,
    ),
    ActionDefinition(
        'update_cloud_init', 'Aktualizuj cloud-init', 'automation', 'Aktualizuje wybrane pola cloud-init.', 'machines.cloud_init.update',
        object_schema({
            'hostname': TEXT,
            'domain': TEXT,
            'dns_servers': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 8},
            'search_domain': TEXT,
            'ipv4': TEXT,
            'ipv6': TEXT,
            'gateway': TEXT,
            'ssh_public_keys': {'type': 'array', 'items': {'type': 'string', 'maxLength': 8192}, 'maxItems': 20},
            'user': SLUG,
        }), mutates_configuration=True,
    ),
    ActionDefinition(
        'update_credentials', 'Aktualizuj credentials', 'automation', 'Wykonuje kontrolowaną operację na koncie gościa przez zapisany credential.', 'machines.credentials.inject',
        object_schema({
            'credential_id': POSITIVE_INT,
            'operation': {'type': 'string', 'enum': ['create_user', 'update_ssh_key', 'rotate_password', 'disable_user', 'remove_user']},
            'username': SLUG,
        }, ('credential_id', 'operation', 'username')), supports_cancel=True,
    ),
    ActionDefinition(
        'run_ansible', 'Uruchom Ansible', 'automation', 'Uruchamia zatwierdzony playbook na bieżącej VM.', 'ansible.execute',
        object_schema({
            'playbook': SLUG,
            'credential_id': POSITIVE_INT,
            'variables': {'type': 'object'},
            'timeout': {'type': 'integer', 'minimum': 1, 'maximum': 86400},
            'tags': {'type': 'array', 'items': SLUG, 'maxItems': 50},
            'skip_tags': {'type': 'array', 'items': SLUG, 'maxItems': 50},
        }, ('playbook', 'credential_id')), supports_cancel=True,
    ),
    ActionDefinition('install_package', 'Zainstaluj pakiet', 'automation', 'Instaluje pakiet przez zatwierdzony automation executor.', 'day2.package.manage',
                     object_schema({'package': SLUG, 'credential_id': POSITIVE_INT}, ('package', 'credential_id')), supports_cancel=True),
    ActionDefinition('remove_package', 'Usuń pakiet', 'automation', 'Usuwa pakiet przez zatwierdzony automation executor.', 'day2.package.manage',
                     object_schema({'package': SLUG, 'credential_id': POSITIVE_INT}, ('package', 'credential_id')), destructive=True, supports_cancel=True),
    ActionDefinition('update_packages', 'Aktualizuj pakiety', 'automation', 'Aktualizuje pakiety przez zatwierdzony automation executor.', 'day2.package.manage',
                     object_schema({'credential_id': POSITIVE_INT}, ('credential_id',)), supports_cancel=True),
    ActionDefinition('patch_system', 'Patch system', 'automation', 'Uruchamia zatwierdzony proces patchowania.', 'day2.package.manage',
                     object_schema({'credential_id': POSITIVE_INT}, ('credential_id',)), supports_cancel=True),
    ActionDefinition('update_tags', 'Zmień tagi', 'metadata', 'Aktualizuje logiczne i providerowe tagi.', 'day2.tags.manage',
                     object_schema({'tags': {'type': 'array', 'items': SLUG, 'maxItems': 50}}, ('tags',)), mutates_configuration=True),
    ActionDefinition('add_tag', 'Dodaj tag', 'metadata', 'Dodaje tag.', 'day2.tags.manage',
                     object_schema({'tag': SLUG}, ('tag',)), mutates_configuration=True),
    ActionDefinition('remove_tag', 'Usuń tag', 'metadata', 'Usuwa tag.', 'day2.tags.manage',
                     object_schema({'tag': SLUG}, ('tag',)), mutates_configuration=True),
    ActionDefinition('update_metadata', 'Zmień metadata', 'metadata', 'Aktualizuje metadata utrzymywane przez Cloudportal.', 'machines.metadata.update',
                     object_schema({'metadata': {'type': 'object'}}, ('metadata',))),
    ActionDefinition(
        'apply_availability', 'Zastosuj Availability Plan', 'availability',
        'Przypisuje VM do konfiguracji Proxmox HA z wybranego Availability Planu.',
        'availability.assign',
        object_schema({
            'plan_id': {'type': 'string', 'minLength': 36, 'maxLength': 36},
            'state': {'type': 'string', 'enum': ['started', 'stopped', 'ignored', 'disabled']},
            'group': {'type': ['string', 'null'], 'maxLength': 63},
            'max_restart': {'type': 'integer', 'minimum': 0, 'maximum': 100},
            'max_relocate': {'type': 'integer', 'minimum': 0, 'maximum': 100},
        }, ('plan_id', 'state', 'max_restart', 'max_relocate')),
    ),
    ActionDefinition(
        'migrate_vm', 'Migruj VM', 'lifecycle', 'Migruje VM do wybranego węzła.', 'machines.migrate',
        object_schema({'target_node': SLUG, 'online': {'type': 'boolean'}, 'with_local_disks': {'type': 'boolean'}}, ('target_node',)),
        destructive=True, mutates_configuration=True, supports_cancel=True,
    ),
    ActionDefinition(
        'move_storage', 'Przenieś dysk', 'storage', 'Przenosi wolumen do innego storage.', 'machines.disk.migrate',
        object_schema({'device': {'type': 'string', 'pattern': r'^(?:scsi|virtio|sata|ide)\d{1,2}$'}, 'target_storage': SLUG, 'delete_source': {'type': 'boolean'}}, ('device', 'target_storage')),
        mutates_configuration=True, supports_cancel=True,
    ),
    ActionDefinition(
        'clone_vm', 'Klonuj VM', 'lifecycle', 'Tworzy klon istniejącej VM.', 'machines.clone',
        object_schema({
            'new_vm_id': {'type': 'integer', 'minimum': 100, 'maximum': 999999999},
            'name': TEXT,
            'target_node': SLUG,
            'target_storage': SLUG,
            'full': {'type': 'boolean', 'default': True},
        }, ('new_vm_id', 'name')), supports_cancel=True,
    ),
    ActionDefinition(
        'rebuild_vm', 'Przebuduj VM', 'lifecycle', 'Odtwarza VM z zachowanego deploymentu/blueprintu.', 'machines.rebuild',
        object_schema({
            'preserve_disks': {'type': 'boolean'},
            'preserve_metadata': {'type': 'boolean'},
            'preserve_network_identity': {'type': 'boolean'},
            'confirmation': TEXT,
        }, ('confirmation',)), destructive=True, requires_confirmation=True, approval_default=True, supports_cancel=True,
    ),
    ActionDefinition(
        'delete_vm', 'Usuń VM', 'lifecycle', 'Usuwa zasób u providera i synchronizuje inventory.', 'machines.delete',
        object_schema({'purge': {'type': 'boolean'}, 'destroy_unreferenced_disks': {'type': 'boolean'}, 'confirmation': TEXT}, ('confirmation',)),
        destructive=True, requires_confirmation=True, approval_default=True, supports_cancel=True,
    ),
    ActionDefinition('refresh_state', 'Odśwież stan', 'lifecycle', 'Pobiera stan rzeczywisty z providera i wykrywa drift.', 'machines.read', EMPTY_SCHEMA),
)

REGISTRY = {action.id: action for action in ACTIONS}


def get_action(action_id: str) -> ActionDefinition:
    action = REGISTRY.get(str(action_id).strip().lower())
    if action is None:
        raise failure('ACTION_NOT_SUPPORTED', status_code=404)
    return action


def all_actions():
    return ACTIONS
