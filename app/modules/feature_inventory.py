from app.api import inventory, ipam
from app.modules.spec import ModuleSpec


MODULES = (
    ModuleSpec('inventory', inventory.router, order=60),
    ModuleSpec('ipam', ipam.router, order=70),
)
