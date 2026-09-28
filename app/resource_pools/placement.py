"""Explainable, concurrency-safe, provider-agnostic placement engine."""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Iterable

from fastapi import HTTPException
from sqlalchemy import func, select, update

from app.database import session
from app.executors.base import ExecutionFailed
from app.models import Blueprint, Credential, Deployment, Job, Provider, now
from app.policy_engine.engine import evaluate_condition
from app.providers.registry import provider_for
from app.resource_scope.database import reference_visible
from app.resource_scope.authorization import Scope
from app.resource_pools.models import (
    PlacementDecision, PlacementRule, ResourcePool, ResourcePoolMember, ResourceReservation,
)
from app.resource_pools.schemas import PlacementRequest
from app.resource_pools.service import (
    decision_public, expire_reservations, network_mapping_for, scoped_pool,
    storage_mapping_for,
)


class NoValidPlacementTarget(HTTPException):
    def __init__(self, detail: Any):
        if isinstance(detail, str):
            detail = {'code': 'NO_VALID_PLACEMENT_TARGET', 'message': detail}
        elif isinstance(detail, dict):
            detail = {'code': 'NO_VALID_PLACEMENT_TARGET', **detail}
        super().__init__(409, detail)


@dataclass
class Resolution:
    pool: ResourcePool | None
    selected: dict
    candidates: list[dict]
    matched_rules: list[PlacementRule]
    decision: PlacementDecision
    reservation: ResourceReservation | None


def _pct(value: float, total: float) -> float:
    return 0.0 if not total else max(0.0, min(100.0, float(value) * 100.0 / float(total)))


def _ratio_free(free: float, total: float) -> float:
    return 0.0 if not total else max(0.0, min(1.0, float(free) / float(total)))


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def placement_context(scope, request: PlacementRequest | dict, *, blueprint=None) -> dict:
    values = request.model_dump(mode='json') if hasattr(request, 'model_dump') else dict(request or {})
    environment = values.get('environment')
    apmid = values.get('apmid')
    organization = values.get('organization')
    project = values.get('project')
    tags = values.get('tags') or []
    blueprint_id = values.get('blueprint_id') or getattr(blueprint, 'id', None)
    blueprint_name = getattr(blueprint, 'name', None)
    blueprint_slug = getattr(blueprint, 'slug', None)
    resource = {
        'type': 'vm',
        'apmid': apmid,
        'environment': environment,
        'provider_type': values.get('provider_type'),
        'location': values.get('location'),
        'operating_system': values.get('operating_system'),
        'vm_size': values.get('vm_size'),
        'cpu': values.get('cpu'),
        'ram': values.get('ram_mb'),
        'memory': values.get('ram_mb'),
        'disk_size': values.get('disk_gb'),
        'tags': tags,
        'metadata': values.get('metadata') or {},
    }
    return {
        'tenant': scope.tenant_id,
        'tenant_id': scope.tenant_id,
        'organization': organization,
        'project': project,
        'project_id': scope.project_id,
        'APMID': apmid,
        'apmid': apmid,
        'ENV': environment,
        'environment': environment,
        'blueprint': {
            'id': blueprint_id,
            'name': blueprint_name,
            'slug': blueprint_slug,
        },
        'operating_system': values.get('operating_system'),
        'vm_size': values.get('vm_size'),
        'cpu': values.get('cpu'),
        'ram': values.get('ram_mb'),
        'memory': values.get('ram_mb'),
        'disk_size': values.get('disk_gb'),
        'tags': tags,
        'provider_type': values.get('provider_type'),
        'location': values.get('location'),
        'metadata': values.get('metadata') or {},
        'scope': {
            'tenant_id': scope.tenant_id,
            'project_id': scope.project_id,
            'organization': organization,
            'project': project,
            'key': f'{scope.tenant_id}:{scope.project_id}',
        },
        'resource': resource,
    }


def _rule_rows(db, scope, *, pool_id: str | None = None) -> list[PlacementRule]:
    query = (
        select(PlacementRule)
        .join(ResourcePool, ResourcePool.id == PlacementRule.pool_id)
        .where(
            ResourcePool.tenant_id == scope.tenant_id,
            ResourcePool.project_id == scope.project_id,
            ResourcePool.enabled.is_(True),
            PlacementRule.enabled.is_(True),
        )
        .order_by(PlacementRule.priority.desc(), PlacementRule.created_at.asc(), PlacementRule.id.asc())
    )
    if pool_id:
        query = query.where(PlacementRule.pool_id == str(pool_id))
    return list(db.scalars(query))


def _matching_rules(db, scope, context: dict, *, pool_id: str | None = None) -> list[PlacementRule]:
    matched = []
    for rule in _rule_rows(db, scope, pool_id=pool_id):
        if evaluate_condition(rule.conditions or {}, context):
            matched.append(rule)
            if rule.stop_processing:
                break
    return matched


def _resolve_policy_pool(db, scope, context: dict) -> tuple[ResourcePool, list[PlacementRule]]:
    matched = _matching_rules(db, scope, context)
    if not matched:
        raise NoValidPlacementTarget('No Placement Rule matched this deployment context')
    selected_pool_id = matched[0].pool_id
    pool = scoped_pool(db, scope, selected_pool_id, active_only=True)
    selected_rules = [rule for rule in matched if rule.pool_id == selected_pool_id]
    if not selected_rules:
        selected_rules = _matching_rules(db, scope, context, pool_id=selected_pool_id)
    return pool, selected_rules


def _pool_and_rules(db, scope, request: PlacementRequest, context: dict):
    if request.placement_mode == 'POLICY':
        return _resolve_policy_pool(db, scope, context)
    if request.placement_mode == 'POOL':
        pool = scoped_pool(db, scope, request.pool_id, active_only=True)
        return pool, _matching_rules(db, scope, context, pool_id=pool.id)
    return None, []


def _rule_actions(rules: Iterable[PlacementRule]) -> dict:
    result = {
        'allowed_provider_types': None,
        'allowed_provider_ids': None,
        'allowed_nodes': None,
        'allowed_locations': None,
        'required_tags': set(),
        'required_network': None,
        'required_storage_class': None,
        'preferred_location': None,
        'fallback_locations': [],
        'hard_constraints': [],
        'soft_preferences': [],
        'affinity': None,
    }
    for rule in rules:
        actions = dict(rule.actions or {})
        for key in ('allowed_provider_types', 'allowed_provider_ids', 'allowed_nodes', 'allowed_locations'):
            if key in actions:
                values = set(actions.get(key) or [])
                result[key] = values if result[key] is None else result[key] & values
        result['required_tags'].update(str(x) for x in (actions.get('required_tags') or []))
        for key in ('required_network', 'required_storage_class', 'preferred_location', 'affinity'):
            if actions.get(key) is not None:
                result[key] = actions.get(key)
        result['fallback_locations'].extend(str(x) for x in (actions.get('fallback_locations') or []))
        hard = actions.get('hard_constraints')
        if hard:
            result['hard_constraints'].append(hard)
        soft = actions.get('soft_preferences')
        if soft:
            result['soft_preferences'].append(soft)
    return result


