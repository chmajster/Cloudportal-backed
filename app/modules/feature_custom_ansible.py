from app.ansible_custom import api
from app.modules.spec import ModuleSpec


MODULES = (
    ModuleSpec('custom-ansible', api.router, order=35),
)
