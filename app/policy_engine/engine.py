from __future__ import annotations

import copy
import ipaddress
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping


MISSING = object()

OPERATOR_ALIASES = {
    "equals": "eq",
    "not_equals": "neq",
    "less_than": "lt",
    "less_than_or_equal": "lte",
    "greater_than": "gt",
    "greater_than_or_equal": "gte",
    "matches_regex": "regex",
}

OPERATORS = frozenset({
    "eq", "neq", "in", "not_in", "contains", "not_contains",
    "contains_any", "contains_all", "starts_with", "ends_with", "regex",
    "exists", "not_exists", "gt", "gte", "lt", "lte", "between",
    "cidr_contains", "before", "after", "is_owner", "is_member_of",
    "has_tag", "count_lt", "count_lte", "count_gt", "count_gte",
})

EFFECT_TYPES = frozenset({
    "allow", "deny", "warn", "require_approval",
    "set_default", "force_value", "limit_value",
    "add_tag", "remove_tag",
    "select_provider", "select_cluster", "select_storage", "select_network",
    "set_lease", "require_backup", "require_snapshot", "require_ansible",
    "require_mfa", "require_justification", "require_change_ticket",
    "notify",
})

POLICY_TYPES = (
    "access", "deployment", "approval", "quota", "lease", "day2",
    "placement", "compute", "storage", "network", "image", "naming",
    "tagging", "security", "backup", "snapshot", "schedule", "cost",
    "lifecycle", "compliance", "credential", "ansible", "terraform",
    "deletion", "exception",
)

STATUSES = frozenset({"draft", "dry_run", "enforced", "disabled", "archived"})
ENFORCEMENTS = frozenset({"hard", "soft", "advisory"})

MAX_REGEX_PATTERN_LENGTH = 256
MAX_REGEX_SUBJECT_LENGTH = 4096
_REGEX_UNSAFE_TOKENS = ("(?=", "(?!", "(?<=", "(?<!", "(?P=", "(?>")
_APPROVER_TYPES = frozenset({
    "any", "any_approver", "user", "role", "group", "permission",
    "project_admin", "tenant_admin", "apmid_owner",
})


def validate_safe_regex(pattern: Any) -> str:
    text = str(pattern or "")
    if not text:
        raise ValueError("Regex pattern cannot be empty")
    if len(text) > MAX_REGEX_PATTERN_LENGTH:
        raise ValueError("Regex pattern is too long")
    if any(token in text for token in _REGEX_UNSAFE_TOKENS):
        raise ValueError("Regex lookarounds/backtracking control are not supported")
    if re.search(r"\\[1-9]", text):
        raise ValueError("Regex backreferences are not supported")
    # Quantified groups are the main source of catastrophic nested
    # backtracking, e.g. (a+)+ or (a|aa)+. Keep the policy matcher on a
    # deliberately conservative subset instead of accepting unsafe patterns.
    if re.search(r"\)(?:[+*?]|\{\d+(?:,\d*)?\})", text):
        raise ValueError("Quantified regex groups are not supported")
    for match in re.finditer(r"\{(\d+)(?:,(\d*))?\}", text):
        lower = int(match.group(1))
        upper_text = match.group(2)
        upper = lower if upper_text is None else (int(upper_text) if upper_text else 1001)
        if lower > 1000 or upper > 1000:
            raise ValueError("Regex repetition limit cannot exceed 1000")
    try:
        re.compile(text)
    except re.error:
        raise ValueError("Invalid regex pattern") from None
    return text


@dataclass(frozen=True, slots=True)
class Evaluation:
    decision: str
    effective_context: dict
    matched_policy_ids: list[str]
    applied_effects: list[dict]
    warnings: list[str]
    violations: list[dict]
    approvals: list[dict]
    obligations: list[dict]
    trace: list[dict]
    dry_run_impacts: list[dict]

    def public(self) -> dict:
        return {
            "decision": self.decision,
            "effective_context": self.effective_context,
            "matched_policy_ids": self.matched_policy_ids,
            "applied_effects": self.applied_effects,
            "warnings": self.warnings,
            "violations": self.violations,
            "approvals": self.approvals,
            "obligations": self.obligations,
            "trace": self.trace,
            "dry_run_impacts": self.dry_run_impacts,
        }


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "__dict__"):
        return vars(value)
    return {}


