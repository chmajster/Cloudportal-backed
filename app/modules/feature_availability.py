from app.availability.routes import router
from app.modules.spec import ModuleSpec


MODULES = (ModuleSpec('availability', router, order=66),)
