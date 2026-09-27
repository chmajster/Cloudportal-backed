from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from app.api.schemas import Input


NodeName = Annotated[str, Field(pattern=r'^[A-Za-z0-9_.-]{1,63}$')]
VMID = Annotated[int, Field(ge=100, le=999999999)]
SnapshotName = Annotated[str, Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$')]
ConfigKey = Annotated[str, Field(pattern=r'^(?:scsi|sata|ide|virtio|net|mp)\\d+$')]


class ProxmoxAdminSettingsInput(Input):
    enabled: bool


class PowerInput(Input):
    action: Literal['start', 'shutdown', 'stop', 'reboot', 'reset', 'suspend', 'resume']
    confirmation: Annotated[str | None, Field(max_length=100)] = None


class DeleteResourceInput(Input):
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]
    purge: bool = False
    destroy_unreferenced_disks: bool = False


class SnapshotCreateInput(Input):
    name: SnapshotName
    description: Annotated[str, Field(max_length=1000)] = ''
    include_ram: bool = False


class ConfirmationInput(Input):
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class CloneInput(Input):
    new_vmid: VMID
    name: Annotated[str, Field(min_length=1, max_length=100)]
    target: NodeName | None = None
    full: bool = True
    storage: Annotated[str | None, Field(pattern=r'^[A-Za-z0-9_.-]{1,64}$')] = None
    pool: Annotated[str | None, Field(pattern=r'^[A-Za-z0-9_.-]{1,64}$')] = None


class MigrateInput(Input):
    target: NodeName
    online: bool = False
    with_local_disks: bool = False


class VMConfigInput(Input):
    cores: Annotated[int | None, Field(ge=1, le=512)] = None
    sockets: Annotated[int | None, Field(ge=1, le=16)] = None
    memory_mb: Annotated[int | None, Field(ge=16, le=16777216)] = None
    balloon_mb: Annotated[int | None, Field(ge=0, le=16777216)] = None
    tags: Annotated[str | None, Field(max_length=2048)] = None
    description: Annotated[str | None, Field(max_length=8192)] = None
    boot: Annotated[str | None, Field(max_length=1024)] = None
    onboot: bool | None = None
    protection: bool | None = None
    agent: bool | None = None

    @model_validator(mode='after')
    def at_least_one_change(self):
        if all(getattr(self, key) is None for key in (
            'cores', 'sockets', 'memory_mb', 'balloon_mb', 'tags',
            'description', 'boot', 'onboot', 'protection', 'agent',
        )):
            raise ValueError('At least one VM configuration field is required')
        return self


class CloudInitInput(Input):
    ciuser: Annotated[str | None, Field(min_length=1, max_length=64)] = None
    ipconfig0: Annotated[str | None, Field(max_length=512)] = None
    nameserver: Annotated[str | None, Field(max_length=512)] = None
    searchdomain: Annotated[str | None, Field(max_length=253)] = None
    sshkeys: Annotated[str | None, Field(max_length=32768)] = None

    @model_validator(mode='after')
    def at_least_one_change(self):
        if all(getattr(self, key) is None for key in ('ciuser', 'ipconfig0', 'nameserver', 'searchdomain', 'sshkeys')):
            raise ValueError('At least one cloud-init field is required')
        return self


class DiskResizeInput(Input):
    disk: Annotated[str, Field(pattern=r'^(?:scsi|sata|ide|virtio)\\d+$')]
    grow_gib: Annotated[int, Field(ge=1, le=65536)]


class DiskAddInput(Input):
    disk: Annotated[str, Field(pattern=r'^(?:scsi|sata|ide|virtio)\\d+$')]
    storage: Annotated[str, Field(pattern=r'^[A-Za-z0-9_.-]{1,64}$')]
    size_gib: Annotated[int, Field(ge=1, le=65536)]
    discard: bool = True
    ssd: bool = False


