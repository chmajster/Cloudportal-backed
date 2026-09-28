from app.modules.spec import ModuleSpec
from app.onboarding import api


MODULES = (
    ModuleSpec('onboarding', api.router, order=65),
)