def _constraint_condition(value: Any) -> dict:
    if isinstance(value, dict) and any(k in value for k in ('all', 'any', 'not', 'field')):
        return value
    if isinstance(value, dict):
        return {'all': [
            {'field': str(key), 'operator': 'equals', 'value': expected}
            for key, expected in value.items()
        ]}
    return {}


def _candidate_context(candidate: dict, request: PlacementRequest) -> dict:
    capacity = candidate.get('capacity') or {}
    return {
        'provider_id': candidate.get('provider_id'),
        'provider_type': candidate.get('provider_type'),
        'platform': candidate.get('platform'),
        'cluster': candidate.get('cluster'),
        'node': candidate.get('node'),
        'location': candidate.get('location'),
        'storage': candidate.get('storage'),
        'network': candidate.get('network'),
        'tags': candidate.get('tags') or [],
        'cpu_usage': capacity.get('cpu_usage_pct'),
        'memory_usage': capacity.get('memory_usage_pct'),
        'storage_usage': capacity.get('storage_usage_pct'),
        'free_memory_mb': capacity.get('memory_mb_free'),
        'free_storage_gb': capacity.get('storage_gb_free'),
        'vm_count': capacity.get('vm_count'),
        'request': request.model_dump(mode='json'),
    }


def _logical_target(db, scope, adapter, member, provider, node: str, request: PlacementRequest):
    network = member.network
    storage = member.storage
    mapping_notes = []
    if request.logical_network:
        logical, mapping = network_mapping_for(db, scope, request.logical_network, provider.id, node)
        if mapping is None:
            return None, None, [f'logical network {logical.name} has no mapping for this provider/node']
        network = mapping.network
        mapping_notes.append(f'logical network {logical.name} -> {network}')
    if request.storage_class:
        storage_class, mapping = storage_mapping_for(db, scope, request.storage_class, provider.id, node)
        if mapping is None:
            return None, None, [f'storage class {storage_class.name} has no mapping for this provider/node']
        storage = mapping.storage
        mapping_notes.append(f'storage class {storage_class.name} -> {storage}')

    storages = list(adapter.get_storages({'node': node}) or [])
    networks = list(adapter.get_networks({'node': node}) or [])

    if storage:
        matches = [row for row in storages if str(row.get('storage') or row.get('id') or '') == str(storage)]
        if not matches:
            return None, None, [f'storage {storage} is unavailable on {node}']
        storage_row = matches[0]
    else:
        eligible = [
            row for row in storages
            if row.get('active', row.get('enabled', True)) not in {False, 0, '0'}
            and 'images' in str(row.get('content') or 'images')
        ]
        storage_row = max(eligible, key=lambda row: _safe_float(row.get('avail') or row.get('free')), default=None)
        storage = str((storage_row or {}).get('storage') or (storage_row or {}).get('id') or '') or None
    if storage_row is None:
        return None, None, ['no active VM storage is available']

    if storage_row.get('active', storage_row.get('enabled', True)) in {False, 0, '0'}:
        return None, None, [f'storage {storage} is inactive']

    if network:
        matches = [row for row in networks if str(row.get('iface') or row.get('network') or row.get('id') or '') == str(network)]
        if not matches:
            return None, None, [f'network {network} is unavailable on {node}']
        network_row = matches[0]
    else:
        eligible = [
            row for row in networks
            if row.get('active', True) not in {False, 0, '0'}
            and str(row.get('type') or '').lower() in {'bridge', 'linux_bridge', 'ovsbridge', 'network', 'vnet', 'subnet', ''}
        ]
        network_row = eligible[0] if eligible else (networks[0] if networks else None)
        network = str((network_row or {}).get('iface') or (network_row or {}).get('network') or (network_row or {}).get('id') or '') or None
    if network_row is None:
        return None, None, ['no network is available']
    return storage, network, mapping_notes


def _provider_template_node(adapter, request: PlacementRequest):
    if request.template_id is None:
        return None, None
    try:
        templates = list(adapter.discover('templates') or [])
    except Exception:
        return None, 'template discovery failed'
    match = next((row for row in templates if str(row.get('vmid') or row.get('id') or '') == str(request.template_id)), None)
    if match is None:
        return None, f'template {request.template_id} is not present on this provider'
    return str(match.get('node') or '') or None, None


