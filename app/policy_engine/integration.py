from __future__ import annotations

import copy
import re

from fastapi import HTTPException
from sqlalchemy import select

from app.models import ManagedResource, Provider
from app.policy_engine.entities import entity_keys_for_permissions
from app.policy_engine.service import evaluate_context
from app.projects.models import Project
from app.tenancy.models import Tenant


def _role_values(db, user, scope):
    roles = list(getattr(user, "roles", []) or [])
    role_ids = {int(getattr(role, "id")) for role in roles if getattr(role, "id", None) is not None}
    role_names = {str(getattr(role, "name")) for role in roles if getattr(role, "name", None)}
    if db is not None and user is not None and scope:
        from app.models import Role
        from app.projects.models import ProjectRoleAssignment
        from app.tenancy.models import TenantRoleAssignment

        user_id = getattr(user, "id", None)
        tenant_id = scope.get("tenant_id")
        project_id = scope.get("project_id")
        if user_id is not None and tenant_id:
            role_ids.update(db.scalars(select(TenantRoleAssignment.role_id).where(
                TenantRoleAssignment.tenant_id == str(tenant_id),
                TenantRoleAssignment.user_id == int(user_id),
            )).all())
        if user_id is not None and tenant_id and project_id:
            role_ids.update(db.scalars(select(ProjectRoleAssignment.role_id).where(
                ProjectRoleAssignment.tenant_id == str(tenant_id),
                ProjectRoleAssignment.project_id == str(project_id),
                ProjectRoleAssignment.user_id == int(user_id),
            )).all())
        if role_ids:
            role_names.update(str(role.name) for role in db.scalars(
                select(Role).where(Role.id.in_(role_ids))
            ).all())
    return sorted(role_ids), sorted(role_names)


def _user_actor(user, permissions, db=None, scope=None):
    role_ids, role_names = _role_values(db, user, scope or {})
    permission_set = set(permissions or ())
    return {
        "id": getattr(user, "id", None),
        "username": getattr(user, "username", ""),
        "role_ids": role_ids,
        "roles": role_names,
        "groups": [],
        "entities": entity_keys_for_permissions(
            permission_set,
            apmid=(scope or {}).get("apmid"),
            environment=(scope or {}).get("environment"),
        ),
        "permissions": sorted(permission_set),
    }


def _actor(actor, permissions, db=None, scope=None):
    return _user_actor(getattr(actor, "user", None), permissions, db, scope)


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


def _write_aliases(db, rendered, effective_resource, effective_scope=None):
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
        try:
            selected_provider = db.get(Provider, int(effective_resource["provider_id"]))
        except (TypeError, ValueError):
            selected_provider = None
        if selected_provider is not None:
            rendered["credentials_id"] = selected_provider.credentials_id
    if effective_resource.get("template"):
        rendered["template"] = effective_resource["template"]


def _merge_policy_results(primary, secondary):
    """Merge two persisted evaluations for one logical request."""
    result = copy.deepcopy(primary)
    for key in ("matched_policy_ids", "warnings", "violations", "approvals", "obligations", "trace", "conflicts"):
        values = list(result.get(key) or [])
        for item in list(secondary.get(key) or []):
            if item not in values:
                values.append(copy.deepcopy(item))
        result[key] = values
    if primary.get("decision") == "deny" or secondary.get("decision") == "deny":
        result["decision"] = "deny"
    elif primary.get("decision") == "approval_required" or secondary.get("decision") == "approval_required":
        result["decision"] = "approval_required"
    else:
        result["decision"] = "allow"
    if secondary.get("decision_id"):
        result.setdefault("related_decision_ids", []).append(secondary["decision_id"])
    return result


