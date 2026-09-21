from app.modules.spec import ModuleSpec
from app.projects.routes import router
MODULES = (ModuleSpec(name='projects', router=router, order=26),)
