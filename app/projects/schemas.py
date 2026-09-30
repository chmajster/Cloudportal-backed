"""Project-specific API contracts reuse bounded tenant metadata validation."""
from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator
from app.tenancy.schemas import TenantFields, TenantOutput, TenantAuditOutput


class ProjectFields(TenantFields):
    default_environment: str = Field(default='dev', min_length=1, max_length=32, pattern=r'^[a-z][a-z0-9_-]*
    blueprint_auto_approve_for_executors: bool | None = None
    blueprint_approval_timeout_hours: int | None = Field(default=None, ge=1, le=720)


    @field_validator('allowed_apmids')
    @classmethod
    def normalize_allowed_apmids(cls, value):
        if value is None:
            return None
        result = []
        for item in value:
            code = str(item).strip().upper()
            if code and code not in result:
                result.append(code)
        return result


class ProjectCreate(ProjectFields):
    tenant_id: UUID


class ProjectUpdate(ProjectFields):
    expected_version: int = Field(ge=1)


class ProjectOutput(TenantOutput):
    tenant_id: UUID
    default_environment: str
    allowed_apmids: list[str] | None
    blueprint_auto_approve_for_executors: bool | None
    blueprint_approval_timeout_hours: int | None


class ProjectPage(BaseModel):
    items: list[ProjectOutput]
    total: int
    limit: int
    offset: int


class ProjectPermissions(BaseModel):
    tenant_id: UUID
    project_id: UUID
    scope: Literal['PROJECT'] = 'PROJECT'
    permissions: list[str]
    inherited_permissions: list[str]
    global_administration: bool


class ProjectMemberOutput(BaseModel):
    tenant_id: UUID
    project_id: UUID
    user_id: int
    username: str
    status: Literal['active', 'disabled']
    role_ids: list[int]
    version: int
    created_at: datetime
    updated_at: datetime


class ProjectMemberPage(BaseModel):
    items: list[ProjectMemberOutput]
    total: int
    limit: int
    offset: int


class UserProjectMembershipOutput(ProjectMemberOutput):
    project_name: str
    project_slug: str
    tenant_name: str
    tenant_slug: str


class UserProjectMembershipPage(BaseModel):
    items: list[UserProjectMembershipOutput]
    total: int
    limit: int
    offset: int


class ProjectMembershipSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    project_ids: list[UUID] = Field(default_factory=list, max_length=1000)

    @field_validator('project_ids')
    @classmethod
    def unique_project_ids(cls, value):
        if len(value) != len(set(value)):
            raise ValueError('Project IDs must be distinct')
        return value


class EligibleMember(BaseModel):
    user_id: int
    username: str


class EligibleMemberPage(BaseModel):
    items: list[EligibleMember]
    total: int
    limit: int
    offset: int


class ProjectAuditPage(BaseModel):
    items: list[TenantAuditOutput]
    total: int
    limit: int
    offset: int


class ContextInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tenant_id: UUID
    project_id: UUID


class ContextOutput(BaseModel):
    tenant_id: UUID
    project_id: UUID
    actor_id: int
    permissions: list[str]


class CreationScope(BaseModel):
    tenant_id: UUID
    name: str
    slug: str


class CreationScopePage(BaseModel):
    items: list[CreationScope]
    total: int
    limit: int
    offset: int
)
    allowed_apmids: list[str] | None = Field(default=None, max_length=500)
    blueprint_auto_approve_for_executors: bool | None = None
    blueprint_approval_timeout_hours: int | None = Field(default=None, ge=1, le=720)


class ProjectCreate(ProjectFields):
    tenant_id: UUID


class ProjectUpdate(ProjectFields):
    expected_version: int = Field(ge=1)


class ProjectOutput(TenantOutput):
    tenant_id: UUID
    default_environment: str
    blueprint_auto_approve_for_executors: bool | None
    blueprint_approval_timeout_hours: int | None


class ProjectPage(BaseModel):
    items: list[ProjectOutput]
    total: int
    limit: int
    offset: int


class ProjectPermissions(BaseModel):
    tenant_id: UUID
    project_id: UUID
    scope: Literal['PROJECT'] = 'PROJECT'
    permissions: list[str]
    inherited_permissions: list[str]
    global_administration: bool


class ProjectMemberOutput(BaseModel):
    tenant_id: UUID
    project_id: UUID
    user_id: int
    username: str
    status: Literal['active', 'disabled']
    role_ids: list[int]
    version: int
    created_at: datetime
    updated_at: datetime


class ProjectMemberPage(BaseModel):
    items: list[ProjectMemberOutput]
    total: int
    limit: int
    offset: int


class UserProjectMembershipOutput(ProjectMemberOutput):
    project_name: str
    project_slug: str
    tenant_name: str
    tenant_slug: str


class UserProjectMembershipPage(BaseModel):
    items: list[UserProjectMembershipOutput]
    total: int
    limit: int
    offset: int


class ProjectMembershipSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    project_ids: list[UUID] = Field(default_factory=list, max_length=1000)

    @field_validator('project_ids')
    @classmethod
    def unique_project_ids(cls, value):
        if len(value) != len(set(value)):
            raise ValueError('Project IDs must be distinct')
        return value


class EligibleMember(BaseModel):
    user_id: int
    username: str


class EligibleMemberPage(BaseModel):
    items: list[EligibleMember]
    total: int
    limit: int
    offset: int


class ProjectAuditPage(BaseModel):
    items: list[TenantAuditOutput]
    total: int
    limit: int
    offset: int


class ContextInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tenant_id: UUID
    project_id: UUID


class ContextOutput(BaseModel):
    tenant_id: UUID
    project_id: UUID
    actor_id: int
    permissions: list[str]


class CreationScope(BaseModel):
    tenant_id: UUID
    name: str
    slug: str


class CreationScopePage(BaseModel):
    items: list[CreationScope]
    total: int
    limit: int
    offset: int
