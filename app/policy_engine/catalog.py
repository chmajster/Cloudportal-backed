"""No-code Policy Engine catalogs and deterministic policy descriptions.

This module contains UI metadata only. Runtime authorization remains in
:mod:`app.policy_engine.engine`; catalogs never make authorization decisions.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any


ACTION_CATALOG = (
    ("VM", (
        ("vm.create", "Tworzenie VM"),
        ("vm.read", "Odczyt VM"),
        ("vm.update", "Edycja VM"),
        ("vm.delete", "Usuwanie VM"),
        ("vm.power_on", "Włączanie VM"),
        ("vm.power_off", "Wyłączanie VM"),
        ("vm.restart", "Restart VM"),
        ("vm.hard_stop", "Hard stop"),
        ("vm.rebuild", "Rebuild"),
        ("vm.clone", "Klonowanie"),
        ("vm.migrate", "Migracja"),
        ("vm.resize", "Zmiana CPU/RAM"),
    )),
    ("Dyski", (
        ("disk.add", "Dodawanie dysku"),
        ("disk.remove", "Usuwanie dysku"),
        ("disk.resize", "Powiększanie dysku"),
        ("disk.attach", "Podpinanie dysku"),
        ("disk.detach", "Odłączanie dysku"),
    )),
    ("Sieć", (
        ("nic.add", "Dodawanie NIC"),
        ("nic.remove", "Usuwanie NIC"),
        ("nic.edit", "Edycja NIC"),
    )),
    ("Snapshoty", (
        ("snapshot.create", "Tworzenie snapshotu"),
        ("snapshot.restore", "Przywracanie snapshotu"),
        ("snapshot.delete", "Usuwanie snapshotu"),
    )),
    ("Blueprint", (
        ("blueprint.view", "Podgląd"),
        ("blueprint.execute", "Uruchamianie"),
        ("blueprint.create", "Tworzenie"),
        ("blueprint.edit", "Edycja"),
        ("blueprint.delete", "Usuwanie"),
    )),
    ("Terraform", (
        ("terraform.plan", "Plan"),
        ("terraform.apply", "Apply"),
        ("terraform.destroy", "Destroy"),
        ("terraform.import", "Import"),
    )),
    ("Ansible", (
        ("ansible.read", "Odczyt playbooków"),
        ("ansible.execute", "Uruchamianie"),
        ("ansible.manage", "Zarządzanie własnymi playbookami"),
    )),
    ("Dostępy", (
        ("credentials.read", "Odczyt metadanych"),
        ("credentials.create", "Tworzenie"),
        ("credentials.update", "Edycja"),
        ("credentials.delete", "Usuwanie"),
        ("credentials.use", "Używanie"),
    )),
    ("Platformy", (
        ("provider.read", "Odczyt"),
        ("provider.configure", "Konfiguracja"),
        ("provider.use", "Używanie"),
    )),
    ("Harmonogramy", (
        ("schedule.read", "Odczyt"),
        ("schedule.create", "Tworzenie"),
        ("schedule.update", "Edycja"),
        ("schedule.delete", "Usuwanie"),
    )),
    ("Webhooki", (
        ("webhook.read", "Odczyt"),
        ("webhook.create", "Tworzenie"),
        ("webhook.update", "Edycja"),
        ("webhook.delete", "Usuwanie"),
    )),
    ("API", (
        ("api.use", "Używanie API"),
        ("api.token.create", "Tworzenie tokenów"),
    )),
    ("System", (
        ("system.monitoring", "Monitoring"),
        ("system.audit", "Audyt"),
        ("system.settings", "Ustawienia"),
    )),
)

RESOURCE_TYPES = (
    "vm", "template", "blueprint", "deployment", "snapshot", "disk", "nic",
    "credentials", "ansible", "terraform", "api_token", "schedule", "webhook",
)

CONDITION_FIELDS = (
    {"group": "Tożsamość", "path": "actor.id", "label": "Użytkownik", "type": "user"},
    {"group": "Tożsamość", "path": "actor.role_ids", "label": "Rola", "type": "role_list"},
    {"group": "Tożsamość", "path": "actor.service_account", "label": "Service Account", "type": "boolean"},
    {"group": "Organizacja", "path": "scope.tenant_id", "label": "Organizacja", "type": "organization"},
    {"group": "Projekt", "path": "scope.project_id", "label": "Projekt", "type": "project"},
    {"group": "VM", "path": "resource.cpu", "label": "CPU", "type": "number"},
    {"group": "VM", "path": "resource.memory_mb", "label": "RAM (MB)", "type": "number"},
    {"group": "VM", "path": "resource.disk_gb", "label": "Dysk (GB)", "type": "number"},
    {"group": "VM", "path": "resource.vlan", "label": "VLAN", "type": "number"},
    {"group": "VM", "path": "resource.network", "label": "Sieć", "type": "network"},
    {"group": "VM", "path": "resource.storage", "label": "Storage", "type": "storage"},
    {"group": "VM", "path": "resource.tags", "label": "Tagi", "type": "tags"},
    {"group": "VM", "path": "resource.hostname", "label": "Hostname", "type": "text"},
    {"group": "VM", "path": "resource.template", "label": "Template", "type": "template"},
    {"group": "VM", "path": "resource.provider_id", "label": "Platforma", "type": "provider"},
    {"group": "VM", "path": "resource.node", "label": "Node", "type": "text"},
    {"group": "VM", "path": "resource.status", "label": "Status", "type": "text"},
    {"group": "CloudPortal", "path": "resource.apmid", "label": "APMID", "type": "apmid"},
    {"group": "CloudPortal", "path": "resource.environment", "label": "Environment", "type": "environment"},
    {"group": "CloudPortal", "path": "blueprint.id", "label": "Blueprint", "type": "blueprint"},
    {"group": "CloudPortal", "path": "resource.deployment_id", "label": "Deployment", "type": "text"},
    {"group": "CloudPortal", "path": "resource.owner_id", "label": "Właściciel zasobu", "type": "user"},
    {"group": "CloudPortal", "path": "resource.created_by", "label": "Utworzone przez", "type": "user"},
    {"group": "CloudPortal", "path": "resource.age_days", "label": "Wiek zasobu (dni)", "type": "number"},
    {"group": "CloudPortal", "path": "resource.type", "label": "Typ zasobu", "type": "resource_type"},
    {"group": "Czas", "path": "time.weekday", "label": "Dzień tygodnia", "type": "weekday"},
    {"group": "Czas", "path": "time.hour", "label": "Godzina", "type": "number"},
    {"group": "Czas", "path": "time.date", "label": "Data", "type": "date"},
    {"group": "Żądanie", "path": "request.action", "label": "Akcja", "type": "action"},
    {"group": "Żądanie", "path": "request.method", "label": "Metoda HTTP", "type": "method"},
    {"group": "Żądanie", "path": "request.source", "label": "Źródło", "type": "text"},
    {"group": "Żądanie", "path": "request.type", "label": "Typ żądania", "type": "text"},
)

OPERATOR_LABELS = {
    "eq": "jest równe",
    "neq": "nie jest równe",
    "in": "jest jednym z",
    "not_in": "nie jest jednym z",
    "contains": "zawiera",
    "not_contains": "nie zawiera",
    "contains_any": "zawiera dowolne",
    "contains_all": "zawiera wszystkie",
    "starts_with": "zaczyna się od",
    "ends_with": "kończy się na",
    "exists": "istnieje",
    "not_exists": "nie istnieje",
    "gt": "większe niż",
    "gte": "większe lub równe",
    "lt": "mniejsze niż",
    "lte": "mniejsze lub równe",
    "between": "jest w zakresie",
    "is_owner": "jest właścicielem",
    "is_member_of": "należy do",
    "has_tag": "ma tag",
}

FIELD_OPERATORS = {
    "number": ("eq", "neq", "gt", "gte", "lt", "lte", "between", "exists", "not_exists"),
    "boolean": ("eq", "neq", "exists", "not_exists"),
    "text": ("eq", "neq", "in", "not_in", "contains", "not_contains", "starts_with", "ends_with", "exists", "not_exists"),
    "tags": ("contains", "not_contains", "contains_any", "contains_all", "has_tag", "exists", "not_exists"),
    "role_list": ("contains", "not_contains", "contains_any", "contains_all", "exists", "not_exists"),
}
_DEFAULT_ENUM_OPERATORS = ("eq", "neq", "in", "not_in", "exists", "not_exists")


def condition_fields():
    result = []
    for item in CONDITION_FIELDS:
        field = dict(item)
        field["operators"] = list(FIELD_OPERATORS.get(field["type"], _DEFAULT_ENUM_OPERATORS))
        result.append(field)
    return result


def action_catalog():
    return [
        {
            "category": category,
            "actions": [{"value": value, "label": label} for value, label in actions],
        }
        for category, actions in ACTION_CATALOG
    ]


def _base_policy(name: str, description: str, *, policy_type="access", effects=None, scope=None, condition=None):
    return {
        "name": name,
        "description": description,
        "policy_type": policy_type,
        "priority": 500,
        "enforcement": "hard",
        "status": "draft",
        "scope_level": "project",
        "scope": deepcopy(scope or {"resource_types": ["vm"]}),
        "condition": deepcopy(condition or {}),
        "effects": deepcopy(effects or [{"type": "allow", "mode": "whitelist"}]),
    }


def policy_templates():
    return [
        {
            "id": "developer-dev",
            "name": "Developer DEV",
            "description": "Tworzenie i obsługa niewielkich VM w DEV.",
            "policy": _base_policy(
                "Developer DEV",
                "Developerzy mogą tworzyć VM w DEV w kontrolowanych limitach.",
                scope={"resource_types": ["vm"], "environments": ["dev"], "actions": [
                    "vm.create", "vm.read", "vm.power_on", "vm.power_off", "vm.restart",
                ]},
                effects=[
                    {"type": "allow", "mode": "whitelist"},
                    {"type": "limit_value", "field": "resource.cpu", "max": 4},
                    {"type": "limit_value", "field": "resource.memory_mb", "max": 8192},
                    {"type": "limit_value", "field": "resource.disk_gb", "max": 100},
                ],
            ),
        },
        {
            "id": "developer-prod-restricted",
            "name": "Developer PROD restricted",
            "description": "Ograniczony dostęp developerów do PROD.",
            "policy": _base_policy(
                "Developer PROD restricted",
                "Dostęp do PROD z limitami i approval dla większych VM.",
                scope={"resource_types": ["vm"], "environments": ["prod"], "actions": [
                    "vm.create", "vm.read", "vm.power_on", "vm.power_off", "vm.restart",
                ]},
                effects=[
                    {"type": "allow", "mode": "whitelist"},
                    {"type": "limit_value", "field": "resource.cpu", "max": 4},
                    {"type": "limit_value", "field": "resource.memory_mb", "max": 16384},
                    {"type": "require_approval", "message": "RAM powyżej 8 GB wymaga zatwierdzenia",
                     "when": {"field": "resource.memory_mb", "operator": "gt", "value": 8192}},
                ],
            ),
        },
        {
            "id": "production-security",
            "name": "Production security",
            "description": "Twarde ograniczenia bezpieczeństwa dla PROD.",
            "policy": _base_policy(
                "Production security",
                "Wymaga MFA i uzasadnienia dla operacji w PROD.",
                policy_type="security",
                scope={"environments": ["prod"], "resource_types": ["vm"]},
                effects=[{"type": "require_mfa"}, {"type": "require_justification"}],
            ),
        },
        {
            "id": "read-only",
            "name": "Read-only",
            "description": "Wyłącznie operacje odczytu.",
            "policy": _base_policy(
                "Read-only",
                "Pozwala tylko na odczyt zasobów.",
                scope={"actions": ["vm.read", "blueprint.view", "schedule.read"], "resource_types": ["vm", "blueprint", "schedule"]},
                effects=[{"type": "allow", "mode": "whitelist"}],
            ),
        },
        {
            "id": "vm-size-limits",
            "name": "VM size limits",
            "description": "Limity CPU, RAM i dysku.",
            "policy": _base_policy(
                "VM size limits",
                "Ogranicza maksymalny rozmiar VM.",
                policy_type="compute",
                effects=[
                    {"type": "limit_value", "field": "resource.cpu", "max": 8},
                    {"type": "limit_value", "field": "resource.memory_mb", "max": 32768},
                    {"type": "limit_value", "field": "resource.disk_gb", "max": 500},
                ],
            ),
        },
        {
            "id": "network-isolation",
            "name": "Network isolation",
            "description": "Allow-list sieci.",
            "policy": _base_policy(
                "Network isolation",
                "Pozwala używać wyłącznie wskazanych sieci.",
                policy_type="network",
                effects=[{"type": "limit_value", "field": "resource.network", "allowed": ["vmbr10"]}],
            ),
        },
        {
            "id": "storage-restriction",
            "name": "Storage restriction",
            "description": "Allow-list storage.",
            "policy": _base_policy(
                "Storage restriction",
                "Pozwala używać wyłącznie zatwierdzonego storage.",
                policy_type="storage",
                effects=[{"type": "limit_value", "field": "resource.storage", "allowed": ["local-lvm"]}],
            ),
        },
        {
            "id": "require-approval-prod",
            "name": "Require approval for PROD",
            "description": "Każda wskazana operacja w PROD wymaga approval.",
            "policy": _base_policy(
                "Require approval for PROD",
                "Wymaga zatwierdzenia operacji w PROD.",
                policy_type="approval",
                scope={"environments": ["prod"], "resource_types": ["vm"]},
                effects=[{"type": "require_approval", "approver": {"type": "project_admin"}}],
            ),
        },
        {
            "id": "restrict-destructive",
            "name": "Restrict destructive actions",
            "description": "Blokada destrukcyjnych operacji.",
            "policy": _base_policy(
                "Restrict destructive actions",
                "Blokuje delete, rebuild i Terraform destroy.",
                policy_type="deletion",
                scope={"actions": ["vm.delete", "vm.rebuild", "terraform.destroy"]},
                effects=[{"type": "deny", "message": "Operacja destrukcyjna jest zablokowana przez politykę."}],
            ),
        },
        {
            "id": "apmid-isolation",
            "name": "APMID isolation",
            "description": "Izolacja operacji do wybranego APMID.",
            "policy": _base_policy(
                "APMID isolation",
                "Dostęp tylko do wskazanego APMID.",
                scope={"apmids": ["LEO"], "resource_types": ["vm"]},
                effects=[{"type": "allow", "mode": "whitelist"}],
            ),
        },
        {
            "id": "environment-isolation",
            "name": "Environment isolation",
            "description": "Izolacja do DEV/TEST.",
            "policy": _base_policy(
                "Environment isolation",
                "Dostęp tylko do DEV i TEST.",
                scope={"environments": ["dev", "test"], "resource_types": ["vm"]},
                effects=[{"type": "allow", "mode": "whitelist"}],
            ),
        },
        {
            "id": "blueprint-allow-list",
            "name": "Blueprint allow-list",
            "description": "Allow-list Blueprintów.",
            "policy": _base_policy(
                "Blueprint allow-list",
                "Pozwala wykonywać tylko zatwierdzone Blueprinty.",
                scope={"actions": ["blueprint.execute"], "resource_types": ["blueprint"]},
                effects=[{"type": "allow", "mode": "whitelist"}],
            ),
        },
    ]


def _values(scope: dict[str, Any], key: str, label: str) -> str | None:
    values = scope.get(key)
    if not values:
        return None
    if not isinstance(values, (list, tuple, set)):
        values = [values]
    return f"{label}: " + ", ".join(map(str, values))


def human_policy_summary(policy: dict[str, Any]) -> str:
    """Return a deterministic Polish description; no LLM is involved."""
    name = str(policy.get("name") or "Polityka")
    scope = dict(policy.get("scope") or {})
    effects = list(policy.get("effects") or [])
    parts = [name + "."]

    scope_parts = [
        _values(scope, "organizations", "organizacja"),
        _values(scope, "projects", "projekt"),
        _values(scope, "apmids", "APMID"),
        _values(scope, "environments", "środowisko"),
    ]
    scope_parts = [value for value in scope_parts if value]
    if scope_parts:
        parts.append("Zakres: " + "; ".join(scope_parts) + ".")

    actions = scope.get("actions") or []
    if actions:
        parts.append("Akcje: " + ", ".join(map(str, actions)) + ".")

    descriptions = []
    for effect in effects:
        kind = effect.get("type")
        if kind == "allow":
            descriptions.append("pozwala na operacje w tym zakresie")
        elif kind == "deny":
            descriptions.append("blokuje operacje w tym zakresie")
        elif kind == "limit_value":
            field = str(effect.get("field") or "").replace("resource.", "")
            limits = []
            if effect.get("min") is not None:
                limits.append(f"min {effect['min']}")
            if effect.get("max") is not None:
                limits.append(f"max {effect['max']}")
            if effect.get("allowed") is not None:
                limits.append("dozwolone: " + ", ".join(map(str, effect.get("allowed") or [])))
            descriptions.append(f"{field}: " + ", ".join(limits))
        elif kind == "require_approval":
            descriptions.append("wymaga zatwierdzenia" + (" po spełnieniu warunku" if effect.get("when") else ""))
        elif kind.startswith("select_"):
            descriptions.append(f"{kind.replace('select_', '')}: {effect.get('value')}")
        elif kind.startswith("require_"):
            descriptions.append(kind.replace("_", " "))
    if descriptions:
        parts.append("Efekt: " + "; ".join(descriptions) + ".")
    return " ".join(parts)
