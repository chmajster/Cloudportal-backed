from app.api import updates
from app.modules.spec import ModuleSpec


MODULES = (ModuleSpec('updates', updates.router, order=95),)
