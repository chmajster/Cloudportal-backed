from fastapi.routing import APIRoute


_CREATION_RESOURCES = {
    'users', 'roles', 'tokens', 'credentials', 'providers', 'deployments', 'jobs',
    'blueprints', 'hostname-schemes', 'generate', 'execute', 'reset-password', 'destroy', 'power', 'events',
}


def decorate_router(router):
    """Attach shared correlation/idempotency headers to one feature router."""
    for route in router.routes:
        if not isinstance(route, APIRoute):
            continue
        parameters = [{
            'name': 'X-Request-ID',
            'in': 'header',
            'required': False,
            'schema': {'type': 'string', 'format': 'uuid'},
            'description': 'Correlation ID returned in the response and recorded in jobs and audit.',
        }]
        is_creation = (
            'POST' in route.methods
            and route.path.rsplit('/', 1)[-1] in _CREATION_RESOURCES
        )
        is_destroy = 'DELETE' in route.methods and route.path == '/deployments/{id}'
        if (is_creation or is_destroy) and route.path != '/auth/reset-password':
            required = (
                route.path in {
                    '/deployments', '/jobs', '/events', '/blueprints/{id}/execute',
                    '/deployments/{id}/destroy',
                    '/providers/{provider_id}/vms/{node}/{vmid}/power',
                }
                or is_destroy
            )
            parameters.append({
                'name': 'Idempotency-Key',
                'in': 'header',
                'required': required,
                'schema': {'type': 'string', 'format': 'uuid'},
                'description': 'Reuse the same UUID only when retrying the same operation and payload.',
            })
        route.openapi_extra = {
            **(route.openapi_extra or {}),
            'parameters': parameters,
        }
