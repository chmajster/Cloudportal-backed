from app.api import operations
from app.modules.spec import ModuleSpec


MODULES = (ModuleSpec('operations', operations.router, order=80),)
