from app.api import infrastructure, proxmox_management
from app.modules.spec import ModuleSpec


MODULES = (
    ModuleSpec('infrastructure', infrastructure.router, order=30),
    ModuleSpec('proxmox-management', proxmox_management.router, order=40),
)
