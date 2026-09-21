from app.api import event_enterprise
from app.modules.spec import ModuleSpec


MODULES = (ModuleSpec('event-enterprise', event_enterprise.router, order=84),)