class DiskRemoveInput(Input):
    disk: Annotated[str, Field(pattern=r'^(?:scsi|sata|ide|virtio)\\d+$')]
    delete_volume: bool = False
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class DiskMoveInput(Input):
    disk: Annotated[str, Field(pattern=r'^(?:scsi|sata|ide|virtio)\\d+

class NICInput(Input):
    nic: Annotated[str, Field(pattern=r'^net\\d+$')]
    bridge: Annotated[str, Field(pattern=r'^[A-Za-z0-9_.:-]{1,64}$')]
    model: Literal['virtio', 'e1000', 'e1000e', 'vmxnet3', 'rtl8139'] = 'virtio'
    mac: Annotated[str | None, Field(pattern=r'^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$')] = None
    vlan: Annotated[int | None, Field(ge=1, le=4094)] = None
    firewall: bool = False
    rate_mbps: Annotated[float | None, Field(gt=0, le=1000000)] = None
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class NICRemoveInput(Input):
    nic: Annotated[str, Field(pattern=r'^net\\d+$')]
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class LXCConfigInput(Input):
    cores: Annotated[int | None, Field(ge=1, le=512)] = None
    memory_mb: Annotated[int | None, Field(ge=16, le=16777216)] = None
    swap_mb: Annotated[int | None, Field(ge=0, le=16777216)] = None
    tags: Annotated[str | None, Field(max_length=2048)] = None
    description: Annotated[str | None, Field(max_length=8192)] = None
    onboot: bool | None = None
    protection: bool | None = None

    @model_validator(mode='after')
    def at_least_one_change(self):
        if all(getattr(self, key) is None for key in (
            'cores', 'memory_mb', 'swap_mb', 'tags', 'description', 'onboot', 'protection',
        )):
            raise ValueError('At least one LXC configuration field is required')
        return self


class ISOAttachInput(Input):
    volume: Annotated[str, Field(min_length=1, max_length=2048)]
    drive: Annotated[str, Field(pattern=r'^(?:ide|sata|scsi)\\d+    storage: Annotated[str, Field(pattern=r'^[A-Za-z0-9_.-]{1,64}$')]
    mode: Literal['snapshot', 'suspend', 'stop'] = 'snapshot'
    compress: Literal['0', 'gzip', 'lzo', 'zstd'] = 'zstd'
    notes: Annotated[str | None, Field(max_length=1024)] = None


class BackupRestoreInput(Input):
    node: NodeName
    vmid: VMID
    archive: Annotated[str, Field(min_length=1, max_length=2048)]
    storage: Annotated[str | None, Field(pattern=r'^[A-Za-z0-9_.-]{1,64}$')] = None
    unique: bool = True
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class BackupDeleteInput(Input):
    node: NodeName
    storage: Annotated[str, Field(pattern=r'^[A-Za-z0-9_.-]{1,64}$')]
    volume: Annotated[str, Field(min_length=1, max_length=2048)]
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class NodeServiceInput(Input):
    action: Literal['start', 'stop', 'restart']
    confirmation: Annotated[str | None, Field(max_length=128)] = None


class FirewallRuleInput(Input):
    type: Literal['in', 'out', 'group']
    action: Literal['ACCEPT', 'DROP', 'REJECT'] | None = None
    source: Annotated[str | None, Field(max_length=512)] = None
    dest: Annotated[str | None, Field(max_length=512)] = None
    proto: Annotated[str | None, Field(max_length=32)] = None
    dport: Annotated[str | None, Field(max_length=128)] = None
    sport: Annotated[str | None, Field(max_length=128)] = None
    iface: Annotated[str | None, Field(max_length=64)] = None
    macro: Annotated[str | None, Field(max_length=64)] = None
    log: Annotated[str | None, Field(max_length=32)] = None
    comment: Annotated[str | None, Field(max_length=1024)] = None
    enable: bool = True
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class FirewallRuleUpdateInput(FirewallRuleInput):
    pos: Annotated[int, Field(ge=0, le=100000)]


class FirewallRuleDeleteInput(Input):
    pos: Annotated[int, Field(ge=0, le=100000)]
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class BulkTarget(Input):
    node: NodeName
    vmid: VMID


class BulkInput(Input):
    action: Literal['start', 'shutdown', 'reboot', 'stop', 'add_tag', 'remove_tag', 'snapshot']
    targets: Annotated[list[BulkTarget], Field(min_length=1, max_length=100)]
    tag: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    snapshot: SnapshotName | None = None

    @model_validator(mode='after')
    def action_parameters(self):
        if self.action in {'add_tag', 'remove_tag'} and not self.tag:
            raise ValueError('tag is required for tag bulk actions')
        if self.action == 'snapshot' and not self.snapshot:
            raise ValueError('snapshot is required for snapshot bulk action')
        return self


class SearchQuery(Input):
    query: Annotated[str, Field(min_length=1, max_length=200)]


class RawCommandParameters(Input):
    parameters: dict[str, Any] = Field(default_factory=dict)
)]
    storage: Annotated[str, Field(pattern=r'^[A-Za-z0-9_.-]{1,64}

class NICInput(Input):
    nic: Annotated[str, Field(pattern=r'^net\\d+$')]
    bridge: Annotated[str, Field(pattern=r'^[A-Za-z0-9_.:-]{1,64}$')]
    model: Literal['virtio', 'e1000', 'e1000e', 'vmxnet3', 'rtl8139'] = 'virtio'
    mac: Annotated[str | None, Field(pattern=r'^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$')] = None
    vlan: Annotated[int | None, Field(ge=1, le=4094)] = None
    firewall: bool = False
    rate_mbps: Annotated[float | None, Field(gt=0, le=1000000)] = None


class NICRemoveInput(Input):
    nic: Annotated[str, Field(pattern=r'^net\\d+$')]
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class LXCConfigInput(Input):
    cores: Annotated[int | None, Field(ge=1, le=512)] = None
    memory_mb: Annotated[int | None, Field(ge=16, le=16777216)] = None
    swap_mb: Annotated[int | None, Field(ge=0, le=16777216)] = None
    tags: Annotated[str | None, Field(max_length=2048)] = None
    description: Annotated[str | None, Field(max_length=8192)] = None
    onboot: bool | None = None
    protection: bool | None = None

    @model_validator(mode='after')
    def at_least_one_change(self):
        if all(getattr(self, key) is None for key in (
            'cores', 'memory_mb', 'swap_mb', 'tags', 'description', 'onboot', 'protection',
        )):
            raise ValueError('At least one LXC configuration field is required')
        return self


class BackupRunInput(Input):
    storage: Annotated[str, Field(pattern=r'^[A-Za-z0-9_.-]{1,64}$')]
    mode: Literal['snapshot', 'suspend', 'stop'] = 'snapshot'
    compress: Literal['0', 'gzip', 'lzo', 'zstd'] = 'zstd'
    notes: Annotated[str | None, Field(max_length=1024)] = None


class BackupRestoreInput(Input):
    node: NodeName
    vmid: VMID
    archive: Annotated[str, Field(min_length=1, max_length=2048)]
    storage: Annotated[str | None, Field(pattern=r'^[A-Za-z0-9_.-]{1,64}$')] = None
    unique: bool = True
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class BackupDeleteInput(Input):
    node: NodeName
    storage: Annotated[str, Field(pattern=r'^[A-Za-z0-9_.-]{1,64}$')]
    volume: Annotated[str, Field(min_length=1, max_length=2048)]
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class NodeServiceInput(Input):
    action: Literal['start', 'stop', 'restart']
    confirmation: Annotated[str | None, Field(max_length=128)] = None


class FirewallRuleInput(Input):
    type: Literal['in', 'out', 'group']
    action: Literal['ACCEPT', 'DROP', 'REJECT'] | None = None
    source: Annotated[str | None, Field(max_length=512)] = None
    dest: Annotated[str | None, Field(max_length=512)] = None
    proto: Annotated[str | None, Field(max_length=32)] = None
    dport: Annotated[str | None, Field(max_length=128)] = None
    sport: Annotated[str | None, Field(max_length=128)] = None
    iface: Annotated[str | None, Field(max_length=64)] = None
    macro: Annotated[str | None, Field(max_length=64)] = None
    log: Annotated[str | None, Field(max_length=32)] = None
    comment: Annotated[str | None, Field(max_length=1024)] = None
    enable: bool = True
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class FirewallRuleUpdateInput(FirewallRuleInput):
    pos: Annotated[int, Field(ge=0, le=100000)]


class FirewallRuleDeleteInput(Input):
    pos: Annotated[int, Field(ge=0, le=100000)]
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class BulkTarget(Input):
    node: NodeName
    vmid: VMID


class BulkInput(Input):
    action: Literal['start', 'shutdown', 'reboot', 'stop', 'add_tag', 'remove_tag', 'snapshot']
    targets: Annotated[list[BulkTarget], Field(min_length=1, max_length=100)]
    tag: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    snapshot: SnapshotName | None = None

    @model_validator(mode='after')
    def action_parameters(self):
        if self.action in {'add_tag', 'remove_tag'} and not self.tag:
            raise ValueError('tag is required for tag bulk actions')
        if self.action == 'snapshot' and not self.snapshot:
            raise ValueError('snapshot is required for snapshot bulk action')
        return self


class SearchQuery(Input):
    query: Annotated[str, Field(min_length=1, max_length=200)]


class RawCommandParameters(Input):
    parameters: dict[str, Any] = Field(default_factory=dict)
)]
    delete_source: bool = True
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class NICInput(Input):
    nic: Annotated[str, Field(pattern=r'^net\\d+$')]
    bridge: Annotated[str, Field(pattern=r'^[A-Za-z0-9_.:-]{1,64}$')]
    model: Literal['virtio', 'e1000', 'e1000e', 'vmxnet3', 'rtl8139'] = 'virtio'
    mac: Annotated[str | None, Field(pattern=r'^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$')] = None
    vlan: Annotated[int | None, Field(ge=1, le=4094)] = None
    firewall: bool = False
    rate_mbps: Annotated[float | None, Field(gt=0, le=1000000)] = None


class NICRemoveInput(Input):
    nic: Annotated[str, Field(pattern=r'^net\\d+$')]
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class LXCConfigInput(Input):
    cores: Annotated[int | None, Field(ge=1, le=512)] = None
    memory_mb: Annotated[int | None, Field(ge=16, le=16777216)] = None
    swap_mb: Annotated[int | None, Field(ge=0, le=16777216)] = None
    tags: Annotated[str | None, Field(max_length=2048)] = None
    description: Annotated[str | None, Field(max_length=8192)] = None
    onboot: bool | None = None
    protection: bool | None = None

    @model_validator(mode='after')
    def at_least_one_change(self):
        if all(getattr(self, key) is None for key in (
            'cores', 'memory_mb', 'swap_mb', 'tags', 'description', 'onboot', 'protection',
        )):
            raise ValueError('At least one LXC configuration field is required')
        return self


class BackupRunInput(Input):
    storage: Annotated[str, Field(pattern=r'^[A-Za-z0-9_.-]{1,64}$')]
    mode: Literal['snapshot', 'suspend', 'stop'] = 'snapshot'
    compress: Literal['0', 'gzip', 'lzo', 'zstd'] = 'zstd'
    notes: Annotated[str | None, Field(max_length=1024)] = None


class BackupRestoreInput(Input):
    node: NodeName
    vmid: VMID
    archive: Annotated[str, Field(min_length=1, max_length=2048)]
    storage: Annotated[str | None, Field(pattern=r'^[A-Za-z0-9_.-]{1,64}$')] = None
    unique: bool = True
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class BackupDeleteInput(Input):
    node: NodeName
    storage: Annotated[str, Field(pattern=r'^[A-Za-z0-9_.-]{1,64}$')]
    volume: Annotated[str, Field(min_length=1, max_length=2048)]
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class NodeServiceInput(Input):
    action: Literal['start', 'stop', 'restart']
    confirmation: Annotated[str | None, Field(max_length=128)] = None


class FirewallRuleInput(Input):
    type: Literal['in', 'out', 'group']
    action: Literal['ACCEPT', 'DROP', 'REJECT'] | None = None
    source: Annotated[str | None, Field(max_length=512)] = None
    dest: Annotated[str | None, Field(max_length=512)] = None
    proto: Annotated[str | None, Field(max_length=32)] = None
    dport: Annotated[str | None, Field(max_length=128)] = None
    sport: Annotated[str | None, Field(max_length=128)] = None
    iface: Annotated[str | None, Field(max_length=64)] = None
    macro: Annotated[str | None, Field(max_length=64)] = None
    log: Annotated[str | None, Field(max_length=32)] = None
    comment: Annotated[str | None, Field(max_length=1024)] = None
    enable: bool = True
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class FirewallRuleUpdateInput(FirewallRuleInput):
    pos: Annotated[int, Field(ge=0, le=100000)]


class FirewallRuleDeleteInput(Input):
    pos: Annotated[int, Field(ge=0, le=100000)]
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class BulkTarget(Input):
    node: NodeName
    vmid: VMID


class BulkInput(Input):
    action: Literal['start', 'shutdown', 'reboot', 'stop', 'add_tag', 'remove_tag', 'snapshot']
    targets: Annotated[list[BulkTarget], Field(min_length=1, max_length=100)]
    tag: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    snapshot: SnapshotName | None = None

    @model_validator(mode='after')
    def action_parameters(self):
        if self.action in {'add_tag', 'remove_tag'} and not self.tag:
            raise ValueError('tag is required for tag bulk actions')
        if self.action == 'snapshot' and not self.snapshot:
            raise ValueError('snapshot is required for snapshot bulk action')
        return self


class SearchQuery(Input):
    query: Annotated[str, Field(min_length=1, max_length=200)]


class RawCommandParameters(Input):
    parameters: dict[str, Any] = Field(default_factory=dict)
)] = 'ide2'


