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

function ldapDetailRow(label, value) {
  return node('div', { class: 'detail-item' },
    node('span', { class: 'muted', text: label }),
    node('strong', { text: String(value ?? '—') }));
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

function vmEnvironmentPayload(config) {
  return {
    test: config.environments?.test !== false,
    dev: config.environments?.dev !== false,
    nonprod: config.environments?.nonprod !== false,
    prod: config.environments?.prod !== false,
  };
}

function environmentSettingsForm(config) {
  const environmentLabels = {
    test: 'TEST',
    dev: 'DEV',
    nonprod: 'NONPROD',
    prod: 'PROD',
  };
  const environmentControls = node('div', { class: 'settings-environment-grid wide' },
    ...Object.entries(environmentLabels).map(([key, label]) =>
      node('label', { class: 'settings-environment-card' },
        node('input', {
          type: 'checkbox',
          name: 'environment_' + key,
          checked: config.environments?.[key] !== false,
        }),
        node('span', {},
          node('strong', { text: label }),
          node('small', { text: 'Dostępne w kreatorze Blueprintu' }))))
  );

  const fields = node('div', { class: 'form-grid' },
    node('div', { class: 'wide blueprint-wizard-info' },
      node('strong', { text: 'Environment' }),
      node('span', { text: 'Wybierz środowiska dostępne przy tworzeniu nowych Blueprintów i VM.' })),
    node('div', { class: 'wide settings-form-heading' },
      node('strong', { text: 'Dostępne środowiska' }),
      node('span', { class: 'muted', text: 'Wyłączone środowiska nie pojawią się w kreatorze Blueprintu.' })),
    environmentControls
  );

  openModal({
    title: 'Environment',
    eyebrow: 'Klasyfikacja VM',
    body: fields,
    submitLabel: 'Zapisz Environment',
    wide: true,
    onSubmit: async (data) => {
      await api('/settings/vm-classification', {
        method: 'PUT',
        body: {
          environments: {
            test: data.has('environment_test'),
            dev: data.has('environment_dev'),
            nonprod: data.has('environment_nonprod'),
            prod: data.has('environment_prod'),
          },
          apmids: config.apmids || [],
        },
      });
      toast('Ustawienia Environment zapisane.');
      navigate('settings');
    },
  });
}

function apmidSettingsForm(config) {
  const fields = node('div', { class: 'form-grid' },
    node('div', { class: 'wide blueprint-wizard-info' },
      node('strong', { text: 'APMID' }),
      node('span', { text: 'Zarządzaj identyfikatorami aplikacji dostępnymi w kreatorze VM. APMID jest również dodawany jako tag Proxmox.' })),
    field('APMID', 'apmids', {
      tag: 'textarea',
      value: (config.apmids || []).join('\n'),
      wide: true,
      placeholder: 'IAASTEAM\nCRM\nPAYMENTS',
      help: 'Jeden APMID w wierszu. Dozwolone: litery, cyfry, _ oraz -. Wartości są zapisywane wielkimi literami.',
    })
  );

  openModal({
    title: 'APMID',
    eyebrow: 'Klasyfikacja VM',
    body: fields,
    submitLabel: 'Zapisz APMID',
    wide: true,
    onSubmit: async (data) => {
      const apmids = String(data.get('apmids') || '')
        .split(/[\n,]+/)
        .map(value => value.trim().toUpperCase())
        .filter(Boolean);
      await api('/settings/vm-classification', {
        method: 'PUT',
        body: {
          environments: vmEnvironmentPayload(config),
          apmids,
        },
      });
      toast('Ustawienia APMID zapisane.');
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
  const [config, vmClassification, health, updateSettings] = await Promise.all([
    api('/settings/ldap'),
    api('/settings/vm-classification'),
    api('/health', { auth: false, allow: [503] }),
    allowed('updates.read') ? api('/updates/settings').catch(() => null) : Promise.resolve(null),
  ]);

  const actions = [];
  if (allowed('settings.update')) {
    actions.push(button('Konfiguruj LDAP', () => ldapSettingsForm(config), 'primary'));
    actions.push(button('Testuj LDAP', testLdap));
  }

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

  const environmentCard = settingsCard(
    'server',
    'Environment',
    'Środowiska dostępne przy tworzeniu Blueprintów i maszyn wirtualnych.',
    node('div', { class: 'settings-values' },
      settingsValue('TEST', vmClassification.environments?.test ? 'Włączony' : 'Wyłączony'),
      settingsValue('DEV', vmClassification.environments?.dev ? 'Włączony' : 'Wyłączony'),
      settingsValue('NONPROD', vmClassification.environments?.nonprod ? 'Włączony' : 'Wyłączony'),
      settingsValue('PROD', vmClassification.environments?.prod ? 'Włączony' : 'Wyłączony')),
    allowed('settings.update')
      ? node('div', { class: 'settings-card-actions' },
          button('Konfiguruj Environment', () => environmentSettingsForm(vmClassification), 'primary'))
      : null
  );

  const apmidCard = settingsCard(
    'box',
    'APMID',
    'Identyfikatory aplikacji używane przez kreator VM i tagi Proxmox.',
    node('div', { class: 'settings-values' },
      settingsValue('Liczba APMID', String((vmClassification.apmids || []).length)),
      settingsValue('Wartości', (vmClassification.apmids || []).length ? vmClassification.apmids.join(', ') : 'Brak')),
    allowed('settings.update')
      ? node('div', { class: 'settings-card-actions' },
          button('Konfiguruj APMID', () => apmidSettingsForm(vmClassification), 'primary'))
      : null
  );

  const ldap = node('section', { class: 'panel settings-ldap-panel' },
    node('div', { class: 'panel-header' },
      node('div', {},
        node('h2', { text: 'LDAP' }),
        node('p', { class: 'muted', text: 'Logowanie katalogowe z automatycznym utworzeniem lokalnego konta i późniejszym przypisaniem ról przez RBAC.' })),
      badge(config.enabled ? 'Włączony' : 'Wyłączony', config.enabled ? 'ok' : 'warning')),
    node('div', { class: 'detail-grid' },
      ldapDetailRow('Serwer', config.url || '—'),
      ldapDetailRow('Base DN', config.base_dn || '—'),
      ldapDetailRow('Bind DN', config.bind_dn || 'Anonymous bind'),
      ldapDetailRow('Sekret bind', config.bind_password_configured ? 'Skonfigurowany' : 'Brak'),
      ldapDetailRow('TLS', config.url?.startsWith('ldaps://') ? 'LDAPS' : (config.start_tls ? 'StartTLS' : 'Bez TLS')),
      ldapDetailRow('Weryfikacja TLS', config.verify_tls ? 'Włączona' : 'Wyłączona'),
      ldapDetailRow('Filtr użytkownika', config.user_filter || '—'),
      ldapDetailRow('Mapowanie loginu', config.username_attribute || 'uid'),
      ldapDetailRow('Mapowanie e-mail', config.email_attribute || 'mail')),
    node('div', { class: 'callout info' },
      node('strong', { text: 'JIT provisioning i RBAC' }),
      node('p', { text: 'Po pierwszym poprawnym logowaniu LDAP Cloudportal tworzy konto z auth_source=ldap bez żadnych ról. Administrator przypisuje role później w Użytkownicy → Role. Hasło pozostaje wyłącznie w LDAP i nie jest zapisywane przez Cloudportal.' })));

  dom.content.replaceChildren(
    heading('Ustawienia panelu, konta, systemu, aktualizacji i integracji katalogowych.', actions),
    node('div', { class: 'settings-grid' }, appearance, account, system, updates, security, environmentCard, apmidCard),
    ldap,
    node('section', { class: 'panel' },
      node('div', { class: 'panel-header' }, node('h2', { text: 'Przykładowe filtry LDAP' })),
      node('div', { class: 'detail-grid' },
        ldapDetailRow('LDAP / LLDAP', '(&(objectClass=person)(uid={username}))'),
        ldapDetailRow('Active Directory', '(&(objectClass=user)(sAMAccountName={username}))')))
  );
}

registerView({ id: 'settings', label: 'Ustawienia', icon: 'S', permission: 'settings.read', order: 150 }, settingsView);
})();
