from __future__ import annotations

import copy

from fastapi import HTTPException

from app.models import ManagedResource
from app.policy_engine.service import evaluate_context


def _actor(actor, permissions):
    user = getattr(actor, "user", None)
    roles = list(getattr(user, "roles", []) or [])
    return {
        "id": getattr(actor, "user_id", None),
        "username": getattr(user, "username", ""),
        "role_ids": [getattr(role, "id", None) for role in roles],
        "roles": [getattr(role, "name", "") for role in roles],
        "groups": [],
        "permissions": sorted(set(permissions or ())),
    }


def _number_from(values, keys):
    for key in keys:
        value = values.get(key)
        if value is not None and not isinstance(value, bool):
            try:
                return int(value)
            except (TypeError, ValueError):
                pass
    return None


def _tags(values):
    raw = values.get("tags") or []
    if isinstance(raw, str):
        return [item for item in raw.replace(",", ";").split(";") if item]
    if isinstance(raw, list):
        return list(raw)
    return []


def _classification_from_tags(tags):
    apmid = None
    environment = None
    for tag in tags:
        text = str(tag or "").strip().lower()
        if text.startswith("apmid-") and len(text) > 6 and apmid is None:
            apmid = text[6:].upper()
        elif text.startswith("env-") and text[4:] in {"dev", "test", "nonprod", "prod"} and environment is None:
            environment = text[4:]
    return apmid, environment


def _scope(request, fallback=None):
    scope = getattr(request.state, "resource_scope", None)
    if scope is not None:
        return {"tenant_id": str(scope.tenant_id), "project_id": str(scope.project_id)}
    if fallback is not None:
        return {
            "tenant_id": str(getattr(fallback, "tenant_id", "") or ""),
            "project_id": str(getattr(fallback, "project_id", "") or ""),
        }
    return {}


def _deny(result):
    if result.get("decision") != "deny":
        return
    raise HTTPException(403, {
        "code": "POLICY_DENIED",
        "message": "Operation denied by CloudPortal Policy Engine",
        "violations": result.get("violations") or [],
        "matched_policy_ids": result.get("matched_policy_ids") or [],
        "decision_id": result.get("decision_id"),
    })


def _write_aliases(rendered, effective_resource):
    variables = dict(rendered.get("variables") or {})
    alias_groups = {
        "cpu": ("cores", "cpu", "vcpu", "cpu_cores"),
        "memory_mb": ("memory", "memory_mb", "ram_mb"),
        "disk_gb": ("disk_size_gb", "disk_gb", "disk_size"),
    }
    for field, keys in alias_groups.items():
        value = effective_resource.get(field)
        if value is None:
            continue
        target = next((key for key in keys if key in variables), keys[0])
        variables[target] = value
    if "tags" in effective_resource:
        variables["tags"] = list(effective_resource.get("tags") or [])

    # Placement effects must change the actual Terraform input, not only the
    # decision trace. These names match the canonical Proxmox template and are
    # intentionally generic enough for future provider adapters.
    placement_aliases = {
        "storage": ("storage", "datastore", "datastore_id"),
        "network": ("network", "bridge", "network_id"),
        "node": ("node", "host", "target_node"),
        "cluster": ("cluster", "cluster_id"),
    }
    for field, keys in placement_aliases.items():
        value = effective_resource.get(field)
        if value is None:
            continue
        target = next((key for key in keys if key in variables), keys[0])
        variables[target] = value

    rendered["variables"] = variables
    if effective_resource.get("provider_id") is not None:
        rendered["provider_id"] = effective_resource["provider_id"]
    if effective_resource.get("template"):
        rendered["template"] = effective_resource["template"]


def enforce_blueprint_execution(db, request, actor, permissions, blueprint, rendered, *, apmid=None, environment=None):
    rendered = copy.deepcopy(rendered)
    variables = dict(rendered.get("variables") or {})
    classification_tags = _tags(variables)
    tagged_apmid, tagged_environment = _classification_from_tags(classification_tags)
    context = {
        "actor": _actor(actor, permissions),
        "scope": _scope(request, blueprint),
        "request": {
            "action": "vm.create",
            "source": getattr(request.state, "source", "API"),
            "phase": "pre_provision",
            "payload": copy.deepcopy(rendered),
        },
        "blueprint": {
            "id": getattr(blueprint, "id", None),
            "slug": getattr(blueprint, "slug", ""),
            "version": getattr(blueprint, "version", None),
            "tags": list((getattr(blueprint, "visibility", {}) or {}).get("tags") or []),
        },
        "resource": {
            "type": "vm",
            "apmid": apmid or variables.get("apmid") or tagged_apmid,
            "environment": environment or variables.get("environment") or tagged_environment,
            "provider_id": rendered.get("provider_id"),
            "provider_type": rendered.get("provider"),
            "template": rendered.get("template"),
            "cpu": _number_from(variables, ("cores", "cpu", "vcpu", "cpu_cores")),
            "memory_mb": _number_from(variables, ("memory", "memory_mb", "ram_mb")),
            "disk_gb": _number_from(variables, ("disk_size_gb", "disk_gb", "disk_size")),
            "tags": classification_tags,
            "variables": copy.deepcopy(variables),
        },
    }
    result = evaluate_context(db, context, persist=True)
    _deny(result)

    effective = result.get("effective_context") or context
    payload = ((effective.get("request") or {}).get("payload"))
    if isinstance(payload, dict):
        rendered = copy.deepcopy(payload)
    effective_resource = effective.get("resource") or {}
    _write_aliases(rendered, effective_resource)
    return rendered, result


