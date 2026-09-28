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

function ssoSettingsForm(config) {
  const defaultRedirect = location.origin + '/api/v1/auth/sso/callback';
  const fields = node('div', { class: 'form-grid' },
    checkboxField('Włącz logowanie SSO', 'enabled', Boolean(config.enabled)),
    field('Nazwa dostawcy', 'provider_name', {
      required: true,
      value: config.provider_name || 'OpenSSO',
      placeholder: 'OpenSSO',
    }),
    field('Issuer OIDC', 'issuer', {
      required: true,
      value: config.issuer || '',
      placeholder: 'https://sso.example.com',
      wide: true,
      help: 'Cloudportal pobierze automatycznie /.well-known/openid-configuration.',
    }),
    field('Client ID', 'client_id', {
      required: true,
      value: config.client_id || '',
      placeholder: 'cloudportal',
      wide: true,
    }),
    field('Client secret', 'client_secret', {
      type: 'password',
      value: '',
      autocomplete: 'new-password',
      wide: true,
      placeholder: config.client_secret_configured ? '•••••••• (zapisany)' : 'Sekret klienta OIDC',
      help: config.client_secret_configured
        ? 'Sekret jest zapisany zaszyfrowany. Pozostaw puste, aby go nie zmieniać.'
        : 'Dla publicznego klienta PKCE wybierz metodę none. Dla OpenSSO zalecany jest klient confidential.',
    }),
    field('Redirect URI', 'redirect_uri', {
      required: true,
      value: config.redirect_uri || defaultRedirect,
      wide: true,
      help: 'Ten adres musi być zarejestrowany dokładnie po stronie dostawcy SSO.',
    }),
    field('Scopes', 'scopes', {
      required: true,
      value: (config.scopes || ['openid', 'profile', 'email', 'groups']).join(' '),
      wide: true,
      help: 'Lista rozdzielona spacją lub przecinkiem. Scope openid jest wymagany.',
    }),
    selectField('Uwierzytelnienie token endpoint', 'token_endpoint_auth_method', [
      { value: 'client_secret_post', label: 'client_secret_post' },
      { value: 'client_secret_basic', label: 'client_secret_basic' },
      { value: 'none', label: 'none (public client + PKCE)' },
    ], config.token_endpoint_auth_method || 'client_secret_post', { required: true, wide: true }),
    checkboxField('Weryfikuj TLS dostawcy SSO', 'verify_tls', config.verify_tls !== false),
    checkboxField('Zezwól na HTTP (tylko laboratorium)', 'allow_insecure_http', Boolean(config.allow_insecure_http)),
    field('Claim loginu', 'username_claim', { required: true, value: config.username_claim || 'preferred_username' }),
    field('Claim e-mail', 'email_claim', { required: true, value: config.email_claim || 'email' }),
    field('Claim imienia', 'first_name_claim', { required: true, value: config.first_name_claim || 'given_name' }),
    field('Claim nazwiska', 'last_name_claim', { required: true, value: config.last_name_claim || 'family_name' })
  );

  openModal({
    title: 'Konfiguracja SSO / OIDC',
    eyebrow: 'Ustawienia uwierzytelniania',
    body: fields,
    submitLabel: 'Zapisz konfigurację',
    wide: true,
    onSubmit: async data => {
      const scopes = String(data.get('scopes') || '')
        .split(/[\s,]+/)
        .map(value => value.trim())
        .filter(Boolean);
      const payload = {
        enabled: data.has('enabled'),
        provider_name: data.get('provider_name'),
        issuer: data.get('issuer'),
        client_id: data.get('client_id'),
        redirect_uri: data.get('redirect_uri'),
        scopes,
        token_endpoint_auth_method: data.get('token_endpoint_auth_method'),
        verify_tls: data.has('verify_tls'),
        allow_insecure_http: data.has('allow_insecure_http'),
        username_claim: data.get('username_claim'),
        email_claim: data.get('email_claim'),
        first_name_claim: data.get('first_name_claim'),
        last_name_claim: data.get('last_name_claim'),
      };
      if (data.get('client_secret')) payload.client_secret = data.get('client_secret');
      await api('/settings/sso', { method: 'PUT', body: payload });
      toast('Konfiguracja SSO zapisana.');
      navigate('settings');
    },
  });
}

