from datetime import datetime
from typing import Any, Literal

from app.api.outputs import Output


class BlueprintOutput(Output):
    id: int
    slug: str
    name: str
    description: str
    avatar_id: str | None
    version: int
    is_active: bool
    visibility: dict[str, bool]
    allowed_role_ids: list[int]
    allowed_user_ids: list[int]
    allowed_entities: list[str]
    manager_role_ids: list[int]
    can_manage: bool | None = None
    variables_schema: dict[str, Any]
    deployment: dict[str, Any]
    workflow: list[dict[str, Any]]
    requires_approval: bool
    auto_approve_for_executors: bool | None
    approval_timeout_hours: int | None
    recovery_policy: Literal['preserve', 'destroy_on_failure']
    created_by: int
    created_at: datetime
    updated_at: datetime