def _policy_value(policy: Any, name: str, default=None):
    if isinstance(policy, Mapping):
        return policy.get(name, default)
    return getattr(policy, name, default)


def get_path(document: Any, path: str, default=MISSING):
    current = document
    for part in str(path or "").split("."):
        if not part:
            continue
        if isinstance(current, Mapping):
            if part not in current:
                return default
            current = current[part]
            continue
        if isinstance(current, (list, tuple)) and part.isdigit():
            index = int(part)
            if index >= len(current):
                return default
            current = current[index]
            continue
        if hasattr(current, part):
            current = getattr(current, part)
            continue
        return default
    return current


def set_path(document: dict, path: str, value: Any, *, only_missing: bool = False) -> bool:
    parts = [part for part in str(path or "").split(".") if part]
    if not parts:
        return False
    current = document
    for part in parts[:-1]:
        next_value = current.get(part)
        if not isinstance(next_value, dict):
            next_value = {}
            current[part] = next_value
        current = next_value
    leaf = parts[-1]
    if only_missing and leaf in current and current[leaf] is not None:
        return False
    current[leaf] = copy.deepcopy(value)
    return True


def _sequence(value: Any) -> list:
    if value is MISSING or value is None:
        return []
    if isinstance(value, (list, tuple, set, frozenset)):
        return list(value)
    return [value]


def _number(value):
    if isinstance(value, bool):
        raise ValueError
    return float(value)


def _dt(value):
    if isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result


def _tags(value) -> set[str]:
    if value is MISSING or value is None:
        return set()
    if isinstance(value, Mapping):
        return {str(key) for key, enabled in value.items() if enabled}
    if isinstance(value, str):
        return {item.strip() for item in re.split(r"[;,]", value) if item.strip()}
    return {str(item) for item in _sequence(value)}


def compare(operator: str, actual: Any, expected: Any, context: Mapping[str, Any]) -> bool:
    operator = OPERATOR_ALIASES.get(str(operator), str(operator))
    if operator not in OPERATORS:
        return False
    if operator == "exists":
        return actual is not MISSING and actual is not None
    if operator == "not_exists":
        return actual is MISSING or actual is None
    if actual is MISSING:
        return False
    if operator == "eq":
        return actual == expected
    if operator == "neq":
        return actual != expected
    if operator == "in":
        return actual in _sequence(expected)
    if operator == "not_in":
        return actual not in _sequence(expected)
    if operator == "contains":
        try:
            return expected in actual
        except TypeError:
            return False
    if operator == "not_contains":
        try:
            return expected not in actual
        except TypeError:
            return True
    if operator == "contains_any":
        values = set(_sequence(actual))
        return bool(values & set(_sequence(expected)))
    if operator == "contains_all":
        values = set(_sequence(actual))
        return set(_sequence(expected)) <= values
    if operator == "starts_with":
        return str(actual).startswith(str(expected))
    if operator == "ends_with":
        return str(actual).endswith(str(expected))
    if operator == "regex":
        subject = str(actual)
        if len(subject) > MAX_REGEX_SUBJECT_LENGTH:
            return False
        try:
            pattern = validate_safe_regex(expected)
        except ValueError:
            return False
        return re.search(pattern, subject) is not None
    if operator in {"gt", "gte", "lt", "lte"}:
        try:
            left, right = _number(actual), _number(expected)
        except (TypeError, ValueError):
            return False
        return {
            "gt": left > right,
            "gte": left >= right,
            "lt": left < right,
            "lte": left <= right,
        }[operator]
    if operator == "between":
        values = _sequence(expected)
        if len(values) != 2:
            return False
        try:
            value, low, high = _number(actual), _number(values[0]), _number(values[1])
        except (TypeError, ValueError):
            return False
        return low <= value <= high
    if operator == "cidr_contains":
        try:
            return ipaddress.ip_address(str(actual)) in ipaddress.ip_network(str(expected), strict=False)
        except ValueError:
            return False
    if operator in {"before", "after"}:
        try:
            left, right = _dt(actual), _dt(expected)
        except (TypeError, ValueError):
            return False
        return left < right if operator == "before" else left > right
    if operator == "is_owner":
        return str(actual) == str(get_path(context, "actor.id", None))
    if operator == "is_member_of":
        memberships = (
            _sequence(get_path(context, "actor.groups", []))
            + _sequence(get_path(context, "actor.roles", []))
            + _sequence(get_path(context, "actor.role_ids", []))
        )
        return str(expected) in {str(item) for item in memberships}
    if operator == "has_tag":
        return str(expected) in _tags(actual)
    if operator.startswith("count_"):
        try:
            count = len(actual)
            expected_count = int(expected)
        except (TypeError, ValueError):
            return False
        return {
            "count_lt": count < expected_count,
            "count_lte": count <= expected_count,
            "count_gt": count > expected_count,
            "count_gte": count >= expected_count,
        }[operator]
    return False


