'use strict';

(() => {
  const GROUPS = Object.freeze([
    { id: 'start', label: 'Start', rank: 0, routes: ['dashboard'] },
    { id: 'infrastructure', label: 'Infrastruktura', rank: 10, routes: ['my-resources', 'inventory', 'providers', 'ipam'] },
    { id: 'automation', label: 'Automatyzacja', rank: 20, routes: ['deployments', 'blueprints', 'catalog', 'jobs', 'schedules', 'webhooks'] },
    { id: 'access', label: 'Dostęp', rank: 30, routes: ['users', 'roles', 'credentials', 'tokens'] },
    { id: 'administration', label: 'Administracja', rank: 40, routes: ['tools', 'settings', 'observability', 'audit', 'account'] },
  ]);

  const ROUTE_PATHS = Object.freeze({
    dashboard: '/dashboard',
    'my-resources': '/resources',
    inventory: '/inventory',
    providers: '/providers',
    ipam: '/ipam',
    deployments: '/products',
    blueprints: '/blueprints',
    catalog: '/catalog',
    jobs: '/jobs',
    schedules: '/operations/schedules',
    webhooks: '/operations/webhooks',
    users: '/access/users',
    roles: '/access/roles',
    credentials: '/access/credentials',
    tokens: '/access/tokens',
    tools: '/admin/tools',
    environments: '/admin/tools/environments',
    apmid: '/admin/tools/apmid',
    hostnames: '/admin/tools/hostnames',
    'hostname-defaults': '/admin/tools/hostname-defaults',
    'ansible-host-entry': '/admin/tools/ansible-host-entry',
    settings: '/admin/settings',
    updates: '/admin/updates',
    observability: '/admin/monitoring',
    audit: '/admin/audit',
    account: '/account',
  });

  const PATH_ROUTES = new Map(
    Object.entries(ROUTE_PATHS).map(([id, path]) => [path, id])
  );

  const GROUP_BY_ROUTE = new Map();
  GROUPS.forEach(group => group.routes.forEach(routeId => GROUP_BY_ROUTE.set(routeId, group)));

  function normalizeRouteValue(value) {
    const raw = String(value || '').trim();
    const withoutHash = raw.startsWith('#') ? raw.slice(1) : raw;
    const withoutSurface = withoutHash.split('/page/')[0];
    if (!withoutSurface) return 'dashboard';
    if (withoutSurface === '/') return 'dashboard';
    return withoutSurface.length > 1 && withoutSurface.endsWith('/')
      ? withoutSurface.slice(0, -1)
      : withoutSurface;
  }

  function resolveView(value) {
    const normalized = normalizeRouteValue(value);
    if (PATH_ROUTES.has(normalized)) return PATH_ROUTES.get(normalized);
    if (ROUTE_PATHS[normalized]) return normalized;
    if (normalized.startsWith('/')) {
      const first = '/' + normalized.split('/').filter(Boolean)[0];
      if (PATH_ROUTES.has(first)) return PATH_ROUTES.get(first);
    }
    return normalized.replace(/^\//, '') || 'dashboard';
  }

  function routePath(routeOrId) {
    const id = typeof routeOrId === 'string' ? routeOrId : routeOrId?.id;
    return ROUTE_PATHS[id] || ('/' + (id || 'dashboard'));
  }

  function groupForRoute(route) {
    if (!route) return null;
    const parent = route.navigationParent || route.id;
    return GROUP_BY_ROUTE.get(parent) || GROUP_BY_ROUTE.get(route.id) || GROUPS.at(-1);
  }

  function pageEyebrow(route) {
    if (!route) return 'Cloudportal';
    if (route.id === 'dashboard') return 'Stan systemu';
    if (route.id === 'account') return 'Konto';
    return groupForRoute(route)?.label || 'Cloudportal';
  }

  window.uiNavigation = Object.freeze({ groups: GROUPS, paths: ROUTE_PATHS });
  window.uiNavigationGroup = route => groupForRoute(route)?.label || '';
  window.uiNavigationRank = route => groupForRoute(route)?.rank ?? 999;
  window.uiRoutePath = routePath;
  window.uiResolveView = resolveView;
  window.uiPageEyebrow = pageEyebrow;
})();
