from app.modules.spec import ModuleSpec
from app.quotas.routes import router

MODULES = (ModuleSpec('quotas', router, order=28),)
