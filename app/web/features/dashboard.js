'use strict';

(() => {
function dashboardMetric(iconName, label, value, detail, route = null) {
  const content = [
    node('div', { class: 'dashboard-metric-icon', 'aria-hidden': 'true' }, appIcon(iconName)),
    node('div', { class: 'dashboard-metric-copy' },
      node('span', { text: label }),
      node('strong', { text: String(value ?? '—') }),
      node('small', { text: detail })),
  ];
  if (!route) return node('article', { class: 'dashboard-metric-card' }, content);
  return node('button', {
    class: 'dashboard-metric-card dashboard-metric-action',
    type: 'button',
    title: 'Otwórz: ' + label,
    onClick: () => navigate(route),
  }, content);
}

function metric(label, value, detail) {
  return node('section', { class: 'metric' },
    node('span', { text: label }),
    node('strong', { text: String(value) }),
    node('small', { text: detail }));
}

function dashboardAction(iconName, label, route, command = null) {
  return node('button', {
    class: 'dashboard-quick-action',
    type: 'button',
    onClick: async () => {
      await navigate(route);
      if (command && hasCommand(command)) await runCommand(command);
    },
  },
    node('span', { class: 'dashboard-action-icon', 'aria-hidden': 'true' }, appIcon(iconName)),
    node('span', { class: 'dashboard-action-label', text: label }),
    node('span', { class: 'dashboard-action-chevron', 'aria-hidden': 'true' }, appIcon('chevron-right')));
}

function deploymentStatusIndicator(status) {
  const normalized = String(status || '').toLowerCase();
  const kind = normalized === 'failed' || normalized === 'error'
    ? 'danger'
    : ['running', 'pending', 'planning', 'applying', 'destroying'].includes(normalized)
      ? 'warning'
      : ['ready', 'success', 'completed', 'active', 'applied'].includes(normalized)
        ? 'ok'
        : '';
  return node('span', { class: 'dashboard-status' },
    node('span', { class: `dashboard-status-dot ${kind}` }),
    node('span', { text: statusLabel(status) }));
}

async function dashboardView() {
  const user = state.identity.user || {};
  const greetingName = String(user.first_name || user.username || 'Użytkowniku').trim();
  const requests = {
    users: allowed('users.read') ? api('/users?limit=200') : Promise.resolve(null),
    providers: allowed('providers.read') ? api('/providers?limit=200') : Promise.resolve(null),
    deployments: allowed('deployments.read') ? api('/deployments?limit=200') : Promise.resolve(null),
    jobs: allowed('jobs.read') ? api('/jobs?limit=200') : Promise.resolve(null),
    health: api('/health', { auth: false, allow: [503] }),
  };
  const settled = await Promise.allSettled(Object.values(requests));
  const values = Object.keys(requests).reduce((acc, key, index) => {
    acc[key] = settled[index].status === 'fulfilled' ? settled[index].value : null;
    return acc;
  }, {});

  const providers = values.providers?.items || [];
  const deployments = values.deployments?.items || [];
  const providerById = new Map(providers.map(provider => [Number(provider.id), provider]));
  const counts = {
    users: values.users?.items?.length,
    providers: values.providers?.items?.length,
    deployments: values.deployments?.items?.length,
    jobs: values.jobs?.items?.length,
  };

  const hero = node('section', { class: 'dashboard-hero' },
    node('div', {},
      node('span', { class: 'dashboard-kicker', text: 'Cloudportal' }),
      node('h1', { text: `Witaj, ${greetingName}!` }),
      node('p', { text: 'Oto przegląd Twojego środowiska w Cloudportal.' })));

  const metricCards = node('section', { class: 'dashboard-metrics', 'aria-label': 'Metryki środowiska' },
    dashboardMetric('users', 'Użytkownicy', allowed('users.read') ? (counts.users ?? '—') : '—', allowed('users.read') ? 'konta widoczne dla Ciebie' : 'brak uprawnienia', allowed('users.read') ? 'users' : null),
    dashboardMetric('server', 'Platformy', allowed('providers.read') ? (counts.providers ?? '—') : '—', allowed('providers.read') ? 'skonfigurowane połączenia' : 'brak uprawnienia', allowed('providers.read') ? 'providers' : null),
    dashboardMetric('rocket', 'Wdrożenia', allowed('deployments.read') ? (counts.deployments ?? '—') : '—', allowed('deployments.read') ? 'wszystkie wdrożenia' : 'brak uprawnienia', allowed('deployments.read') ? 'deployments' : null),
    dashboardMetric('list-check', 'Zadania', allowed('jobs.read') ? (counts.jobs ?? '—') : '—', allowed('jobs.read') ? 'ostatnie 200 rekordów' : 'brak uprawnienia', allowed('jobs.read') ? 'jobs' : null));

  const recentDeployments = deployments
    .slice()
    .sort((a, b) => new Date(b.updated_at || b.created_at || 0) - new Date(a.updated_at || a.created_at || 0))
    .slice(0, 5);

  const recentBody = node('div', { class: 'dashboard-deployments' });
  if (allowed('deployments.read') && recentDeployments.length) {
    recentDeployments.forEach(item => {
      const provider = providerById.get(Number(item.provider_id));
      const providerType = provider?.type || item.provider || '';
      recentBody.append(node('button', {
        class: 'dashboard-deployment-row',
        type: 'button',
        onClick: () => navigate('deployments'),
        title: 'Otwórz widok wdrożeń',
      },
        node('span', { class: 'dashboard-deployment-name' },
          node('strong', { text: item.name || short(item.id, 18) }),
          node('small', { class: 'muted', text: formatDate(item.updated_at || item.created_at) })),
        node('span', { class: 'dashboard-provider-badge' },
          node('span', { class: 'dashboard-provider-icon', 'aria-hidden': 'true' }, appIcon('server')),
          node('span', { text: CREDENTIAL_TYPE_CONFIG[providerType]?.label || provider?.name || providerType || `#${item.provider_id}` })),
        deploymentStatusIndicator(item.status)));
    });
  } else {
    recentBody.append(node('div', { class: 'dashboard-empty', text: allowed('deployments.read') ? 'Brak wdrożeń do wyświetlenia.' : 'Brak uprawnienia deployments.read.' }));
  }

  const actions = [];
  if (allowed('users.create')) actions.push(dashboardAction('users', 'Dodaj użytkownika', 'users', 'users.create'));
  if (allowed('tokens.create')) actions.push(dashboardAction('key', 'Utwórz token API', 'tokens', 'tokens.create'));
  if (allowed('providers.create') && allowed('credentials.read')) {
    actions.push(dashboardAction('server', 'Dodaj platformę', 'providers', 'providers.create'));
  }
  if (allowed('blueprints.create') && allowed('providers.read') && allowed('credentials.read') && allowed('terraform.read')) {
    actions.push(dashboardAction('workflow', 'Utwórz Blueprint', 'blueprints', 'blueprints.create'));
  }
  if (allowed('deployments.create') && allowed('providers.read') && allowed('credentials.read') && allowed('terraform.read')) {
    actions.push(dashboardAction('rocket', 'Nowe wdrożenie', 'deployments', 'deployments.create'));
  }

  const deploymentPanel = node('section', { class: 'panel dashboard-panel' },
    node('div', { class: 'dashboard-panel-header' },
      node('div', {}, node('span', { class: 'dashboard-panel-kicker', text: 'Aktywność' }), node('h2', { text: 'Ostatnie wdrożenia' })),
      allowed('deployments.read') ? button('Wszystkie', () => navigate('deployments')) : null),
    node('div', { class: 'dashboard-deployment-head' },
      node('span', { text: 'Nazwa' }), node('span', { text: 'Platforma' }), node('span', { text: 'Status' })),
    recentBody);

  const quickPanel = node('section', { class: 'panel dashboard-panel dashboard-actions-panel' },
    node('div', { class: 'dashboard-panel-header' },
      node('div', {}, node('span', { class: 'dashboard-panel-kicker', text: 'Skróty' }), node('h2', { text: 'Szybkie akcje' }))),
    actions.length
      ? node('div', { class: 'dashboard-quick-actions' }, actions)
      : node('div', { class: 'dashboard-empty', text: 'Brak dostępnych akcji dla bieżących uprawnień.' }));

  dom.content.replaceChildren(hero, metricCards, node('section', { class: 'dashboard-grid' }, deploymentPanel, quickPanel));
  if (values.health) setApiStatus(values.health.status === 'ok');
}

async function auditView(requestId = '') {
  const result = await api(`/audit?limit=200${requestId ? `&request_id=${encodeURIComponent(requestId)}` : ''}`);
  const search = node('form', { class: 'action-group', onSubmit: event => { event.preventDefault(); auditView(event.currentTarget.elements.request_id.value.trim()); } }, field('Request ID', 'request_id', { value: requestId, placeholder: 'UUID korelacji' }), node('button', { class: 'button primary', type: 'submit' }, 'Filtruj'));
  dom.content.replaceChildren(heading('Niezmienna historia operacji bezpieczeństwa i infrastruktury.', [search]),
    table([
      { label: 'Czas', value: item => formatDate(item.timestamp) }, { label: 'Akcja', value: item => node('strong', { text: auditActionLabel(item.action) }) },
      { label: 'Zasób', value: item => `${AUDIT_RESOURCE_LABELS[item.resource] || item.resource || '—'} ${item.resource_id || ''}` }, { label: 'Źródło', value: item => item.source }, { label: 'Wynik', value: item => badge(item.result, item.result === 'success' ? 'ok' : 'danger') },
      { label: 'Użytkownik', value: item => item.user_id ?? '—' }, { label: 'Request ID', class: 'mono', value: item => short(item.request_id, 18) },
    ], result.items));
}

async function observabilityView() {
  const [health, alerts, metricsText] = await Promise.all([
    api('/health', { auth: false, allow: [503] }),
    api('/alerts'),
    apiText('/metrics'),
  ]);
  const items = alerts.items || [];
  dom.content.replaceChildren(
    heading('Stan platformy, alerty oraz surowe metryki w formacie Prometheus.'),
    node('div', { class: 'metrics' },
      metric('Backend', statusLabel(health.status), 'health'),
      metric('Workery', `${health.checks.workers.online}/${health.checks.workers.expected}`, 'online/expected'),
      metric('Alerty', items.length, 'aktywne'),
      metric('Dispatcher', health.checks.dispatcher ? 'OK' : 'Niedostępny', 'heartbeat')),
    node('section', { class: 'panel' },
      node('div', { class: 'panel-header' }, node('h2', { text: 'Alerty' })),
      table([
        { label: 'Ważność', value: item => badge(statusLabel(item.severity), item.severity === 'critical' ? 'danger' : 'warning') },
        { label: 'Kod', class: 'mono', value: item => item.code },
        { label: 'Zasób', class: 'mono', value: item => item.resource_id || '—' },
        { label: 'Opis', value: item => item.message },
      ], items)),
    node('section', { class: 'panel' },
      node('div', { class: 'panel-header' }, node('h2', { text: 'Prometheus exposition' }), badge('/api/v1/metrics', 'info')),
      node('pre', { class: 'log-output mono', text: metricsText }))
  );
}

registerView({ id: 'dashboard', label: 'Dashboard', icon: '◫', order: 0 }, dashboardView);
registerView({ id: 'observability', label: 'Monitoring', icon: 'O', permission: 'metrics.read', order: 150 }, observabilityView);
registerView({ id: 'audit', label: 'Audyt', icon: 'A', permission: 'audit.read', order: 160 }, auditView);
})();
