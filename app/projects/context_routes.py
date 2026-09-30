"""Additive project selection HTTP contracts; existing /resolve remains compatible."""
from uuid import UUID
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from app.database import get_db
from app.projects import context
from app.projects.authorization import authorize
from app.policy_engine.entities import entity_catalog, entity_role_catalog
from app.projects.schemas import ProjectOutput
from app.security.core import audit, authenticate
from app.tenancy.authorization import Principal
from app.vm_classification import vm_classification_for_tenant


class SelectionInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tenant_id: UUID
    project_id: UUID
    entity_key: str | None = Field(default=None, max_length=160)
    expected_version: int = Field(ge=0)


class SelectionOutput(BaseModel):
    selected: ProjectOutput | None
    tenant_name: str | None = None
    entity_key: str | None = None
    entity_permissions: list[str] | None = None
    version: int


router = APIRouter(tags=['projects'])


@router.get('/project-context', response_model=SelectionOutput, response_model_exclude_unset=True)
def current_selection(actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    return context.context_get(db, Principal.from_token(actor))


@router.get('/project-context/entities')
def context_entities(
    tenant_id: UUID,
    project_id: UUID,
    actor=Depends(authenticate),
    db=Depends(get_db, scope='function'),
):
    access = authorize(
        db,
        Principal.from_token(actor),
        project_id,
        'projects.read',
        tenant_id=tenant_id,
    )
    classification = vm_classification_for_tenant(db, access.tenant.id)
    return {
        'items': entity_catalog(classification),
        'roles': entity_role_catalog(),
        'tenant_id': str(access.tenant.id),
        'project_id': str(access.project.id),
    }


@router.put('/project-context', response_model=SelectionOutput, response_model_exclude_unset=True)
def select_context(data: SelectionInput, request: Request, actor=Depends(authenticate),
                   db=Depends(get_db, scope='function')):
    result = context.context_set(db, Principal.from_token(actor), data)
    audit(db, request, 'project.context.selected', 'projects', data.project_id)
    return result


@router.delete('/project-context', response_model=SelectionOutput, response_model_exclude_unset=True)
def clear_context(request: Request, expected_version: int | None = Query(default=None, ge=0),
                  actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    result = context.context_clear(db, Principal.from_token(actor), expected_version)
    audit(db, request, 'project.context.cleared', 'users', actor.user_id)
    return result
