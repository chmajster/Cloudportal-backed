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

function settingsHealth(value) {
  if (value === true || value === 'ok' || value === 'ready') return badge('OK', 'ok');
  if (value === false || value === 'failed') return badge('Błąd', 'danger');
  return badge(String(value ?? 'Nieznany'), 'info');
}

function ldapDetailRow(label, value, options = {}) {
  return node('div', { class: 'settings-ldap-detail' + (options.wide ? ' wide' : '') },
    node('span', { class: 'settings-ldap-detail-label', text: label }),
    node('strong', { class: options.mono ? 'mono' : '', text: String(value ?? '—') }));
}

function ldapFilterCard(title, filter) {
  return node('article', { class: 'settings-ldap-filter-card' },
    node('div', { class: 'settings-ldap-filter-head' },
      node('span', { class: 'settings-ldap-filter-icon', 'aria-hidden': 'true' }, appIcon('filter')),
      node('strong', { text: title })),
    node('code', { text: filter }));
}

function ldapSettingsForm(config) {
  const fields = node('div', { class: 'form-grid' },
    checkboxField('Włącz logowanie LDAP', 'enabled', Boolean(config.enabled)),
    field('Adres LDAP', 'url', {
      required: true,
      value: config.url || 'ldap://localhost:389',
      placeholder: 'ldaps://ldap.example.com:636',
      wide: true,
      help: 'Obsługiwane: ldap://, ldap:// + StartTLS oraz ldaps://.',
    }),
    checkboxField('StartTLS', 'start_tls', Boolean(config.start_tls)),
    checkboxField('Weryfikuj certyfikat TLS', 'verify_tls', config.verify_tls !== false),
    field('Bind DN', 'bind_dn', {
      value: config.bind_dn || '',
      placeholder: 'cn=cloudportal,ou=services,dc=example,dc=com',
      wide: true,
      help: 'Opcjonalne. Puste pole oznacza próbę anonymous bind.',
    }),
    field('Hasło Bind', 'bind_password', {
      type: 'password',
      value: '',
      autocomplete: 'new-password',
      wide: true,
      placeholder: config.bind_password_configured ? '•••••••• (zapisane)' : 'Hasło konta serwisowego',
      help: config.bind_password_configured
        ? 'Hasło jest już zapisane w formie zaszyfrowanej. Pozostaw puste, aby go nie zmieniać.'
        : 'Sekret zostanie zaszyfrowany kluczem głównym Cloudportal.',
    }),
    field('Base DN', 'base_dn', {
      required: true,
      value: config.base_dn || '',
      placeholder: 'ou=people,dc=example,dc=com',
      wide: true,
    }),
    field('Filtr użytkownika', 'user_filter', {
      required: true,
      value: config.user_filter || '(&(objectClass=person)(uid={username}))',
      placeholder: '(&(objectClass=person)(uid={username}))',
      wide: true,
      help: 'Musi zawierać dokładnie jeden placeholder {username}. Wartość loginu jest escapowana przed wyszukiwaniem.',
    }),
    field('Atrybut loginu', 'username_attribute', { required: true, value: config.username_attribute || 'uid' }),
    field('Atrybut e-mail', 'email_attribute', { required: true, value: config.email_attribute || 'mail' }),
    field('Atrybut imienia', 'first_name_attribute', { required: true, value: config.first_name_attribute || 'givenName' }),
    field('Atrybut nazwiska', 'last_name_attribute', { required: true, value: config.last_name_attribute || 'sn' })
  );

  openModal({
    title: 'Konfiguracja LDAP',
    eyebrow: 'Ustawienia uwierzytelniania',
    body: fields,
    submitLabel: 'Zapisz konfigurację',
    wide: true,
    onSubmit: async (data) => {
      const payload = {
        enabled: data.has('enabled'),
        url: data.get('url'),
        start_tls: data.has('start_tls'),
        verify_tls: data.has('verify_tls'),
        bind_dn: data.get('bind_dn'),
        base_dn: data.get('base_dn'),
        user_filter: data.get('user_filter'),
        username_attribute: data.get('username_attribute'),
        email_attribute: data.get('email_attribute'),
        first_name_attribute: data.get('first_name_attribute'),
        last_name_attribute: data.get('last_name_attribute'),
      };
      if (data.get('bind_password')) payload.bind_password = data.get('bind_password');
      await api('/settings/ldap', { method: 'PUT', body: payload });
      toast('Konfiguracja LDAP zapisana.');
      navigate('settings');
    },
  });
}