async function testSsoConnection() {
  const result = await api('/settings/sso/test', { method: 'POST' });
  toast('OIDC działa. Zweryfikowano discovery i ' + result.signing_keys + ' kluczy JWKS.');
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

function ldapDiagnosticBadge(status) {
  if (status === 'ok') return badge('OK', 'ok');
  if (status === 'error') return badge('Błąd', 'danger');
  if (status === 'warning') return badge('Uwaga', 'warning');
  return badge('Pominięto', 'info');
}

function renderLdapDiagnostics(host, result) {
  const steps = Array.isArray(result?.steps) ? result.steps : [];
  const summaryKind = result?.login_ready ? 'ok' : (result?.ok ? 'warning' : 'danger');
  const profile = result?.profile || null;
  const content = [
    node('div', { class: 'settings-ldap-diagnostics-summary ' + summaryKind },
      node('div', {},
        node('strong', { text: result?.message || 'Diagnostyka zakończona.' }),
        node('small', {
          text: result?.password_tested
            ? 'Sprawdzono również hasło i bind użytkownika.'
            : 'Hasło użytkownika nie było testowane.',
        })),
      result?.login_ready ? badge('Logowanie gotowe', 'ok') : (result?.ok ? badge('Test częściowy', 'warning') : badge('Wykryto problem', 'danger'))),
    node('div', { class: 'settings-ldap-diagnostic-steps' },
      ...steps.map(step => node('article', { class: 'settings-ldap-diagnostic-step ' + String(step.status || '') },
        node('div', { class: 'settings-ldap-diagnostic-step-head' },
          node('strong', { text: step.label || step.key || 'Test' }),
          ldapDiagnosticBadge(step.status)),
        node('p', { text: step.message || '—' }),
        step.detail ? node('code', { text: step.detail }) : null))),
  ];
  if (profile) {
    content.push(node('div', { class: 'settings-ldap-diagnostic-profile' },
      node('strong', { text: 'Użytkownik znaleziony w LDAP' }),
      node('div', { class: 'settings-ldap-detail-grid' },
        ldapDetailRow('DN', profile.dn || '—', { mono: true, wide: true }),
        ldapDetailRow('Login', profile.username || '—', { mono: true }),
        ldapDetailRow('E-mail', profile.email || '—', { mono: true }),
        ldapDetailRow('Imię', profile.first_name || '—'),
        ldapDetailRow('Nazwisko', profile.last_name || '—'))));
  }
  host.replaceChildren(...content);
}

function ldapDiagnosticsForm(config) {
  const usernameField = field('Login użytkownika LDAP', 'username', {
    value: '',
    placeholder: 'np. chris',
    wide: true,
    help: 'Podaj dokładnie taki login, jaki wpisujesz na ekranie logowania CloudPortal.',
  });
  const passwordField = field('Hasło użytkownika LDAP', 'password', {
    type: 'password',
    value: '',
    autocomplete: 'new-password',
    wide: true,
    help: 'Hasło jest używane wyłącznie do tego testu i nie jest zapisywane.',
  });
  const passwordInput = passwordField.querySelector('input');
  const results = node('div', { class: 'settings-ldap-diagnostics-results wide' },
    node('div', { class: 'settings-ldap-diagnostics-empty' },
      node('strong', { text: 'Diagnostyka jeszcze nieuruchomiona' }),
      node('p', { text: 'Najpierw sprawdź połączenie lub podaj login i hasło, aby przejść pełną ścieżkę logowania.' })));

  const runBasic = async () => {
    results.replaceChildren(node('p', { class: 'muted', text: 'Sprawdzanie połączenia, TLS, bind i Base DN…' }));
    try {
      const result = await api('/settings/ldap/diagnostics', {
        method: 'POST',
        body: { username: '' },
      });
      renderLdapDiagnostics(results, result);
    } catch (error) {
      results.replaceChildren(node('p', { class: 'form-error', text: error.message }));
    }
  };

  const body = node('div', { class: 'stack' },
    node('div', { class: 'settings-ldap-diagnostics-intro' },
      node('strong', { text: 'Diagnostyka krok po kroku' }),
      node('p', {
        text: 'Test sprawdza: stan LDAP, połączenie TCP, TLS, bind serwisowy, Base DN, routing logowania CloudPortal, filtr użytkownika, mapowanie atrybutów, bind hasłem użytkownika oraz kolizje JIT z kontami lokalnymi.',
      })),
    node('div', { class: 'settings-ldap-diagnostics-config' },
      ldapDetailRow('Serwer', config.url || '—', { mono: true }),
      ldapDetailRow('Base DN', config.base_dn || '—', { mono: true }),
      ldapDetailRow('Bind DN', config.bind_dn || 'Anonymous bind', { mono: true }),
      ldapDetailRow('LDAP', config.enabled ? 'Włączony' : 'Wyłączony')),
    node('div', { class: 'settings-ldap-diagnostics-actions' },
      button('Sprawdź samo połączenie', () => runBasic())),
    node('div', { class: 'form-grid' }, usernameField, passwordField),
    node('div', { class: 'settings-ldap-diagnostics-note' },
      node('strong', { text: 'Test nie wykonuje JIT provisioning' }),
      node('p', { text: 'Diagnostyka tylko symuluje decyzje logowania i wykrywa blokady. Nie tworzy użytkownika, nie przypisuje ról i nie zapisuje hasła.' })),
    results
  );

  openModal({
    title: 'Diagnostyka LDAP',
    eyebrow: 'Połączenie / wyszukiwanie / logowanie',
    body,
    submitLabel: 'Przetestuj pełne logowanie',
    wide: true,
    onSubmit: async data => {
      const username = String(data.get('username') || '').trim();
      const password = String(data.get('password') || '');
      if (!username) throw new Error('Podaj login użytkownika LDAP.');
      if (!password) throw new Error('Podaj hasło użytkownika LDAP do pełnego testu.');
      results.replaceChildren(node('p', { class: 'muted', text: 'Sprawdzanie pełnej ścieżki logowania LDAP…' }));
      try {
        const result = await api('/settings/ldap/diagnostics', {
          method: 'POST',
          body: { username, password },
        });
        passwordInput.value = '';
        renderLdapDiagnostics(results, result);
      } catch (error) {
        passwordInput.value = '';
        throw error;
      }
      return false;
    },
  });
}

function testLdap() {
  navigate('/admin/settings/ldap/diagnostics');
}

function executionParallelForm(parallelLimit) {
  const body = node('div', { class: 'form-grid' },
    field('Maksymalna liczba równoległych zadań', 'max_parallel_jobs', {
      type: 'number',
      min: 1,
      max: 64,
      required: true,
      value: parallelLimit,
      help: 'Zakres 1–64. Dispatcher nie przekaże nowych zadań ponad ten limit. Rzeczywista równoległość nie przekroczy liczby workerów online.',
    }),
    node('p', {
      class: 'muted wide',
      text: 'Zmiana działa bez restartu. Zadania już uruchomione lub przekazane do workerów nie są przerywane; nowy limit obowiązuje kolejne uruchomienia.',
    }));
  openModal({
    title: 'Równoległość wykonywania zadań',
    eyebrow: 'System · kolejka wykonawcza',
    body,
    submitLabel: 'Zapisz limit',
    onSubmit: async data => {
      await api('/settings/execution', {
        method: 'PUT',
        body: { max_parallel_jobs: Number(data.get('max_parallel_jobs')) },
      });
      toast('Limit równoległych zadań został zapisany.');
      navigate('settings');
    },
  });
}

function blueprintApprovalTimeoutForm(blueprintSettings) {
  const body = field('Timeout approval (h)', 'approval_timeout_hours', {
    type: 'number',
    min: 1,
    max: 720,
    required: true,
    value: blueprintSettings.approval_timeout_hours || 48,
    help: 'Po tym czasie ręczny request approval zostanie automatycznie anulowany.',
  });
  openModal({
    title: 'Timeout approval Blueprintów',
    eyebrow: 'Globalna polityka domyślna Blueprintów',
    body,
    submitLabel: 'Zapisz timeout',
    onSubmit: async data => {
      await api('/settings/blueprints', {
        method: 'PUT',
        body: {
          auto_approve_for_executors: blueprintSettings.auto_approve_for_executors,
          approval_timeout_hours: Number(data.get('approval_timeout_hours')),
        },
      });
      toast('Timeout approval zapisany.');
      navigate('settings');
    },
  });
}

async function platformSettingsView() {
  const result = await api('/settings/platforms');
  const platforms = result.items || [];
  const actions = allowed('providers.read') ? [button('Połączenia z platformami', () => navigate('providers'))] : [];
  dom.content.replaceChildren(
    heading('Ustawienia → Platformy', actions),
    node('section', { class: 'panel' },
      node('p', { class: 'muted', text: 'Wyłączenie platformy blokuje nowe provisioning, Blueprinty, discovery i Day-2 Actions. Istniejąca konfiguracja oraz dane dostępowe pozostają zapisane.' }),
      table([
        { label: 'Platforma', value: item => node('strong', { text: item.label || item.name }) },
        { label: 'Status', value: item => badge(item.enabled ? 'Włączona' : 'Wyłączona', item.enabled ? 'ok' : 'warning') },
        { label: 'Konfiguracja', value: item => badge(item.configured ? 'Skonfigurowana' : 'Brak konfiguracji', item.configured ? 'info' : 'warning') },
        { label: 'Stan połączenia', value: item => item.connection_status === 'not_configured' ? 'Brak konfiguracji' : 'Nie sprawdzono' },
        { label: 'Połączenia', value: item => String(item.connection_count || 0) },
      ], platforms, item => {
        const rowActions = [];
        if (item.enabled && allowed('providers.read')) rowActions.push(button('Konfiguruj', () => navigate('providers')));
        if (item.enabled && item.configured && allowed('providers.update')) rowActions.push(button('Diagnostyka', () => navigate('/admin/tools/provider-diagnostics')));
        if (allowed('settings.update')) rowActions.push(button(item.enabled ? 'Wyłącz' : 'Włącz', async () => {
          await api('/settings/platforms/' + encodeURIComponent(item.name), { method: 'PUT', body: { enabled: !item.enabled } });
          toast((item.label || item.name) + (item.enabled ? ' wyłączona.' : ' włączona.'));
          await platformSettingsView();
        }, item.enabled ? 'danger' : 'primary'));
        return rowActions;
      }))
  );
}

async function settingsView() {
  const [config, ssoConfig, health, updateSettings, blueprintSettings, executionSettings, executionCapabilities, platformSettings] = await Promise.all([
    api('/settings/ldap'),
    api('/settings/sso'),
    api('/health', { auth: false, allow: [503] }),
    allowed('updates.read') ? api('/updates/settings').catch(() => null) : Promise.resolve(null),
    api('/settings/blueprints'),
    api('/settings/execution'),
    api('/settings/execution/capabilities'),
    api('/settings/platforms'),
  ]);

  const user = state.identity?.user || {};
  const roles = state.identity?.roles || [];
  const permissions = state.identity?.permissions || [];
  const theme = document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
  const checks = health?.checks || {};
  const workers = checks.workers || {};
  const parallelLimit = Number(executionSettings.max_parallel_jobs || 10);
  const onlineWorkers = Number(workers.online || 0);
  const effectiveParallelism = onlineWorkers > 0 ? Math.min(parallelLimit, onlineWorkers) : 0;
  const workerCapacityShortfall = onlineWorkers < parallelLimit;
  const workerReconciliationSupported = executionCapabilities?.worker_reconciliation_supported === true;
  const installMode = executionCapabilities?.install_mode || 'unknown';
  const platformItems = platformSettings?.items || [];
  const enabledPlatforms = platformItems.filter(item => item.enabled);

  const reconcileWorkerCapacity = async () => {
    const result = await api('/settings/execution/reconcile', {
      method: 'POST',
      body: {},
      idempotent: true,
    });
    toast(result.changed
      ? 'Pula workerów została zwiększona do ' + result.worker_count + '.'
      : 'Pula workerów została zrekonsyliowana do ' + result.worker_count + '.');
    await new Promise(resolve => setTimeout(resolve, result.changed ? 3000 : 250));
    await settingsView();
  };

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
      node('div', { class: 'settings-value' }, node('span', { text: 'Redis' }), settingsHealth(checks.queue)),
      node('div', { class: 'settings-value' }, node('span', { text: 'Dispatcher' }), settingsHealth(checks.dispatcher)),
      settingsValue('Workery online', workers.online !== undefined ? String(workers.online) : '—'),
      settingsValue('Workery oczekiwane', workers.expected !== undefined ? String(workers.expected) : '—'),
      settingsValue('Limit równoległych zadań', String(parallelLimit)),
      settingsValue('Efektywny limit', workers.online !== undefined ? String(effectiveParallelism) : '—')),
    workerCapacityShortfall
      ? node('p', {
          class: 'muted',
          text: workerReconciliationSupported
            ? 'Pula workerów jest mniejsza niż ustawiony limit. Zadania nie osiągną pełnej równoległości, dopóki pula nie zostanie dostosowana.'
            : 'Pula workerów jest mniejsza niż ustawiony limit. W trybie ' + installMode + ' liczbą workerów zarządza orkiestrator, więc CloudPortal nie skaluje ich z tego panelu.',
        })
      : null,
    node('div', { class: 'settings-card-actions' },
      button('Odśwież stan', () => settingsView()),
      allowed('settings.update') && workerCapacityShortfall && workerReconciliationSupported
        ? button('Dostosuj workery', () => reconcileWorkerCapacity().catch(error => toast(error.message, 'error')), 'primary')
        : null,
      allowed('settings.update') ? button('Zmień równoległość', () => navigate('/admin/settings/execution')) : null)
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
    'Globalna polityka domyślna. Projekt i pojedynczy Blueprint mogą ją nadpisać.',
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
      ),
      settingsValue(
        'Timeout ręcznego approval',
        String(blueprintSettings.approval_timeout_hours || 48) + ' h'
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
                  approval_timeout_hours: blueprintSettings.approval_timeout_hours || 48,
                },
              });
              toast('Polityka wykonywania Blueprintów została zapisana.');
              settingsView();
            },
            blueprintSettings.auto_approve_for_executors ? 'danger' : 'primary'
          ),
          button('Zmień timeout', () => navigate('/admin/settings/blueprints/approval'))
        )
      : null
  );

  const platforms = settingsCard(
    'server',
    'Platformy',
    'Globalnie włączaj i wyłączaj providery infrastruktury.',
    node('div', { class: 'settings-values' },
      settingsValue('Włączone', enabledPlatforms.map(item => item.label || item.name).join(', ') || 'Brak'),
      settingsValue('Aktywne typy', String(enabledPlatforms.length)),
      settingsValue('Dostępne typy', String(platformItems.length))),
    node('div', { class: 'settings-card-actions' },
      button('Zarządzaj platformami', () => navigate('/admin/settings/platforms'), 'primary'))
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
      allowed('roles.read') ? button('Role i dostęp', () => navigate('roles')) : null,
      allowed('tokens.read') ? button('Tokeny API', () => navigate('tokens')) : null)
  );

  const ssoActions = [];
  if (allowed('settings.update')) {
    ssoActions.push(button('Testuj OIDC', () => testSsoConnection().catch(error => toast(error.message, 'error'))));
    ssoActions.push(button('Konfiguruj SSO', () => navigate('/admin/settings/sso'), 'primary'));
  }
  const sso = settingsCard(
    'shield',
    'SSO / OIDC',
    'Logowanie przez zewnętrzny Identity Provider z Authorization Code + PKCE. OpenSSO jest obsługiwany bezpośrednio.',
    node('div', { class: 'settings-values' },
      settingsValue('Status', ssoConfig.enabled ? 'Włączone' : 'Wyłączone'),
      settingsValue('Dostawca', ssoConfig.provider_name || 'OpenSSO'),
      settingsValue('Issuer', ssoConfig.issuer || 'Nie skonfigurowano', 'mono'),
      settingsValue('Client ID', ssoConfig.client_id || 'Nie skonfigurowano', 'mono'),
      settingsValue('Client secret', ssoConfig.client_secret_configured ? 'Skonfigurowany' : 'Brak'),
      settingsValue('Token auth', ssoConfig.token_endpoint_auth_method || 'client_secret_post')),
    ssoActions.length ? node('div', { class: 'settings-card-actions' }, ssoActions) : null
  );

  const ldapActions = [];
  if (allowed('settings.update')) {
    ldapActions.push(button('Testuj połączenie', testLdap));
    ldapActions.push(button('Konfiguruj LDAP', () => navigate('/admin/settings/ldap'), 'primary'));
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
    node('div', { class: 'settings-grid' }, appearance, account, system, updates, platforms, blueprints, security, sso),
    ldap
  );
}

