"""Resource Pool CRUD helpers and provider-backed member validation."""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import func, select

from app.models import Credential, Deployment, Provider, now
from app.providers.registry import provider_for
from app.resource_scope.database import reference_visible
from app.resource_pools.models import (
    LogicalNetwork, NetworkMapping, PlacementDecision, PlacementRule, ResourcePool,
    ResourcePoolMember, ResourceReservation, StorageClass, StorageMapping,
)

ACTIVE_JOB_STATES = {'queued', 'running', 'waiting_approval', 'waiting_provider'}


def scoped_pool(db, scope, pool_id: str, *, active_only: bool = False) -> ResourcePool:
    query = select(ResourcePool).where(
        ResourcePool.id == str(pool_id),
        ResourcePool.tenant_id == scope.tenant_id,
        ResourcePool.project_id == scope.project_id,
    )
    if active_only:
        query = query.where(ResourcePool.enabled.is_(True))
    row = db.scalar(query)
    if row is None:
        raise HTTPException(404, 'Resource Pool not found in the selected project')
    return row


def pool_public(row: ResourcePool, *, members: int | None = None) -> dict:
    result = {
        'id': row.id, 'tenant_id': row.tenant_id, 'project_id': row.project_id,
        'name': row.name, 'description': row.description, 'strategy': row.strategy,
        'enabled': row.enabled, 'thresholds': row.thresholds or {},
        'retry_limit': row.retry_limit, 'reservation_ttl_seconds': row.reservation_ttl_seconds,
        'metadata': row.metadata_json or {}, 'created_by': row.created_by,
        'created_at': row.created_at, 'updated_at': row.updated_at,
    }
    if members is not None:
        result['members'] = members
    return result


def member_public(db, row: ResourcePoolMember) -> dict:
    provider = db.get(Provider, row.provider_id)
    return {
        'id': row.id, 'pool_id': row.pool_id, 'provider_id': row.provider_id,
        'platform_id': row.platform_id, 'platform_name': provider.name if provider else None,
        'provider_type': row.provider_type, 'cluster': row.cluster, 'node': row.node,
        'datacenter': row.datacenter, 'storage': row.storage, 'network': row.network,
        'enabled': row.enabled, 'priority': row.priority, 'weight': row.weight,
        'maintenance_mode': row.maintenance_mode, 'max_vm_count': row.max_vm_count,
        'max_cpu_usage': row.max_cpu_usage, 'max_memory_usage': row.max_memory_usage,
        'min_free_memory_mb': row.min_free_memory_mb,
        'min_free_storage_gb': row.min_free_storage_gb,
        'tags': row.tags or [], 'metadata': row.metadata_json or {},
        'created_at': row.created_at, 'updated_at': row.updated_at,
    }


def rule_public(row: PlacementRule) -> dict:
    return {
        'id': row.id, 'pool_id': row.pool_id, 'name': row.name, 'enabled': row.enabled,
        'priority': row.priority, 'conditions': row.conditions or {}, 'actions': row.actions or {},
        'stop_processing': row.stop_processing, 'created_by': row.created_by,
        'created_at': row.created_at, 'updated_at': row.updated_at,
    }


def decision_public(db, row: PlacementDecision) -> dict:
    pool = db.get(ResourcePool, row.pool_id) if row.pool_id else None
    provider = db.get(Provider, row.selected_provider_id)
    return {
        'id': row.id, 'deployment_id': row.deployment_id, 'vm_id': row.vm_id,
        'blueprint_id': row.blueprint_id, 'pool_id': row.pool_id,
        'pool': pool.name if pool else None, 'rule_ids': row.rule_ids or [],
        'placement_mode': row.placement_mode, 'selected_provider': row.selected_provider,
        'selected_provider_id': row.selected_provider_id,
        'selected_platform': row.selected_platform or (provider.name if provider else None),
        'selected_member_id': row.selected_member_id, 'selected_node': row.selected_node,
        'selected_storage': row.selected_storage, 'selected_network': row.selected_network,
        'score': round(float(row.score or 0), 2), 'candidates': row.candidates_snapshot or [],
        'decision_reason': row.decision_reason, 'request': row.request_snapshot or {},
        'is_override': row.is_override, 'created_at': row.created_at,
    }


def reservation_public(db, row: ResourceReservation) -> dict:
    provider = db.get(Provider, row.provider_id)
    return {
        'id': row.id, 'pool_id': row.pool_id, 'member_id': row.member_id,
        'decision_id': row.decision_id, 'deployment_id': row.deployment_id, 'job_id': row.job_id,
        'provider_id': row.provider_id, 'platform': provider.name if provider else None,
        'node': row.node, 'storage': row.storage, 'cpu': row.cpu,
        'memory_mb': row.memory_mb, 'storage_gb': row.storage_gb,
        'status': row.status, 'expires_at': row.expires_at, 'released_at': row.released_at,
        'metadata': row.metadata_json or {}, 'created_at': row.created_at, 'updated_at': row.updated_at,
    }


