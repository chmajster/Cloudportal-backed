from app.api import day2
from app.modules.spec import ModuleSpec


MODULES = (
    ModuleSpec('day2', day2.router, order=65),
)