def _collect_pool_candidates(db, scope, pool: ResourcePool, request: PlacementRequest, actions: dict) -> list[dict]:
    members = list(db.scalars(select(ResourcePoolMember).where(
        ResourcePoolMember.pool_id == pool.id,
    ).order_by(ResourcePoolMember.priority.desc(), ResourcePoolMember.id.asc())))
    candidates = []
    provider_cache: dict[int, tuple[Provider, Any] | None] = {}

    for member in members:
        base = {
            'member_id': member.id, 'provider_id': member.provider_id,
            'provider_type': member.provider_type, 'cluster': member.cluster,
            'location': member.datacenter or member.cluster,
            'priority': member.priority, 'weight': member.weight,
            'tags': member.tags or [], 'status': 'AVAILABLE', 'score': 0.0,
            'reasons': [],
        }
        if not member.enabled:
            candidates.append({**base, 'status': 'REJECTED', 'reasons': ['member disabled']})
            continue
        if member.maintenance_mode:
            candidates.append({**base, 'status': 'REJECTED', 'reasons': ['member in maintenance mode']})
            continue
        if actions['allowed_provider_types'] is not None and member.provider_type not in actions['allowed_provider_types']:
            candidates.append({**base, 'status': 'REJECTED', 'reasons': ['provider type blocked by hard constraint']})
            continue
        if actions['allowed_provider_ids'] is not None and member.provider_id not in {int(x) for x in actions['allowed_provider_ids']}:
            candidates.append({**base, 'status': 'REJECTED', 'reasons': ['provider blocked by hard constraint']})
            continue
        if request.provider_type and member.provider_type != request.provider_type:
            candidates.append({**base, 'status': 'REJECTED', 'reasons': ['provider type does not match request']})
            continue
        required_tags = set(actions['required_tags'])
        if required_tags and not required_tags <= set(member.tags or []):
            candidates.append({**base, 'status': 'REJECTED', 'reasons': ['member is missing required tag(s)']})
            continue

        cached = provider_cache.get(member.provider_id, 'missing')
        if cached == 'missing':
            provider = db.get(Provider, member.provider_id)
            if provider is None or not reference_visible(db, 'provider', member.provider_id, scope):
                provider_cache[member.provider_id] = None
            else:
                credential = db.get(Credential, provider.credentials_id)
                if credential is None or not reference_visible(db, 'credential', provider.credentials_id, scope):
                    provider_cache[member.provider_id] = None
                else:
                    try:
                        provider_cache[member.provider_id] = (provider, provider_for(credential))
                    except Exception:
                        provider_cache[member.provider_id] = None
            cached = provider_cache[member.provider_id]
        if cached is None:
            candidates.append({**base, 'status': 'REJECTED', 'reasons': ['provider or credential unavailable in project scope']})
            continue
        provider, adapter = cached
        base['platform'] = provider.name

        try:
            targets = list(adapter.get_placement_targets() or [])
        except Exception as exc:
            candidates.append({
                **base, 'status': 'REJECTED',
                'reasons': [f'provider capacity discovery failed: {exc.__class__.__name__}'],
            })
            continue
        if member.node:
            targets = [target for target in targets if str(target.get('node') or target.get('id') or '') == member.node]
        if member.cluster:
            targets = [target for target in targets if not target.get('cluster') or str(target.get('cluster')) == member.cluster]
        if not targets:
            candidates.append({**base, 'status': 'REJECTED', 'reasons': ['member has no matching placement target']})
            continue

        template_node, template_error = _provider_template_node(adapter, request)
        if template_error:
            candidates.append({**base, 'status': 'REJECTED', 'reasons': [template_error]})
            continue

        for target in targets:
            node = str(target.get('node') or target.get('id') or '')
            candidate = {**base, 'node': node}
            candidate['cluster'] = target.get('cluster') or member.cluster
            candidate['location'] = (
                target.get('datacenter') or target.get('location')
                or member.datacenter or target.get('cluster') or member.cluster or provider.name
            )
            if actions['allowed_nodes'] is not None and node not in {str(x) for x in actions['allowed_nodes']}:
                candidate['status'] = 'REJECTED'
                candidate['reasons'] = ['node blocked by hard constraint']
                candidates.append(candidate)
                continue
            if actions['allowed_locations'] is not None and str(candidate['location']) not in {str(x) for x in actions['allowed_locations']}:
                candidate['status'] = 'REJECTED'
                candidate['reasons'] = ['location blocked by hard constraint']
                candidates.append(candidate)
                continue
            if not target.get('online', str(target.get('status') or '').lower() in {'online', 'available', 'connected'}):
                candidate['status'] = 'REJECTED'
                candidate['reasons'] = ['node offline']
                candidates.append(candidate)
                continue
            try:
                storage, network, mapping_notes = _logical_target(
                    db, scope, adapter, member, provider, node, request
                )
            except HTTPException as exc:
                candidate['status'] = 'REJECTED'
                candidate['reasons'] = [str(exc.detail)]
                candidates.append(candidate)
                continue
            if storage is None or network is None:
                candidate['status'] = 'REJECTED'
                candidate['reasons'] = mapping_notes
                candidates.append(candidate)
                continue
            candidate['storage'] = storage
            candidate['network'] = network
            candidate['template_node'] = template_node
            candidate['reasons'] = mapping_notes
            if actions.get('required_network') and str(network) != str(actions['required_network']):
                candidate['status'] = 'REJECTED'
                candidate['reasons'].append('network violates hard constraint')
                candidates.append(candidate)
                continue
            if actions.get('required_storage_class') and str(request.storage_class or '') != str(actions['required_storage_class']):
                candidate['status'] = 'REJECTED'
                candidate['reasons'].append('storage class violates hard constraint')
                candidates.append(candidate)
                continue
            try:
                capacity = adapter.get_capacity({'node': node, 'storage': storage})
            except Exception as exc:
                candidate['status'] = 'REJECTED'
                candidate['reasons'].append(f'capacity check failed: {exc.__class__.__name__}')
                candidates.append(candidate)
                continue
            candidate['capacity'] = dict(capacity or {})
            candidate['_member'] = member
            candidate['_provider'] = provider
            candidate['_adapter'] = adapter
            candidates.append(candidate)
    return candidates


def _active_job_count(db, provider_id: int, node: str | None) -> int:
    rows = list(db.scalars(
        select(Deployment).where(
            Deployment.provider_id == int(provider_id),
            Deployment.status.in_({'queued', 'running', 'waiting_provider', 'waiting_approval'}),
        )
    ))
    return sum(1 for row in rows if not node or str((row.variables or {}).get('node') or '') == str(node))


def _reservation_usage(db, candidate: dict) -> dict:
    rows = list(db.scalars(select(ResourceReservation).where(
        ResourceReservation.status == 'RESERVED',
        ResourceReservation.expires_at > now(),
        ResourceReservation.provider_id == int(candidate['provider_id']),
        ResourceReservation.node == candidate.get('node'),
    )))
    storage = candidate.get('storage')
    return {
        'cpu': sum(int(row.cpu or 0) for row in rows),
        'memory_mb': sum(int(row.memory_mb or 0) for row in rows),
        'storage_gb': sum(int(row.storage_gb or 0) for row in rows if not storage or row.storage == storage),
        'count': len(rows),
    }


