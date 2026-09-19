"""Composable backend feature modules.

New domains should be added as app/modules/feature_<name>.py and expose
MODULES = (ModuleSpec(...),). The registry discovers them automatically.
"""
from app.modules.registry import backend_modules

__all__ = ['backend_modules']
