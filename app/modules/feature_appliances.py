from app.appliances import api
from app.modules.spec import ModuleSpec


MODULES = (
    ModuleSpec('appliances', api.router, order=55),
)
