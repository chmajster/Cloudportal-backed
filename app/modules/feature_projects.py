from app.modules.spec import ModuleSpec
from app.projects.routes import router
from app.projects.context_routes import router as context_router

router.include_router(context_router)

MODULES = (ModuleSpec('projects', router, order=26),)