class ISODeleteInput(Input):
    node: NodeName
    storage: Annotated[str, Field(pattern=r'^[A-Za-z0-9_.-]{1,64}    storage: Annotated[str, Field(pattern=r'^[A-Za-z0-9_.-]{1,64}$')]
    mode: Literal['snapshot', 'suspend', 'stop'] = 'snapshot'
    compress: Literal['0', 'gzip', 'lzo', 'zstd'] = 'zstd'
    notes: Annotated[str | None, Field(max_length=1024)] = None


class BackupRestoreInput(Input):
    node: NodeName
    vmid: VMID
    archive: Annotated[str, Field(min_length=1, max_length=2048)]
    storage: Annotated[str | None, Field(pattern=r'^[A-Za-z0-9_.-]{1,64}$')] = None
    unique: bool = True
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class BackupDeleteInput(Input):
    node: NodeName
    storage: Annotated[str, Field(pattern=r'^[A-Za-z0-9_.-]{1,64}$')]
    volume: Annotated[str, Field(min_length=1, max_length=2048)]
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class NodeServiceInput(Input):
    action: Literal['start', 'stop', 'restart']
    confirmation: Annotated[str | None, Field(max_length=128)] = None


class FirewallRuleInput(Input):
    type: Literal['in', 'out', 'group']
    action: Literal['ACCEPT', 'DROP', 'REJECT'] | None = None
    source: Annotated[str | None, Field(max_length=512)] = None
    dest: Annotated[str | None, Field(max_length=512)] = None
    proto: Annotated[str | None, Field(max_length=32)] = None
    dport: Annotated[str | None, Field(max_length=128)] = None
    sport: Annotated[str | None, Field(max_length=128)] = None
    iface: Annotated[str | None, Field(max_length=64)] = None
    macro: Annotated[str | None, Field(max_length=64)] = None
    log: Annotated[str | None, Field(max_length=32)] = None
    comment: Annotated[str | None, Field(max_length=1024)] = None
    enable: bool = True
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class FirewallRuleUpdateInput(FirewallRuleInput):
    pos: Annotated[int, Field(ge=0, le=100000)]


class FirewallRuleDeleteInput(Input):
    pos: Annotated[int, Field(ge=0, le=100000)]
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class BulkTarget(Input):
    node: NodeName
    vmid: VMID


class BulkInput(Input):
    action: Literal['start', 'shutdown', 'reboot', 'stop', 'add_tag', 'remove_tag', 'snapshot']
    targets: Annotated[list[BulkTarget], Field(min_length=1, max_length=100)]
    tag: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    snapshot: SnapshotName | None = None

    @model_validator(mode='after')
    def action_parameters(self):
        if self.action in {'add_tag', 'remove_tag'} and not self.tag:
            raise ValueError('tag is required for tag bulk actions')
        if self.action == 'snapshot' and not self.snapshot:
            raise ValueError('snapshot is required for snapshot bulk action')
        return self


class SearchQuery(Input):
    query: Annotated[str, Field(min_length=1, max_length=200)]


class RawCommandParameters(Input):
    parameters: dict[str, Any] = Field(default_factory=dict)
)]
    storage: Annotated[str, Field(pattern=r'^[A-Za-z0-9_.-]{1,64}

class NICInput(Input):
    nic: Annotated[str, Field(pattern=r'^net\\d+$')]
    bridge: Annotated[str, Field(pattern=r'^[A-Za-z0-9_.:-]{1,64}$')]
    model: Literal['virtio', 'e1000', 'e1000e', 'vmxnet3', 'rtl8139'] = 'virtio'
    mac: Annotated[str | None, Field(pattern=r'^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$')] = None
    vlan: Annotated[int | None, Field(ge=1, le=4094)] = None
    firewall: bool = False
    rate_mbps: Annotated[float | None, Field(gt=0, le=1000000)] = None


class NICRemoveInput(Input):
    nic: Annotated[str, Field(pattern=r'^net\\d+$')]
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class LXCConfigInput(Input):
    cores: Annotated[int | None, Field(ge=1, le=512)] = None
    memory_mb: Annotated[int | None, Field(ge=16, le=16777216)] = None
    swap_mb: Annotated[int | None, Field(ge=0, le=16777216)] = None
    tags: Annotated[str | None, Field(max_length=2048)] = None
    description: Annotated[str | None, Field(max_length=8192)] = None
    onboot: bool | None = None
    protection: bool | None = None

    @model_validator(mode='after')
    def at_least_one_change(self):
        if all(getattr(self, key) is None for key in (
            'cores', 'memory_mb', 'swap_mb', 'tags', 'description', 'onboot', 'protection',
        )):
            raise ValueError('At least one LXC configuration field is required')
        return self


class BackupRunInput(Input):
    storage: Annotated[str, Field(pattern=r'^[A-Za-z0-9_.-]{1,64}$')]
    mode: Literal['snapshot', 'suspend', 'stop'] = 'snapshot'
    compress: Literal['0', 'gzip', 'lzo', 'zstd'] = 'zstd'
    notes: Annotated[str | None, Field(max_length=1024)] = None


class BackupRestoreInput(Input):
    node: NodeName
    vmid: VMID
    archive: Annotated[str, Field(min_length=1, max_length=2048)]
    storage: Annotated[str | None, Field(pattern=r'^[A-Za-z0-9_.-]{1,64}$')] = None
    unique: bool = True
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class BackupDeleteInput(Input):
    node: NodeName
    storage: Annotated[str, Field(pattern=r'^[A-Za-z0-9_.-]{1,64}$')]
    volume: Annotated[str, Field(min_length=1, max_length=2048)]
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class NodeServiceInput(Input):
    action: Literal['start', 'stop', 'restart']
    confirmation: Annotated[str | None, Field(max_length=128)] = None


class FirewallRuleInput(Input):
    type: Literal['in', 'out', 'group']
    action: Literal['ACCEPT', 'DROP', 'REJECT'] | None = None
    source: Annotated[str | None, Field(max_length=512)] = None
    dest: Annotated[str | None, Field(max_length=512)] = None
    proto: Annotated[str | None, Field(max_length=32)] = None
    dport: Annotated[str | None, Field(max_length=128)] = None
    sport: Annotated[str | None, Field(max_length=128)] = None
    iface: Annotated[str | None, Field(max_length=64)] = None
    macro: Annotated[str | None, Field(max_length=64)] = None
    log: Annotated[str | None, Field(max_length=32)] = None
    comment: Annotated[str | None, Field(max_length=1024)] = None
    enable: bool = True
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class FirewallRuleUpdateInput(FirewallRuleInput):
    pos: Annotated[int, Field(ge=0, le=100000)]


class FirewallRuleDeleteInput(Input):
    pos: Annotated[int, Field(ge=0, le=100000)]
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class BulkTarget(Input):
    node: NodeName
    vmid: VMID


class BulkInput(Input):
    action: Literal['start', 'shutdown', 'reboot', 'stop', 'add_tag', 'remove_tag', 'snapshot']
    targets: Annotated[list[BulkTarget], Field(min_length=1, max_length=100)]
    tag: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    snapshot: SnapshotName | None = None

    @model_validator(mode='after')
    def action_parameters(self):
        if self.action in {'add_tag', 'remove_tag'} and not self.tag:
            raise ValueError('tag is required for tag bulk actions')
        if self.action == 'snapshot' and not self.snapshot:
            raise ValueError('snapshot is required for snapshot bulk action')
        return self


class SearchQuery(Input):
    query: Annotated[str, Field(min_length=1, max_length=200)]


class RawCommandParameters(Input):
    parameters: dict[str, Any] = Field(default_factory=dict)
)]
    delete_source: bool = True
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class NICInput(Input):
    nic: Annotated[str, Field(pattern=r'^net\\d+$')]
    bridge: Annotated[str, Field(pattern=r'^[A-Za-z0-9_.:-]{1,64}$')]
    model: Literal['virtio', 'e1000', 'e1000e', 'vmxnet3', 'rtl8139'] = 'virtio'
    mac: Annotated[str | None, Field(pattern=r'^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$')] = None
    vlan: Annotated[int | None, Field(ge=1, le=4094)] = None
    firewall: bool = False
    rate_mbps: Annotated[float | None, Field(gt=0, le=1000000)] = None


