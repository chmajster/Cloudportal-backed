from app.modules.spec import ModuleSpec
from app.projects.routes import router

MODULES = (ModuleSpec('projects', router, order=26),)