def _deployment_policy_resource(deployment, *, resource_type="terraform"):
    if deployment is None:
        return {}, None, None
    variables = dict(getattr(deployment, "variables", {}) or {})
    workflow = dict(getattr(deployment, "workflow", {}) or {})
    blueprint = dict(workflow.get("blueprint") or {})
    blueprint_variables = dict(blueprint.get("variables") or {})
    tags = _tags(variables)
    tagged_apmid, tagged_environment = _classification_from_tags(tags)
    apmid = variables.get("apmid") or blueprint_variables.get("apmid") or tagged_apmid
    environment = variables.get("environment") or blueprint_variables.get("environment") or tagged_environment
    resource = {
        "id": str(getattr(deployment, "id", "") or ""),
        "type": resource_type,
        "deployment_id": str(getattr(deployment, "id", "") or ""),
        "name": str(getattr(deployment, "name", "") or ""),
        "provider_id": getattr(deployment, "provider_id", None),
        "provider_type": getattr(deployment, "provider", None),
        "template": getattr(deployment, "template", None),
        "apmid": apmid,
        "environment": environment,
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
    return resource, apmid, environment


def enforce_job_operation(db, request, actor, permissions, operation, *, deployment=None, parameters=None):
    """Authorize direct Terraform/Ansible/provider jobs before they are queued."""
    resource_type = "ansible" if str(operation) == "ansible.execute" else "terraform"
    resource, apmid, environment = _deployment_policy_resource(
        deployment, resource_type=resource_type
    )
    parameters = copy.deepcopy(dict(parameters or {}))
    if resource_type == "ansible":
        resource.update({
            "playbook": parameters.get("playbook"),
            "credential_id": parameters.get("credentials_id"),
        })
        apmid = parameters.get("apmid") or apmid
        environment = parameters.get("environment") or environment
    scope_context = _scope(
        db, request, deployment,
        apmid=apmid, environment=environment,
    )
    resource.setdefault("type", resource_type)
    resource.setdefault("organization", scope_context.get("organization"))
    resource.setdefault("project", scope_context.get("project"))
    resource.setdefault("scope_key", scope_context.get("key"))
    context = {
        "actor": _actor(actor, permissions, db, scope_context),
        "scope": scope_context,
        "request": {
            "action": str(operation),
            "source": getattr(request.state, "source", "API"),
            "phase": "pre_request",
            "parameters": parameters,
        },
        "resource": resource,
    }
    result = evaluate_context(db, context, persist=True, durable_denies=True)
    _deny(result)
    credential_id = (
        parameters.get("credentials_id")
        if resource_type == "ansible"
        else getattr(deployment, "credentials_id", None)
    )
    if credential_id:
        credential_result = enforce_credential_use(
            db, request, actor, permissions, credential_id,
            deployment=deployment, purpose=str(operation),
        )
        result = _merge_policy_results(result, credential_result)
    return result


def enforce_credential_use(db, request, actor, permissions, credential_id, *, deployment=None, purpose=None):
    resource, apmid, environment = _deployment_policy_resource(
        deployment, resource_type="credentials"
    )
    resource.update({
        "id": str(credential_id),
        "type": "credentials",
        "credential_id": credential_id,
        "purpose": purpose,
    })
    scope_context = _scope(db, request, deployment, apmid=apmid, environment=environment)
    resource["organization"] = scope_context.get("organization")
    resource["project"] = scope_context.get("project")
    resource["scope_key"] = scope_context.get("key")
    context = {
        "actor": _actor(actor, permissions, db, scope_context),
        "scope": scope_context,
        "request": {
            "action": "credentials.use",
            "source": getattr(request.state, "source", "API"),
            "phase": "pre_request",
            "purpose": purpose,
        },
        "resource": resource,
    }
    result = evaluate_context(db, context, persist=True, durable_denies=True)
    _deny(result)
    return result


def evaluate_scheduled_operation(db, schedule, deployment, user, permissions):
    """Evaluate schedule.execute and the scheduled infrastructure operation."""
    resource, apmid, environment = _deployment_policy_resource(
        deployment, resource_type="schedule"
    )
    scope_context = _scope_from_ids(
        db, getattr(schedule, "tenant_id", ""), getattr(schedule, "project_id", ""),
        apmid=apmid, environment=environment,
    )
    schedule_context = {
        "actor": _user_actor(user, permissions, db, scope_context),
        "scope": scope_context,
        "request": {
            "action": "schedule.execute",
            "source": "Scheduler",
            "phase": "scheduled",
            "scheduled_operation": str(getattr(schedule, "operation", "") or ""),
        },
        "resource": {
            **resource,
            "id": str(getattr(schedule, "id", "") or ""),
            "type": "schedule",
            "schedule_id": str(getattr(schedule, "id", "") or ""),
        },
    }
    result = evaluate_context(db, schedule_context, persist=True)

    operation = str(getattr(schedule, "operation", "") or "")
    op_resource, _, _ = _deployment_policy_resource(
        deployment, resource_type="terraform"
    )
    op_resource.update({
        "organization": scope_context.get("organization"),
        "project": scope_context.get("project"),
        "scope_key": scope_context.get("key"),
    })
    operation_context = {
        "actor": _user_actor(user, permissions, db, scope_context),
        "scope": scope_context,
        "request": {
            "action": operation,
            "source": "Scheduler",
            "phase": "scheduled",
        },
        "resource": op_resource,
    }
    operation_result = evaluate_context(db, operation_context, persist=True)
    result = _merge_policy_results(result, operation_result)

    credential_id = getattr(deployment, "credentials_id", None)
    if credential_id:
        credential_context = {
            "actor": _user_actor(user, permissions, db, scope_context),
            "scope": scope_context,
            "request": {
                "action": "credentials.use",
                "source": "Scheduler",
                "phase": "scheduled",
                "purpose": operation,
            },
            "resource": {
                **op_resource,
                "id": str(credential_id),
                "type": "credentials",
                "credential_id": credential_id,
            },
        }
        result = _merge_policy_results(
            result, evaluate_context(db, credential_context, persist=True)
        )
    return result


def evaluate_webhook_delivery(db, endpoint, delivery, user, permissions):
    """Authorize an outbound webhook immediately before network delivery."""
    payload = dict(getattr(delivery, "payload", {}) or {})
    scope_data = payload.get("scope") if isinstance(payload.get("scope"), dict) else {}
    tenant_id = str(scope_data.get("tenant_id") or "")
    project_id = str(scope_data.get("project_id") or "")
    scope_context = _scope_from_ids(db, tenant_id, project_id)
    context = {
        "actor": _user_actor(user, permissions, db, scope_context),
        "scope": scope_context,
        "request": {
            "action": "webhook.execute",
            "source": "WebhookWorker",
            "phase": "pre_request",
            "event": str(getattr(delivery, "event", "") or ""),
        },
        "resource": {
            "id": str(getattr(endpoint, "id", "") or ""),
            "type": "webhook",
            "name": str(getattr(endpoint, "name", "") or ""),
            "event": str(getattr(delivery, "event", "") or ""),
            "organization": scope_context.get("organization"),
            "project": scope_context.get("project"),
            "scope_key": scope_context.get("key"),
        },
    }
    return evaluate_context(db, context, persist=True)


def revalidate_job_operation(db, job, user, permissions, deployment=None):
    """Re-evaluate a queued direct job immediately before worker execution."""
    operation = str(getattr(job, "operation", "") or "")
    if operation.startswith("day2."):
        return None
    if operation not in {
        "terraform.plan", "terraform.apply", "terraform.destroy", "terraform.import",
        "proxmox.provision", "proxmox.destroy", "ansible.execute",
    }:
        return None

    payload = dict(getattr(job, "payload", {}) or {})
    parameters = dict(payload.get("ansible") or {}) if operation == "ansible.execute" else {}
    resource_type = "ansible" if operation == "ansible.execute" else "terraform"
    resource, apmid, environment = _deployment_policy_resource(
        deployment, resource_type=resource_type
    )
    if operation == "ansible.execute":
        resource.update({
            "playbook": parameters.get("playbook"),
            "credential_id": parameters.get("credentials_id"),
        })
        apmid = parameters.get("apmid") or apmid
        environment = parameters.get("environment") or environment

    scope_context = _scope_from_ids(
        db,
        getattr(job, "tenant_id", ""),
        getattr(job, "project_id", ""),
        apmid=apmid, environment=environment,
    )
    resource.setdefault("organization", scope_context.get("organization"))
    resource.setdefault("project", scope_context.get("project"))
    resource.setdefault("scope_key", scope_context.get("key"))
    context = {
        "actor": _user_actor(user, permissions, db, scope_context),
        "scope": scope_context,
        "request": {
            "action": operation,
            "source": getattr(job, "source", "Worker"),
            "phase": "worker_revalidate",
            "parameters": copy.deepcopy(parameters),
        },
        "resource": resource,
    }
    result = evaluate_context(db, context, persist=True)

    credential_id = (
        parameters.get("credentials_id")
        if operation == "ansible.execute"
        else getattr(deployment, "credentials_id", None)
    )
    if credential_id:
        credential_context = {
            "actor": _user_actor(user, permissions, db, scope_context),
            "scope": scope_context,
            "request": {
                "action": "credentials.use",
                "source": getattr(job, "source", "Worker"),
                "phase": "worker_revalidate",
                "purpose": operation,
            },
            "resource": {
                **resource,
                "id": str(credential_id),
                "type": "credentials",
                "credential_id": credential_id,
            },
        }
        credential_result = evaluate_context(db, credential_context, persist=True)
        result = _merge_policy_results(result, credential_result)

    blueprint = dict(payload.get("blueprint") or {})
    if blueprint and operation in {"terraform.apply", "proxmox.provision"} and deployment is not None:
        blueprint_result = revalidate_blueprint_job(db, job, user, permissions, deployment)
        if blueprint_result:
            result = _merge_policy_results(result, blueprint_result)
            if blueprint_result.get("input_policy_drift"):
                result["input_policy_drift"] = copy.deepcopy(
                    blueprint_result["input_policy_drift"]
                )
    return result

def enforce_vm_create(db, request, actor, permissions, rendered):
    """Apply vm.create policy to a non-Blueprint deployment request."""
    rendered = copy.deepcopy(dict(rendered or {}))
    variables = dict(rendered.get("variables") or {})
    tags = _tags(variables)
    tagged_apmid, tagged_environment = _classification_from_tags(tags)
    apmid = variables.get("apmid") or tagged_apmid
    environment = variables.get("environment") or tagged_environment
    scope_context = _scope(db, request, apmid=apmid, environment=environment)
    context = {
        "actor": _actor(actor, permissions, db, scope_context),
        "scope": scope_context,
        "request": {
            "action": "vm.create",
            "source": getattr(request.state, "source", "API"),
            "phase": "pre_provision",
            "payload": copy.deepcopy(rendered),
        },
        "resource": {
            "type": "vm",
            "apmid": apmid,
            "environment": environment,
            "organization": scope_context.get("organization"),
            "project": scope_context.get("project"),
            "scope_key": scope_context.get("key"),
            "provider_id": rendered.get("provider_id"),
            "template": rendered.get("template"),
            "cpu": _number_from(variables, ("cores", "cpu", "vcpu", "cpu_cores")),
            "memory_mb": _number_from(variables, ("memory", "memory_mb", "ram_mb")),
            "disk_gb": _number_from(variables, ("disk_size_gb", "disk_gb", "disk_size")),
            "storage": variables.get("storage") or variables.get("datastore") or variables.get("datastore_id"),
            "network": variables.get("network") or variables.get("bridge") or variables.get("network_id"),
            "node": variables.get("node") or variables.get("host") or variables.get("target_node"),
            "cluster": variables.get("cluster") or variables.get("cluster_id"),
            "tags": tags,
            "variables": copy.deepcopy(variables),
        },
    }
    result = evaluate_context(db, context, persist=True, durable_denies=True)
    _deny(result)
    effective = result.get("effective_context") or context
    payload = (effective.get("request") or {}).get("payload")
    if isinstance(payload, dict):
        rendered = copy.deepcopy(payload)
    _write_aliases(
        db, rendered, effective.get("resource") or {},
        effective.get("scope") or {},
    )
    return rendered, result


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
    blueprint_context = {
        "actor": _actor(actor, permissions, db, scope_context),
        "scope": scope_context,
        "request": {
            "action": "blueprint.execute",
            "source": getattr(request.state, "source", "API"),
            "phase": "pre_request",
        },
        "blueprint": {
            "id": getattr(blueprint, "id", None),
            "slug": getattr(blueprint, "slug", ""),
            "version": getattr(blueprint, "version", None),
            "tags": list((getattr(blueprint, "visibility", {}) or {}).get("tags") or []),
        },
        "resource": {
            "id": str(getattr(blueprint, "id", "") or ""),
            "type": "blueprint",
            "apmid": apmid_value,
            "environment": environment_value,
            "organization": scope_context.get("organization"),
            "project": scope_context.get("project"),
            "scope_key": scope_context.get("key"),
        },
    }
    blueprint_result = evaluate_context(
        db, blueprint_context, persist=True, durable_denies=True
    )
    _deny(blueprint_result)

    context = {
        "actor": _actor(actor, permissions, db, scope_context),
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
            "storage": variables.get("storage") or variables.get("datastore") or variables.get("datastore_id"),
            "network": variables.get("network") or variables.get("bridge") or variables.get("network_id"),
            "node": variables.get("node") or variables.get("host") or variables.get("target_node"),
            "cluster": variables.get("cluster") or variables.get("cluster_id"),
            "tags": classification_tags,
            "variables": copy.deepcopy(variables),
        },
    }
    result = evaluate_context(db, context, persist=True, durable_denies=True)
    _deny(result)
    result = _merge_policy_results(result, blueprint_result)

    effective = result.get("effective_context") or context
    payload = ((effective.get("request") or {}).get("payload"))
    if isinstance(payload, dict):
        rendered = copy.deepcopy(payload)
    effective_resource = effective.get("resource") or {}
    _write_aliases(db, rendered, effective_resource, effective.get("scope") or {})
    return rendered, result


