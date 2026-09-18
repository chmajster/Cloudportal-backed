from app.modules import backend_modules


def test_backend_feature_modules_are_discovered_and_ordered():
    modules = backend_modules()
    names = [module.name for module in modules]

    assert names == [
        'web-ui',
        'auth',
        'administration',
        'infrastructure',
        'proxmox-management',
        'automation',
        'inventory',
        'ipam',
        'operations',
        'health',
    ]
    assert len(names) == len(set(names))
    assert [module.order for module in modules] == sorted(module.order for module in modules)
    prefixes = {module.name: module.prefix for module in modules}
    assert prefixes['web-ui'] == '/ui'
    assert all(prefix == '/api/v1' for name, prefix in prefixes.items() if name != 'web-ui')
    assert all(module.router.routes for module in modules)


def test_feature_registry_keeps_main_free_of_domain_router_imports():
    import app.main as main

    paths = {getattr(route, 'path', '') for route in main.app.routes}
    assert '/api/v1/auth/login' in paths
    assert '/api/v1/providers' in paths
    assert '/api/v1/blueprints' in paths
    assert '/api/v1/ipam/pools' in paths
    assert '/api/v1/webhooks' in paths
    assert '/api/v1/health' in paths
    assert '/ui/manifest.json' in paths