def validate_member_target(db, scope, data):
    provider = db.get(Provider, int(data.provider_id))
    if provider is None or not reference_visible(db, 'provider', int(data.provider_id), scope):
        raise HTTPException(404, 'Provider is not assigned to the selected project')
    credential = db.get(Credential, provider.credentials_id)
    if credential is None or not reference_visible(db, 'credential', provider.credentials_id, scope):
        raise HTTPException(409, 'Provider credential is not assigned to the selected project')
    if data.provider_type and data.provider_type != provider.type:
        raise HTTPException(422, 'Resource Pool member provider_type does not match the selected provider')
    adapter = provider_for(credential)
    nodes = list(adapter.discover('nodes') or [])
    if data.node and not any(str(row.get('node') or row.get('id') or '') == data.node for row in nodes):
        raise HTTPException(422, 'Selected node does not exist on the provider')
    if data.storage:
        storages = list(adapter.discover('storages', data.node) or [])
        if not any(str(row.get('storage') or row.get('id') or '') == data.storage for row in storages):
            raise HTTPException(422, 'Selected storage does not exist on the provider target')
    if data.network:
        networks = list(adapter.discover('networks', data.node) or [])
        if not any(str(row.get('iface') or row.get('network') or row.get('id') or '') == data.network for row in networks):
            raise HTTPException(422, 'Selected network does not exist on the provider target')
    return provider


def active_deployments_for_pool(db, pool_id: str) -> int:
    deployment_ids = [value for value in db.scalars(select(PlacementDecision.deployment_id).where(
        PlacementDecision.pool_id == pool_id,
        PlacementDecision.deployment_id.is_not(None),
    )) if value]
    if not deployment_ids:
        return 0
    return int(db.scalar(select(func.count()).select_from(Deployment).where(
        Deployment.id.in_(deployment_ids), Deployment.status.in_(ACTIVE_JOB_STATES)
    )) or 0)


def expire_reservations(db, pool_id: str | None = None) -> int:
    query = select(ResourceReservation).where(
        ResourceReservation.status == 'RESERVED',
        ResourceReservation.expires_at <= now(),
    ).with_for_update()
    if pool_id:
        query = query.where(ResourceReservation.pool_id == str(pool_id))
    rows = list(db.scalars(query))
    stamp = now()
    for row in rows:
        row.status = 'EXPIRED'
        row.released_at = stamp
    return len(rows)


def scoped_logical_network(db, scope, value: str) -> LogicalNetwork:
    normalized = str(value).strip().casefold()
    row = db.scalar(select(LogicalNetwork).where(
        LogicalNetwork.tenant_id == scope.tenant_id,
        LogicalNetwork.project_id == scope.project_id,
        ((LogicalNetwork.id == str(value)) | (LogicalNetwork.normalized_name == normalized)),
        LogicalNetwork.enabled.is_(True),
    ))
    if row is None:
        raise HTTPException(422, 'Logical network is not configured in the selected project')
    return row


def scoped_storage_class(db, scope, value: str) -> StorageClass:
    normalized = str(value).strip().casefold()
    row = db.scalar(select(StorageClass).where(
        StorageClass.tenant_id == scope.tenant_id,
        StorageClass.project_id == scope.project_id,
        ((StorageClass.id == str(value)) | (StorageClass.normalized_name == normalized)),
        StorageClass.enabled.is_(True),
    ))
    if row is None:
        raise HTTPException(422, 'Storage class is not configured in the selected project')
    return row


def network_mapping_for(db, scope, logical_value: str, provider_id: int, node: str | None):
    logical = scoped_logical_network(db, scope, logical_value)
    rows = list(db.scalars(select(NetworkMapping).where(
        NetworkMapping.logical_network_id == logical.id,
        NetworkMapping.provider_id == int(provider_id),
    )))
    exact = next((row for row in rows if row.node and row.node == node), None)
    fallback = next((row for row in rows if not row.node), None)
    return logical, exact or fallback


def storage_mapping_for(db, scope, class_value: str, provider_id: int, node: str | None):
    storage_class = scoped_storage_class(db, scope, class_value)
    rows = list(db.scalars(select(StorageMapping).where(
        StorageMapping.storage_class_id == storage_class.id,
        StorageMapping.provider_id == int(provider_id),
    )))
    exact = next((row for row in rows if row.node and row.node == node), None)
    fallback = next((row for row in rows if not row.node), None)
    return storage_class, exact or fallback
