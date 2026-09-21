from app.modules.spec import ModuleSpec
from app.tenancy.routes import router


MODULES = (ModuleSpec('tenancy', router, order=25),)