def evaluate_condition(condition: Any, context: Mapping[str, Any]) -> bool:
    if condition in (None, {}, []):
        return True
    if not isinstance(condition, Mapping):
        return False
    if "all" in condition:
        values = condition.get("all")
        return isinstance(values, list) and all(evaluate_condition(item, context) for item in values)
    if "any" in condition:
        values = condition.get("any")
        return isinstance(values, list) and bool(values) and any(evaluate_condition(item, context) for item in values)
    if "not" in condition:
        return not evaluate_condition(condition.get("not"), context)
    field = condition.get("field")
    operator = condition.get("operator", "eq")
    if not field:
        return False
    return compare(operator, get_path(context, str(field)), condition.get("value"), context)


_SCOPE_FIELDS = {
    "user_ids": "actor.id",
    "users": "actor.username",
    "role_ids": "actor.role_ids",
    "roles": "actor.roles",
    "groups": "actor.groups",
    "actions": "request.action",
    "resource_types": "resource.type",
    "organization_ids": "scope.tenant_id",
    "project_ids": "scope.project_id",
    "organizations": "scope.organization",
    "organization_slugs": "scope.organization_slug",
    "projects": "scope.project",
    "project_slugs": "scope.project_slug",
    "scope_keys": "scope.key",
    "apmids": "resource.apmid",
    "environments": "resource.environment",
    "blueprint_ids": "blueprint.id",
    "blueprints": "blueprint.slug",
    "provider_ids": "resource.provider_id",
    "provider_types": "resource.provider_type",
}

SCOPE_DIMENSIONS = frozenset(set(_SCOPE_FIELDS) | {"tags", "conditions"})


def _matches_dimension(actual: Any, allowed: Any) -> bool:
    allowed_values = {str(value) for value in _sequence(allowed)}
    if not allowed_values or "*" in allowed_values:
        return True
    if isinstance(actual, (list, tuple, set, frozenset)):
        return bool({str(value) for value in actual} & allowed_values)
    return str(actual) in allowed_values


def scope_matches(policy: Any, context: Mapping[str, Any], *, identity_only: bool = False) -> bool:
    tenant_id = _policy_value(policy, "tenant_id")
    project_id = _policy_value(policy, "project_id")
    if tenant_id is not None and str(get_path(context, "scope.tenant_id", "")) != str(tenant_id):
        return False
    if project_id is not None and str(get_path(context, "scope.project_id", "")) != str(project_id):
        return False

    scope = _policy_value(policy, "scope", {}) or {}
    identity_dimensions = {
        "user_ids", "users", "role_ids", "roles", "groups", "actions", "resource_types",
        "organization_ids", "project_ids", "organizations", "organization_slugs",
        "projects", "project_slugs",
    }
    for key, path in _SCOPE_FIELDS.items():
        if identity_only and key not in identity_dimensions:
            continue
        allowed = scope.get(key)
        if allowed in (None, [], {}, ""):
            continue
        if not _matches_dimension(get_path(context, path, None), allowed):
            return False

    if not identity_only:
        tags = scope.get("tags")
        if tags not in (None, [], {}, ""):
            actual_tags = _tags(get_path(context, "resource.tags", []))
            if not set(map(str, _sequence(tags))) <= actual_tags:
                return False
        extra = scope.get("conditions")
        if extra and not evaluate_condition(extra, context):
            return False
    return True


