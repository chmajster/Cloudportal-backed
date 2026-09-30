from app.modules.spec import ModuleSpec
from app.tls_config import api


MODULES = (ModuleSpec('tls-settings', api.router, order=96),)
