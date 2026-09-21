from app.modules.spec import ModuleSpec
from app.resource_scope.routes import router

MODULES = (ModuleSpec('resource-scope', router, order=27),)