def _resource_metadata(db, target):
    row = db.get(ManagedResource, str(getattr(target, "resource_id", "")))
    if row is None and getattr(target, "deployment_id", None):
        row = db.scalar(select(ManagedResource).where(
            ManagedResource.deployment_id == str(target.deployment_id)
        ))
    metadata = dict(getattr(row, "metadata_json", {}) or {}) if row is not None else {}
    tags = metadata.get("tags") or []
    if isinstance(tags, str):
        tags = [item for item in tags.replace(",", ";").split(";") if item]
    return row, metadata, list(tags) if isinstance(tags, list) else []


def _day2_requested_resource(action_id, params):
    """Project requested Day-2 values onto canonical resource fields.

    Policy constraints are defined against resource.*. Provider request schemas
    use action-specific names, so this adapter prevents limits from silently
    ignoring a resize, disk or network value because the canonical field would
    otherwise be missing from the evaluation context.
    """
    action = str(action_id or "")
    values = dict(params or {})
    requested = {}
    if action == "resize_compute":
        if values.get("cpu_cores") is not None:
            requested["cpu"] = values["cpu_cores"]
        if values.get("memory_mb") is not None:
            requested["memory_mb"] = values["memory_mb"]
    elif action == "add_disk":
        requested["disk_gb"] = values.get("size_gib")
        requested["storage"] = values.get("storage")
    elif action == "resize_disk":
        requested["disk_gb"] = values.get("new_size_gib")
    elif action in {"add_nic", "edit_nic"}:
        requested["network"] = values.get("bridge")
        requested["vlan"] = values.get("vlan")
    elif action == "migrate_vm":
        requested["node"] = values.get("target_node")
    elif action == "move_storage":
        requested["storage"] = values.get("target_storage")
    elif action == "clone_vm":
        requested["node"] = values.get("target_node")
        requested["storage"] = values.get("target_storage")
    return {key: value for key, value in requested.items() if value is not None}


