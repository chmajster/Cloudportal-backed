from __future__ import annotations

import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.ansible_custom.service import (
    custom_playbook_definition,
    delete_custom_playbook,
    save_custom_playbook,
)
from app.catalog import builtin_playbook_definition, playbook_public
from app.catalog_control import catalog_item_enabled, catalog_item_public, set_catalog_item_enabled
from app.database import get_db
from app.security.core import audit
from app.resource_scope.http import require


router = APIRouter(prefix='/ansible/custom-playbooks', tags=['custom-ansible'])
SLUG = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$')


class VariableDefinition(BaseModel):
    model_config = ConfigDict(extra='forbid')
    required: bool = False
    pattern: str = Field(default=r'.{1,8192}', min_length=1, max_length=512)


class CustomPlaybookInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str | None = Field(default=None, min_length=1, max_length=63)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=1000)
    category: str = Field(default='Własne', min_length=1, max_length=100)
    transport: Literal['ssh', 'winrm'] = 'ssh'
    variables: dict[str, VariableDefinition] = Field(default_factory=dict)
    wait_for_connection: bool = True
    validate_after: bool = True
    content: str = Field(min_length=1, max_length=262144)

    @field_validator('id')
    @classmethod
    def valid_id(cls, value):
        if value is not None and not SLUG.fullmatch(value):
            raise ValueError('Invalid custom playbook identifier')
        return value


class CustomPlaybookState(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool


def _detail(db, playbook_id):
    item = custom_playbook_definition(db, playbook_id)
    if item is None:
        raise HTTPException(404, 'Custom playbook not found')
    return {
        'id': item['id'],
        'name': item['name'],
        'description': item.get('description') or '',
        'category': item.get('category') or 'Własne',
        'transport': item['transport'],
        'variables': item.get('variables') or {},
        'wait_for_connection': bool(item.get('wait')),
        'validate_after': bool(item.get('validate')),
        'content': item.get('content') or '',
        'version': int(item.get('version') or 1),
        'enabled': catalog_item_enabled(db, 'playbooks', playbook_id),
        'custom': True,
    }


@router.post('', status_code=201)
def create_custom_playbook(
    data: CustomPlaybookInput,
    request: Request,
    actor=Depends(require('ansible.manage')),
    db=Depends(get_db, scope='function'),
):
    if not data.id:
        raise HTTPException(422, 'Custom playbook id is required')
    if builtin_playbook_definition(data.id) is not None:
        raise HTTPException(409, 'A built-in Ansible playbook already uses this id')
    if custom_playbook_definition(db, data.id) is not None:
        raise HTTPException(409, 'Custom playbook already exists')
    item = save_custom_playbook(db, data, created_by=actor.user_id)
    set_catalog_item_enabled(db, 'playbooks', item['id'], True)
    audit(db, request, 'ansible.custom_playbook_created', 'ansible_playbooks', item['id'])
    return catalog_item_public(db, 'playbooks', playbook_public(item['id'], db=db))


@router.get('/{playbook_id}')
def get_custom_playbook(
    playbook_id: str,
    actor=Depends(require('ansible.manage')),
    db=Depends(get_db, scope='function'),
):
    return _detail(db, playbook_id)


@router.put('/{playbook_id}')
def update_custom_playbook(
    playbook_id: str,
    data: CustomPlaybookInput,
    request: Request,
    actor=Depends(require('ansible.manage')),
    db=Depends(get_db, scope='function'),
):
    if builtin_playbook_definition(playbook_id) is not None:
        raise HTTPException(409, 'Built-in playbooks cannot be edited')
    if custom_playbook_definition(db, playbook_id) is None:
        raise HTTPException(404, 'Custom playbook not found')
    if data.id and data.id != playbook_id:
        raise HTTPException(422, 'Custom playbook id cannot be changed')
    item = save_custom_playbook(db, data, playbook_id=playbook_id, created_by=actor.user_id)
    audit(db, request, 'ansible.custom_playbook_updated', 'ansible_playbooks', item['id'])
    return catalog_item_public(db, 'playbooks', playbook_public(item['id'], db=db))


@router.put('/{playbook_id}/enabled')
def set_custom_playbook_state(
    playbook_id: str,
    data: CustomPlaybookState,
    request: Request,
    actor=Depends(require('ansible.manage')),
    db=Depends(get_db, scope='function'),
):
    if custom_playbook_definition(db, playbook_id) is None:
        raise HTTPException(404, 'Custom playbook not found')
    set_catalog_item_enabled(db, 'playbooks', playbook_id, data.enabled)
    audit(
        db,
        request,
        'ansible.custom_playbook_enabled' if data.enabled else 'ansible.custom_playbook_disabled',
        'ansible_playbooks',
        playbook_id,
    )
    return catalog_item_public(db, 'playbooks', playbook_public(playbook_id, db=db))


@router.delete('/{playbook_id}')
def remove_custom_playbook(
    playbook_id: str,
    request: Request,
    actor=Depends(require('ansible.manage')),
    db=Depends(get_db, scope='function'),
):
    if builtin_playbook_definition(playbook_id) is not None:
        raise HTTPException(409, 'Built-in playbooks cannot be deleted')
    delete_custom_playbook(db, playbook_id)
    set_catalog_item_enabled(db, 'playbooks', playbook_id, True)
    audit(db, request, 'ansible.custom_playbook_deleted', 'ansible_playbooks', playbook_id)
    return {'deleted': True}
