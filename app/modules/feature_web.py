from app.modules.spec import ModuleSpec
from app.ui.manifest import router


MODULES = (ModuleSpec('web-ui', router, order=5, prefix='/ui'),)
