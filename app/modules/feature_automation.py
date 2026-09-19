from app.api import automation
from app.modules.spec import ModuleSpec


MODULES = (ModuleSpec('automation', automation.router, order=50),)
