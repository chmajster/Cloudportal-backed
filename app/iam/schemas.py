from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


SubjectType = Literal['USER', 'GROUP', 'SERVICE_ACCOUNT', 'API_TOKEN']
ScopeType = Literal[
    'GLOBAL', 'ORGANIZATION', 'PROJECT', 'APMID', 'ENVIRONMENT',
    'RESOURCE_POOL', 'BLUEPRINT', 'DEPLOYMENT', 'RESOURCE', 'MACHINE',
]
Effect = Literal['ALLOW', 'DENY']


class IAMModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ScopeInput(IAMModel):
    scope_type: ScopeType = 'GLOBAL'
    scope_id: str | None = Field(default=None, max_length=160)
    tenant_id: str | None = Field(default=None, max_length=36)
    organization_id: str | None = Field(default=None, max_length=36)
    project_id: str | None = Field(default=None, max_length=36)
    apmid: str | None = Field(default=None, max_length=63)
    environment: str | None = Field(default=None, max_length=32)
    resource_pool_id: str | None = Field(default=None, max_length=160)
    blueprint_id: str | None = Field(default=None, max_length=160)
    deployment_id: str | None = Field(default=None, max_length=160)
    resource_id: str | None = Field(default=None, max_length=160)
    machine_id: str | None = Field(default=None, max_length=160)


class AssignmentCreate(ScopeInput):
    subject_type: SubjectType
    subject_id: str = Field(min_length=1, max_length=128)
    role_id: int = Field(gt=0)
    effect: Effect = 'ALLOW'
    conditions: dict[str, Any] = Field(default_factory=dict)
    inherit: bool = True
    approval_required: bool = False
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    enabled: bool = True
    source: str = Field(default='LOCAL', min_length=1, max_length=32)
    source_ref: str | None = Field(default=None, max_length=1024)

    @model_validator(mode='after')
    def valid_window(self):
        if self.valid_from and self.valid_until and self.valid_until <= self.valid_from:
            raise ValueError('valid_until must be later than valid_from')
        return self


class AssignmentUpdate(IAMModel):
    role_id: int | None = Field(default=None, gt=0)
    effect: Effect | None = None
    conditions: dict[str, Any] | None = None
    inherit: bool | None = None
    approval_required: bool | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    enabled: bool | None = None

    @model_validator(mode='after')
    def valid_window(self):
        if self.valid_from and self.valid_until and self.valid_until <= self.valid_from:
            raise ValueError('valid_until must be later than valid_from')
        return self


class AssignmentOutput(IAMModel):
    id: str
    subject_type: SubjectType
    subject_id: str
    role_id: int
    role_name: str
    effect: Effect
    scope_type: ScopeType
    scope_id: str | None
    tenant_id: str | None
    project_id: str | None
    apmid: str | None
    environment: str | None
    conditions: dict[str, Any]
    permission_ceiling: list[str] | None
    inherit: bool
    approval_required: bool
    valid_from: datetime | None
    valid_until: datetime | None
    enabled: bool
    source: str
    source_ref: str | None
    status: Literal['ACTIVE', 'SCHEDULED', 'EXPIRED', 'DISABLED']
    created_by: int | None
    created_at: datetime
    updated_at: datetime


class AssignmentPage(IAMModel):
    items: list[AssignmentOutput]
    total: int
    limit: int
    offset: int


class BulkAssignmentCreate(IAMModel):
    assignments: list[AssignmentCreate] = Field(min_length=1, max_length=500)
    atomic: bool = True


class BulkAssignmentResult(IAMModel):
    items: list[AssignmentOutput]
    created: int


class GroupCreate(IAMModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default='', max_length=4000)
    external_source: Literal['LDAP', 'OIDC', 'SSO'] | None = None
    external_id: str | None = Field(default=None, max_length=1024)
    enabled: bool = True


class GroupUpdate(IAMModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=4000)
    external_source: Literal['LDAP', 'OIDC', 'SSO'] | None = None
    external_id: str | None = Field(default=None, max_length=1024)
    enabled: bool | None = None


class GroupOutput(IAMModel):
    id: str
    name: str
    description: str
    external_source: str | None
    external_id: str | None
    enabled: bool
    member_count: int
    created_at: datetime
    updated_at: datetime


class GroupPage(IAMModel):
    items: list[GroupOutput]
    total: int
    limit: int
    offset: int


class GroupMemberInput(IAMModel):
    user_id: int = Field(gt=0)
    source: str = Field(default='LOCAL', min_length=1, max_length=32)
    external_id: str | None = Field(default=None, max_length=1024)


