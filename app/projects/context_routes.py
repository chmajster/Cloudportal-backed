"""Additive project selection HTTP contracts; existing /resolve remains compatible."""
from uuid import UUID
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from app.database import get_db
from app.projects import context
from app.projects.schemas import ProjectOutput
from app.security.core import audit, authenticate
from app.tenancy.authorization import Principal


class SelectionInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tenant_id: UUID
    project_id: UUID
    expected_version: int = Field(ge=0)


class SelectionOutput(BaseModel):
    selected: ProjectOutput | None
    version: int


router = APIRouter(tags=['projects'])


@router.get('/project-context', response_model=SelectionOutput)
def current_selection(actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    return context.context_get(db, Principal.from_token(actor))


@router.put('/project-context', response_model=SelectionOutput)
def select_context(data: SelectionInput, request: Request, actor=Depends(authenticate),
                   db=Depends(get_db, scope='function')):
    result = context.context_set(db, Principal.from_token(actor), data)
    audit(db, request, 'project.context.selected', 'projects', data.project_id)
    return result


@router.delete('/project-context', response_model=SelectionOutput)
def clear_context(request: Request, expected_version: int | None = Query(default=None, ge=0),
                  actor=Depends(authenticate), db=Depends(get_db, scope='function')):
    result = context.context_clear(db, Principal.from_token(actor), expected_version)
    audit(db, request, 'project.context.cleared', 'users', actor.user_id)
    return result
