from app.instance_backup import api
from app.modules.spec import ModuleSpec


MODULES = (ModuleSpec("instance-backups", api.router, order=92),)
