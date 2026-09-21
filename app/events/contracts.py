from dataclasses import dataclass, field
from typing import Any, Callable


EventHandler = Callable[[Any, Any, Any], None]
HookHandler = Callable[[Any], Any]


@dataclass(frozen=True, slots=True)
class ExtensionSpec:
    """Code-defined extension registered by app/extensions/extension_*.py.

    Extensions are discovered from trusted application code. Runtime API can
    enable/disable them and store configuration, but never uploads or executes
    arbitrary source code.
    """

    name: str
    version: str
    description: str
    event_patterns: tuple[str, ...] = ()
    handler: EventHandler | None = None
    replay_existing_events: bool = False
    hooks: dict[str, HookHandler] = field(default_factory=dict)
