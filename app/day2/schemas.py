from typing import Annotated

from pydantic import Field

from app.api.schemas import Input


class Day2ExecuteInput(Input):
    parameters: dict = Field(default_factory=dict)
    reason: Annotated[str, Field(max_length=2000)] = ''


class Day2BulkInput(Input):
    action: Annotated[str, Field(min_length=1, max_length=64)]
    resource_ids: Annotated[list[str], Field(min_length=1, max_length=100)]
    parameters: dict = Field(default_factory=dict)
    reason: Annotated[str, Field(max_length=2000)] = ''


class Day2ResourceStateInput(Input):
    protected: bool | None = None
    platform_metadata: dict | None = None


class Day2SettingsInput(Input):
    enable_day2_actions: bool = True
    default_approval_policy: str = 'destructive'
    action_approval: dict[str, bool] = Field(default_factory=dict)
    environment_policies: dict[str, dict] = Field(default_factory=dict)
    approval_bypass_permissions: Annotated[list[str], Field(max_length=50)] = Field(default_factory=list)
    allow_force_power_off: bool = True
    allow_delete: bool = True
    allow_rebuild: bool = False
    allow_direct_provider_changes_for_terraform: bool = False
    resource_lock_timeout: int = Field(default=3600, ge=60, le=86400)
    action_timeout: int = Field(default=3600, ge=60, le=86400)
    max_bulk_action_size: int = Field(default=25, ge=1, le=100)
    require_reason_for_destructive_actions: bool = True
    require_confirmation_for_destructive_actions: bool = True