class NICRemoveInput(Input):
    nic: Annotated[str, Field(pattern=r'^net\\d+$')]
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class LXCConfigInput(Input):
    cores: Annotated[int | None, Field(ge=1, le=512)] = None
    memory_mb: Annotated[int | None, Field(ge=16, le=16777216)] = None
    swap_mb: Annotated[int | None, Field(ge=0, le=16777216)] = None
    tags: Annotated[str | None, Field(max_length=2048)] = None
    description: Annotated[str | None, Field(max_length=8192)] = None
    onboot: bool | None = None
    protection: bool | None = None

    @model_validator(mode='after')
    def at_least_one_change(self):
        if all(getattr(self, key) is None for key in (
            'cores', 'memory_mb', 'swap_mb', 'tags', 'description', 'onboot', 'protection',
        )):
            raise ValueError('At least one LXC configuration field is required')
        return self


class BackupRunInput(Input):
    storage: Annotated[str, Field(pattern=r'^[A-Za-z0-9_.-]{1,64}$')]
    mode: Literal['snapshot', 'suspend', 'stop'] = 'snapshot'
    compress: Literal['0', 'gzip', 'lzo', 'zstd'] = 'zstd'
    notes: Annotated[str | None, Field(max_length=1024)] = None


class BackupRestoreInput(Input):
    node: NodeName
    vmid: VMID
    archive: Annotated[str, Field(min_length=1, max_length=2048)]
    storage: Annotated[str | None, Field(pattern=r'^[A-Za-z0-9_.-]{1,64}$')] = None
    unique: bool = True
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class BackupDeleteInput(Input):
    node: NodeName
    storage: Annotated[str, Field(pattern=r'^[A-Za-z0-9_.-]{1,64}$')]
    volume: Annotated[str, Field(min_length=1, max_length=2048)]
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class NodeServiceInput(Input):
    action: Literal['start', 'stop', 'restart']
    confirmation: Annotated[str | None, Field(max_length=128)] = None


