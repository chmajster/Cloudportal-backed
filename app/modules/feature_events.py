from app.api import events
from app.modules.spec import ModuleSpec


MODULES = (ModuleSpec('events', events.router, order=85),)