def _apply_capacity_constraints(db, pool: ResourcePool, request: PlacementRequest, candidate: dict, hard_actions: list) -> dict:
    if candidate.get('status') == 'REJECTED':
        return candidate
    member = candidate['_member']
    capacity = dict(candidate.get('capacity') or {})
    reserved = _reservation_usage(db, candidate)
    cpu_total = _safe_float(capacity.get('cpu_total'))
    cpu_used = _safe_float(capacity.get('cpu_used'))
    mem_total = _safe_float(capacity.get('memory_mb_total'))
    mem_used = _safe_float(capacity.get('memory_mb_used'))
    storage_total = _safe_float(capacity.get('storage_gb_total'))
    storage_used = _safe_float(capacity.get('storage_gb_used'))
    vm_count = _safe_int(capacity.get('vm_count'))
    active_jobs = _active_job_count(db, candidate['provider_id'], candidate.get('node'))

    if min(cpu_total, mem_total, storage_total) <= 0:
        candidate['status'] = 'REJECTED'
        candidate['reasons'].append('provider did not return complete live capacity metrics')
        return candidate

    projected_cpu = cpu_used + reserved['cpu'] + request.cpu
    projected_mem = mem_used + reserved['memory_mb'] + request.ram_mb
    projected_storage = storage_used + reserved['storage_gb'] + request.disk_gb
    cpu_pct = _pct(projected_cpu, cpu_total)
    mem_pct = _pct(projected_mem, mem_total)
    storage_pct = _pct(projected_storage, storage_total)
    free_mem = mem_total - mem_used - reserved['memory_mb'] - request.ram_mb
    free_storage = storage_total - storage_used - reserved['storage_gb'] - request.disk_gb
    free_cpu = cpu_total - cpu_used - reserved['cpu'] - request.cpu

    thresholds = dict(pool.thresholds or {})
    reasons = candidate['reasons']
    if free_cpu < 0:
        reasons.append('insufficient CPU capacity')
    if free_mem < 0:
        reasons.append('insufficient RAM capacity')
    if free_storage < 0:
        reasons.append('insufficient storage capacity')
    if cpu_pct > _safe_float(thresholds.get('cpu_hard_limit'), 95):
        reasons.append('CPU hard limit exceeded')
    if mem_pct > _safe_float(thresholds.get('ram_hard_limit'), 95):
        reasons.append('RAM hard limit exceeded')
    if storage_pct > _safe_float(thresholds.get('storage_hard_limit'), 95):
        reasons.append('storage hard limit exceeded')
    if member.max_vm_count and vm_count + reserved['count'] + 1 > member.max_vm_count:
        reasons.append('member VM count limit exceeded')
    if member.max_cpu_usage is not None and cpu_pct > float(member.max_cpu_usage):
        reasons.append('member CPU usage limit exceeded')
    if member.max_memory_usage is not None and mem_pct > float(member.max_memory_usage):
        reasons.append('member memory usage limit exceeded')
    if member.min_free_memory_mb is not None and free_mem < int(member.min_free_memory_mb):
        reasons.append('member minimum free memory violated')
    if member.min_free_storage_gb is not None and free_storage < int(member.min_free_storage_gb):
        reasons.append('member minimum free storage violated')

    candidate_ctx = _candidate_context(candidate, request)
    candidate_ctx.update({
        'projected_cpu_usage': cpu_pct,
        'projected_memory_usage': mem_pct,
        'projected_storage_usage': storage_pct,
        'free_cpu': free_cpu,
        'free_memory_mb': free_mem,
        'free_storage_gb': free_storage,
    })
    for hard in hard_actions:
        condition = _constraint_condition(hard)
        if condition and not evaluate_condition(condition, candidate_ctx):
            reasons.append('custom hard constraint rejected candidate')

    if any(text for text in reasons if (
        'violated' in text or 'exceeded' in text or 'insufficient' in text
        or text.startswith('custom hard')
    )):
        candidate['status'] = 'REJECTED'

    capacity.update({
        'cpu_reserved': reserved['cpu'],
        'memory_mb_reserved': reserved['memory_mb'],
        'storage_gb_reserved': reserved['storage_gb'],
        'cpu_available_after_request': max(0.0, free_cpu),
        'memory_mb_available_after_request': max(0.0, free_mem),
        'storage_gb_available_after_request': max(0.0, free_storage),
        'cpu_usage_pct': _pct(cpu_used, cpu_total),
        'memory_usage_pct': _pct(mem_used, mem_total),
        'storage_usage_pct': _pct(storage_used, storage_total),
        'projected_cpu_usage_pct': cpu_pct,
        'projected_memory_usage_pct': mem_pct,
        'projected_storage_usage_pct': storage_pct,
        'active_deployments': active_jobs,
        'reserved_deployments': reserved['count'],
    })
    candidate['capacity'] = capacity
    return candidate


def _affinity_peers(db, scope, request: PlacementRequest) -> list[PlacementDecision]:
    affinity = request.affinity
    if not affinity:
        return []
    query = select(PlacementDecision).where(
        PlacementDecision.tenant_id == scope.tenant_id,
        PlacementDecision.project_id == scope.project_id,
    ).order_by(PlacementDecision.created_at.desc()).limit(1000)
    rows = list(db.scalars(query))
    peers = []
    explicit = set(affinity.peer_deployment_ids or [])
    for row in rows:
        if row.deployment_id and row.deployment_id in explicit:
            peers.append(row)
            continue
        snapshot = row.request_snapshot or {}
        previous = snapshot.get('affinity') or {}
        if affinity.group_key and str(previous.get('group_key') or '') == str(affinity.group_key):
            peers.append(row)
    return peers


def _apply_affinity(db, scope, request: PlacementRequest, candidate: dict) -> tuple[bool, float, str | None]:
    if not request.affinity:
        return True, 0.0, None
    peers = _affinity_peers(db, scope, request)
    if not peers:
        return True, 0.0, 'affinity has no existing peers'
    kind = request.affinity.type
    nodes = {str(row.selected_node) for row in peers if row.selected_node}
    locations = {
        str((row.request_snapshot or {}).get('selected_location') or row.selected_platform)
        for row in peers
    }
    node = str(candidate.get('node') or '')
    location = str(candidate.get('location') or candidate.get('platform') or '')
    if kind == 'require_same_node':
        return node in nodes, 0.0, 'require_same_node'
    if kind == 'require_different_node':
        return node not in nodes, 0.0, 'require_different_node'
    if kind == 'require_same_location':
        return location in locations, 0.0, 'require_same_location'
    if kind == 'require_different_location':
        return location not in locations, 0.0, 'require_different_location'
    if kind == 'prefer_same_node':
        return True, 12.0 if node in nodes else 0.0, 'prefer_same_node'
    if kind == 'prefer_different_node':
        return True, 12.0 if node not in nodes else 0.0, 'prefer_different_node'
    if kind == 'prefer_same_location':
        return True, 10.0 if location in locations else 0.0, 'prefer_same_location'
    if kind == 'prefer_different_location':
        return True, 10.0 if location not in locations else 0.0, 'prefer_different_location'
    return True, 0.0, None


def _balanced_component(candidate: dict) -> float:
    c = candidate.get('capacity') or {}
    cpu_total = _safe_float(c.get('cpu_total'))
    mem_total = _safe_float(c.get('memory_mb_total'))
    storage_total = _safe_float(c.get('storage_gb_total'))
    cpu_free = cpu_total - _safe_float(c.get('cpu_used')) - _safe_float(c.get('cpu_reserved'))
    mem_free = mem_total - _safe_float(c.get('memory_mb_used')) - _safe_float(c.get('memory_mb_reserved'))
    storage_free = storage_total - _safe_float(c.get('storage_gb_used')) - _safe_float(c.get('storage_gb_reserved'))
    active = _safe_int(c.get('active_deployments')) + _safe_int(c.get('reserved_deployments'))
    provisioning_factor = 1.0 / (1.0 + max(0, active))
    return (
        0.30 * _ratio_free(cpu_free, cpu_total)
        + 0.35 * _ratio_free(mem_free, mem_total)
        + 0.20 * _ratio_free(storage_free, storage_total)
        + 0.15 * provisioning_factor
    )