def _apply_day2_effective_values(action_id, params, resource):
    """Write policy-selected canonical values back to the provider parameters."""
    action = str(action_id or "")
    result = copy.deepcopy(dict(params or {}))
    resource = dict(resource or {})
    mapping = {
        "resize_compute": {"cpu": "cpu_cores", "memory_mb": "memory_mb"},
        "add_disk": {"disk_gb": "size_gib", "storage": "storage"},
        "resize_disk": {"disk_gb": "new_size_gib"},
        "add_nic": {"network": "bridge", "vlan": "vlan"},
        "edit_nic": {"network": "bridge", "vlan": "vlan"},
        "migrate_vm": {"node": "target_node"},
        "move_storage": {"storage": "target_storage"},
        "clone_vm": {"node": "target_node", "storage": "target_storage"},
    }.get(action, {})
    for canonical, parameter in mapping.items():
        if resource.get(canonical) is not None:
            result[parameter] = resource[canonical]
    return result


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
        "actor": _actor(actor, permissions, db, scope_context),
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
            **_day2_requested_resource(action_id, params),
        },
    }
    result = evaluate_context(db, context, persist=True, durable_denies=True)
    _deny(result)
    effective = result.get("effective_context") or context
    effective_params = ((effective.get("request") or {}).get("parameters"))
    if isinstance(effective_params, dict):
        params = copy.deepcopy(effective_params)
    params = _apply_day2_effective_values(action_id, params, effective.get("resource") or {})
    return params, result


