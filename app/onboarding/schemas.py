from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Mode = Literal['DISCOVER_ONLY', 'INVENTORY_IMPORT', 'MANAGED', 'FULL_ADOPTION']


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class DiscoveryScopeInput(StrictModel):
    scan_all: bool = True
    cluster: str | None = Field(default=None, max_length=255)
    nodes: list[str] = Field(default_factory=list, max_length=100)
    pool: str | None = Field(default=None, max_length=255)
    vm_ids: list[int] = Field(default_factory=list, max_length=1000)
    vmid_min: int | None = Field(default=None, ge=0, le=999999999)
    vmid_max: int | None = Field(default=None, ge=0, le=999999999)
    tags: list[str] = Field(default_factory=list, max_length=100)
    resource_types: list[Literal['qemu', 'lxc']] = Field(default_factory=lambda: ['qemu', 'lxc'])
    include_templates: bool = True

    @model_validator(mode='after')
    def validate_range(self):
        if self.vmid_min is not None and self.vmid_max is not None and self.vmid_min > self.vmid_max:
            raise ValueError('vmid_min cannot be greater than vmid_max')
        return self


class DiscoveryInput(StrictModel):
    provider_id: int = Field(gt=0)
    scope: DiscoveryScopeInput = Field(default_factory=DiscoveryScopeInput)


class MappingInput(StrictModel):
    apmid: str | None = Field(default=None, max_length=63)
    environment: str | None = Field(default=None, max_length=32)
    owner_user_id: int | None = Field(default=None, gt=0)
    group: str | None = Field(default=None, max_length=100)
    cost_center: str | None = Field(default=None, max_length=100)
    business_service: str | None = Field(default=None, max_length=100)
    application: str | None = Field(default=None, max_length=100)
    support_group: str | None = Field(default=None, max_length=100)

    @field_validator('apmid')
    @classmethod
    def normalize_apmid(cls, value):
        return str(value).strip().upper() if value else None

    @field_validator('environment')
    @classmethod
    def normalize_environment(cls, value):
        return str(value).strip().lower() if value else None


class IntegrationInput(StrictModel):
    guest_discovery: bool = False
    enable_qemu_guest_agent: bool = False
    install_guest_agent: bool = False
    add_to_awx: bool = False
    awx_inventory_id: int | None = Field(default=None, gt=0)
    awx_inventory_name: str | None = Field(default=None, max_length=100)
    awx_group: str | None = Field(default=None, max_length=100)
    run_inventory_update: bool = False
    onboarding_playbook: str | None = Field(default=None, max_length=255)
    baseline_playbook: str | None = Field(default=None, max_length=255)
    agent_installation_playbook: str | None = Field(default=None, max_length=255)


class OnboardingItemInput(StrictModel):
    discovered_resource_id: str = Field(min_length=1, max_length=36)
    mode: Mode = 'INVENTORY_IMPORT'
    mapping: MappingInput = Field(default_factory=MappingInput)
    guest_credential_id: int | None = Field(default=None, gt=0)
    awx_credential_id: int | None = Field(default=None, gt=0)
    integrations: IntegrationInput = Field(default_factory=IntegrationInput)


class PreflightInput(StrictModel):
    items: list[OnboardingItemInput] = Field(min_length=1, max_length=500)


class ImportInput(StrictModel):
    items: list[OnboardingItemInput] = Field(min_length=1, max_length=500)


class RuleInput(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    priority: int = Field(default=100, ge=-100000, le=100000)
    enabled: bool = True
    stop_processing: bool = False
    conditions: dict = Field(default_factory=dict)
    effects: dict = Field(default_factory=dict)


class RulePatch(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    priority: int | None = Field(default=None, ge=-100000, le=100000)
    enabled: bool | None = None
    stop_processing: bool | None = None
    conditions: dict | None = None
    effects: dict | None = None


class ConflictResolveInput(StrictModel):
    action: Literal['LINK_EXISTING', 'IMPORT_NEW', 'IGNORE', 'MARK_EXTERNAL', 'REMOVE_STALE']
    managed_vm_id: str | None = Field(default=None, max_length=36)

    @model_validator(mode='after')
    def link_requires_target(self):
        if self.action == 'LINK_EXISTING' and not self.managed_vm_id:
            raise ValueError('managed_vm_id is required for LINK_EXISTING')
        return self


class RelinkInput(StrictModel):
    discovered_resource_id: str = Field(min_length=1, max_length=36)


class ScheduleInput(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    provider_id: int = Field(gt=0)
    discovery_scope: DiscoveryScopeInput = Field(default_factory=DiscoveryScopeInput)
    interval_seconds: int = Field(default=3600, ge=300, le=2592000)
    next_run_at: datetime | None = None
    auto_classification: bool = True
    automatic_inventory_import: bool = False
    is_active: bool = True


class SchedulePatch(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    discovery_scope: DiscoveryScopeInput | None = None
    interval_seconds: int | None = Field(default=None, ge=300, le=2592000)
    next_run_at: datetime | None = None
    auto_classification: bool | None = None
    automatic_inventory_import: bool | None = None
    is_active: bool | None = None
