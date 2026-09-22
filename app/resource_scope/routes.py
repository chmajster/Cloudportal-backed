"""Global assignment writes; resource-scoped execution uses existing endpoints."""
from uuid import UUID
from typing import Annotated
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from app.database import get_db
from app.api.common import Limit, Offset
from app.security.core import authenticate, audit
from app.tenancy.authorization import Principal
from app.resource_scope import service

router = APIRouter(tags=['resource-scope'])


class InfrastructureAccessInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(ge=1)
    provider_ids: list[Annotated[int, Field(strict=True, gt=0)]] = Field(default_factory=list, max_length=200)
    credential_ids: list[Annotated[int, Field(strict=True, gt=0)]] = Field(default_factory=list, max_length=200)


class InfrastructureAccessOutput(BaseModel):
    tenant_id: UUID
    project_id: UUID
    version: int
    provider_ids: list[int]
    credential_ids: list[int]
    provider_total: int
    credential_total: int
    limit: int
    offset: int


@router.get('/projects/{project_id}/infrastructure-access', response_model=InfrastructureAccessOutput)
def get_access(project_id: UUID, limit: Limit = 100, offset: Offset = 0, actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    return service.access_view(db, Principal.from_token(actor), project_id, limit=limit, offset=offset)


@router.put('/projects/{project_id}/infrastructure-access', response_model=InfrastructureAccessOutput)
def put_access(project_id: UUID, data: InfrastructureAccessInput, request: Request,
               actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    result = service.set_access(db, Principal.from_token(actor), project_id, data)
    audit(db, request, 'project.infrastructure_access.changed', 'projects', project_id)
    return result
