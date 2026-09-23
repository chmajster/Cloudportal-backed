from app.modules import backend_modules


def test_backend_feature_modules_are_discovered_and_ordered():
    modules = backend_modules()
    names = [module.name for module in modules]

    assert names == [
        'web-ui',
        'auth',
        'administration',
        'tenancy',
        'projects',
        'resource-scope',
        'quotas',
        'infrastructure',
        'proxmox-management',
        'automation',
        'inventory',
        'day2',
        'ipam',
        'operations',
        'event-enterprise',
        'events',
        'health',
        'instance-backups',
        'updates',
    ]
    assert len(names) == len(set(names))
    assert [module.order for module in modules] == sorted(module.order for module in modules)
    prefixes = {module.name: module.prefix for module in modules}
    assert prefixes['web-ui'] == '/ui'
    assert all(prefix == '/api/v1' for name, prefix in prefixes.items() if name != 'web-ui')
    assert all(module.router.routes for module in modules)


def test_feature_registry_exposes_expected_router_contracts():
    modules = {module.name: module for module in backend_modules()}

    def paths(name):
        return {getattr(route, 'path', '') for route in modules[name].router.routes}

    assert any(path.endswith('/auth/login') for path in paths('auth'))
    assert '/projects' in paths('projects')
    assert '/tenants' in paths('tenancy')
    assert '/tenants/{tenant_id}/members' in paths('tenancy')
    assert '/quotas' in paths('quotas')
    assert '/providers' in paths('infrastructure')
    assert '/blueprints' in paths('automation')
    assert '/ipam/pools' in paths('ipam')
    assert '/webhooks' in paths('operations')
    assert '/event-schemas' in paths('event-enterprise')
    assert '/event-consumers' in paths('event-enterprise')
    assert '/events' in paths('events')
    assert '/health' in paths('health')
    assert '/instance-backups' in paths('instance-backups')
    assert '/updates/status' in paths('updates')
    assert '/manifest.json' in paths('web-ui')
