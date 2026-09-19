from functools import lru_cache
from importlib import import_module
from pathlib import Path
import pkgutil

from app.modules.spec import ModuleSpec


@lru_cache(maxsize=1)
def backend_modules() -> tuple[ModuleSpec, ...]:
    """Discover feature modules without a central import list.

    Only files named feature_*.py participate. This lets independent branches add
    features without touching app/main.py or a shared registry file.
    """
    directory = Path(__file__).parent
    discovered: list[ModuleSpec] = []
    for module_info in pkgutil.iter_modules([str(directory)]):
        if not module_info.name.startswith('feature_'):
            continue
        module = import_module(f'app.modules.{module_info.name}')
        specs = getattr(module, 'MODULES', ())
        if not isinstance(specs, tuple):
            raise RuntimeError(f'{module.__name__}.MODULES must be a tuple')
        if not all(isinstance(spec, ModuleSpec) for spec in specs):
            raise RuntimeError(f'{module.__name__}.MODULES contains an invalid module spec')
        discovered.extend(specs)

    names = [spec.name for spec in discovered]
    if len(names) != len(set(names)):
        raise RuntimeError('Backend module names must be unique')
    return tuple(sorted(discovered, key=lambda spec: (spec.order, spec.name)))
