'use strict';

(() => {
  const GROUPS = Object.freeze([
    { id: 'resources', label: 'ZASOBY', rank: 0, routes: Object.freeze([
      'deployments',
      'my-resources',
      'jobs',
    ]) },
    { id: 'automation', label: 'AUTOMATYZACJA', rank: 1, routes: Object.freeze([
      'providers',
      'blueprints',
      'catalog',
      'schedules',
      'webhooks',
    ]) },
    { id: 'access', label: 'DOSTĘP I BEZPIECZEŃSTWO', rank: 2, routes: Object.freeze([
      'credentials',
      'tokens',
      'users',
      'roles',
    ]) },
    { id: 'organization', label: 'ORGANIZACJA', rank: 3, routes: Object.freeze([
      'tenants',
      'projects',
    ]) },
    { id: 'operations', label: 'OPERACJE', rank: 4, routes: Object.freeze([
      'observability',
      'audit',
    ]) },
    { id: 'administration', label: 'ADMINISTRACJA', rank: 5, routes: Object.freeze([
      'tools',
      'settings',
    ]) },
    { id: 'account', label: 'KONTO', rank: 6, routes: Object.freeze([
      'account',
    ]) },
  ]);

  const CLIENT_NAVIGATION_ROUTES = Object.freeze(
    GROUPS.flatMap(group => group.routes)
  );

  const NAVIGATION_GROUP = new Map(
    GROUPS.flatMap(group => group.routes.map(routeId => [routeId, group]))
  );

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
  const JOB_LOG_ROUTE = /^\/jobs\/([^/]+)$/;

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
    if (JOB_LOG_ROUTE.test(normalized)) return 'job-log';
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

  function routePathForRequest(value, resolvedId) {
    const normalized = normalizeRouteValue(value);
    if (resolvedId === 'job-log') {
      if (JOB_LOG_ROUTE.test(normalized)) return normalized;
      const current = normalizeRouteValue(location.hash.slice(1));
      if (JOB_LOG_ROUTE.test(current)) return current;
    }
    return routePath(resolvedId);
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

  function navigationGroup(route) {
    return NAVIGATION_GROUP.get(navigationParent(route))?.label || '';
  }

  function navigationRank(route) {
    return NAVIGATION_GROUP.get(navigationParent(route))?.rank ?? GROUPS.length;
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
  window.uiNavigationGroup = navigationGroup;
  window.uiNavigationRank = navigationRank;
  window.uiNavigationOrder = navigationPosition;
  window.uiRoutePath = routePath;
  window.uiRoutePathForRequest = routePathForRequest;
  window.uiResolveView = resolveView;
  window.uiPageEyebrow = pageEyebrow;
})();