def specificity(policy: Any) -> int:
    result = int(_policy_value(policy, "tenant_id") is not None) + int(_policy_value(policy, "project_id") is not None)
    scope = _policy_value(policy, "scope", {}) or {}
    result += sum(1 for key, value in scope.items() if key != "conditions" and value not in (None, [], {}, ""))
    if scope.get("conditions"):
        result += 1
    if _policy_value(policy, "condition", {}):
        result += 1
    return result


def _exception_matches(exception: Any, context: Mapping[str, Any], instant: datetime) -> bool:
    if _policy_value(exception, "status", "") != "approved":
        return False
    valid_from = _policy_value(exception, "valid_from")
    valid_until = _policy_value(exception, "valid_until")
    instant_naive = instant.replace(tzinfo=None) if instant.tzinfo else instant
    if valid_from is not None and instant_naive < valid_from.replace(tzinfo=None):
        return False
    if valid_until is not None and instant_naive >= valid_until.replace(tzinfo=None):
        return False
    return evaluate_condition(_policy_value(exception, "condition", {}) or {}, context)


def _message(effect: Mapping[str, Any], fallback: str) -> str:
    return str(effect.get("message") or effect.get("reason") or fallback)


def _limit_violation(effect: Mapping[str, Any], context: Mapping[str, Any]):
    path = str(effect.get("field") or "")
    actual = get_path(context, path)
    if actual is MISSING:
        return None
    try:
        value = _number(actual)
    except (TypeError, ValueError):
        return {"field": path, "actual": actual, "message": _message(effect, f"{path} is not numeric")}
    try:
        minimum = _number(effect["min"]) if effect.get("min") is not None else None
        maximum = _number(effect["max"]) if effect.get("max") is not None else None
    except (TypeError, ValueError):
        return {"field": path, "actual": actual,
                "message": _message(effect, f"{path} policy limit is not numeric")}
    if minimum is not None and value < minimum:
        return {"field": path, "actual": actual, "min": effect["min"],
                "message": _message(effect, f"{path} is below the allowed minimum")}
    if maximum is not None and value > maximum:
        return {"field": path, "actual": actual, "max": effect["max"],
                "message": _message(effect, f"{path} exceeds the allowed maximum")}
    allowed = effect.get("allowed")
    if allowed is not None and actual not in _sequence(allowed):
        return {"field": path, "actual": actual, "allowed": allowed,
                "message": _message(effect, f"{path} is not an allowed value")}
    return None


