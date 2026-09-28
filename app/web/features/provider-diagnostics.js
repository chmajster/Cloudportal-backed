'use strict';

(() => {
const STATUS = {
  pass: ['OK', 'ok'],
  warn: ['Ostrzeżenie', 'warning'],
  fail: ['Błąd', 'danger'],
  skipped: ['Pominięto', 'info'],
};

function providerDiagnosticsCard() {
  return node('article', { class: 'panel tool-card tool-card-featured' },
    node('div', { class: 'tool-card-head' },
      node('div', { class: 'tool-icon', 'aria-hidden': 'true' }, appIcon('network')),
      node('div', { class: 'tool-title' },
        node('span', { class: 'tool-category', text: 'Infrastruktura' }),
        node('h2', { text: 'Diagnostyka połączeń' }),
        node('p', { class: 'muted', text: 'Sprawdź DNS, ping, port TCP, TLS, HTTP, autoryzację API oraz pobieranie zasobów z platform infrastruktury.' })),
      badge('Warstwowa', 'ok')),
    node('div', { class: 'tool-meta-grid' },
      toolMeta('Sieć', 'DNS / ICMP / TCP'),
      toolMeta('Transport', 'TLS / HTTP'),
      toolMeta('API', 'logowanie / token'),
      toolMeta('Zasoby', 'hosty / VM / storage / sieci')),
    node('div', { class: 'tool-card-footer' },
      node('span', { class: 'tool-health' },
        node('span', { class: 'status-dot ok' }),
        'Diagnostyka nie wykonuje zmian na platformach'),
      button('Otwórz diagnostykę', () => navigate('provider-diagnostics'), 'primary')));
}

function statusBadge(status) {
  const current = STATUS[status] || ['Nieznany', 'info'];
  return badge(current[0], current[1]);
}

function summaryBadge(result) {
  if (!result) return badge('Nie sprawdzono', 'info');
  if (result.status === 'ok') return badge('Połączenie OK', 'ok');
  if (result.status === 'degraded') return badge('Częściowo działa', 'warning');
  return badge('Połączenie nie działa', 'danger');
}

function checkMeta(check) {
  const values = [];
  if (check.latency_ms !== null && check.latency_ms !== undefined) values.push(String(check.latency_ms) + ' ms');
  if (check.details?.status_code) values.push('HTTP ' + check.details.status_code);
  if (check.details?.count !== undefined) values.push(String(check.details.count) + ' elementów');
  if (check.details?.protocol) values.push(check.details.protocol);
  return values.join(' · ');
}

function checkDetails(check) {
  const items = [];
  const details = check.details || {};
  if (Array.isArray(details.addresses) && details.addresses.length) {
    items.push(node('div', { class: 'provider-diagnostic-detail mono', text: 'Adresy: ' + details.addresses.join(', ') }));
  }
  if (Array.isArray(details.sample) && details.sample.length) {
    items.push(node('div', { class: 'provider-diagnostic-detail', text: 'Przykłady: ' + details.sample.join(', ') }));
  }
  if (details.url) {
    items.push(node('div', { class: 'provider-diagnostic-detail mono', text: details.url }));
  }
  return items;
}

function diagnosticCheckRow(check) {
  const meta = checkMeta(check);
  return node('div', { class: 'provider-diagnostic-check ' + check.status },
    node('div', { class: 'provider-diagnostic-check-state' }, statusBadge(check.status)),
    node('div', { class: 'provider-diagnostic-check-copy' },
      node('div', { class: 'provider-diagnostic-check-title' },
        node('strong', { text: check.label }),
        meta ? node('span', { class: 'muted', text: meta }) : null),
      node('p', { text: check.message }),
      ...checkDetails(check)));
}

function providerTypeLabel(type) {
  return CREDENTIAL_TYPE_CONFIG[type]?.label || type || 'Platforma';
}

function providerResultPanel(provider, result, running, runProvider) {
  const checks = result?.checks || [];
  const summary = result?.summary || {};
  const actions = [];
  if (running) {
    actions.push(button('Diagnostyka trwa…', () => {}, 'primary', true));
  } else {
    actions.push(button('Szybki test', () => runProvider(provider.id, false), 'primary'));
    actions.push(button('Pełny test', () => runProvider(provider.id, true)));
  }

  return node('section', { class: 'panel provider-diagnostic-provider' },
    node('div', { class: 'provider-diagnostic-provider-head' },
      node('div', {},
        node('div', { class: 'provider-diagnostic-provider-title' },
          node('h2', { text: provider.name }),
          badge(providerTypeLabel(provider.type), 'info'),
          summaryBadge(result)),
        node('p', { class: 'muted', text: result?.provider?.endpoint || 'Endpoint zostanie odczytany z przypisanych danych dostępowych.' })),
      node('div', { class: 'provider-diagnostic-actions' }, ...actions)),
    result
      ? node('div', { class: 'provider-diagnostic-summary' },
          node('span', { text: 'OK: ' + (summary.passed || 0) }),
          node('span', { text: 'Ostrzeżenia: ' + (summary.warnings || 0) }),
          node('span', { text: 'Błędy: ' + (summary.failed || 0) }),
          node('span', { text: 'Pominięte: ' + (summary.skipped || 0) }),
          node('span', { class: 'muted', text: result.mode === 'deep' ? 'Tryb pełny' : 'Tryb szybki' }))
      : node('div', { class: 'provider-diagnostic-empty muted', text: 'Uruchom test, aby sprawdzić połączenie z tej instancji Cloudportal do platformy.' }),
    checks.length
      ? node('div', { class: 'provider-diagnostic-checks' }, ...checks.map(diagnosticCheckRow))
      : null);
}

function requestedProviderId() {
  try {
    const query = String(location.hash || '').split('?')[1] || '';
    const raw = new URLSearchParams(query).get('provider');
    const value = Number(raw);
    return Number.isInteger(value) && value > 0 ? value : null;
  } catch {
    return null;
  }
}

async function providerDiagnosticsView() {
  const providerResult = await api('/providers?limit=200');
  const providers = providerResult.items || [];
  const results = new Map();
  const running = new Set();
  let runningAll = false;

  const render = () => {
    const headerActions = [];
    if (providers.length) {
      headerActions.push(button(
        runningAll ? 'Sprawdzanie wszystkich…' : 'Sprawdź wszystkie',
        async () => {
          if (runningAll) return;
          runningAll = true;
          render();
          let cursor = 0;
          const workers = Array.from({ length: Math.min(3, providers.length) }, async () => {
            while (cursor < providers.length) {
              const provider = providers[cursor++];
              await runProvider(provider.id, false, false);
            }
          });
          await Promise.all(workers);
          runningAll = false;
          render();
        },
        'primary',
        runningAll
      ));
    }

    dom.content.replaceChildren(
      heading('Diagnostyka połączeń Cloudportal → platformy infrastruktury.', [
        button('← Narzędzia', () => navigate('tools')),
        ...headerActions,
      ]),
      node('section', { class: 'provider-diagnostic-hero' },
        node('div', {},
          node('span', { class: 'tools-eyebrow', text: 'Łączność i API' }),
          node('h2', { text: 'Gdzie zatrzymuje się połączenie?' }),
          node('p', { class: 'muted', text: 'Test działa z backendu Cloudportal, czyli z tego samego miejsca co provisioning. Osobno sprawdza DNS, ICMP, port TCP, TLS, HTTP, dane dostępowe i odczyt zasobów. Timeout TCP wskazuje typowo na routing, ACL lub firewall; brak ping nie jest traktowany jako awaria API.' })),
        node('div', { class: 'provider-diagnostic-legend' },
          statusBadge('pass'), statusBadge('warn'), statusBadge('fail'), statusBadge('skipped'))),
      providers.length
        ? node('div', { class: 'provider-diagnostic-list' },
            ...providers.map(provider => providerResultPanel(
              provider,
              results.get(Number(provider.id)),
              running.has(Number(provider.id)),
              runProvider
            )))
        : node('div', { class: 'empty', text: 'Brak skonfigurowanych platform. Dodaj platformę, aby uruchomić diagnostykę.' })
    );
  };

  async function runProvider(providerId, deep, refresh = true) {
    const id = Number(providerId);
    running.add(id);
    if (refresh) render();
    try {
      const result = await api('/diagnostics/providers/' + encodeURIComponent(id) + '?deep=' + (deep ? 'true' : 'false'), {
        method: 'POST',
      });
      results.set(id, result);
    } catch (error) {
      const provider = providers.find(item => Number(item.id) === id);
      results.set(id, {
        provider: { id, name: provider?.name || ('#' + id), type: provider?.type || '' },
        mode: deep ? 'deep' : 'quick',
        status: 'failed',
        summary: { passed: 0, warnings: 0, failed: 1, skipped: 0 },
        checks: [{
          id: 'request',
          label: 'Wywołanie diagnostyki',
          layer: 'cloudportal',
          status: 'fail',
          message: error?.message || 'Nie udało się uruchomić diagnostyki.',
          latency_ms: null,
          critical: true,
          details: {},
        }],
      });
    } finally {
      running.delete(id);
      if (refresh) render();
    }
  }

  render();

  const preselected = requestedProviderId();
  if (preselected && providers.some(item => Number(item.id) === preselected)) {
    await runProvider(preselected, false);
  }
}

window.ProviderDiagnostics = Object.freeze({
  card: providerDiagnosticsCard,
});

registerView({
  id: 'provider-diagnostics',
  label: 'Diagnostyka połączeń',
  iconName: 'network',
  navigation: false,
  navigationParent: 'tools',
  permission: 'providers.update',
  order: 158,
}, providerDiagnosticsView);
})();
