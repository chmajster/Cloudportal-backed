"""REST API for Resource Pools, placement simulation and logical mappings."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select

from app.database import get_db
from app.models import Deployment, Provider
from app.resource_scope.http import require
from app.resource_pools.models import (
    LogicalNetwork, NetworkMapping, PlacementDecision, PlacementRule, ResourcePool,
    ResourcePoolMember, ResourceReservation, StorageClass, StorageMapping,
)
from app.resource_pools.placement import (
    NoValidPlacementTarget, placement_for_deployment, pool_capacity, resolve_placement,
)
from app.resource_pools.schemas import (
    LogicalClassInput, MappingInput, PlacementRequest, PlacementRuleInput,
    ResourcePoolInput, ResourcePoolMemberInput,
)
from app.resource_pools.service import (
    active_deployments_for_pool, decision_public, expire_reservations, member_public,
    pool_public, reservation_public, rule_public, scoped_pool, validate_member_target,
)
from app.security.core import audit


router = APIRouter(tags=['resource-pools'])


def _pool_values(data: ResourcePoolInput):
    return {
        'name': data.name,
        'normalized_name': data.name.casefold(),
        'description': data.description,
        'strategy': data.strategy,
        'enabled': data.enabled,
        'thresholds': data.thresholds.model_dump(mode='json'),
        'retry_limit': data.retry_limit,
        'reservation_ttl_seconds': data.reservation_ttl_seconds,
        'metadata_json': data.metadata,
    }


def _member_values(data: ResourcePoolMemberInput, provider: Provider):
    values = data.model_dump(mode='json')
    values['provider_type'] = provider.type
    values['metadata_json'] = values.pop('metadata')
    return values


def _rule_values(data: PlacementRuleInput):
    return {
        'name': data.name, 'normalized_name': data.name.casefold(),
        'enabled': data.enabled, 'priority': data.priority,
        'conditions': data.conditions, 'actions': data.actions,
        'stop_processing': data.stop_processing,
    }


@router.get('/resource-pools')
def list_resource_pools(request: Request, limit: int = Query(default=200, ge=1, le=500),
                        actor=Depends(require('resource_pool.view')),
                        db=Depends(get_db, scope='function')):
    scope = request.state.resource_scope
    rows = list(db.scalars(select(ResourcePool).where(
        ResourcePool.tenant_id == scope.tenant_id,
        ResourcePool.project_id == scope.project_id,
    ).order_by(ResourcePool.name.asc()).limit(limit)))
    result = []
    for row in rows:
        count = int(db.scalar(select(func.count()).select_from(ResourcePoolMember).where(
            ResourcePoolMember.pool_id == row.id
        )) or 0)
        item = pool_public(row, members=count)
        item['active_deployments'] = active_deployments_for_pool(db, row.id)
        result.append(item)
    return {
        'items': result,
        'permissions': sorted(
            permission for permission in request.state.permissions
            if str(permission).startswith(('resource_pool.', 'placement.'))
        ),
    }


@router.post('/resource-pools', status_code=201)
def create_resource_pool(data: ResourcePoolInput, request: Request,
                         actor=Depends(require('resource_pool.create')),
                         db=Depends(get_db, scope='function')):
    scope = request.state.resource_scope
    exists = db.scalar(select(ResourcePool.id).where(
        ResourcePool.tenant_id == scope.tenant_id,
        ResourcePool.project_id == scope.project_id,
        ResourcePool.normalized_name == data.name.casefold(),
    ).limit(1))
    if exists:
        raise HTTPException(409, 'Resource Pool with this name already exists in the selected project')
    row = ResourcePool(
        tenant_id=scope.tenant_id, project_id=scope.project_id,
        created_by=actor.user_id, **_pool_values(data),
    )
    db.add(row)
    db.flush()
    audit(db, request, 'RESOURCE_POOL_CREATED', 'resource_pools', row.id)
    return pool_public(row, members=0)


@router.get('/resource-pools/{pool_id}')
def get_resource_pool(pool_id: str, request: Request,
                      actor=Depends(require('resource_pool.view')),
                      db=Depends(get_db, scope='function')):
    row = scoped_pool(db, request.state.resource_scope, pool_id)
    count = int(db.scalar(select(func.count()).select_from(ResourcePoolMember).where(
        ResourcePoolMember.pool_id == row.id
    )) or 0)
    result = pool_public(row, members=count)
    result['active_deployments'] = active_deployments_for_pool(db, row.id)
    return result


@router.put('/resource-pools/{pool_id}')
def update_resource_pool(pool_id: str, data: ResourcePoolInput, request: Request,
                         actor=Depends(require('resource_pool.edit')),
                         db=Depends(get_db, scope='function')):
    scope = request.state.resource_scope
    row = scoped_pool(db, scope, pool_id)
    duplicate = db.scalar(select(ResourcePool.id).where(
        ResourcePool.tenant_id == scope.tenant_id,
        ResourcePool.project_id == scope.project_id,
        ResourcePool.normalized_name == data.name.casefold(),
        ResourcePool.id != row.id,
    ).limit(1))
    if duplicate:
        raise HTTPException(409, 'Resource Pool with this name already exists in the selected project')
    for key, value in _pool_values(data).items():
        setattr(row, key, value)
    db.flush()
    audit(db, request, 'RESOURCE_POOL_UPDATED', 'resource_pools', row.id)
    return pool_public(row)


@router.delete('/resource-pools/{pool_id}')
def delete_resource_pool(pool_id: str, request: Request,
                         actor=Depends(require('resource_pool.delete')),
                         db=Depends(get_db, scope='function')):
    row = scoped_pool(db, request.state.resource_scope, pool_id)
    expire_reservations(db, row.id)
    active = db.scalar(select(ResourceReservation.id).where(
        ResourceReservation.pool_id == row.id,
        ResourceReservation.status == 'RESERVED',
    ).limit(1))
    if active:
        raise HTTPException(409, 'Resource Pool has active placement reservations')
    if active_deployments_for_pool(db, row.id):
        raise HTTPException(409, 'Resource Pool has active deployments')
    audit(db, request, 'RESOURCE_POOL_DELETED', 'resource_pools', row.id)
    db.delete(row)
    return {'deleted': True}


@router.get('/resource-pools/{pool_id}/members')
def list_pool_members(pool_id: str, request: Request,
                      actor=Depends(require('resource_pool.view')),
                      db=Depends(get_db, scope='function')):
    pool = scoped_pool(db, request.state.resource_scope, pool_id)
    rows = list(db.scalars(select(ResourcePoolMember).where(
        ResourcePoolMember.pool_id == pool.id
    ).order_by(ResourcePoolMember.priority.desc(), ResourcePoolMember.id.asc())))
    return {'items': [member_public(db, row) for row in rows]}


@router.post('/resource-pools/{pool_id}/members', status_code=201)
def add_pool_member(pool_id: str, data: ResourcePoolMemberInput, request: Request,
                    actor=Depends(require('resource_pool.edit')),
                    db=Depends(get_db, scope='function')):
    pool = scoped_pool(db, request.state.resource_scope, pool_id)
    provider = validate_member_target(db, request.state.resource_scope, data)
    row = ResourcePoolMember(
        pool_id=pool.id, created_by=actor.user_id,
        **_member_values(data, provider),
    )
    db.add(row)
    db.flush()
    audit(db, request, 'RESOURCE_POOL_MEMBER_ADDED', 'resource_pool_members', row.id)
    return member_public(db, row)


def _member(db, pool_id: str, member_id: str):
    row = db.scalar(select(ResourcePoolMember).where(
        ResourcePoolMember.id == str(member_id),
        ResourcePoolMember.pool_id == str(pool_id),
    ))
    if row is None:
        raise HTTPException(404, 'Resource Pool member not found')
    return row


@router.put('/resource-pools/{pool_id}/members/{member_id}')
def update_pool_member(pool_id: str, member_id: str, data: ResourcePoolMemberInput, request: Request,
                       actor=Depends(require('resource_pool.edit')),
                       db=Depends(get_db, scope='function')):
    scoped_pool(db, request.state.resource_scope, pool_id)
    row = _member(db, pool_id, member_id)
    provider = validate_member_target(db, request.state.resource_scope, data)
    old_maintenance = row.maintenance_mode
    for key, value in _member_values(data, provider).items():
        setattr(row, key, value)
    db.flush()
    audit(db, request, 'RESOURCE_POOL_UPDATED', 'resource_pool_members', row.id)
    if old_maintenance != row.maintenance_mode:
        audit(
            db, request,
            'MAINTENANCE_ENABLED' if row.maintenance_mode else 'MAINTENANCE_DISABLED',
            'resource_pool_members', row.id,
        )
    return member_public(db, row)


@router.delete('/resource-pools/{pool_id}/members/{member_id}')
def delete_pool_member(pool_id: str, member_id: str, request: Request,
                       actor=Depends(require('resource_pool.edit')),
                       db=Depends(get_db, scope='function')):
    scoped_pool(db, request.state.resource_scope, pool_id)
    row = _member(db, pool_id, member_id)
    active = db.scalar(select(ResourceReservation.id).where(
        ResourceReservation.member_id == row.id,
        ResourceReservation.status == 'RESERVED',
    ).limit(1))
    if active:
        raise HTTPException(409, 'Resource Pool member has an active reservation')
    audit(db, request, 'RESOURCE_POOL_MEMBER_REMOVED', 'resource_pool_members', row.id)
    db.delete(row)
    return {'deleted': True}


@router.post('/resource-pools/{pool_id}/members/{member_id}/maintenance')
def set_member_maintenance(pool_id: str, member_id: str, enabled: bool, request: Request,
                           actor=Depends(require('resource_pool.edit')),
                           db=Depends(get_db, scope='function')):
    scoped_pool(db, request.state.resource_scope, pool_id)
    row = _member(db, pool_id, member_id)
    row.maintenance_mode = bool(enabled)
    db.flush()
    audit(
        db, request,
        'MAINTENANCE_ENABLED' if enabled else 'MAINTENANCE_DISABLED',
        'resource_pool_members', row.id,
    )
    return member_public(db, row)


@router.get('/resource-pools/{pool_id}/rules')
def list_placement_rules(pool_id: str, request: Request,
                         actor=Depends(require('resource_pool.view')),
                         db=Depends(get_db, scope='function')):
    pool = scoped_pool(db, request.state.resource_scope, pool_id)
    rows = list(db.scalars(select(PlacementRule).where(
        PlacementRule.pool_id == pool.id
    ).order_by(PlacementRule.priority.desc(), PlacementRule.name.asc())))
    return {'items': [rule_public(row) for row in rows]}


@router.post('/resource-pools/{pool_id}/rules', status_code=201)
def create_placement_rule(pool_id: str, data: PlacementRuleInput, request: Request,
                          actor=Depends(require('resource_pool.edit')),
                          db=Depends(get_db, scope='function')):
    pool = scoped_pool(db, request.state.resource_scope, pool_id)
    duplicate = db.scalar(select(PlacementRule.id).where(
        PlacementRule.pool_id == pool.id,
        PlacementRule.normalized_name == data.name.casefold(),
    ).limit(1))
    if duplicate:
        raise HTTPException(409, 'Placement Rule with this name already exists in the pool')
    row = PlacementRule(pool_id=pool.id, created_by=actor.user_id, **_rule_values(data))
    db.add(row)
    db.flush()
    audit(db, request, 'RESOURCE_POOL_UPDATED', 'placement_rules', row.id)
    return rule_public(row)


def _rule(db, pool_id: str, rule_id: str):
    row = db.scalar(select(PlacementRule).where(
        PlacementRule.id == str(rule_id), PlacementRule.pool_id == str(pool_id)
    ))
    if row is None:
        raise HTTPException(404, 'Placement Rule not found')
    return row


@router.put('/resource-pools/{pool_id}/rules/{rule_id}')
def update_placement_rule(pool_id: str, rule_id: str, data: PlacementRuleInput, request: Request,
                          actor=Depends(require('resource_pool.edit')),
                          db=Depends(get_db, scope='function')):
    scoped_pool(db, request.state.resource_scope, pool_id)
    row = _rule(db, pool_id, rule_id)
    for key, value in _rule_values(data).items():
        setattr(row, key, value)
    db.flush()
    audit(db, request, 'RESOURCE_POOL_UPDATED', 'placement_rules', row.id)
    return rule_public(row)


@router.delete('/resource-pools/{pool_id}/rules/{rule_id}')
def delete_placement_rule(pool_id: str, rule_id: str, request: Request,
                          actor=Depends(require('resource_pool.edit')),
                          db=Depends(get_db, scope='function')):
    scoped_pool(db, request.state.resource_scope, pool_id)
    row = _rule(db, pool_id, rule_id)
    db.delete(row)
    audit(db, request, 'RESOURCE_POOL_UPDATED', 'placement_rules', row.id)
    return {'deleted': True}


@router.get('/resource-pools/{pool_id}/capacity')
def resource_pool_capacity(pool_id: str, request: Request,
                           actor=Depends(require('resource_pool.view')),
                           db=Depends(get_db, scope='function')):
    return pool_capacity(db, request.state.resource_scope, pool_id)


@router.get('/resource-pools/{pool_id}/health')
def resource_pool_health(pool_id: str, request: Request,
                         actor=Depends(require('resource_pool.view')),
                         db=Depends(get_db, scope='function')):
    capacity = pool_capacity(db, request.state.resource_scope, pool_id)
    return {
        'pool_id': capacity['pool_id'], 'pool': capacity['pool'],
        'status': capacity['health'], 'reason': capacity['reason'],
        'nodes_online': capacity['nodes_online'], 'nodes_offline': capacity['nodes_offline'],
        'nodes_maintenance': capacity['nodes_maintenance'],
    }


@router.get('/resource-pools/{pool_id}/reservations')
def resource_pool_reservations(pool_id: str, request: Request, limit: int = Query(default=200, ge=1, le=1000),
                               actor=Depends(require('placement.view')),
                               db=Depends(get_db, scope='function')):
    pool = scoped_pool(db, request.state.resource_scope, pool_id)
    expire_reservations(db, pool.id)
    rows = list(db.scalars(select(ResourceReservation).where(
        ResourceReservation.pool_id == pool.id
    ).order_by(ResourceReservation.created_at.desc()).limit(limit)))
    return {'items': [reservation_public(db, row) for row in rows]}


@router.get('/resource-pools/{pool_id}/placements')
def resource_pool_placements(pool_id: str, request: Request, limit: int = Query(default=200, ge=1, le=1000),
                             actor=Depends(require('placement.view')),
                             db=Depends(get_db, scope='function')):
    pool = scoped_pool(db, request.state.resource_scope, pool_id)
    rows = list(db.scalars(select(PlacementDecision).where(
        PlacementDecision.pool_id == pool.id
    ).order_by(PlacementDecision.created_at.desc()).limit(limit)))
    return {'items': [decision_public(db, row) for row in rows]}


def _check_override_permission(request: Request, data: PlacementRequest):
    if data.override and data.placement_mode != 'FIXED' and 'placement.override' not in request.state.permissions:
        raise HTTPException(403, 'placement.override required for manual placement override')


@router.post('/placement/simulate')
def simulate_placement(data: PlacementRequest, request: Request,
                       actor=Depends(require('placement.simulate')),
                       db=Depends(get_db, scope='function')):
    _check_override_permission(request, data)
    simulation = data.model_copy(update={'reserve': False})
    resolution = resolve_placement(
        db, request.state.resource_scope, simulation, actor.user_id,
        reserve=False, is_override=bool(data.override),
    )
    audit(db, request, 'PLACEMENT_SIMULATED', 'placement_decisions', resolution.decision.id)
    result = decision_public(db, resolution.decision)
    result['selected'] = {
        'pool': resolution.pool.name if resolution.pool else None,
        'provider': resolution.selected.get('provider_type'),
        'platform': resolution.selected.get('platform'),
        'node': resolution.selected.get('node'),
        'storage': resolution.selected.get('storage'),
        'network': resolution.selected.get('network'),
        'score': resolution.selected.get('score'),
    }
    return result


@router.post('/placement/resolve')
def resolve_placement_api(data: PlacementRequest, request: Request,
                          actor=Depends(require('placement.view')),
                          db=Depends(get_db, scope='function')):
    _check_override_permission(request, data)
    if data.reserve and 'resource_pool.assign' not in request.state.permissions:
        raise HTTPException(403, 'resource_pool.assign required to reserve a placement target')
    resolution = resolve_placement(
        db, request.state.resource_scope, data, actor.user_id,
        reserve=data.reserve, is_override=bool(data.override),
    )
    audit(db, request, 'PLACEMENT_OVERRIDE' if data.override else 'PLACEMENT_SELECTED',
          'placement_decisions', resolution.decision.id)
    if resolution.reservation:
        audit(db, request, 'RESOURCE_RESERVED', 'resource_reservations', resolution.reservation.id)
    result = decision_public(db, resolution.decision)
    result['reservation'] = reservation_public(db, resolution.reservation) if resolution.reservation else None
    return result


@router.get('/placement/{deployment_id}')
def get_deployment_placement(deployment_id: str, request: Request,
                             actor=Depends(require('placement.view')),
                             db=Depends(get_db, scope='function')):
    return placement_for_deployment(db, request.state.resource_scope, deployment_id)


def _logical_public(row, mappings):
    return {
        'id': row.id, 'name': row.name, 'description': row.description,
        'enabled': row.enabled, 'metadata': row.metadata_json or {},
        'mappings': mappings, 'created_at': row.created_at, 'updated_at': row.updated_at,
    }


@router.get('/logical-networks')
def list_logical_networks(request: Request,
                          actor=Depends(require('resource_pool.view')),
                          db=Depends(get_db, scope='function')):
    scope = request.state.resource_scope
    rows = list(db.scalars(select(LogicalNetwork).where(
        LogicalNetwork.tenant_id == scope.tenant_id,
        LogicalNetwork.project_id == scope.project_id,
    ).order_by(LogicalNetwork.name.asc())))
    result = []
    for row in rows:
        mappings = list(db.scalars(select(NetworkMapping).where(NetworkMapping.logical_network_id == row.id)))
        result.append(_logical_public(row, [{
            'id': m.id, 'provider_id': m.provider_id, 'node': m.node,
            'network': m.network, 'metadata': m.metadata_json or {},
        } for m in mappings]))
    return {'items': result}


@router.post('/logical-networks', status_code=201)
def create_logical_network(data: LogicalClassInput, request: Request,
                           actor=Depends(require('resource_pool.edit')),
                           db=Depends(get_db, scope='function')):
    scope = request.state.resource_scope
    row = LogicalNetwork(
        tenant_id=scope.tenant_id, project_id=scope.project_id,
        name=data.name, normalized_name=data.name.casefold(), description=data.description,
        enabled=data.enabled, metadata_json=data.metadata, created_by=actor.user_id,
    )
    db.add(row)
    db.flush()
    audit(db, request, 'RESOURCE_POOL_UPDATED', 'logical_networks', row.id)
    return _logical_public(row, [])


@router.post('/logical-networks/{logical_id}/mappings', status_code=201)
def add_network_mapping(logical_id: str, data: MappingInput, request: Request,
                        actor=Depends(require('resource_pool.edit')),
                        db=Depends(get_db, scope='function')):
    scope = request.state.resource_scope
    logical = db.scalar(select(LogicalNetwork).where(
        LogicalNetwork.id == logical_id,
        LogicalNetwork.tenant_id == scope.tenant_id,
        LogicalNetwork.project_id == scope.project_id,
    ))
    if logical is None:
        raise HTTPException(404, 'Logical network not found')
    provider = db.get(Provider, data.provider_id)
    if provider is None:
        raise HTTPException(404, 'Provider not found in selected project')
    row = NetworkMapping(
        logical_network_id=logical.id, provider_id=data.provider_id, node=data.node,
        network=data.target, metadata_json=data.metadata, created_by=actor.user_id,
    )
    db.add(row)
    db.flush()
    return {'id': row.id, 'provider_id': row.provider_id, 'node': row.node, 'network': row.network}


@router.get('/storage-classes')
def list_storage_classes(request: Request,
                         actor=Depends(require('resource_pool.view')),
                         db=Depends(get_db, scope='function')):
    scope = request.state.resource_scope
    rows = list(db.scalars(select(StorageClass).where(
        StorageClass.tenant_id == scope.tenant_id,
        StorageClass.project_id == scope.project_id,
    ).order_by(StorageClass.name.asc())))
    result = []
    for row in rows:
        mappings = list(db.scalars(select(StorageMapping).where(StorageMapping.storage_class_id == row.id)))
        result.append(_logical_public(row, [{
            'id': m.id, 'provider_id': m.provider_id, 'node': m.node,
            'storage': m.storage, 'metadata': m.metadata_json or {},
        } for m in mappings]))
    return {'items': result}


@router.post('/storage-classes', status_code=201)
def create_storage_class(data: LogicalClassInput, request: Request,
                         actor=Depends(require('resource_pool.edit')),
                         db=Depends(get_db, scope='function')):
    scope = request.state.resource_scope
    row = StorageClass(
        tenant_id=scope.tenant_id, project_id=scope.project_id,
        name=data.name, normalized_name=data.name.casefold(), description=data.description,
        enabled=data.enabled, metadata_json=data.metadata, created_by=actor.user_id,
    )
    db.add(row)
    db.flush()
    audit(db, request, 'RESOURCE_POOL_UPDATED', 'storage_classes', row.id)
    return _logical_public(row, [])


@router.post('/storage-classes/{storage_class_id}/mappings', status_code=201)
def add_storage_mapping(storage_class_id: str, data: MappingInput, request: Request,
                        actor=Depends(require('resource_pool.edit')),
                        db=Depends(get_db, scope='function')):
    scope = request.state.resource_scope
    storage_class = db.scalar(select(StorageClass).where(
        StorageClass.id == storage_class_id,
        StorageClass.tenant_id == scope.tenant_id,
        StorageClass.project_id == scope.project_id,
    ))
    if storage_class is None:
        raise HTTPException(404, 'Storage class not found')
    provider = db.get(Provider, data.provider_id)
    if provider is None:
        raise HTTPException(404, 'Provider not found in selected project')
    row = StorageMapping(
        storage_class_id=storage_class.id, provider_id=data.provider_id, node=data.node,
        storage=data.target, metadata_json=data.metadata, created_by=actor.user_id,
    )
    db.add(row)
    db.flush()
    return {'id': row.id, 'provider_id': row.provider_id, 'node': row.node, 'storage': row.storage}
