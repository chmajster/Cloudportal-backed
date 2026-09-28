from app.api import events\nfrom app.events.routes import router as event_broker_router\nfrom app.modules.spec import ModuleSpec


MODULES = (\n    ModuleSpec('events', events.router, order=85),\n    ModuleSpec('event-broker', event_broker_router, order=86),\n)\n