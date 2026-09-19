'use strict';

(() => {
async function dashboardView() {
  const resources = [
    ['users', 'users.read', '/users'], ['credentials', 'credentials.read', '/credentials'],
    ['deployments', 'deployments.read', '/deployments'], ['jobs', 'jobs.read', '/jobs'],
  ];
  const [health, systemInfo] = await Promise.all([
    api('/health', { auth: false, allow: [503] }),
    allowed('portal.connect') ? api('/info') : Promise.resolve(null),
  ]);
  const counts = {};
  await Promise.all(resources.map(async ([key, permission, path]) => {
    if (!allowed(permission)) return;
    try { counts[key] = (await api(`${path}?limit=200`)).items.length; } catch { counts[key] = '—'; }
  }));
  const metrics = node('div', { class: 'metrics' },
    metric(
      'Stan backendu',
      statusLabel(health.status),
      `${health.checks.workers.online}/${health.checks.workers.expected} workerów`,
      allowed('metrics.read') ? 'observability' : null,
      'health-' + health.status
    ),
    metric('Użytkownicy', counts.users ?? '—', allowed('users.read') ? 'widoczne konta' : 'brak uprawnienia', allowed('users.read') ? 'users' : null),
    metric('Dane dostępowe', counts.credentials ?? '—', allowed('credentials.read') ? 'skonfigurowane sekrety' : 'brak uprawnienia', allowed('credentials.read') ? 'credentials' : null),
    metric('Wdrożenia', counts.deployments ?? '—', 'łącznie', allowed('deployments.read') ? 'deployments' : null),
    metric('Zadania', counts.jobs ?? '—', 'ostatnie 200 widocznych', allowed('jobs.read') ? 'jobs' : null),
  );
  const checkLabels = {
    api: 'API', database: 'Baza danych', queue: 'Kolejka Redis', dispatcher: 'Dispatcher',
    workers: 'Workery', terraform: 'Terraform', ansible: 'Ansible', disk: 'Miejsce na dysku', encryption: 'Szyfrowanie',
  };
  const checks = node('div', { class: 'checks' });
  Object.entries(health.checks).forEach(([name, value]) => {
    const ok = typeof value === 'object' ? value.online >= value.expected : Boolean(value);
    const detail = typeof value === 'object' ? `${value.online}/${value.expected}` : (ok ? 'OK' : 'Problem');
    checks.append(node('div', { class: 'check' },
      node('span', { class: 'check-label' },
        node('span', { class: 'status-dot ' + (ok ? 'ok' : 'bad'), 'aria-hidden': 'true' }),
        node('span', { text: checkLabels[name] || name })),
      node('strong', { text: detail })));
  });
  const recent = allowed('jobs.read') ? (await api('/jobs?limit=8')).items : [];
  const componentPanel = node('section', { class: 'panel' },
    node('div', { class: 'panel-header' }, node('h2', { text: 'Komponenty' }), badge(statusLabel(health.status), statusKind(health.status))),
    checks);
  if (systemInfo) componentPanel.append(node('p', {
    class: 'muted system-version',
    text: `Cloudportal-backed ${systemInfo.version} · API ${systemInfo.api_version} · ${systemInfo.providers.length} platform`,
  }));
  dom.content.replaceChildren(metrics, node('div', { class: 'panels' },
    node('section', { class: 'panel' }, node('div', { class: 'panel-header' }, node('h2', { text: 'Ostatnie zadania' }), button('Wszystkie', () => navigate('jobs'))),
      recent.length ? table([
        { label: 'Operacja', value: row => operationLabel(row.operation) },
        { label: 'Status', value: row => badge(statusLabel(row.status), statusKind(row.status)) },
        { label: 'Utworzono', value: row => formatDate(row.created_at) },
      ], recent) : node('div', { class: 'empty', text: 'Brak dostępnych zadań.' })),
    componentPanel,
  ));
  setApiStatus(health.status === 'ok');
}

function metric(label, value, detail, route = null, kind = '') {
  const content = [
    node('span', { text: label }),
    node('strong', { text: String(value) }),
    node('small', { text: detail }),
  ];
  if (!route) return node('section', { class: 'metric ' + (kind ? 'metric-' + kind : '') }, content);
  return node('button', {
    type: 'button',
    class: 'metric metric-action ' + (kind ? 'metric-' + kind : ''),
    title: 'Otwórz: ' + label,
    onClick: () => navigate(route),
  }, content);
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