class FirewallRuleInput(Input):
    type: Literal['in', 'out', 'group']
    action: Literal['ACCEPT', 'DROP', 'REJECT'] | None = None
    source: Annotated[str | None, Field(max_length=512)] = None
    dest: Annotated[str | None, Field(max_length=512)] = None
    proto: Annotated[str | None, Field(max_length=32)] = None
    dport: Annotated[str | None, Field(max_length=128)] = None
    sport: Annotated[str | None, Field(max_length=128)] = None
    iface: Annotated[str | None, Field(max_length=64)] = None
    macro: Annotated[str | None, Field(max_length=64)] = None
    log: Annotated[str | None, Field(max_length=32)] = None
    comment: Annotated[str | None, Field(max_length=1024)] = None
    enable: bool = True
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class FirewallRuleUpdateInput(FirewallRuleInput):
    pos: Annotated[int, Field(ge=0, le=100000)]


class FirewallRuleDeleteInput(Input):
    pos: Annotated[int, Field(ge=0, le=100000)]
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class BulkTarget(Input):
    node: NodeName
    vmid: VMID


class BulkInput(Input):
    action: Literal['start', 'shutdown', 'reboot', 'stop', 'add_tag', 'remove_tag', 'snapshot']
    targets: Annotated[list[BulkTarget], Field(min_length=1, max_length=100)]
    tag: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    snapshot: SnapshotName | None = None

    @model_validator(mode='after')
    def action_parameters(self):
        if self.action in {'add_tag', 'remove_tag'} and not self.tag:
            raise ValueError('tag is required for tag bulk actions')
        if self.action == 'snapshot' and not self.snapshot:
            raise ValueError('snapshot is required for snapshot bulk action')
        return self


