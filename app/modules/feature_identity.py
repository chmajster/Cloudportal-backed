from app.api import administration
from app.auth import routes as auth
from app.modules.spec import ModuleSpec


MODULES = (
    ModuleSpec('auth', auth.router, order=10),
    ModuleSpec('administration', administration.router, order=20),
)
