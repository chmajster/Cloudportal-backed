"""Project-specific contracts reuse the existing bounded metadata vocabulary."""
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field
from app.tenancy.schemas import TenantFields, TenantOutput, MemberOutput


class ProjectFields(TenantFields):
    default_environment: str = Field(default='dev', min_length=1, max_length=63,
                                     pattern=r'^[a-zA-Z0-9][a-zA-Z0-9_.-]*$')


class ProjectCreate(ProjectFields):
    tenant_id: UUID


class ProjectUpdate(ProjectFields):
    expected_version: int = Field(ge=1)


class ProjectOutput(TenantOutput):
    tenant_id: UUID
    default_environment: str


class ProjectPage(BaseModel):
    items: list[ProjectOutput]
    total: int
    limit: int
    offset: int


class ProjectMemberOutput(MemberOutput):
    project_id: UUID


class ProjectMemberPage(BaseModel):
    items: list[ProjectMemberOutput]
    total: int
    limit: int
    offset: int


class ProjectPermissions(BaseModel):
    tenant_id: UUID
    project_id: UUID
    scope: Literal['PROJECT'] = 'PROJECT'
    permissions: list[str]
    parent_administration: bool


class ContextInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tenant_id: UUID
    project_id: UUID
    expected_version: int = Field(ge=0)


class ContextOutput(BaseModel):
    selected: ProjectOutput | None
    version: int


class EligibleUser(BaseModel):
    user_id: int
    username: str


class EligibleUserPage(BaseModel):
    items: list[EligibleUser]
    total: int
    limit: int
    offset: int
