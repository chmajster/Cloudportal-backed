from __future__ import annotations

import re
from fnmatch import fnmatchcase
from types import SimpleNamespace
from typing import Any, Mapping


ENTITY_PATTERN = re.compile(
    r"^entity\.(?P<apmid>[A-Z0-9][A-Z0-9_-]{0,62})\."
    r"(?P<environment>[a-z0-9][a-z0-9_-]{0,31})\."
    r"(?P<role>[a-z0-9][a-z0-9-]{0,31})$"
)

ENTITY_ROLES: tuple[dict[str, Any], ...] = (
    {
        "id": "read-only",
        "label": "Read-only",
        "description": "Tylko odczyt Blueprintu i jego metadanych.",
        "rank": 10,
        "anchors": ("blueprints.read",),
        "actions": ("blueprints.read",),
    },
    {
        "id": "operator",
        "label": "Operator",
        "description": "Odczyt i uruchamianie Blueprintu bez zarządzania jego definicją.",
        "rank": 20,
        "anchors": ("blueprints.execute",),
        "actions": ("blueprints.read", "blueprints.execute"),
    },
    {
        "id": "deployer",
        "label": "Deployer",
        "description": "Uruchamianie i klonowanie Blueprintów oraz tworzenie deploymentów.",
        "rank": 30,
        "anchors": ("blueprints.execute", "deployments.create"),
        "actions": ("blueprints.read", "blueprints.execute", "blueprints.clone"),
    },
    {
        "id": "maintainer",
        "label": "Maintainer",
        "description": "Eksploatacja oraz edycja Blueprintu, bez usuwania i zarządzania ACL.",
        "rank": 40,
        "anchors": ("blueprints.execute", "blueprints.update"),
        "actions": (
            "blueprints.read", "blueprints.execute", "blueprints.clone",
            "blueprints.update", "blueprints.publish", "blueprints.approve",
        ),
    },
    {
        "id": "admin",
        "label": "Admin",
        "description": "Pełny dostęp do Blueprintu w danym APMID i Environment.",
        "rank": 50,
        "anchors": (
            "blueprints.execute", "blueprints.update",
            "blueprints.delete", "blueprints.manage_access",
        ),
        "actions": ("blueprints.*",),
    },
)
_ROLE_BY_ID = {row["id"]: row for row in ENTITY_ROLES}


def _apmid(value: Any) -> str:
    return str(value or "").strip().upper()


def _environment(value: Any) -> str:
    return str(value or "").strip().lower()


def _role(value: Any) -> str:
    return str(value or "").strip().lower()


def build_entity_key(apmid: Any, environment: Any, role: Any) -> str:
    value = f"entity.{_apmid(apmid)}.{_environment(environment)}.{_role(role)}"
    parse_entity_key(value)
    return value


def parse_entity_key(value: Any) -> dict[str, str]:
    text = str(value or "").strip()
    match = ENTITY_PATTERN.fullmatch(text)
    if match is None:
        raise ValueError(
            "Entity must use canonical format entity.<APMID>.<env>.<role>"
        )
    result = match.groupdict()
    if result["role"] not in _ROLE_BY_ID:
        raise ValueError(
            "Unknown entity role: " + result["role"]
            + ". Allowed: " + ", ".join(_ROLE_BY_ID)
        )
    return result


def normalize_entity_key(value: Any) -> str:
    raw = str(value or "").strip()
    parts = raw.split(".")
    if len(parts) != 4 or parts[0].lower() != "entity":
        raise ValueError("Entity must use format entity.<APMID>.<env>.<role>")
    return build_entity_key(parts[1], parts[2], parts[3])


def entity_role_catalog() -> list[dict[str, Any]]:
    return [
        {
            "id": row["id"],
            "label": row["label"],
            "description": row["description"],
            "rank": row["rank"],
            "actions": list(row["actions"]),
        }
        for row in ENTITY_ROLES
    ]


