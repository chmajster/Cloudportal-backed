'use strict';

(() => {
  const CLIENT_NAVIGATION_ROUTES = Object.freeze([
    'deployments',
    'my-resources',
    'providers',
    'jobs',
    'blueprints',
    'catalog',
    'credentials',
    'tokens',
    'schedules',
    'webhooks',
    'users',
    'roles',
    'tools',
    'settings',
    'observability',
    'audit',
    'account',
    'tenants',
    'projects',
  ]);

  const GROUPS = Object.freeze([
    { id: 'client', label: '', rank: 0, routes: CLIENT_NAVIGATION_ROUTES },
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
    tenants: '/tenants',
    projects: '/projects',
  });

  const PATH_ROUTES = new Map(
    Object.entries(ROUTE_PATHS).map(([id, path]) => [path, id])
  );

  const NAVIGATION_POSITION = new Map(
    CLIENT_NAVIGATION_ROUTES.map((routeId, index) => [routeId, index])
  );

  function normalizeRouteValue(value) {
    const raw = String(value || '').trim();
    const withoutHash = raw.startsWith('#') ? raw.slice(1) : raw;
    const withoutSurface = withoutHash.split('/page/')[0];
    if (!withoutSurface) return 'deployments';
    if (withoutSurface === '/') return 'deployments';
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
    return normalized.replace(/^\//, '') || 'deployments';
  }

  function routePath(routeOrId) {
    const id = typeof routeOrId === 'string' ? routeOrId : routeOrId?.id;
    return ROUTE_PATHS[id] || ('/' + (id || 'products'));
  }

  function navigationParent(route) {
    return route?.navigationParent || route?.id || '';
  }

  function navigationPosition(route) {
    const parent = navigationParent(route);
    return NAVIGATION_POSITION.has(parent)
      ? NAVIGATION_POSITION.get(parent)
      : Number(route?.order ?? 999);
  }

  function pageEyebrow(route) {
    if (!route) return 'Portal klienta';
    if (route.id === 'dashboard') return 'Stan systemu';
    if (route.id === 'account') return 'Konto';
    return 'Portal klienta';
  }

  window.uiNavigation = Object.freeze({
    groups: GROUPS,
    paths: ROUTE_PATHS,
    routes: CLIENT_NAVIGATION_ROUTES,
  });
  window.uiNavigationVisible = route => NAVIGATION_POSITION.has(route?.id);
  window.uiNavigationGroup = () => '';
  window.uiNavigationRank = () => 0;
  window.uiNavigationOrder = navigationPosition;
  window.uiRoutePath = routePath;
  window.uiResolveView = resolveView;
  window.uiPageEyebrow = pageEyebrow;
})();