async function testLdap() {
  try {
    const result = await api('/settings/ldap/test', { method: 'POST' });
    toast(result.message || 'Połączenie LDAP działa.');
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function settingsView() {
  const [config, health, updateSettings, blueprintSettings] = await Promise.all([
    api('/settings/ldap'),
    api('/health', { auth: false, allow: [503] }),
    allowed('updates.read') ? api('/updates/settings').catch(() => null) : Promise.resolve(null),
    api('/settings/blueprints'),
  ]);

  const user = state.identity?.user || {};
  const roles = state.identity?.roles || [];
  const permissions = state.identity?.permissions || [];
  const theme = document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
  const checks = health?.checks || {};
  const workers = checks.workers || {};

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
      button('Otwórz Moje konto', () => navigate('account'), 'primary'))
  );

  const system = settingsCard(
    'server',
    'System',
    'Stan backendu i podstawowych komponentów wykonawczych.',
    node('div', { class: 'settings-values settings-system-grid' },
      node('div', { class: 'settings-value' }, node('span', { text: 'Backend' }), settingsHealth(health?.status || (health?.ok ? 'ok' : 'unknown'))),
      node('div', { class: 'settings-value' }, node('span', { text: 'PostgreSQL' }), settingsHealth(checks.database)),
      node('div', { class: 'settings-value' }, node('span', { text: 'Redis' }), settingsHealth(checks.redis)),
      node('div', { class: 'settings-value' }, node('span', { text: 'Dispatcher' }), settingsHealth(checks.dispatcher)),
      settingsValue('Workery online', workers.online !== undefined ? String(workers.online) : '—'),
      settingsValue('Workery oczekiwane', workers.expected !== undefined ? String(workers.expected) : '—')),
    node('div', { class: 'settings-card-actions' },
      button('Odśwież stan', () => settingsView()))
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
      ? node('div', { class: 'settings-card-actions' }, button('Otwórz Auto-update', () => navigate('updates'), 'primary'))
      : null
  );

  const blueprints = settingsCard(
    'box',
    'Blueprinty',
    'Globalna polityka zatwierdzania uruchomień Blueprintów.',
    node('div', { class: 'settings-values' },
      settingsValue(
        'Automatyczne zatwierdzanie wykonania',
        blueprintSettings.auto_approve_for_executors ? 'Włączone' : 'Wyłączone'
      ),
      settingsValue(
        'Użytkownik z blueprints.execute',
        blueprintSettings.auto_approve_for_executors
          ? 'Uruchamia bez pytania o approval'
          : 'Tworzy request oczekujący na approval'
      )),
    allowed('settings.update')
      ? node('div', { class: 'settings-card-actions' },
          button(
            blueprintSettings.auto_approve_for_executors
              ? 'Wyłącz auto-approval'
              : 'Włącz auto-approval',
            async () => {
              await api('/settings/blueprints', {
                method: 'PUT',
                body: {
                  auto_approve_for_executors: !blueprintSettings.auto_approve_for_executors,
                },
              });
              toast('Polityka wykonywania Blueprintów została zapisana.');
              settingsView();
            },
            blueprintSettings.auto_approve_for_executors ? 'danger' : 'primary'
          ))
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
      allowed('roles.read') ? button('Role i RBAC', () => navigate('roles')) : null,
      allowed('tokens.read') ? button('Tokeny API', () => navigate('tokens')) : null)
  );

  const ldapActions = [];
  if (allowed('settings.update')) {
    ldapActions.push(button('Testuj połączenie', testLdap));
    ldapActions.push(button('Konfiguruj LDAP', () => ldapSettingsForm(config), 'primary'));
  }

  const ldapTls = config.url?.startsWith('ldaps://') ? 'LDAPS' : (config.start_tls ? 'StartTLS' : 'Bez TLS');
  const ldap = node('section', { class: 'panel settings-ldap-panel' },
    node('div', { class: 'settings-ldap-hero' },
      node('div', { class: 'settings-ldap-heading' },
        node('span', { class: 'settings-ldap-icon', 'aria-hidden': 'true' }, appIcon('server')),
        node('div', {},
          node('div', { class: 'settings-ldap-title-row' },
            node('h2', { text: 'LDAP' }),
            badge(config.enabled ? 'Włączony' : 'Wyłączony', config.enabled ? 'ok' : 'warning')),
          node('p', { class: 'muted', text: 'Logowanie katalogowe, JIT provisioning lokalnego konta i późniejsze przypisanie ról przez RBAC.' }))),
      ldapActions.length ? node('div', { class: 'settings-ldap-actions' }, ldapActions) : null),

    node('div', { class: 'settings-ldap-overview' },
      node('section', { class: 'settings-ldap-section' },
        node('div', { class: 'settings-ldap-section-head' },
          node('span', { class: 'settings-ldap-section-icon', 'aria-hidden': 'true' }, appIcon('server')),
          node('div', {}, node('strong', { text: 'Połączenie' }), node('small', { text: 'Serwer katalogowy i zakres wyszukiwania' }))),
        node('div', { class: 'settings-ldap-detail-grid' },
          ldapDetailRow('Serwer', config.url || '—', { mono: true, wide: true }),
          ldapDetailRow('Base DN', config.base_dn || 'Nie skonfigurowano', { mono: true, wide: true }),
          ldapDetailRow('Bind DN', config.bind_dn || 'Anonymous bind', { mono: Boolean(config.bind_dn), wide: true }),
          ldapDetailRow('Sekret bind', config.bind_password_configured ? 'Skonfigurowany' : 'Brak'))),

      node('section', { class: 'settings-ldap-section' },
        node('div', { class: 'settings-ldap-section-head' },
          node('span', { class: 'settings-ldap-section-icon', 'aria-hidden': 'true' }, appIcon('shield')),
          node('div', {}, node('strong', { text: 'TLS i mapowanie' }), node('small', { text: 'Bezpieczeństwo oraz atrybuty konta' }))),
        node('div', { class: 'settings-ldap-detail-grid' },
          ldapDetailRow('Transport', ldapTls),
          ldapDetailRow('Weryfikacja TLS', config.verify_tls ? 'Włączona' : 'Wyłączona'),
          ldapDetailRow('Login', config.username_attribute || 'uid', { mono: true }),
          ldapDetailRow('E-mail', config.email_attribute || 'mail', { mono: true }),
          ldapDetailRow('Filtr użytkownika', config.user_filter || '—', { mono: true, wide: true })))),
    
    node('div', { class: 'settings-ldap-jit' },
      node('span', { class: 'settings-ldap-jit-icon', 'aria-hidden': 'true' }, appIcon('users')),
      node('div', {},
        node('strong', { text: 'JIT provisioning i RBAC' }),
        node('p', { text: 'Po pierwszym poprawnym logowaniu LDAP Cloudportal tworzy lokalne konto bez ról. Administrator przypisuje role później w Użytkownicy → Role. Hasło pozostaje wyłącznie w LDAP i nie jest zapisywane przez Cloudportal.' }))),

    node('div', { class: 'settings-ldap-filters' },
      node('div', { class: 'settings-ldap-filters-head' },
        node('div', {},
          node('strong', { text: 'Przykładowe filtry użytkownika' }),
          node('span', { class: 'muted', text: 'Gotowe wzorce dla najczęstszych katalogów.' }))),
      node('div', { class: 'settings-ldap-filter-grid' },
        ldapFilterCard('LDAP / LLDAP', '(&(objectClass=person)(uid={username}))'),
        ldapFilterCard('Active Directory', '(&(objectClass=user)(sAMAccountName={username}))'))));

  dom.content.replaceChildren(
    heading('Ustawienia panelu, konta, systemu, aktualizacji i integracji katalogowych.'),
    node('div', { class: 'settings-grid' }, appearance, account, system, updates, blueprints, security),
    ldap
  );
}

registerView({ id: 'settings', label: 'Ustawienia', icon: 'S', permission: 'settings.read', order: 150 }, settingsView);
})();