def _soft_bonus(candidate: dict, request: PlacementRequest, actions: dict) -> tuple[float, list[str]]:
    bonus = 0.0
    reasons = []
    location = str(candidate.get('location') or '')
    preferred = actions.get('preferred_location') or request.location
    if preferred and location == str(preferred):
        bonus += 10.0
        reasons.append(f'preferred location {preferred}')
    fallbacks = [str(x) for x in actions.get('fallback_locations') or []]
    if location in fallbacks:
        index = fallbacks.index(location)
        bonus += max(1.0, 6.0 - index)
        reasons.append(f'fallback location priority {index + 1}')
    ctx = _candidate_context(candidate, request)
    for preference in actions.get('soft_preferences') or []:
        if isinstance(preference, dict) and any(k in preference for k in ('all', 'any', 'not', 'field')):
            if evaluate_condition(preference, ctx):
                bonus += 5.0
                reasons.append('matched soft preference')
        elif isinstance(preference, dict):
            condition = _constraint_condition(preference)
            if condition and evaluate_condition(condition, ctx):
                bonus += 5.0
                reasons.append('matched soft preference')
    return bonus, reasons


def _score_candidates(db, scope, pool: ResourcePool, request: PlacementRequest, candidates: list[dict], actions: dict):
    available = [row for row in candidates if row.get('status') != 'REJECTED']
    max_priority = max([max(0, _safe_int(row.get('priority'))) for row in available] or [1])
    max_weight = max([max(1, _safe_int(row.get('weight'), 1)) for row in available] or [1])
    strategy = str(pool.strategy or 'BALANCED').upper()
    rng = random.SystemRandom()

    for candidate in available:
        c = candidate.get('capacity') or {}
        balanced = _balanced_component(candidate)
        priority = max(0.0, _safe_float(candidate.get('priority'))) / max_priority
        weight = max(0.0, _safe_float(candidate.get('weight'))) / max_weight
        if strategy == 'LEAST_USED':
            base = 100.0 * balanced
        elif strategy == 'MOST_FREE_MEMORY':
            base = 100.0 * _ratio_free(
                _safe_float(c.get('memory_mb_available_after_request')),
                _safe_float(c.get('memory_mb_total')),
            )
        elif strategy == 'MOST_FREE_CPU':
            base = 100.0 * _ratio_free(
                _safe_float(c.get('cpu_available_after_request')),
                _safe_float(c.get('cpu_total')),
            )
        elif strategy == 'WEIGHTED':
            base = 70.0 * weight + 30.0 * balanced
        elif strategy == 'PRIORITY':
            base = 80.0 * priority + 20.0 * balanced
        elif strategy == 'RANDOM':
            base = rng.uniform(0.0, 100.0)
            candidate['reasons'].append('random strategy component recorded in decision')
        else:
            base = 100.0 * (
                0.75 * balanced + 0.15 * priority + 0.10 * weight
            )
        affinity_ok, affinity_bonus, affinity_reason = _apply_affinity(db, scope, request, candidate)
        if not affinity_ok:
            candidate['status'] = 'REJECTED'
            candidate['score'] = 0.0
            candidate['reasons'].append(f'affinity hard constraint failed: {affinity_reason}')
            continue
        soft_bonus, soft_reasons = _soft_bonus(candidate, request, actions)
        candidate['score'] = round(min(100.0, max(0.0, base + affinity_bonus + soft_bonus)), 2)
        if affinity_bonus:
            candidate['reasons'].append(f'{affinity_reason} affinity preference')
        candidate['reasons'].extend(soft_reasons)
        candidate['reasons'].append(f'pool priority={candidate.get("priority")}')
        candidate['reasons'].append(
            'projected usage CPU/RAM/storage='
            f'{_safe_float(c.get("projected_cpu_usage_pct")):.1f}%/'
            f'{_safe_float(c.get("projected_memory_usage_pct")):.1f}%/'
            f'{_safe_float(c.get("projected_storage_usage_pct")):.1f}%'
        )

    available = [row for row in candidates if row.get('status') != 'REJECTED']
    if strategy == 'ROUND_ROBIN' and available:
        ordered = sorted(
            available,
            key=lambda row: (int(row['provider_id']), str(row.get('node') or ''), str(row.get('member_id') or '')),
        )
        index = int(pool.round_robin_cursor or 0) % len(ordered)
        for offset, candidate in enumerate(ordered):
            candidate['score'] = 100.0 if offset == index else max(1.0, 99.0 - abs(offset - index))
            candidate['reasons'].append(f'round-robin cursor={pool.round_robin_cursor}')
    return candidates


def _serialise_candidate(candidate: dict) -> dict:
    return {
        key: value for key, value in candidate.items()
        if not key.startswith('_') and key not in {'adapter'}
    }


def _select_candidate(pool: ResourcePool, candidates: list[dict]) -> dict:
    available = [row for row in candidates if row.get('status') != 'REJECTED']
    if not available:
        raise NoValidPlacementTarget({
            'message': 'No Resource Pool member satisfies all hard constraints and live capacity checks',
            'candidates': [_serialise_candidate(row) for row in candidates],
        })
    selected = sorted(
        available,
        key=lambda row: (
            -_safe_float(row.get('score')),
            -_safe_int(row.get('priority')),
            int(row.get('provider_id') or 0),
            str(row.get('node') or ''),
        ),
    )[0]
    selected['status'] = 'SELECTED'
    return selected


def _lock_pool(db, pool: ResourcePool):
    db.execute(
        update(ResourcePool)
        .where(ResourcePool.id == pool.id)
        .values(placement_version=ResourcePool.placement_version + 1)
    )
    db.flush()
    return scoped_pool(db, Scope(pool.tenant_id, pool.project_id), pool.id, active_only=True)


def _create_decision(db, scope, request: PlacementRequest, pool, rules, selected, candidates, actor_id, *, blueprint_id=None, is_override=False):
    rule_names = [rule.name for rule in rules]
    reasons = list(selected.get('reasons') or [])
    reason = (
        f"Selected {selected.get('platform')} / {selected.get('node')} with score {selected.get('score')}. "
        + ('Matched rules: ' + ', '.join(rule_names) + '. ' if rule_names else '')
        + ('; '.join(reasons[:8]) if reasons else 'Fixed placement target validated.')
    )
    snapshot = request.model_dump(mode='json')
    snapshot['selected_location'] = selected.get('location')
    row = PlacementDecision(
        tenant_id=scope.tenant_id,
        project_id=scope.project_id,
        blueprint_id=blueprint_id or request.blueprint_id,
        pool_id=pool.id if pool else None,
        rule_ids=[rule.id for rule in rules],
        placement_mode=request.placement_mode,
        selected_provider=str(selected.get('provider_type') or ''),
        selected_provider_id=int(selected['provider_id']),
        selected_platform=str(selected.get('platform') or selected['provider_id']),
        selected_member_id=selected.get('member_id'),
        selected_node=selected.get('node'),
        selected_storage=selected.get('storage'),
        selected_network=selected.get('network'),
        score=float(selected.get('score') or 0),
        candidates_snapshot=[_serialise_candidate(row) for row in candidates],
        decision_reason=reason,
        request_snapshot=snapshot,
        is_override=bool(is_override),
        created_by=actor_id,
    )
    db.add(row)
    db.flush()
    return row