def _apply_effect(effect: Mapping[str, Any], context: dict, *, policy_id: str, advisory: bool,
                  warnings: list[str], violations: list[dict], approvals: list[dict],
                  obligations: list[dict], applied: list[dict], locked_fields: set[str]):
    kind = str(effect.get("type") or "")
    item = {"policy_id": policy_id, **copy.deepcopy(dict(effect))}
    if kind == "allow":
        applied.append(item)
        return
    if kind == "deny":
        message = _message(effect, "Denied by policy")
        if advisory:
            warnings.append(message)
        else:
            violations.append({"policy_id": policy_id, "type": "deny", "message": message})
        applied.append(item)
        return
    if kind == "warn":
        warnings.append(_message(effect, "Policy warning"))
        applied.append(item)
        return
    if kind == "require_approval":
        if advisory:
            warnings.append(_message(effect, "Approval would be required"))
        else:
            approvals.append({"policy_id": policy_id, **copy.deepcopy(dict(effect))})
        applied.append(item)
        return
    if kind in {"set_default", "force_value"}:
        field = str(effect.get("field") or "")
        if kind == "force_value" and field in locked_fields:
            item["changed"] = False
            item["ignored"] = "higher_priority_value_already_selected"
        else:
            changed = set_path(context, field, effect.get("value"), only_missing=kind == "set_default")
            item["changed"] = changed
            if kind == "force_value" and changed:
                locked_fields.add(field)
        applied.append(item)
        return
    if kind == "limit_value":
        violation = _limit_violation(effect, context)
        if violation:
            violation["policy_id"] = policy_id
            violation["type"] = "limit"
            if advisory:
                warnings.append(violation["message"])
            else:
                violations.append(violation)
        applied.append(item)
        return
    if kind in {"add_tag", "remove_tag"}:
        field = str(effect.get("field") or "resource.tags")
        tags = list(dict.fromkeys(str(tag) for tag in _sequence(get_path(context, field, []))))
        value = str(effect.get("value") or "")
        if kind == "add_tag" and value and value not in tags:
            tags.append(value)
        elif kind == "remove_tag":
            tags = [tag for tag in tags if tag != value]
        set_path(context, field, tags)
        applied.append(item)
        return
    if kind.startswith("select_"):
        default_fields = {
            "select_provider": "resource.provider_id",
            "select_cluster": "resource.cluster",
            "select_storage": "resource.storage",
            "select_network": "resource.network",
        }
        field = str(effect.get("field") or default_fields[kind])
        if field in locked_fields:
            item["changed"] = False
            item["ignored"] = "higher_priority_value_already_selected"
        else:
            item["changed"] = set_path(context, field, effect.get("value"))
            if item["changed"]:
                locked_fields.add(field)
        applied.append(item)
        return

    if kind == "set_lease":
        field = str(effect.get("field") or "resource.lease_hours")
        value = effect.get("value", effect.get("hours"))
        item["changed"] = set_path(context, field, value)
        applied.append(item)
        return

    if kind.startswith("require_"):
        obligation_fields = {
            "require_mfa": ("actor.mfa_verified", "request.mfa_verified"),
            "require_justification": ("request.justification", "request.reason"),
            "require_change_ticket": ("request.change_ticket", "request.ticket"),
            "require_backup": ("resource.has_backup", "request.backup_verified"),
            "require_snapshot": ("resource.has_snapshot", "request.snapshot_verified"),
            "require_ansible": ("resource.has_ansible", "blueprint.has_ansible", "request.has_ansible"),
        }
        paths = [str(effect.get("field"))] if effect.get("field") else list(obligation_fields.get(kind, ()))
        satisfied = any(bool(get_path(context, path, None)) for path in paths)
        obligations.append({**item, "satisfied": satisfied})
        if not satisfied:
            message = _message(effect, f"{kind} requirement is not satisfied")
            if advisory:
                warnings.append(message)
            else:
                violations.append({
                    "policy_id": policy_id,
                    "type": "obligation",
                    "effect": kind,
                    "message": message,
                })
        applied.append(item)
        return

    # Non-blocking lifecycle side effects such as notify are recorded for
    # downstream consumers but do not grant access by themselves.
    obligations.append(item)
    applied.append(item)


def _is_whitelist_access(policy: Any) -> bool:
    if str(_policy_value(policy, "policy_type", "")) != "access":
        return False
    for effect in _policy_value(policy, "effects", []) or []:
        if isinstance(effect, Mapping) and effect.get("type") == "allow" and effect.get("mode") == "whitelist":
            return True
    return False


