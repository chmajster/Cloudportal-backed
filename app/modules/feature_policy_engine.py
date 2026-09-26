from app.modules.spec import ModuleSpec
from app.policy_engine.routes import router

MODULES = (ModuleSpec("policy-engine", router, order=29),)
