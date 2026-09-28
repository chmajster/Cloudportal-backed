from app.api import provider_diagnostics
from app.modules.spec import ModuleSpec


MODULES = (
    ModuleSpec('provider-diagnostics', provider_diagnostics.router, order=35),
)
