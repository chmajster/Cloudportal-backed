"""Bounded tenant input and explicit OpenAPI response contracts."""
import json
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

TenantStatus = Literal['active', 'suspended', 'disabled']
MemberStatus = Literal['active', 'disabled']
PositiveID = Annotated[int, Field(strict=True, ge=1)]


def bounded_json(value, *, max_depth=8, max_bytes=16384):
    def visit(item, depth=0):
        if depth > max_depth:
            raise ValueError('Metadata nesting exceeds eight levels')
        if isinstance(item, dict):
            if len(item) > 128:
                raise ValueError('Metadata object has too many keys')
            for key, child in item.items():
                if not isinstance(key, str) or not key or len(key) > 100 or key in {'__proto__', 'prototype', 'constructor'}:
                    raise ValueError('Metadata contains an invalid key')
                visit(child, depth + 1)
        elif isinstance(item, list):
            if len(item) > 256:
                raise ValueError('Metadata list has too many items')
            for child in item:
                visit(child, depth + 1)
    visit(value)
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')) > max_bytes:
        raise ValueError('Metadata exceeds 16 KiB')
    return value


class TenantFields(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=100)
    slug: str = Field(min_length=1, max_length=63, pattern=r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
    description: str = Field(default='', max_length=4000)
    status: TenantStatus = 'active'
    labels: dict[str, str] = Field(default_factory=dict, max_length=64)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator('name', mode='before')
    @classmethod
    def clean_name(cls, value):
        if isinstance(value, str):
            value = value.strip()
            if any(ord(character) < 32 for character in value):
                raise ValueError('Name contains control characters')
        return value

    @field_validator('slug', mode='before')
    @classmethod
    def clean_slug(cls, value):
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator('metadata')
    @classmethod
    def valid_metadata(cls, value):
        return bounded_json(value)

    @field_validator('labels')
    @classmethod
    def valid_labels(cls, value):
        if any(not key.strip() or len(key) > 64 or len(item) > 256
               or key in {'__proto__', 'prototype', 'constructor'} for key, item in value.items()):
            raise ValueError('Labels require keys of 1-64 characters and values of at most 256 characters')
        return value


class TenantCreate(TenantFields):
    pass


class TenantUpdate(TenantFields):
    expected_version: int = Field(ge=1)


class MemberCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    user_id: int = Field(ge=1, strict=True)
    status: MemberStatus = 'active'
    role_ids: list[PositiveID] = Field(default_factory=list, max_length=32)

    @field_validator('role_ids')
    @classmethod
    def valid_roles(cls, value):
        if len(value) != len(set(value)) or any(isinstance(item, bool) or item < 1 for item in value):
            raise ValueError('Role IDs must be distinct positive integers')
        return sorted(value)


class MemberUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: MemberStatus
    expected_version: int = Field(ge=1)


class MemberRoles(BaseModel):
    model_config = ConfigDict(extra='forbid')
    role_ids: list[PositiveID] = Field(max_length=32)
    expected_version: int = Field(ge=1)
    validate_roles = field_validator('role_ids')(MemberCreate.valid_roles.__func__)


class TenantOutput(BaseModel):
    id: UUID
    name: str
    slug: str
    description: str
    status: TenantStatus
    labels: dict[str, str]
    metadata: dict[str, JsonValue]
    is_system: bool
    created_by: int | None
    created_at: datetime
    updated_at: datetime
    version: int


class TenantPage(BaseModel):
    items: list[TenantOutput]
    total: int
    limit: int
    offset: int


class MemberOutput(BaseModel):
    tenant_id: UUID
    user_id: int
    username: str
    status: MemberStatus
    role_ids: list[int]
    version: int
    created_at: datetime
    updated_at: datetime


class MemberPage(BaseModel):
    items: list[MemberOutput]
    total: int
    limit: int
    offset: int


class TenantPermissions(BaseModel):
    tenant_id: UUID
    scope: Literal['TENANT'] = 'TENANT'
    permissions: list[str]
    global_administration: bool


class AssignableRole(BaseModel):
    id: int
    name: str
    permissions: list[str]


class RolePage(BaseModel):
    items: list[AssignableRole]
    total: int
    limit: int
    offset: int


class TenantAuditOutput(BaseModel):
    id: int
    timestamp: datetime
    user_id: int | None
    action: str
    resource: str
    resource_id: str | None
    result: str
    request_id: str


class TenantAuditPage(BaseModel):
    items: list[TenantAuditOutput]
    total: int
    limit: int
    offset: int