def _create_reservation(db, pool: ResourcePool, request: PlacementRequest, selected: dict, decision, actor_id):
    expiry = now() + timedelta(seconds=int(pool.reservation_ttl_seconds or 600))
    row = ResourceReservation(
        pool_id=pool.id,
        member_id=selected['member_id'],
        decision_id=decision.id,
        provider_id=int(selected['provider_id']),
        node=selected.get('node'),
        storage=selected.get('storage'),
        cpu=request.cpu,
        memory_mb=request.ram_mb,
        storage_gb=request.disk_gb,
        status='RESERVED',
        expires_at=expiry,
        metadata_json={'placement_score': selected.get('score')},
        created_by=actor_id,
    )
    db.add(row)
    if str(pool.strategy).upper() == 'ROUND_ROBIN':
        pool.round_robin_cursor = int(pool.round_robin_cursor or 0) + 1
    db.flush()
    return row


def _fixed_candidate(db, scope, request: PlacementRequest) -> list[dict]:
    target = request.override
    provider = db.get(Provider, int(target.provider_id))
    if provider is None or not reference_visible(db, 'provider', int(target.provider_id), scope):
        raise NoValidPlacementTarget('Fixed provider is not assigned to the selected project')
    credential = db.get(Credential, provider.credentials_id)
    if credential is None or not reference_visible(db, 'credential', provider.credentials_id, scope):
        raise NoValidPlacementTarget('Fixed provider credential is unavailable in the selected project')
    adapter = provider_for(credential)
    targets = list(adapter.get_placement_targets() or [])
    if target.node:
        targets = [row for row in targets if str(row.get('node') or row.get('id') or '') == target.node]
    if not targets:
        raise NoValidPlacementTarget('Fixed placement node does not exist on the provider')
    live = targets[0]
    node = str(live.get('node') or live.get('id') or '')
    if not live.get('online', str(live.get('status') or '').lower() in {'online', 'available', 'connected'}):
        raise NoValidPlacementTarget('Fixed placement node is offline')
    storage = target.storage
    network = target.network
    if storage:
        storages = list(adapter.get_storages({'node': node}) or [])
        if not any(str(row.get('storage') or row.get('id') or '') == storage and row.get('active', True) not in {False, 0, '0'} for row in storages):
            raise NoValidPlacementTarget('Fixed placement storage is unavailable')
    if network:
        networks = list(adapter.get_networks({'node': node}) or [])
        if not any(str(row.get('iface') or row.get('network') or row.get('id') or '') == network for row in networks):
            raise NoValidPlacementTarget('Fixed placement network is unavailable')
    capacity = adapter.get_capacity({'node': node, 'storage': storage})
    template_node, template_error = _provider_template_node(adapter, request)
    if template_error:
        raise NoValidPlacementTarget(template_error)
    return [{
        'provider_id': provider.id, 'provider_type': provider.type, 'platform': provider.name,
        'member_id': None, 'node': node, 'storage': storage, 'network': network,
        'template_node': template_node, 'location': live.get('location') or live.get('datacenter') or provider.name,
        'priority': 100, 'weight': 100, 'capacity': capacity,
        'status': 'SELECTED', 'score': 100.0, 'reasons': ['fixed target validated against live provider data'],
        '_provider': provider, '_adapter': adapter,
    }]


def resolve_placement(
    db,
    scope,
    request: PlacementRequest,
    actor_id: int,
    *,
    blueprint=None,
    reserve: bool | None = None,
    is_override: bool = False,
    exclude: set[tuple[int, str | None]] | None = None,
) -> Resolution:
    context = placement_context(scope, request, blueprint=blueprint)
    reserve = request.reserve if reserve is None else reserve
    if request.placement_mode == 'FIXED':
        candidates = _fixed_candidate(db, scope, request)
        selected = candidates[0]
        decision = _create_decision(
            db, scope, request, None, [], selected, candidates, actor_id,
            blueprint_id=getattr(blueprint, 'id', None), is_override=is_override,
        )
        return Resolution(None, selected, candidates, [], decision, None)

    pool, rules = _pool_and_rules(db, scope, request, context)
    actions = _rule_actions(rules)
    candidates = _collect_pool_candidates(db, scope, pool, request, actions)
    exclude = exclude or set()
    for candidate in candidates:
        if (int(candidate.get('provider_id') or 0), candidate.get('node')) in exclude:
            candidate['status'] = 'REJECTED'
            candidate.setdefault('reasons', []).append('candidate invalidated by previous placement attempt')

    if reserve:
        pool = _lock_pool(db, pool)
        expire_reservations(db, pool.id)
    for candidate in candidates:
        _apply_capacity_constraints(db, pool, request, candidate, actions['hard_constraints'])
    _score_candidates(db, scope, pool, request, candidates, actions)
    selected = _select_candidate(pool, candidates)
    decision = _create_decision(
        db, scope, request, pool, rules, selected, candidates, actor_id,
        blueprint_id=getattr(blueprint, 'id', None), is_override=is_override,
    )
    reservation = _create_reservation(db, pool, request, selected, decision, actor_id) if reserve else None
    return Resolution(pool, selected, candidates, rules, decision, reservation)


def request_from_blueprint(rendered: dict, placement: dict, *, scope_context: dict, blueprint_id: int, override=None) -> PlacementRequest:
    variables = dict(rendered.get('variables') or {})
    mode = str(placement.get('placement_mode') or 'FIXED').upper()
    data = {
        'placement_mode': mode,
        'pool_id': placement.get('resource_pool_id'),
        'blueprint_id': blueprint_id,
        'organization': scope_context.get('organization'),
        'project': scope_context.get('project'),
        'apmid': scope_context.get('apmid'),
        'environment': scope_context.get('environment'),
        'operating_system': placement.get('operating_system') or variables.get('operating_system'),
        'vm_size': placement.get('vm_size') or variables.get('vm_size'),
        'cpu': variables.get('cpu') or 1,
        'ram_mb': variables.get('memory') or 512,
        'disk_gb': variables.get('disk') or 1,
        'tags': variables.get('tags') or [],
        'provider_type': placement.get('provider_type'),
        'location': placement.get('location'),
        'logical_network': placement.get('logical_network'),
        'storage_class': placement.get('storage_class'),
        'template_id': variables.get('template_id'),
        'metadata': placement.get('metadata') or {},
        'affinity': placement.get('affinity'),
        'override': override,
        'reserve': mode != 'FIXED',
    }
    if mode == 'FIXED' and override is None:
        data['override'] = {
            'provider_id': rendered.get('provider_id'),
            'node': variables.get('node'),
            'storage': variables.get('storage'),
            'network': variables.get('network'),
        }
    return PlacementRequest.model_validate(data)