def evaluate(policies: Iterable[Any], context: Mapping[str, Any], exceptions: Iterable[Any] = (),
             *, instant: datetime | None = None) -> Evaluation:
    instant = instant or datetime.now(timezone.utc)
    effective = copy.deepcopy(dict(context))
    exceptions_by_policy: dict[str, list[Any]] = {}
    for exception in exceptions:
        exceptions_by_policy.setdefault(str(_policy_value(exception, "policy_id", "")), []).append(exception)

    candidates = []
    whitelist_candidates = []
    trace: list[dict] = []

    for policy in policies:
        policy_id = str(_policy_value(policy, "id", ""))
        status = str(_policy_value(policy, "status", "draft"))
        if status not in {"enforced", "dry_run"}:
            trace.append({"policy_id": policy_id, "matched": False, "reason": f"status:{status}"})
            continue
        if _is_whitelist_access(policy) and scope_matches(policy, effective, identity_only=True):
            whitelist_candidates.append(policy)

        if not scope_matches(policy, effective):
            trace.append({"policy_id": policy_id, "matched": False, "reason": "scope"})
            continue
        if not evaluate_condition(_policy_value(policy, "condition", {}) or {}, effective):
            trace.append({"policy_id": policy_id, "matched": False, "reason": "condition"})
            continue
        matched_exception = next(
            (row for row in exceptions_by_policy.get(policy_id, []) if _exception_matches(row, effective, instant)),
            None,
        )
        if matched_exception is not None:
            trace.append({
                "policy_id": policy_id,
                "matched": False,
                "reason": "exception",
                "exception_id": str(_policy_value(matched_exception, "id", "")),
            })
            continue
        candidates.append(policy)

    candidates.sort(key=lambda row: (
        -int(_policy_value(row, "priority", 0)),
        -specificity(row),
        str(_policy_value(row, "id", "")),
    ))

    matched_ids: list[str] = []
    applied: list[dict] = []
    warnings: list[str] = []
    violations: list[dict] = []
    approvals: list[dict] = []
    obligations: list[dict] = []
    dry_run_impacts: list[dict] = []
    matched_whitelist_ids: set[str] = set()
    locked_fields: set[str] = set()

    for policy in candidates:
        policy_id = str(_policy_value(policy, "id", ""))
        status = str(_policy_value(policy, "status", ""))
        enforcement = str(_policy_value(policy, "enforcement", "hard"))
        matched_ids.append(policy_id)
        if _is_whitelist_access(policy) and status == "enforced":
            matched_whitelist_ids.add(policy_id)

        if status == "dry_run":
            sandbox = copy.deepcopy(effective)
            dry_warnings: list[str] = []
            dry_violations: list[dict] = []
            dry_approvals: list[dict] = []
            dry_obligations: list[dict] = []
            dry_applied: list[dict] = []
            dry_locked_fields = set(locked_fields)
            for effect in _policy_value(policy, "effects", []) or []:
                _apply_effect(
                    effect, sandbox, policy_id=policy_id, advisory=enforcement == "advisory",
                    warnings=dry_warnings, violations=dry_violations, approvals=dry_approvals,
                    obligations=dry_obligations, applied=dry_applied, locked_fields=dry_locked_fields,
                )
            dry_run_impacts.append({
                "policy_id": policy_id,
                "priority": int(_policy_value(policy, "priority", 0)),
                "warnings": dry_warnings,
                "violations": dry_violations,
                "approvals": dry_approvals,
                "obligations": dry_obligations,
                "effects": dry_applied,
                "effective_context": sandbox,
            })
            trace.append({"policy_id": policy_id, "matched": True, "enforced": False, "reason": "dry_run"})
            continue

        advisory = enforcement == "advisory"
        for effect in _policy_value(policy, "effects", []) or []:
            _apply_effect(
                effect, effective, policy_id=policy_id, advisory=advisory,
                warnings=warnings, violations=violations, approvals=approvals,
                obligations=obligations, applied=applied, locked_fields=locked_fields,
            )
        trace.append({
            "policy_id": policy_id,
            "matched": True,
            "enforced": True,
            "priority": int(_policy_value(policy, "priority", 0)),
            "specificity": specificity(policy),
            "enforcement": enforcement,
        })

    enforced_whitelists = [
        row for row in whitelist_candidates
        if str(_policy_value(row, "status", "")) == "enforced"
        and not any(_exception_matches(ex, effective, instant)
                    for ex in exceptions_by_policy.get(str(_policy_value(row, "id", "")), []))
    ]
    if enforced_whitelists and not matched_whitelist_ids:
        violations.append({
            "type": "access_whitelist",
            "message": "No whitelist access policy allows this request",
            "candidate_policy_ids": [str(_policy_value(row, "id", "")) for row in enforced_whitelists],
        })

    decision = "deny" if violations else "approval_required" if approvals else "allow"
    return Evaluation(
        decision=decision,
        effective_context=effective,
        matched_policy_ids=matched_ids,
        applied_effects=applied,
        warnings=warnings,
        violations=violations,
        approvals=approvals,
        obligations=obligations,
        trace=trace,
        dry_run_impacts=dry_run_impacts,
    )


def validate_condition_tree(condition: Any):
    if condition in (None, {}, []):
        return
    if not isinstance(condition, Mapping):
        raise ValueError("Condition must be an object")
    logical = [name for name in ("all", "any", "not") if name in condition]
    if logical:
        if len(logical) != 1 or len(condition) != 1:
            raise ValueError("Logical condition must contain exactly one of all/any/not")
        name = logical[0]
        value = condition[name]
        if name in {"all", "any"}:
            if not isinstance(value, list) or not value:
                raise ValueError(f"{name} requires a non-empty list")
            for child in value:
                validate_condition_tree(child)
        else:
            validate_condition_tree(value)
        return
    field = condition.get("field")
    operator = OPERATOR_ALIASES.get(str(condition.get("operator", "eq")), str(condition.get("operator", "eq")))
    if not isinstance(field, str) or not field.strip():
        raise ValueError("Leaf condition requires field")
    if operator not in OPERATORS:
        raise ValueError(f"Unsupported operator: {operator}")
    if operator == "regex":
        validate_safe_regex(condition.get("value"))


