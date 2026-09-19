'use strict';

(() => {
function settingsCard(iconName, title, description, ...content) {
  return node('section', { class: 'settings-card' },
    node('div', { class: 'settings-card-icon', 'aria-hidden': 'true' }, appIcon(iconName)),
    node('div', { class: 'settings-card-content' },
      node('div', { class: 'settings-card-head' },
        node('div', {},
          node('h2', { text: title }),
          node('p', { class: 'muted', text: description }))),
      ...content));
}

function settingsValue(label, value, kind = '') {
  return node('div', { class: 'settings-value' },
    node('span', { text: label }),
    node('strong', { class: kind, text: value ?? '—' }));
}

function settingsAction(label, action, kind = 'ghost') {
  return button(label, action, kind);
}

function healthStatus(value) {
  if (value === true || value === 'ok' || value === 'ready') return badge('OK', 'ok');
  if (value === false || value === 'failed') return badge('Błąd', 'danger');
  return badge(String(value ?? 'Nieznany'), 'info');
}

async function settingsView() {
  const [health, updateSettings] = await Promise.all([
    api('/health', { auth: false, allow: [503] }),
    allowed('updates.read')
      ? api('/updates/settings').catch(() => null)
      : Promise.resolve(null),
  ]);

  const user = state.identity?.user || {};
  const roles = state.identity?.roles || [];
  const permissions = state.identity?.permissions || [];
  const theme = document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';

  const appearance = settingsCard(
    'sun',
    'Wygląd',
    'Ustaw wygląd panelu na tym urządzeniu.',
    node('div', { class: 'settings-choice-row' },
      node('button', {
        type: 'button',
        class: 'settings-choice' + (theme === 'light' ? ' active' : ''),
        onClick: () => { setTheme('light'); settingsView(); },
      },
        node('span', { class: 'settings-choice-icon' }, appIcon('sun')),
        node('span', {}, node('strong', { text: 'Jasny' }), node('small', { text: 'Jasne tło interfejsu' }))),
      node('button', {
        type: 'button',
        class: 'settings-choice' + (theme === 'dark' ? ' active' : ''),
        onClick: () => { setTheme('dark'); settingsView(); },
      },
        node('span', { class: 'settings-choice-icon' }, appIcon('moon')),
        node('span', {}, node('strong', { text: 'Ciemny' }), node('small', { text: 'Ciemny motyw operatorski' }))))
  );

  const account = settingsCard(
    'user',
    'Konto i sesja',
    'Informacje o zalogowanym użytkowniku i jego dostępie.',
    node('div', { class: 'settings-values' },
      settingsValue('Użytkownik', user.username || '—'),
      settingsValue('E-mail', user.email || '—'),
      settingsValue('Role', roles.map(role => role.name).join(', ') || 'Brak'),
      settingsValue('Uprawnienia', String(permissions.length))),
    node('div', { class: 'settings-card-actions' },
      settingsAction('Otwórz Moje konto', () => navigate('account'), 'primary'))
  );

  const checks = health?.checks || {};
  const workers = checks.workers || {};
  const system = settingsCard(
    'server',
    'System',
    'Stan backendu i podstawowych komponentów wykonawczych.',
    node('div', { class: 'settings-values settings-system-grid' },
      node('div', { class: 'settings-value' }, node('span', { text: 'Backend' }), healthStatus(health?.status || (health?.ok ? 'ok' : 'unknown'))),
      node('div', { class: 'settings-value' }, node('span', { text: 'PostgreSQL' }), healthStatus(checks.database)),
      node('div', { class: 'settings-value' }, node('span', { text: 'Redis' }), healthStatus(checks.redis)),
      node('div', { class: 'settings-value' }, node('span', { text: 'Dispatcher' }), healthStatus(checks.dispatcher)),
      settingsValue('Workery online', workers.online !== undefined ? String(workers.online) : '—'),
      settingsValue('Workery oczekiwane', workers.expected !== undefined ? String(workers.expected) : '—')),
    node('div', { class: 'settings-card-actions' },
      settingsAction('Odśwież stan', () => settingsView()))
  );

  const updates = settingsCard(
    'refresh',
    'Aktualizacje',
    'Polityka aktualizacji i kanał źródłowy Cloudportal.',
    updateSettings
      ? node('div', { class: 'settings-values' },
          settingsValue('Auto-update', updateSettings.enabled ? 'Włączony' : 'Wyłączony'),
          settingsValue('Kanał / ref', updateSettings.ref || 'main', 'mono'),
          settingsValue('Interwał', updateSettings.interval_hours ? updateSettings.interval_hours + ' h' : '—'))
      : node('p', { class: 'muted', text: allowed('updates.read') ? 'Nie udało się odczytać ustawień aktualizacji.' : 'Brak uprawnienia do ustawień aktualizacji.' }),
    allowed('updates.read')
      ? node('div', { class: 'settings-card-actions' },
          settingsAction('Otwórz Auto-update', () => navigate('updates'), 'primary'))
      : null
  );

  const security = settingsCard(
    'shield',
    'Bezpieczeństwo',
    'Dostęp do ustawień administracyjnych jest kontrolowany przez RBAC.',
    node('div', { class: 'settings-values' },
      settingsValue('Dostęp do ustawień', allowed('settings.update') ? 'Odczyt i zmiana' : 'Tylko odczyt'),
      settingsValue('Źródło sesji', state.identity?.token_type || 'session'),
      settingsValue('HTTPS', location.protocol === 'https:' ? 'Aktywne' : 'HTTP')),
    node('div', { class: 'settings-card-actions' },
      allowed('roles.read') ? settingsAction('Role i RBAC', () => navigate('roles')) : null,
      allowed('tokens.read') ? settingsAction('Tokeny API', () => navigate('tokens')) : null)
  );

  dom.content.replaceChildren(
    heading('Ustawienia panelu, konta, systemu i aktualizacji.'),
    node('div', { class: 'settings-grid' }, appearance, account, system, updates, security)
  );
}

registerView({
  id: 'settings',
  label: 'Ustawienia',
  icon: 'S',
  permission: 'settings.read',
  order: 160,
}, settingsView);
})();
