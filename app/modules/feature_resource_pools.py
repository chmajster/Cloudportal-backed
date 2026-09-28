from app.modules.spec import ModuleSpec
from app.resource_pools.routes import router


MODULES = (ModuleSpec('resource-pools', router, order=67),)