def _validate_approver(spec: Any):
    if spec in (None, "", {}, []):
        return
    if isinstance(spec, str):
        if not spec.strip():
            raise ValueError("Approval role cannot be empty")
        return
    if isinstance(spec, list):
        if not spec:
            raise ValueError("Approval approver list cannot be empty")
        for item in spec:
            _validate_approver(item)
        return
    if not isinstance(spec, Mapping):
        raise ValueError("Approval approver must be a string, object or list")

    kind = str(spec.get("type") or "permission").lower()
    if kind not in _APPROVER_TYPES:
        raise ValueError(f"Unsupported approval approver type: {kind}")
    if kind == "user" and not (
        spec.get("user_id") is not None
        or spec.get("id") is not None
        or str(spec.get("username") or "").strip()
    ):
        raise ValueError("User approver requires user_id, id or username")
    if kind in {"role", "group"} and not (
        spec.get("role_id") is not None
        or spec.get("id") is not None
        or str(spec.get("role") or spec.get("group") or spec.get("name") or "").strip()
    ):
        raise ValueError(f"{kind} approver requires an id or name")
    if kind == "permission" and not str(spec.get("permission") or "").strip():
        raise ValueError("Permission approver requires permission")


def _validate_approval_effect(effect: Mapping[str, Any]):
    if "approver" in effect:
        _validate_approver(effect.get("approver"))
    if "timeout_hours" in effect and effect.get("timeout_hours") is not None:
        try:
            timeout = int(effect["timeout_hours"])
        except (TypeError, ValueError):
            raise ValueError("require_approval timeout_hours must be an integer") from None
        if not 1 <= timeout <= 720:
            raise ValueError("require_approval timeout_hours must be between 1 and 720")

    if "stages" not in effect:
        return
    stages = effect.get("stages")
    if not isinstance(stages, list) or not stages:
        raise ValueError("require_approval stages must be a non-empty list")
    if len(stages) > 20:
        raise ValueError("require_approval supports at most 20 stages")
    for stage in stages:
        if not isinstance(stage, Mapping):
            raise ValueError("Each require_approval stage must be an object")
        if "approver" in stage:
            _validate_approver(stage.get("approver"))
        if "timeout_hours" in stage and stage.get("timeout_hours") is not None:
            try:
                timeout = int(stage["timeout_hours"])
            except (TypeError, ValueError):
                raise ValueError("Approval stage timeout_hours must be an integer") from None
            if not 1 <= timeout <= 720:
                raise ValueError("Approval stage timeout_hours must be between 1 and 720")


def validate_effects(effects: Any):
    if not isinstance(effects, list) or not effects:
        raise ValueError("At least one policy effect is required")
    for effect in effects:
        if not isinstance(effect, Mapping):
            raise ValueError("Policy effect must be an object")
        kind = str(effect.get("type") or "")
        if kind not in EFFECT_TYPES:
            raise ValueError(f"Unsupported effect type: {kind}")
        if kind in {"set_default", "force_value", "limit_value"} and not effect.get("field"):
            raise ValueError(f"{kind} requires field")
        if kind == "require_approval":
            _validate_approval_effect(effect)
        if kind == "limit_value":
            if not any(key in effect for key in ("min", "max", "allowed")):
                raise ValueError("limit_value requires min, max or allowed")
            numeric = {}
            for key in ("min", "max"):
                if effect.get(key) is None:
                    continue
                try:
                    numeric[key] = _number(effect[key])
                except (TypeError, ValueError):
                    raise ValueError(f"limit_value {key} must be numeric") from None
            if "min" in numeric and "max" in numeric and numeric["min"] > numeric["max"]:
                raise ValueError("limit_value min cannot exceed max")