def apply_resolution_to_rendered(rendered: dict, resolution: Resolution) -> dict:
    selected = resolution.selected
    provider = selected.get('_provider')
    if provider is None:
        raise HTTPException(500, 'Placement candidate lost provider identity')
    result = dict(rendered)
    variables = dict(result.get('variables') or {})
    result['provider_id'] = provider.id
    result['credentials_id'] = provider.credentials_id
    variables['node'] = selected.get('node')
    if selected.get('storage'):
        variables['storage'] = selected['storage']
    if selected.get('network'):
        variables['network'] = selected['network']
    if selected.get('template_node'):
        variables['template_node'] = selected['template_node']
    result['variables'] = variables
    return result


def bind_resolution(resolution: Resolution, deployment, job=None):
    resolution.decision.deployment_id = deployment.id
    if resolution.reservation is not None:
        resolution.reservation.deployment_id = deployment.id
        if job is not None:
            resolution.reservation.job_id = job.id


def release_reservation(db, reservation: ResourceReservation | None, *, status='RELEASED'):
    if reservation is None or reservation.status != 'RESERVED':
        return
    reservation.status = status
    reservation.released_at = now()


def release_job_placement(db, job_id: str, *, status='RELEASED'):
    rows = list(db.scalars(select(ResourceReservation).where(
        ResourceReservation.job_id == str(job_id),
        ResourceReservation.status == 'RESERVED',
    ).with_for_update()))
    for row in rows:
        release_reservation(db, row, status=status)
    return len(rows)


def _request_from_decision(decision: PlacementDecision) -> PlacementRequest:
    payload = dict(decision.request_snapshot or {})
    payload['placement_mode'] = decision.placement_mode
    payload['pool_id'] = decision.pool_id
    payload['reserve'] = True
    payload['override'] = None
    return PlacementRequest.model_validate(payload)


def _latest_decision(db, deployment_id: str):
    return db.scalar(select(PlacementDecision).where(
        PlacementDecision.deployment_id == str(deployment_id)
    ).order_by(PlacementDecision.created_at.desc(), PlacementDecision.id.desc()).limit(1))


def prepare_job_placement(context, *, invalidate_current: bool = False, reason: str | None = None) -> bool:
    if context.deployment is None:
        return False
    with session() as db:
        decision = _latest_decision(db, context.deployment.id)
        if decision is None or decision.placement_mode == 'FIXED' or decision.pool_id is None:
            return False
        pool = db.scalar(select(ResourcePool).where(ResourcePool.id == decision.pool_id).with_for_update())
        if pool is None or not pool.enabled:
            raise ExecutionFailed('NO_VALID_PLACEMENT_TARGET: Resource Pool is unavailable')
        existing = db.scalar(select(ResourceReservation).where(
            ResourceReservation.deployment_id == context.deployment.id,
            ResourceReservation.status == 'RESERVED',
        ).order_by(ResourceReservation.created_at.desc()).limit(1))
        request = _request_from_decision(decision)
        exclude: set[tuple[int, str | None]] = set()
        attempts = 0
        payload = dict(context.job.payload or {})
        placement_runtime = dict(payload.get('_placement_runtime') or {})
        for item in placement_runtime.get('invalidated', []):
            try:
                exclude.add((int(item['provider_id']), item.get('node')))
            except (KeyError, TypeError, ValueError):
                continue
        if invalidate_current:
            exclude.add((decision.selected_provider_id, decision.selected_node))
            placement_runtime.setdefault('invalidated', []).append({
                'provider_id': decision.selected_provider_id,
                'node': decision.selected_node,
                'reason': str(reason or 'provider unavailable')[:200],
            })
            if existing is not None:
                release_reservation(db, existing, status='RELEASED')
        attempts = int(placement_runtime.get('attempts') or 0)
        if invalidate_current:
            attempts += 1
            placement_runtime['attempts'] = attempts
        if attempts >= int(pool.retry_limit or 3):
            raise ExecutionFailed(
                f'NO_VALID_PLACEMENT_TARGET: failover exhausted after {attempts} placement attempts'
            )

        if not invalidate_current:
            provider = db.get(Provider, decision.selected_provider_id)
            credential = db.get(Credential, provider.credentials_id) if provider else None
            if provider and credential:
                try:
                    adapter = provider_for(credential)
                    live_targets = list(adapter.get_placement_targets() or [])
                    live = next(
                        (row for row in live_targets if str(row.get('node') or row.get('id') or '') == str(decision.selected_node or '')),
                        None,
                    )
                    if live and live.get('online', str(live.get('status') or '').lower() in {'online', 'available', 'connected'}):
                        db.commit()
                        return False
                except Exception:
                    pass
            exclude.add((decision.selected_provider_id, decision.selected_node))
            placement_runtime.setdefault('invalidated', []).append({
                'provider_id': decision.selected_provider_id,
                'node': decision.selected_node,
                'reason': 'pre-provision live validation failed',
            })
            if existing is not None:
                release_reservation(db, existing, status='RELEASED')
            placement_runtime['attempts'] = attempts + 1

        scope = Scope(context.deployment.tenant_id, context.deployment.project_id)
        blueprint = db.get(Blueprint, decision.blueprint_id) if decision.blueprint_id else None
        resolution = resolve_placement(
            db, scope, request, context.job.created_by,
            blueprint=blueprint, reserve=True, exclude=exclude,
        )
        deployment = db.get(Deployment, context.deployment.id)
        provider = resolution.selected['_provider']
        deployment.provider_id = provider.id
        deployment.provider = provider.type
        deployment.credentials_id = provider.credentials_id
        variables = dict(deployment.variables or {})
        variables['node'] = resolution.selected.get('node')
        if resolution.selected.get('storage'):
            variables['storage'] = resolution.selected['storage']
        if resolution.selected.get('network'):
            variables['network'] = resolution.selected['network']
        if resolution.selected.get('template_node'):
            variables['template_node'] = resolution.selected['template_node']
        deployment.variables = variables
        resolution.decision.deployment_id = deployment.id
        if resolution.reservation:
            resolution.reservation.deployment_id = deployment.id
            resolution.reservation.job_id = context.job.id
        current_job = db.get(Job, context.job.id)
        payload = dict(current_job.payload or {})
        placement_runtime['decision_id'] = resolution.decision.id
        placement_runtime['reservation_id'] = resolution.reservation.id if resolution.reservation else None
        placement_runtime['selected'] = {
            'provider_id': provider.id,
            'platform': provider.name,
            'node': resolution.selected.get('node'),
            'storage': resolution.selected.get('storage'),
            'network': resolution.selected.get('network'),
        }
        payload['_placement_runtime'] = placement_runtime
        current_job.payload = payload
        db.commit()
        context.job.payload = dict(payload)
        context.deployment = deployment
        context.credential = db.get(Credential, provider.credentials_id)
        context.stage('placement.selected')
        context.log(
            'placement.selected: '
            f'{provider.name} / {resolution.selected.get("node")} / '
            f'{resolution.selected.get("storage")} score={resolution.selected.get("score")}'
        )
        return True


