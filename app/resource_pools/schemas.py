"""Validated API contracts for resource pools and placement."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.policy_engine.engine import validate_condition_tree

PlacementStrategy = Literal[
    'BALANCED', 'LEAST_USED', 'MOST_FREE_MEMORY', 'MOST_FREE_CPU',
    'ROUND_ROBIN', 'WEIGHTED', 'PRIORITY', 'RANDOM',
]
PlacementMode = Literal['FIXED', 'POOL', 'POLICY']
AffinityType = Literal[
    'prefer_same_node', 'require_same_node', 'prefer_different_node', 'require_different_node',
    'prefer_same_location', 'require_same_location', 'prefer_different_location', 'require_different_location',
]


class PoolThresholds(BaseModel):
    cpu_warning: float = Field(default=80, ge=0, le=100)
    cpu_hard_limit: float = Field(default=95, ge=0, le=100)
    ram_warning: float = Field(default=80, ge=0, le=100)
    ram_hard_limit: float = Field(default=95, ge=0, le=100)
    storage_warning: float = Field(default=80, ge=0, le=100)
    storage_hard_limit: float = Field(default=95, ge=0, le=100)

    @model_validator(mode='after')
    def coherent(self):
        if self.cpu_warning > self.cpu_hard_limit:
            raise ValueError('CPU warning threshold cannot exceed hard limit')
        if self.ram_warning > self.ram_hard_limit:
            raise ValueError('RAM warning threshold cannot exceed hard limit')
        if self.storage_warning > self.storage_hard_limit:
            raise ValueError('Storage warning threshold cannot exceed hard limit')
        return self


class ResourcePoolInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=4000)
    strategy: PlacementStrategy = 'BALANCED'
    enabled: bool = True
    thresholds: PoolThresholds = Field(default_factory=PoolThresholds)
    retry_limit: int = Field(default=3, ge=1, le=10)
    reservation_ttl_seconds: int = Field(default=600, ge=30, le=86400)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator('name')
    @classmethod
    def clean_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('Pool name cannot be empty')
        return value


class ResourcePoolMemberInput(BaseModel):
    provider_id: int = Field(gt=0)
    platform_id: str | None = Field(default=None, max_length=100)
    provider_type: str | None = Field(default=None, max_length=32)
    cluster: str | None = Field(default=None, max_length=100)
    node: str | None = Field(default=None, max_length=100)
    datacenter: str | None = Field(default=None, max_length=100)
    storage: str | None = Field(default=None, max_length=100)
    network: str | None = Field(default=None, max_length=100)
    enabled: bool = True
    priority: int = Field(default=100, ge=0, le=10000)
    weight: int = Field(default=100, ge=1, le=10000)
    maintenance_mode: bool = False
    max_vm_count: int | None = Field(default=None, ge=1)
    max_cpu_usage: float | None = Field(default=None, ge=0, le=100)
    max_memory_usage: float | None = Field(default=None, ge=0, le=100)
    min_free_memory_mb: int | None = Field(default=None, ge=0)
    min_free_storage_gb: int | None = Field(default=None, ge=0)
    tags: list[str] = Field(default_factory=list, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator('cluster', 'node', 'datacenter', 'storage', 'network', 'platform_id')
    @classmethod
    def normalize_optional(cls, value):
        text = str(value or '').strip()
        return text or None

    @field_validator('tags')
    @classmethod
    def clean_tags(cls, values):
        return sorted({str(value).strip() for value in values if str(value).strip()})


class PlacementRuleInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    enabled: bool = True
    priority: int = Field(default=100, ge=-10000, le=10000)
    conditions: dict[str, Any] = Field(default_factory=dict)
    actions: dict[str, Any] = Field(default_factory=dict)
    stop_processing: bool = False

    @field_validator('conditions')
    @classmethod
    def valid_conditions(cls, value):
        validate_condition_tree(value)
        return value

    @field_validator('actions')
    @classmethod
    def valid_actions(cls, value):
        allowed = {
            'hard_constraints', 'soft_preferences', 'allowed_provider_types', 'allowed_provider_ids',
            'allowed_nodes', 'allowed_locations', 'required_network', 'required_storage_class',
            'required_tags', 'preferred_location', 'fallback_locations', 'affinity', 'reason',
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError('Unsupported placement action(s): ' + ', '.join(sorted(unknown)))
        return value

    @field_validator('name')
    @classmethod
    def clean_name(cls, value):
        return value.strip()


class AffinityInput(BaseModel):
    type: AffinityType
    group_key: str | None = Field(default=None, max_length=128)
    peer_deployment_ids: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode='after')
    def has_reference(self):
        if not self.group_key and not self.peer_deployment_ids:
            raise ValueError('Affinity requires group_key or peer_deployment_ids')
        return self


class PlacementOverride(BaseModel):
    provider_id: int = Field(gt=0)
    node: str | None = Field(default=None, max_length=100)
    storage: str | None = Field(default=None, max_length=100)
    network: str | None = Field(default=None, max_length=100)


class PlacementRequest(BaseModel):
    placement_mode: PlacementMode = 'POLICY'
    pool_id: str | None = Field(default=None, min_length=36, max_length=36)
    blueprint_id: int | None = Field(default=None, gt=0)
    organization: str | None = Field(default=None, max_length=100)
    project: str | None = Field(default=None, max_length=100)
    apmid: str | None = Field(default=None, max_length=63)
    environment: str | None = Field(default=None, max_length=32)
    operating_system: str | None = Field(default=None, max_length=100)
    vm_size: str | None = Field(default=None, max_length=100)
    cpu: int = Field(default=1, ge=1, le=4096)
    ram_mb: int = Field(default=512, ge=1)
    disk_gb: int = Field(default=1, ge=1)
    tags: list[str] = Field(default_factory=list, max_length=100)
    provider_type: str | None = Field(default=None, max_length=32)
    location: str | None = Field(default=None, max_length=100)
    logical_network: str | None = Field(default=None, max_length=100)
    storage_class: str | None = Field(default=None, max_length=100)
    template_id: int | None = Field(default=None, ge=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    affinity: AffinityInput | None = None
    override: PlacementOverride | None = None
    reserve: bool = False

    @model_validator(mode='after')
    def mode_requirements(self):
        if self.placement_mode == 'POOL' and not self.pool_id:
            raise ValueError('POOL placement requires pool_id')
        if self.placement_mode == 'FIXED' and not self.override:
            raise ValueError('FIXED placement requires override target')
        return self


class MappingInput(BaseModel):
    provider_id: int = Field(gt=0)
    node: str | None = Field(default=None, max_length=100)
    target: str = Field(min_length=1, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)


class LogicalClassInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=4000)
    enabled: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator('name')
    @classmethod
    def clean_name(cls, value):
        return value.strip()