def _resource_metadata(db, target):
    row = db.get(ManagedResource, str(getattr(target, "resource_id", "")))
    metadata = dict(getattr(row, "metadata_json", {}) or {}) if row is not None else {}
    tags = metadata.get("tags") or []
    if isinstance(tags, str):
        tags = [item for item in tags.replace(",", ";").split(";") if item]
    return row, metadata, list(tags) if isinstance(tags, list) else []


def enforce_day2(db, request, actor, permissions, target, action_id, params):
    params = copy.deepcopy(dict(params or {}))
    row, metadata, tags = _resource_metadata(db, target)
    tagged_apmid, tagged_environment = _classification_from_tags(tags)
    context = {
        "actor": _actor(actor, permissions),
        "scope": _scope(request, row),
        "request": {
            "action": "day2." + str(action_id),
            "source": getattr(request.state, "source", "API"),
            "phase": "pre_day2",
            "parameters": copy.deepcopy(params),
        },
        "resource": {
            "id": str(getattr(target, "resource_id", "") or ""),
            "type": str(getattr(target, "resource_type", "resource") or "resource"),
            "name": str(getattr(target, "name", "") or ""),
            "provider_id": getattr(target, "provider_id", None),
            "provider_type": getattr(target, "provider_type", None),
            "node": getattr(target, "node", None),
            "management_mode": getattr(target, "management_mode", None),
            "apmid": metadata.get("apmid") or tagged_apmid,
            "environment": metadata.get("environment") or tagged_environment,
            "tags": tags,
            "metadata": metadata,
        },
    }
    result = evaluate_context(db, context, persist=True)
    _deny(result)
    effective = result.get("effective_context") or context
    effective_params = ((effective.get("request") or {}).get("parameters"))
    if isinstance(effective_params, dict):
        params = copy.deepcopy(effective_params)
    return params, result


def revalidate_blueprint_job(db, job, user, permissions, deployment):
    """Re-evaluate current policy immediately before a persisted Blueprint job executes."""
    if getattr(job, "operation", None) != "terraform.apply":
        return None
    blueprint = dict((getattr(job, "payload", {}) or {}).get("blueprint") or {})
    if not blueprint or deployment is None:
        return None

    variables = dict(getattr(deployment, "variables", {}) or {})
    tags = _tags(variables)
    tagged_apmid, tagged_environment = _classification_from_tags(tags)
    roles = list(getattr(user, "roles", []) or [])
    resource = {
        "type": "vm",
        "id": str(getattr(deployment, "id", "") or ""),
        "apmid": tagged_apmid,
        "environment": tagged_environment,
        "provider_id": getattr(deployment, "provider_id", None),
        "provider_type": getattr(deployment, "provider", None),
        "template": getattr(deployment, "template", None),
        "cpu": _number_from(variables, ("cores", "cpu", "vcpu", "cpu_cores")),
        "memory_mb": _number_from(variables, ("memory", "memory_mb", "ram_mb")),
        "disk_gb": _number_from(variables, ("disk_size_gb", "disk_gb", "disk_size")),
        "storage": variables.get("storage") or variables.get("datastore") or variables.get("datastore_id"),
        "network": variables.get("network") or variables.get("bridge") or variables.get("network_id"),
        "node": variables.get("node") or variables.get("host") or variables.get("target_node"),
        "cluster": variables.get("cluster") or variables.get("cluster_id"),
        "tags": tags,
        "variables": copy.deepcopy(variables),
    }
    context = {
        "actor": {
            "id": getattr(user, "id", None),
            "username": getattr(user, "username", ""),
            "role_ids": [getattr(role, "id", None) for role in roles],
            "roles": [getattr(role, "name", "") for role in roles],
            "groups": [],
            "permissions": sorted(set(permissions or ())),
        },
        "scope": {
            "tenant_id": str(getattr(job, "tenant_id", "") or ""),
            "project_id": str(getattr(job, "project_id", "") or ""),
        },
        "request": {
            "action": "vm.create",
            "source": getattr(job, "source", "Worker"),
            "phase": "worker_revalidate",
        },
        "blueprint": {
            "id": blueprint.get("id"),
            "slug": blueprint.get("slug"),
            "version": blueprint.get("version"),
        },
        "resource": resource,
    }
    result = evaluate_context(db, context, persist=True)

    effective_resource = (result.get("effective_context") or {}).get("resource") or {}
    protected = (
        "cpu", "memory_mb", "disk_gb", "storage", "network", "node",
        "cluster", "provider_id", "template",
    )
    drift = {
        field: {"queued": resource.get(field), "policy_now": effective_resource.get(field)}
        for field in protected
        if effective_resource.get(field) != resource.get(field)
    }
    result["input_policy_drift"] = drift
    return result