class SearchQuery(Input):
    query: Annotated[str, Field(min_length=1, max_length=200)]


class RawCommandParameters(Input):
    parameters: dict[str, Any] = Field(default_factory=dict)
)]
    volume: Annotated[str, Field(min_length=1, max_length=2048)]
    confirmation: Annotated[str, Field(min_length=1, max_length=2048)]


class BackupRunInput(Input):
    storage: Annotated[str, Field(pattern=r'^[A-Za-z0-9_.-]{1,64}$')]
    mode: Literal['snapshot', 'suspend', 'stop'] = 'snapshot'
    compress: Literal['0', 'gzip', 'lzo', 'zstd'] = 'zstd'
    notes: Annotated[str | None, Field(max_length=1024)] = None


class BackupRestoreInput(Input):
    node: NodeName
    vmid: VMID
    archive: Annotated[str, Field(min_length=1, max_length=2048)]
    storage: Annotated[str | None, Field(pattern=r'^[A-Za-z0-9_.-]{1,64}$')] = None
    unique: bool = True
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class BackupDeleteInput(Input):
    node: NodeName
    storage: Annotated[str, Field(pattern=r'^[A-Za-z0-9_.-]{1,64}$')]
    volume: Annotated[str, Field(min_length=1, max_length=2048)]
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class NodeServiceInput(Input):
    action: Literal['start', 'stop', 'restart']
    confirmation: Annotated[str | None, Field(max_length=128)] = None


class FirewallRuleInput(Input):
    type: Literal['in', 'out', 'group']
    action: Literal['ACCEPT', 'DROP', 'REJECT'] | None = None
    source: Annotated[str | None, Field(max_length=512)] = None
    dest: Annotated[str | None, Field(max_length=512)] = None
    proto: Annotated[str | None, Field(max_length=32)] = None
    dport: Annotated[str | None, Field(max_length=128)] = None
    sport: Annotated[str | None, Field(max_length=128)] = None
    iface: Annotated[str | None, Field(max_length=64)] = None
    macro: Annotated[str | None, Field(max_length=64)] = None
    log: Annotated[str | None, Field(max_length=32)] = None
    comment: Annotated[str | None, Field(max_length=1024)] = None
    enable: bool = True
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class FirewallRuleUpdateInput(FirewallRuleInput):
    pos: Annotated[int, Field(ge=0, le=100000)]


