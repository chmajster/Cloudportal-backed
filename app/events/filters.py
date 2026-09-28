"""Bounded filter AST for Event Broker subscriptions.

The broker intentionally reuses Policy Engine comparison semantics so conditions
have one implementation for numeric operators, tags and conservative regexes.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.policy_engine.engine import evaluate_condition, validate_condition_tree


MAX_FILTER_NODES = 128
MAX_FILTER_DEPTH = 12
MAX_FILTER_FIELD_LENGTH = 160

ALLOWED_ROOTS = frozenset({
    "event", "actor", "scope", "resource", "correlation", "data",
    "request", "blueprint", "job", "workflow",
})


def _walk(value: Any, depth: int = 0) -> int:
    if depth > MAX_FILTER_DEPTH:
        raise ValueError("Filter nesting exceeds the maximum depth")
    if not isinstance(value, Mapping):
        raise ValueError("Filter condition must be an object")
    logical = [name for name in ("all", "any", "not") if name in value]
    if logical:
        name = logical[0]
        child = value[name]
        if name in {"all", "any"}:
            count = 1
            for item in child:
                count += _walk(item, depth + 1)
                if count > MAX_FILTER_NODES:
                    raise ValueError("Filter contains too many conditions")
            return count
        return 1 + _walk(child, depth + 1)

    field = str(value.get("field") or "")
    if len(field) > MAX_FILTER_FIELD_LENGTH:
        raise ValueError("Filter field path is too long")
    root = field.split(".", 1)[0]
    if root not in ALLOWED_ROOTS:
        raise ValueError("Filter field root is not available to Event Broker")
    return 1


def validate_filter(condition: dict | None) -> dict:
    condition = condition or {}
    if not condition:
        return {}
    validate_condition_tree(condition)
    _walk(condition)
    return condition


def matches_filter(condition: dict | None, envelope: Mapping[str, Any]) -> bool:
    if not condition:
        return True
    return bool(evaluate_condition(condition, envelope))
