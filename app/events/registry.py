from fnmatch import fnmatchcase
from functools import lru_cache
from importlib import import_module
from pathlib import Path
import pkgutil

from app.events.contracts import ExtensionSpec


def event_matches(patterns, event_type: str) -> bool:
    return any(fnmatchcase(event_type, pattern) for pattern in patterns or ())


@lru_cache(maxsize=1)
def extension_specs() -> tuple[ExtensionSpec, ...]:
    directory = Path(__file__).resolve().parents[1] / 'extensions'
    discovered: list[ExtensionSpec] = []
    for module_info in pkgutil.iter_modules([str(directory)]):
        if not module_info.name.startswith('extension_'):
            continue
        module = import_module(f'app.extensions.{module_info.name}')
        specs = getattr(module, 'EXTENSIONS', ())
        if not isinstance(specs, tuple) or not all(isinstance(spec, ExtensionSpec) for spec in specs):
            raise RuntimeError(f'{module.__name__}.EXTENSIONS must be a tuple[ExtensionSpec, ...]')
        discovered.extend(specs)

    names = [spec.name for spec in discovered]
    if len(names) != len(set(names)):
        raise RuntimeError('Extension names must be unique')
    return tuple(sorted(discovered, key=lambda spec: spec.name))


def extension_by_name(name: str) -> ExtensionSpec | None:
    return next((spec for spec in extension_specs() if spec.name == name), None)


def hook_names() -> tuple[str, ...]:
    return tuple(sorted({name for spec in extension_specs() for name in spec.hooks}))


def run_hook(name: str, value):
    """Run enabled-code hook implementations in deterministic extension order.

    Hook enablement is intentionally evaluated by callers that have a DB session.
    This pure helper is useful for hook composition and tests.
    """
    current = value
    for spec in extension_specs():
        handler = spec.hooks.get(name)
        if handler is None:
            continue
        result = handler(current)
        if result is not None:
            current = result
    return current
