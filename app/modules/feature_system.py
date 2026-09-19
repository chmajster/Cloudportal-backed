from app.api import health
from app.modules.spec import ModuleSpec


MODULES = (ModuleSpec('health', health.router, order=90),)
