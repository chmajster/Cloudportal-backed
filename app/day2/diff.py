import re
from collections.abc import Mapping


_SECRET_KEY = re.compile(
    r'(?:password|passwd|secret|token|authorization|private[_-]?key|client[_-]?secret)',
    re.IGNORECASE,
)


def redact(value, key: str = ''):
    if key and _SECRET_KEY.search(str(key)):
        return '[REDACTED]'
    if isinstance(value, Mapping):
        return {str(k): redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return [redact(item) for item in value]
    return value


def configuration_diff(current: Mapping | None, requested: Mapping | None) -> dict:
    before = dict(current or {})
    after = dict(requested or {})
    changes = {}
    for key in sorted(set(before) | set(after)):
        old = before.get(key)
        new = after.get(key)
        if old != new:
            changes[str(key)] = {
                'before': redact(old, str(key)),
                'after': redact(new, str(key)),
            }
    return changes