class FirewallRuleDeleteInput(Input):
    pos: Annotated[int, Field(ge=0, le=100000)]
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class BulkTarget(Input):
    node: NodeName
    vmid: VMID


class BulkInput(Input):
    action: Literal['start', 'shutdown', 'reboot', 'stop', 'add_tag', 'remove_tag', 'snapshot']
    targets: Annotated[list[BulkTarget], Field(min_length=1, max_length=100)]
    tag: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    snapshot: SnapshotName | None = None

    @model_validator(mode='after')
    def action_parameters(self):
        if self.action in {'add_tag', 'remove_tag'} and not self.tag:
            raise ValueError('tag is required for tag bulk actions')
        if self.action == 'snapshot' and not self.snapshot:
            raise ValueError('snapshot is required for snapshot bulk action')
        return self


class SearchQuery(Input):
    query: Annotated[str, Field(min_length=1, max_length=200)]


class RawCommandParameters(Input):
    parameters: dict[str, Any] = Field(default_factory=dict)
)]
    storage: Annotated[str, Field(pattern=r'^[A-Za-z0-9_.-]{1,64}

class NICInput(Input):
    nic: Annotated[str, Field(pattern=r'^net\\d+$')]
    bridge: Annotated[str, Field(pattern=r'^[A-Za-z0-9_.:-]{1,64}$')]
    model: Literal['virtio', 'e1000', 'e1000e', 'vmxnet3', 'rtl8139'] = 'virtio'
    mac: Annotated[str | None, Field(pattern=r'^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$')] = None
    vlan: Annotated[int | None, Field(ge=1, le=4094)] = None
    firewall: bool = False
    rate_mbps: Annotated[float | None, Field(gt=0, le=1000000)] = None


class NICRemoveInput(Input):
    nic: Annotated[str, Field(pattern=r'^net\\d+$')]
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class LXCConfigInput(Input):
    cores: Annotated[int | None, Field(ge=1, le=512)] = None
    memory_mb: Annotated[int | None, Field(ge=16, le=16777216)] = None
    swap_mb: Annotated[int | None, Field(ge=0, le=16777216)] = None
    tags: Annotated[str | None, Field(max_length=2048)] = None
    description: Annotated[str | None, Field(max_length=8192)] = None
    onboot: bool | None = None
    protection: bool | None = None

    @model_validator(mode='after')
    def at_least_one_change(self):
        if all(getattr(self, key) is None for key in (
            'cores', 'memory_mb', 'swap_mb', 'tags', 'description', 'onboot', 'protection',
        )):
            raise ValueError('At least one LXC configuration field is required')
        return self


class BackupRunInput(Input):
    storage: Annotated[str, Field(pattern=r'^[A-Za-z0-9_.-]{1,64}$')]
    mode: Literal['snapshot', 'suspend', 'stop'] = 'snapshot'
    compress: Literal['0', 'gzip', 'lzo', 'zstd'] = 'zstd'
    notes: Annotated[str | None, Field(max_length=1024)] = None


class BackupRestoreInput(Input):
    node: NodeName
    vmid: VMID
    archive: Annotated[str, Field(min_length=1, max_length=2048)]
    storage: Annotated[str | None, Field(pattern=r'^[A-Za-z0-9_.-]{1,64}$')] = None
    unique: bool = True
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class BackupDeleteInput(Input):
    node: NodeName
    storage: Annotated[str, Field(pattern=r'^[A-Za-z0-9_.-]{1,64}$')]
    volume: Annotated[str, Field(min_length=1, max_length=2048)]
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class NodeServiceInput(Input):
    action: Literal['start', 'stop', 'restart']
    confirmation: Annotated[str | None, Field(max_length=128)] = None


class FirewallRuleInput(Input):
    type: Literal['in', 'out', 'group']
    action: Literal['ACCEPT', 'DROP', 'REJECT'] | None = None
    source: Annotated[str | None, Field(max_length=512)] = None
    dest: Annotated[str | None, Field(max_length=512)] = None
    proto: Annotated[str | None, Field(max_length=32)] = None
    dport: Annotated[str | None, Field(max_length=128)] = None
    sport: Annotated[str | None, Field(max_length=128)] = None
    iface: Annotated[str | None, Field(max_length=64)] = None
    macro: Annotated[str | None, Field(max_length=64)] = None
    log: Annotated[str | None, Field(max_length=32)] = None
    comment: Annotated[str | None, Field(max_length=1024)] = None
    enable: bool = True
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class FirewallRuleUpdateInput(FirewallRuleInput):
    pos: Annotated[int, Field(ge=0, le=100000)]


class FirewallRuleDeleteInput(Input):
    pos: Annotated[int, Field(ge=0, le=100000)]
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class BulkTarget(Input):
    node: NodeName
    vmid: VMID


class BulkInput(Input):
    action: Literal['start', 'shutdown', 'reboot', 'stop', 'add_tag', 'remove_tag', 'snapshot']
    targets: Annotated[list[BulkTarget], Field(min_length=1, max_length=100)]
    tag: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    snapshot: SnapshotName | None = None

    @model_validator(mode='after')
    def action_parameters(self):
        if self.action in {'add_tag', 'remove_tag'} and not self.tag:
            raise ValueError('tag is required for tag bulk actions')
        if self.action == 'snapshot' and not self.snapshot:
            raise ValueError('snapshot is required for snapshot bulk action')
        return self


class SearchQuery(Input):
    query: Annotated[str, Field(min_length=1, max_length=200)]


class RawCommandParameters(Input):
    parameters: dict[str, Any] = Field(default_factory=dict)
)]
    delete_source: bool = True
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class NICInput(Input):
    nic: Annotated[str, Field(pattern=r'^net\\d+$')]
    bridge: Annotated[str, Field(pattern=r'^[A-Za-z0-9_.:-]{1,64}$')]
    model: Literal['virtio', 'e1000', 'e1000e', 'vmxnet3', 'rtl8139'] = 'virtio'
    mac: Annotated[str | None, Field(pattern=r'^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$')] = None
    vlan: Annotated[int | None, Field(ge=1, le=4094)] = None
    firewall: bool = False
    rate_mbps: Annotated[float | None, Field(gt=0, le=1000000)] = None


