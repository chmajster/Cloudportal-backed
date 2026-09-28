from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class NormalizedDiscoveryResource:
    external_id: str
    provider_id: int
    resource_type: str
    name: str
    hostname: str | None = None
    power_state: str = 'unknown'
    cpu: int | None = None
    memory_mb: int | None = None
    disks: list[dict] = field(default_factory=list)
    networks: list[dict] = field(default_factory=list)
    addresses: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    location: dict = field(default_factory=dict)
    guest: dict = field(default_factory=dict)
    capabilities: dict = field(default_factory=dict)
    raw_metadata: dict = field(default_factory=dict)
    provider_uuid: str | None = None
    is_template: bool = False
    uptime: int | None = None
    snapshots: list[dict] = field(default_factory=list)
    description: str = ''
    bios: str | None = None
    machine_type: str | None = None
    cloud_init: bool = False

    def public(self) -> dict[str, Any]:
        return {
            'external_id': self.external_id,
            'provider_id': self.provider_id,
            'resource_type': self.resource_type,
            'name': self.name,
            'hostname': self.hostname,
            'power_state': self.power_state,
            'cpu': self.cpu,
            'memory_mb': self.memory_mb,
            'disks': self.disks,
            'networks': self.networks,
            'addresses': self.addresses,
            'tags': self.tags,
            'location': self.location,
            'guest': self.guest,
            'capabilities': self.capabilities,
            'provider_uuid': self.provider_uuid,
            'is_template': self.is_template,
            'uptime': self.uptime,
            'snapshots': self.snapshots,
            'description': self.description,
            'bios': self.bios,
            'machine_type': self.machine_type,
            'cloud_init': self.cloud_init,
        }


class OnboardingProviderAdapter(ABC):
    provider_type: str

    def __init__(self, provider_row, infrastructure_adapter):
        self.provider_row = provider_row
        self.provider = infrastructure_adapter

    @classmethod
    @abstractmethod
    def static_capabilities(cls) -> dict:
        raise NotImplementedError

    @abstractmethod
    def discover_resources(self, scope: dict, *, concurrency: int = 5, cancelled=None) -> list[NormalizedDiscoveryResource]:
        raise NotImplementedError

    @abstractmethod
    def get_resource(self, *, resource_type: str, external_id: str) -> NormalizedDiscoveryResource | None:
        raise NotImplementedError

    @abstractmethod
    def normalize_resource(self, raw: dict, details: dict | None = None) -> NormalizedDiscoveryResource:
        raise NotImplementedError

    @abstractmethod
    def get_external_identity(self, resource: NormalizedDiscoveryResource) -> dict:
        raise NotImplementedError

    @abstractmethod
    def get_capabilities(self, resource: NormalizedDiscoveryResource | None = None) -> dict:
        raise NotImplementedError

    @abstractmethod
    def get_guest_agent_status(self, resource: NormalizedDiscoveryResource) -> str:
        raise NotImplementedError

    @abstractmethod
    def refresh_resource(self, identity) -> NormalizedDiscoveryResource | None:
        raise NotImplementedError

    @abstractmethod
    def validate_adoption(self, resource: NormalizedDiscoveryResource, mode: str) -> list[dict]:
        raise NotImplementedError

    def permission_diagnostics(self) -> list[dict]:
        return []
