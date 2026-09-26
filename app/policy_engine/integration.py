from __future__ import annotations

import copy
import re

from fastapi import HTTPException

from app.models import ManagedResource
from app.policy_engine.service import evaluate_context
from app.projects.models import Project
from app.tenancy.models import Tenant


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


def _scope_component(value, *, uppercase=False):
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"[\\/:|]+", "-", text)
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text.upper() if uppercase else text


def compose_scope_key(organization, project, apmid, environment):
    """Canonical human-readable VM scope: Organization-Project-APMID-ENV."""
    parts = [
        _scope_component(organization),
        _scope_component(project),
        _scope_component(apmid, uppercase=True),
        _scope_component(environment, uppercase=True),
    ]
    return "-".join(parts) if all(parts) else None


def _scope_from_ids(db, tenant_id, project_id, *, apmid=None, environment=None):
    tenant_id = str(tenant_id or "")
    project_id = str(project_id or "")
    tenant = db.get(Tenant, tenant_id) if tenant_id else None
    project = db.get(Project, project_id) if project_id else None

    organization_name = str(getattr(tenant, "name", "") or tenant_id)
    organization_slug = str(getattr(tenant, "slug", "") or "")
    project_name = str(getattr(project, "name", "") or project_id)
    project_slug = str(getattr(project, "slug", "") or "")
    normalized_apmid = _scope_component(apmid, uppercase=True) or None
    normalized_environment = str(environment or "").strip().lower() or None

    return {
        "tenant_id": tenant_id,
        "project_id": project_id,
        "organization": organization_name,
        "organization_slug": organization_slug,
        "project": project_name,
        "project_slug": project_slug,
        "apmid": normalized_apmid,
        "environment": normalized_environment,
        "key": compose_scope_key(
            organization_name,
            project_name,
            normalized_apmid,
            normalized_environment,
        ),
    }


def _scope(db, request, fallback=None, *, apmid=None, environment=None):
    scope = getattr(request.state, "resource_scope", None)
    if scope is not None:
        return _scope_from_ids(
            db, scope.tenant_id, scope.project_id,
            apmid=apmid, environment=environment,
        )
    if fallback is not None:
        return _scope_from_ids(
            db,
            getattr(fallback, "tenant_id", ""),
            getattr(fallback, "project_id", ""),
            apmid=apmid, environment=environment,
        )
    return _scope_from_ids(db, "", "", apmid=apmid, environment=environment)


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


def _write_aliases(rendered, effective_resource, effective_scope=None):
    variables = dict(rendered.get("variables") or {})
    effective_scope = effective_scope or {}
    canonical_scope_key = compose_scope_key(
        effective_resource.get("organization") or effective_scope.get("organization"),
        effective_resource.get("project") or effective_scope.get("project"),
        effective_resource.get("apmid"),
        effective_resource.get("environment"),
    )
    if canonical_scope_key:
        effective_resource["scope_key"] = canonical_scope_key
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

    persisted_scope_fields = {
        "apmid": "apmid",
        "environment": "environment",
        "organization": "organization",
        "project": "project",
        "scope_key": "resource_scope_key",
    }
    for resource_field, variable_name in persisted_scope_fields.items():
        value = effective_resource.get(resource_field)
        if value not in (None, ""):
            variables[variable_name] = value

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
    apmid_value = apmid or variables.get("apmid") or tagged_apmid
    environment_value = environment or variables.get("environment") or tagged_environment
    scope_context = _scope(
        db, request, blueprint,
        apmid=apmid_value, environment=environment_value,
    )
    context = {
        "actor": _actor(actor, permissions),
        "scope": scope_context,
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
            "apmid": apmid_value,
            "environment": environment_value,
            "organization": scope_context.get("organization"),
            "project": scope_context.get("project"),
            "scope_key": scope_context.get("key"),
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
    _write_aliases(rendered, effective_resource, effective.get("scope") or {})
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
    apmid_value = metadata.get("apmid") or tagged_apmid
    environment_value = metadata.get("environment") or tagged_environment
    scope_context = _scope(
        db, request, row,
        apmid=apmid_value, environment=environment_value,
    )
    context = {
        "actor": _actor(actor, permissions),
        "scope": scope_context,
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
            "apmid": apmid_value,
            "environment": environment_value,
            "organization": scope_context.get("organization") or metadata.get("organization"),
            "project": scope_context.get("project") or metadata.get("project"),
            "scope_key": scope_context.get("key") or metadata.get("resource_scope_key"),
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
    apmid_value = variables.get("apmid") or tagged_apmid
    environment_value = variables.get("environment") or tagged_environment
    scope_context = _scope_from_ids(
        db,
        getattr(job, "tenant_id", ""),
        getattr(job, "project_id", ""),
        apmid=apmid_value,
        environment=environment_value,
    )
    roles = list(getattr(user, "roles", []) or [])
    resource = {
        "type": "vm",
        "id": str(getattr(deployment, "id", "") or ""),
        "apmid": apmid_value,
        "environment": environment_value,
        "organization": scope_context.get("organization") or variables.get("organization"),
        "project": scope_context.get("project") or variables.get("project"),
        "scope_key": scope_context.get("key") or variables.get("resource_scope_key"),
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
        "scope": scope_context,
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