def revalidate_blueprint_job(db, job, user, permissions, deployment):
    """Re-evaluate current policy immediately before a persisted Blueprint job executes."""
    if getattr(job, "operation", None) not in {"terraform.apply", "proxmox.provision"}:
        return None
    blueprint = dict((getattr(job, "payload", {}) or {}).get("blueprint") or {})
    if not blueprint or deployment is None:
        return None

    variables = dict(getattr(deployment, "variables", {}) or {})
    blueprint_variables = dict(blueprint.get("variables") or {})
    tags = _tags(variables)
    tagged_apmid, tagged_environment = _classification_from_tags(tags)
    apmid_value = blueprint_variables.get("apmid") or tagged_apmid
    environment_value = blueprint_variables.get("environment") or tagged_environment
    scope_context = _scope_from_ids(
        db,
        getattr(job, "tenant_id", ""),
        getattr(job, "project_id", ""),
        apmid=apmid_value,
        environment=environment_value,
    )
    resource = {
        "type": "vm",
        "id": str(getattr(deployment, "id", "") or ""),
        "apmid": apmid_value,
        "environment": environment_value,
        "organization": scope_context.get("organization") or blueprint_variables.get("organization"),
        "project": scope_context.get("project") or blueprint_variables.get("project"),
        "scope_key": scope_context.get("key") or blueprint_variables.get("scope_key"),
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
        "actor": _user_actor(user, permissions, db, scope_context),
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
        "cluster", "provider_id", "template", "tags", "variables",
        "apmid", "environment", "organization", "project", "scope_key",
    )
    drift = {
        field: {"queued": resource.get(field), "policy_now": effective_resource.get(field)}
        for field in protected
        if effective_resource.get(field) != resource.get(field)
    }
    result["input_policy_drift"] = drift
    return result


def revalidate_day2_job(db, job, action_request, user, permissions, target):
    """Re-evaluate current Day-2 policy immediately before provider dispatch."""
    row, metadata, tags = _resource_metadata(db, target)
    tagged_apmid, tagged_environment = _classification_from_tags(tags)
    apmid_value = metadata.get("apmid") or tagged_apmid
    environment_value = metadata.get("environment") or tagged_environment
    scope_context = _scope_from_ids(
        db,
        getattr(job, "tenant_id", ""),
        getattr(job, "project_id", ""),
        apmid=apmid_value,
        environment=environment_value,
    )
    context = {
        "actor": _user_actor(user, permissions, db, scope_context),
        "scope": scope_context,
        "request": {
            "action": "day2." + str(getattr(action_request, "action", "")),
            "source": getattr(job, "source", "Worker"),
            "phase": "worker_revalidate",
            "parameters": copy.deepcopy(getattr(action_request, "parameters", {}) or {}),
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
            **_day2_requested_resource(
                getattr(action_request, "action", ""),
                getattr(action_request, "parameters", {}) or {},
            ),
        },
    }
    return evaluate_context(db, context, persist=True)