registerRoutedForm({
  id: 'settings-platforms',
  pattern: /^\/admin\/settings\/platforms$/,
  parent: 'settings',
  permission: 'settings.read',
  label: 'Platformy',
}, platformSettingsView);
registerRoutedForm({
  id: 'settings-sso',
  pattern: /^\/admin\/settings\/sso$/,
  parent: 'settings',
  permission: 'settings.update',
  label: 'Ustawienia',
}, async () => ssoSettingsForm(await api('/settings/sso')));
registerRoutedForm({
  id: 'settings-ldap',
  pattern: /^\/admin\/settings\/ldap$/,
  parent: 'settings',
  permission: 'settings.update',
  label: 'Ustawienia',
}, async () => ldapSettingsForm(await api('/settings/ldap')));
registerRoutedForm({
  id: 'settings-ldap-diagnostics',
  pattern: /^\/admin\/settings\/ldap\/diagnostics$/,
  parent: 'settings',
  permission: 'settings.update',
  label: 'Ustawienia',
}, async () => ldapDiagnosticsForm(await api('/settings/ldap')));
registerRoutedForm({
  id: 'settings-execution',
  pattern: /^\/admin\/settings\/execution$/,
  parent: 'settings',
  permission: 'settings.update',
  label: 'Ustawienia',
}, async () => {
  const execution = await api('/settings/execution');
  executionParallelForm(Number(execution.max_parallel_jobs || 10));
});
registerRoutedForm({
  id: 'settings-blueprint-approval',
  pattern: /^\/admin\/settings\/blueprints\/approval$/,
  parent: 'settings',
  permission: 'settings.update',
  label: 'Ustawienia',
}, async () => blueprintApprovalTimeoutForm(await api('/settings/blueprints')));
registerView({ id: 'settings', label: 'Ustawienia', icon: 'S', permission: 'settings.read', order: 150 }, settingsView);
})();
