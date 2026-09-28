from app.iam.routes import router
from app.modules.spec import ModuleSpec

MODULES = (ModuleSpec('enterprise-iam', router, order=18),)