def placement_for_deployment(db, scope, deployment_id: str):
    row = db.scalar(select(PlacementDecision).where(
        PlacementDecision.deployment_id == str(deployment_id),
        PlacementDecision.tenant_id == scope.tenant_id,
        PlacementDecision.project_id == scope.project_id,
    ).order_by(PlacementDecision.created_at.desc()).limit(1))
    if row is None:
        raise HTTPException(404, 'Placement decision not found for deployment')
    return decision_public(db, row)


def pool_capacity(db, scope, pool_id: str) -> dict:
    pool = scoped_pool(db, scope, pool_id)
    members = list(db.scalars(select(ResourcePoolMember).where(
        ResourcePoolMember.pool_id == pool.id
    ).order_by(ResourcePoolMember.priority.desc(), ResourcePoolMember.id.asc())))
    totals = {
        'cpu_total': 0.0, 'cpu_used': 0.0, 'cpu_reserved': 0.0,
        'memory_mb_total': 0.0, 'memory_mb_used': 0.0, 'memory_mb_reserved': 0.0,
        'storage_gb_total': 0.0, 'storage_gb_used': 0.0, 'storage_gb_reserved': 0.0,
    }
    nodes_online = nodes_offline = nodes_maintenance = 0
    unavailable = []
    seen = set()
    expire_reservations(db, pool.id)

    for member in members:
        if member.maintenance_mode:
            nodes_maintenance += 1
            continue
        if not member.enabled:
            unavailable.append({'member_id': member.id, 'reason': 'disabled'})
            continue
        provider = db.get(Provider, member.provider_id)
        credential = db.get(Credential, provider.credentials_id) if provider else None
        if not provider or not credential:
            unavailable.append({'member_id': member.id, 'reason': 'provider unavailable'})
            continue
        try:
            adapter = provider_for(credential)
            targets = list(adapter.get_placement_targets() or [])
        except Exception as exc:
            unavailable.append({'member_id': member.id, 'reason': f'provider discovery failed: {exc.__class__.__name__}'})
            continue
        if member.node:
            targets = [row for row in targets if str(row.get('node') or row.get('id') or '') == member.node]
        for target in targets:
            node = str(target.get('node') or target.get('id') or '')
            key = (provider.id, node, member.storage or '')
            if key in seen:
                continue
            seen.add(key)
            if not target.get('online', str(target.get('status') or '').lower() in {'online', 'available', 'connected'}):
                nodes_offline += 1
                unavailable.append({'member_id': member.id, 'provider_id': provider.id, 'node': node, 'reason': 'offline'})
                continue
            nodes_online += 1
            storage = member.storage
            try:
                storages = list(adapter.get_storages({'node': node}) or [])
                if not storage:
                    eligible = [row for row in storages if row.get('active', True) and 'images' in str(row.get('content') or 'images')]
                    chosen = max(eligible, key=lambda row: _safe_float(row.get('avail') or row.get('free')), default=None)
                    storage = str((chosen or {}).get('storage') or (chosen or {}).get('id') or '') or None
                capacity = adapter.get_capacity({'node': node, 'storage': storage})
            except Exception as exc:
                nodes_online -= 1
                nodes_offline += 1
                unavailable.append({'member_id': member.id, 'provider_id': provider.id, 'node': node, 'reason': f'capacity failed: {exc.__class__.__name__}'})
                continue
            synthetic = {'provider_id': provider.id, 'node': node, 'storage': storage}
            reserved = _reservation_usage(db, synthetic)
            totals['cpu_total'] += _safe_float(capacity.get('cpu_total'))
            totals['cpu_used'] += _safe_float(capacity.get('cpu_used'))
            totals['cpu_reserved'] += reserved['cpu']
            totals['memory_mb_total'] += _safe_float(capacity.get('memory_mb_total'))
            totals['memory_mb_used'] += _safe_float(capacity.get('memory_mb_used'))
            totals['memory_mb_reserved'] += reserved['memory_mb']
            totals['storage_gb_total'] += _safe_float(capacity.get('storage_gb_total'))
            totals['storage_gb_used'] += _safe_float(capacity.get('storage_gb_used'))
            totals['storage_gb_reserved'] += reserved['storage_gb']

    totals['cpu_available'] = max(0.0, totals['cpu_total'] - totals['cpu_used'] - totals['cpu_reserved'])
    totals['memory_mb_available'] = max(0.0, totals['memory_mb_total'] - totals['memory_mb_used'] - totals['memory_mb_reserved'])
    totals['storage_gb_available'] = max(0.0, totals['storage_gb_total'] - totals['storage_gb_used'] - totals['storage_gb_reserved'])
    totals['cpu_usage_pct'] = _pct(totals['cpu_used'] + totals['cpu_reserved'], totals['cpu_total'])
    totals['ram_usage_pct'] = _pct(totals['memory_mb_used'] + totals['memory_mb_reserved'], totals['memory_mb_total'])
    totals['storage_usage_pct'] = _pct(totals['storage_gb_used'] + totals['storage_gb_reserved'], totals['storage_gb_total'])

    thresholds = dict(pool.thresholds or {})
    warning = (
        totals['cpu_usage_pct'] >= _safe_float(thresholds.get('cpu_warning'), 80)
        or totals['ram_usage_pct'] >= _safe_float(thresholds.get('ram_warning'), 80)
        or totals['storage_usage_pct'] >= _safe_float(thresholds.get('storage_warning'), 80)
    )
    if nodes_online == 0 and nodes_maintenance and not unavailable:
        health = 'MAINTENANCE'
        reason = 'All placement targets are in maintenance mode.'
    elif nodes_online == 0:
        health = 'UNAVAILABLE'
        reason = 'No placement target is currently available.'
    elif unavailable or nodes_offline:
        health = 'DEGRADED'
        reason = f'{len(unavailable)} placement target(s) unavailable.'
    elif warning:
        health = 'CAPACITY_WARNING'
        reason = 'Pool capacity crossed a configured warning threshold.'
    else:
        health = 'HEALTHY'
        reason = 'All placement targets are available and below warning thresholds.'

    return {
        'pool_id': pool.id, 'pool': pool.name, 'strategy': pool.strategy,
        **{key: round(value, 2) if isinstance(value, float) else value for key, value in totals.items()},
        'nodes_online': nodes_online, 'nodes_offline': nodes_offline,
        'nodes_maintenance': nodes_maintenance,
        'members_total': len(members),
        'health': health, 'reason': reason, 'unavailable': unavailable,
    }
