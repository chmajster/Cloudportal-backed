from app.access.routes import router
from app.modules.spec import ModuleSpec

MODULES = (ModuleSpec('access', router, order=19),)