class GroupMemberOutput(IAMModel):
    user_id: int
    username: str
    email: str
    source: str
    external_id: str | None
    created_at: datetime


class GroupMemberPage(IAMModel):
    items: list[GroupMemberOutput]
    total: int


class RoleProfileInput(IAMModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=4000)
    permissions: list[str] = Field(default_factory=list, max_length=1000)
    permission_patterns: list[str] = Field(default_factory=list, max_length=200)
    scope_types: list[ScopeType] = Field(default_factory=lambda: ['GLOBAL'])
    inheritance_enabled: bool = True
    enabled: bool = True
    assignable_by: list[str] = Field(default_factory=list, max_length=200)


class RoleProfileUpdate(IAMModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=4000)
    permissions: list[str] | None = Field(default=None, max_length=1000)
    permission_patterns: list[str] | None = Field(default=None, max_length=200)
    scope_types: list[ScopeType] | None = None
    inheritance_enabled: bool | None = None
    enabled: bool | None = None
    assignable_by: list[str] | None = Field(default=None, max_length=200)


class RoleProfileOutput(IAMModel):
    id: int
    name: str
    description: str
    permissions: list[str]
    permission_patterns: list[str]
    scope_types: list[str]
    inheritance_enabled: bool
    system_role: bool
    enabled: bool
    assignable_by: list[str]
    assignment_count: int


class RolePage(IAMModel):
    items: list[RoleProfileOutput]
    total: int
    limit: int
    offset: int


class PermissionDescriptor(IAMModel):
    name: str
    module: str
    action: str
    force_or_sensitive: bool


class PermissionCatalog(IAMModel):
    items: list[PermissionDescriptor]
    modules: dict[str, list[str]]


class AuthorizationInput(ScopeInput):
    action: str = Field(min_length=3, max_length=160)
    user_id: int | None = Field(default=None, gt=0)
    resource: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)


class AuthorizationDecisionOutput(IAMModel):
    allowed: bool
    decision: Literal['ALLOW', 'DENY', 'REQUIRES_APPROVAL']
    action: str
    permissions: list[str]
    roles: list[dict[str, Any]]
    assignments: list[dict[str, Any]]
    inherited: list[dict[str, Any]]
    denied: list[dict[str, Any]]
    reason: str
    trace: list[dict[str, Any]]
    break_glass: bool
    policy: dict[str, Any] | None = None


class EffectiveAccessOutput(IAMModel):
    user_id: int
    scope: dict[str, Any]
    permissions: list[str]
    roles: list[dict[str, Any]]
    assignments: list[AssignmentOutput]


class MeAccessOutput(IAMModel):
    user_id: int
    organizations: list[dict[str, Any]]
    projects: list[dict[str, Any]]
    global_permissions: list[str]
    assignments: list[AssignmentOutput]


class JITRequestInput(ScopeInput):
    role_id: int = Field(gt=0)
    duration_minutes: int = Field(ge=15, le=1440)
    reason: str = Field(min_length=8, max_length=4000)


class JITDecisionInput(IAMModel):
    reason: str = Field(default='', max_length=4000)


class JITRequestOutput(IAMModel):
    id: str
    requester_id: int
    role_id: int
    role_name: str
    scope_type: str
    scope_id: str | None
    tenant_id: str | None
    project_id: str | None
    apmid: str | None
    environment: str | None
    requested_duration_minutes: int
    reason: str
    status: str
    approval_id: str | None
    assignment_id: str | None
    decided_by: int | None
    decided_at: datetime | None
    created_at: datetime


class JITRequestPage(IAMModel):
    items: list[JITRequestOutput]
    total: int


class BreakGlassStart(IAMModel):
    reason: str = Field(min_length=12, max_length=4000)
    duration_minutes: int = Field(ge=15, le=240)


class BreakGlassOutput(IAMModel):
    id: str
    user_id: int
    reason: str
    valid_from: datetime
    valid_until: datetime
    enabled: bool
    created_by: int
    ended_by: int | None
    ended_at: datetime | None


class AccessReviewItem(IAMModel):
    user_id: int
    username: str
    email: str
    organization_id: str | None
    project_id: str | None
    roles: list[str]
    source: list[str]
    expirations: list[datetime]
    last_login_at: datetime | None


class AccessReviewOutput(IAMModel):
    items: list[AccessReviewItem]
    total: int


class ImpactOutput(IAMModel):
    role_id: int
    affected_users: list[int]
    affected_groups: list[str]
    active_assignments: int
    legacy_global_users: list[int]
    legacy_tenant_assignments: int
    legacy_project_assignments: int