def entity_catalog(classification: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    classification = dict(classification or {})
    apmids = sorted({_apmid(value) for value in classification.get("apmids", []) if _apmid(value)})
    environments = [
        name
        for name, enabled in dict(classification.get("environments") or {}).items()
        if enabled is not False and _environment(name)
    ]
    environments = sorted({_environment(value) for value in environments})
    items = []
    for apmid in apmids:
        for environment in environments:
            for role in ENTITY_ROLES:
                try:
                    key = build_entity_key(apmid, environment, role["id"])
                except ValueError:
                    # Legacy/free-form classification values that cannot be
                    # represented safely in a dotted entity key are omitted.
                    continue
                items.append({
                    "key": key,
                    "apmid": apmid,
                    "environment": environment,
                    "role": role["id"],
                    "role_label": role["label"],
                    "description": role["description"],
                    "rank": role["rank"],
                })
    return items


def _action_allowed(role: str, action: str) -> bool:
    profile = _ROLE_BY_ID.get(role)
    if profile is None:
        return False
    return any(fnmatchcase(str(action), pattern) for pattern in profile["actions"])


def entity_keys_for_permissions(
    permissions: set[str] | frozenset[str] | list[str] | tuple[str, ...],
    *,
    apmid: Any,
    environment: Any,
) -> list[str]:
    normalized_apmid = _apmid(apmid)
    normalized_environment = _environment(environment)
    if not normalized_apmid or not normalized_environment:
        return []
    granted = set(permissions or ())
    result = []
    for role in ENTITY_ROLES:
        if set(role["anchors"]) <= granted:
            result.append(build_entity_key(normalized_apmid, normalized_environment, role["id"]))
    return result


def _actor_user_id(actor: Any) -> int | None:
    for candidate in (
        getattr(actor, "user_id", None),
        getattr(getattr(actor, "user", None), "id", None),
        getattr(actor, "id", None),
    ):
        try:
            if candidate is not None:
                return int(candidate)
        except (TypeError, ValueError):
            continue
    return None


def _authorization_actor(actor: Any):
    if getattr(actor, "user_id", None) is not None and getattr(actor, "user", None) is not None:
        return actor
    user_id = getattr(actor, "id", None)
    if user_id is None:
        return actor
    return SimpleNamespace(
        user_id=int(user_id),
        user=actor,
        id=None,
        kind="simulation",
        scopes=(),
    )


def actor_matches_entity(
    db,
    actor: Any,
    entity: str,
    *,
    action: str,
    tenant_id: Any,
    project_id: Any,
    resource: Mapping[str, Any] | None = None,
) -> bool:
    """Return whether the actor qualifies for a virtual Policy Engine entity.

    Entity ACLs are an additional restriction. They never grant a permission:
    every anchor and the requested action must already be ALLOW/REQUIRES_APPROVAL
    in Enterprise IAM for the same APMID/Environment scope.
    """
    parts = parse_entity_key(entity)
    if not _action_allowed(parts["role"], action):
        return False
    user_id = _actor_user_id(actor)
    if user_id is None:
        return False

    from app.iam.service import authorize

    authorization_actor = _authorization_actor(actor)
    scope = {
        "scope_type": "ENVIRONMENT",
        "scope_id": f'{parts["apmid"]}:{parts["environment"]}',
        "tenant_id": str(tenant_id or "") or None,
        "project_id": str(project_id or "") or None,
        "apmid": parts["apmid"],
        "environment": parts["environment"],
    }
    target = {
        "type": "blueprint",
        "apmid": parts["apmid"],
        "environment": parts["environment"],
        **dict(resource or {}),
    }
    required = list(_ROLE_BY_ID[parts["role"]]["anchors"])
    if action not in required:
        required.append(action)
    for permission in required:
        decision = authorize(
            db,
            authorization_actor,
            permission,
            scope=scope,
            resource=target,
            write=permission not in {"blueprints.read"},
            subject_user_id=user_id,
        )
        if decision.decision not in {"ALLOW", "REQUIRES_APPROVAL"}:
            return False
    return True


def actor_matches_any_entity(
    db,
    actor: Any,
    entities: list[str] | tuple[str, ...],
    *,
    action: str,
    tenant_id: Any,
    project_id: Any,
    apmid: Any = None,
    environment: Any = None,
    resource: Mapping[str, Any] | None = None,
) -> bool:
    wanted_apmid = _apmid(apmid)
    wanted_environment = _environment(environment)
    for raw in entities or ():
        try:
            parts = parse_entity_key(raw)
        except ValueError:
            continue
        if wanted_apmid and parts["apmid"] != wanted_apmid:
            continue
        if wanted_environment and parts["environment"] != wanted_environment:
            continue
        if actor_matches_entity(
            db,
            actor,
            raw,
            action=action,
            tenant_id=tenant_id,
            project_id=project_id,
            resource=resource,
        ):
            return True
    return False