class NICRemoveInput(Input):
    nic: Annotated[str, Field(pattern=r'^net\\d+$')]
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class LXCConfigInput(Input):
    cores: Annotated[int | None, Field(ge=1, le=512)] = None
    memory_mb: Annotated[int | None, Field(ge=16, le=16777216)] = None
    swap_mb: Annotated[int | None, Field(ge=0, le=16777216)] = None
    tags: Annotated[str | None, Field(max_length=2048)] = None
    description: Annotated[str | None, Field(max_length=8192)] = None
    onboot: bool | None = None
    protection: bool | None = None

    @model_validator(mode='after')
    def at_least_one_change(self):
        if all(getattr(self, key) is None for key in (
            'cores', 'memory_mb', 'swap_mb', 'tags', 'description', 'onboot', 'protection',
        )):
            raise ValueError('At least one LXC configuration field is required')
        return self


class BackupRunInput(Input):
    storage: Annotated[str, Field(pattern=r'^[A-Za-z0-9_.-]{1,64}$')]
    mode: Literal['snapshot', 'suspend', 'stop'] = 'snapshot'
    compress: Literal['0', 'gzip', 'lzo', 'zstd'] = 'zstd'
    notes: Annotated[str | None, Field(max_length=1024)] = None


class BackupRestoreInput(Input):
    node: NodeName
    vmid: VMID
    archive: Annotated[str, Field(min_length=1, max_length=2048)]
    storage: Annotated[str | None, Field(pattern=r'^[A-Za-z0-9_.-]{1,64}$')] = None
    unique: bool = True
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class BackupDeleteInput(Input):
    node: NodeName
    storage: Annotated[str, Field(pattern=r'^[A-Za-z0-9_.-]{1,64}$')]
    volume: Annotated[str, Field(min_length=1, max_length=2048)]
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class NodeServiceInput(Input):
    action: Literal['start', 'stop', 'restart']
    confirmation: Annotated[str | None, Field(max_length=128)] = None


class FirewallRuleInput(Input):
    type: Literal['in', 'out', 'group']
    action: Literal['ACCEPT', 'DROP', 'REJECT'] | None = None
    source: Annotated[str | None, Field(max_length=512)] = None
    dest: Annotated[str | None, Field(max_length=512)] = None
    proto: Annotated[str | None, Field(max_length=32)] = None
    dport: Annotated[str | None, Field(max_length=128)] = None
    sport: Annotated[str | None, Field(max_length=128)] = None
    iface: Annotated[str | None, Field(max_length=64)] = None
    macro: Annotated[str | None, Field(max_length=64)] = None
    log: Annotated[str | None, Field(max_length=32)] = None
    comment: Annotated[str | None, Field(max_length=1024)] = None
    enable: bool = True
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class FirewallRuleUpdateInput(FirewallRuleInput):
    pos: Annotated[int, Field(ge=0, le=100000)]


class FirewallRuleDeleteInput(Input):
    pos: Annotated[int, Field(ge=0, le=100000)]
    confirmation: Annotated[str, Field(min_length=1, max_length=100)]


class BulkTarget(Input):
    node: NodeName
    vmid: VMID


class BulkInput(Input):
    action: Literal['start', 'shutdown', 'reboot', 'stop', 'add_tag', 'remove_tag', 'snapshot']
    targets: Annotated[list[BulkTarget], Field(min_length=1, max_length=100)]
    tag: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    snapshot: SnapshotName | None = None

    @model_validator(mode='after')
    def action_parameters(self):
        if self.action in {'add_tag', 'remove_tag'} and not self.tag:
            raise ValueError('tag is required for tag bulk actions')
        if self.action == 'snapshot' and not self.snapshot:
            raise ValueError('snapshot is required for snapshot bulk action')
        return self


class SearchQuery(Input):
    query: Annotated[str, Field(min_length=1, max_length=200)]


class RawCommandParameters(Input):
    parameters: dict[str, Any] = Field(default_factory=dict)
