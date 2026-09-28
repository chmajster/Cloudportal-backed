"""Non-executable Event Broker payload templates."""
from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping
from typing import Any

from app.policy_engine.engine import MISSING, get_path


TOKEN = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)*)\s*}}")
EXACT_TOKEN = re.compile(r"^{{\s*([A-Za-z_][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)*)\s*}}$")
MAX_TEMPLATE_DEPTH = 24
MAX_RENDERED_BYTES = 256 * 1024


def _lookup(context: Mapping[str, Any], path: str):
    value = get_path(context, path, MISSING)
    if value is MISSING:
        raise ValueError(f"Template field does not exist: {path}")
    return value


def _render_string(value: str, context: Mapping[str, Any]):
    exact = EXACT_TOKEN.fullmatch(value)
    if exact:
        return copy.deepcopy(_lookup(context, exact.group(1)))

    def replace(match):
        resolved = _lookup(context, match.group(1))
        if isinstance(resolved, (dict, list, tuple, set)):
            raise ValueError(
                f"Template field {match.group(1)} is structured; use it as the complete value"
            )
        if resolved is None:
            return ""
        if isinstance(resolved, bool):
            return "true" if resolved else "false"
        return str(resolved)

    return TOKEN.sub(replace, value)


def render_template(value: Any, context: Mapping[str, Any], *, _depth: int = 0):
    if _depth > MAX_TEMPLATE_DEPTH:
        raise ValueError("Template nesting is too deep")
    if isinstance(value, str):
        rendered = _render_string(value, context)
    elif isinstance(value, list):
        rendered = [render_template(item, context, _depth=_depth + 1) for item in value]
    elif isinstance(value, dict):
        rendered = {
            str(key): render_template(item, context, _depth=_depth + 1)
            for key, item in value.items()
        }
    elif value is None or isinstance(value, (bool, int, float)):
        rendered = value
    else:
        raise ValueError("Template contains an unsupported value type")

    if _depth == 0:
        encoded = json.dumps(rendered, separators=(",", ":"), default=str).encode()
        if len(encoded) > MAX_RENDERED_BYTES:
            raise ValueError("Rendered template exceeds 256 KiB")
    return rendered
