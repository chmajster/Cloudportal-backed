from app.api.proxmox_admin import router
from app.modules.spec import ModuleSpec


module = ModuleSpec(
    name='proxmox-admin',
    router=router,
    order=86,
)
