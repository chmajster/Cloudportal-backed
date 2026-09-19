'use strict';

(() => {
function credentialSecretField(spec, enabled) {
  const wrapper = field(spec.label, 'secret_' + spec.key, {
    type: spec.type || 'text', tag: spec.tag, required: enabled && spec.required,
    value: enabled ? (spec.value || '') : '', placeholder: spec.placeholder,
    autocomplete: spec.autocomplete || 'off', wide: spec.wide, help: spec.help,
  });
  wrapper.classList.add('credential-secret-field');
  const input = wrapper.querySelector('input,textarea');
  input.dataset.secretKey = spec.key;
  input.disabled = !enabled;
  return wrapper;
}

function renderCredentialSecretFields(container, config, authMode, enabled) {
  const mode = config.authModes[authMode] || config.authModes[config.defaultAuth];
  container.replaceChildren(...mode.fields.map(spec => credentialSecretField(spec, enabled)));
}

function splitProxmoxEndpoint(value) {
  const raw = String(value || '').trim().replace(/\/$/, '');
  if (!raw) return { address: '', port: 8006 };

  if (raw.includes('://')) {
    try {
      const parsed = new URL(raw);
      const address = parsed.protocol + '//' + parsed.hostname.replace(/^\[(.*)\]$/, '[$1]');
      const defaultPort = parsed.protocol === 'https:' ? 443 : parsed.protocol === 'http:' ? 80 : 8006;
      return { address, port: Number(parsed.port || defaultPort) };
    } catch {
      return { address: raw, port: 8006 };
    }
  }

  const bracketed = raw.match(/^(\[[^\]]+\])(?::(\d+))?$/);
  if (bracketed) return { address: bracketed[1], port: Number(bracketed[2] || 8006) };

  const hostPort = raw.match(/^(.+):(\d+)$/);
  if (hostPort && !hostPort[1].includes(':')) {
    return { address: hostPort[1], port: Number(hostPort[2]) };
  }
  return { address: raw, port: 8006 };
}

function buildProxmoxEndpoint(address, port) {
  const parsed = splitProxmoxEndpoint(address);
  const selectedPort = Number(port || parsed.port || 8006);
  if (!Number.isInteger(selectedPort) || selectedPort < 1 || selectedPort > 65535) {
    throw new Error('Port Proxmox musi być liczbą od 1 do 65535.');
  }

  let base = parsed.address.trim().replace(/\/$/, '');
  if (!base) throw new Error('Podaj adres Proxmox.');
  if (base.includes('://')) {
    const url = new URL(base);
    url.port = String(selectedPort);
    return url.origin;
  }
  if (base.includes(':') && !base.startsWith('[')) base = '[' + base + ']';
  return base + ':' + selectedPort;
}

function splitProxmoxUsername(value) {
  const raw = String(value || '').trim();
  if (!raw) return { username: '', realm: 'pam' };
  const separator = raw.lastIndexOf('@');
  if (separator <= 0 || separator === raw.length - 1) {
    return { username: raw, realm: 'pam' };
  }
  return {
    username: raw.slice(0, separator),
    realm: raw.slice(separator + 1),
  };
}

function buildProxmoxUsername(username, realm) {
  const user = String(username || '').trim();
  const selectedRealm = String(realm || 'pam').trim();
  if (!user) throw new Error('Podaj użytkownika Proxmox.');
  if (!selectedRealm) throw new Error('Podaj realm Proxmox.');
  if (user.includes('@')) {
    const parsed = splitProxmoxUsername(user);
    return parsed.username + '@' + (parsed.realm || selectedRealm);
  }
  return user + '@' + selectedRealm;
}


function proxmoxAuthModeLabel(mode) {
  return mode === 'token' ? 'Token API' : 'Login i hasło';
}

function proxmoxDuplicateTokenDetail(error) {
  const detail = error?.data?.detail;
  if (error?.status !== 409 || !detail || typeof detail !== 'object') return null;
  return detail.code === 'proxmox_token_duplicate' ? detail : null;
}

function applyProxmoxDuplicateTokenSuggestion(form, error) {
  const detail = proxmoxDuplicateTokenDetail(error);
  if (!detail) return null;
  const tokenInput = form.querySelector('[data-secret-key="token_name"]');
  const suggested = String(detail.suggested_token_name || '').trim();
  if (tokenInput && suggested) {
    tokenInput.value = suggested;
    tokenInput.focus();
    tokenInput.select();
  }
  return {
    message: detail.message || 'Token API Proxmox o tej nazwie już istnieje.',
    suggested,
  };
}


function setProxmoxTokenVerificationState(form, active, message = '') {
  let status = form.querySelector('.proxmox-token-verification');
  if (!status && active) {
    status = node('div', { class: 'proxmox-token-verification', role: 'status', 'aria-live': 'polite' },
      node('span', { class: 'proxmox-token-verification-spinner', 'aria-hidden': 'true' }),
      node('span', { class: 'proxmox-token-verification-text' }));
    const secretPanel = form.querySelector('.credential-secret-panel');
    (secretPanel || form).append(status);
  }
  if (!status) return;
  status.hidden = !active;
  status.classList.toggle('is-checking', active);
  const text = status.querySelector('.proxmox-token-verification-text');
  if (text) text.textContent = message;
}

function proxmoxTokenRejectedDetail(error) {
  const detail = error?.data?.detail;
  if (!detail || typeof detail !== 'object') return null;
  if (!['proxmox_token_create_rejected', 'proxmox_token_duplicate_check_failed'].includes(detail.code)) return null;
  return detail;
}


function renderProxmoxConnectionResult(container, result = null, error = null) {
  container.hidden = false;
  container.classList.toggle('success', Boolean(result));
  container.classList.toggle('error', Boolean(error));
  if (error) {
    container.replaceChildren(
      node('div', { class: 'credential-connection-result-header' },
        badge('Błąd', 'danger'),
        node('strong', { text: 'Nie udało się nawiązać połączenia z Proxmox VE' })),
      node('p', { class: 'credential-connection-message', text: error.message || 'Test połączenia nie powiódł się.' }),
      node('div', { class: 'credential-connection-hints' },
        node('strong', { text: 'Sprawdź kolejno:' }),
        node('ol', {},
          node('li', { text: 'adres IP / hostname, port i protokół HTTP/HTTPS;' }),
          node('li', { text: 'czy API Proxmox odpowiada na wskazanym porcie;' }),
          node('li', { text: 'użytkownika oraz realm, np. root@pam;' }),
          node('li', { text: 'hasło albo Token ID i Token secret;' }),
          node('li', { text: 'certyfikat TLS oraz ustawienie akceptacji certyfikatu self-signed;' }),
          node('li', { text: 'uprawnienia konta, jeśli Cloudportal ma utworzyć nowy token API.' }))));
    return;
  }

  const endpoint = result?.endpoint || '';
  let protocol = '—';
  let port = '—';
  try {
    const parsed = new URL(endpoint);
    protocol = parsed.protocol.replace(':', '').toUpperCase();
    port = parsed.port || (parsed.protocol === 'https:' ? '443' : '80');
  } catch { /* Endpoint is already validated by the backend. */ }

  const rows = [
    ['Status', 'Połączenie działa poprawnie'],
    ['Endpoint', endpoint || '—'],
    ['Protokół', protocol],
    ['Port', port],
    ['Użytkownik', result?.username || '—'],
    ['Uwierzytelnienie', proxmoxAuthModeLabel(result?.auth_mode)],
    ['Wersja Proxmox VE', result?.version || 'nie podano'],
    ['Weryfikacja TLS', result?.verify_ssl === false ? 'wyłączona dla tego połączenia' : 'włączona'],
    ['Czas odpowiedzi', Number.isFinite(Number(result?.latency_ms)) ? result.latency_ms + ' ms' : '—'],
  ];
  container.replaceChildren(
    node('div', { class: 'credential-connection-result-header' },
      badge('OK', 'ok'),
      node('strong', { text: result?.message || 'Połączenie z Proxmox VE działa poprawnie.' })),
    node('div', { class: 'credential-connection-details' },
      ...rows.map(([label, value]) => node('div', { class: 'credential-connection-detail' },
        node('span', { text: label }), node('strong', { text: value })))));
}

async function testProxmoxCredentialForm(item, form) {
  const data = new FormData(form);
  const type = data.get('type');
  if (type !== 'proxmox') throw new Error('Sprawdzenie połączenia przed zapisem jest dostępne dla Proxmox VE.');

  const config = CREDENTIAL_TYPE_CONFIG.proxmox;
  const endpoint = buildProxmoxEndpoint(form.elements.endpoint?.value || '', form.elements.port?.value || config.port.default);
  const username = buildProxmoxUsername(form.elements.username?.value || '', form.elements.realm?.value || config.realm.default);
  const verifySsl = !data.has('accept_untrusted_tls');
  const sameType = Boolean(item && item.type === 'proxmox');
  const replaceSecrets = !item || !sameType || data.has('replace_secrets');
  const identityChanged = Boolean(item && sameType && (
    item.endpoint !== endpoint || item.username !== username || item.verify_ssl !== verifySsl
  ));

  if (item && sameType && !replaceSecrets && !identityChanged) {
    return api('/credentials/' + item.id + '/test', { method: 'POST' });
  }
  if (item && sameType && !replaceSecrets && identityChanged) {
    throw new Error('Aby przetestować zmieniony endpoint, użytkownika lub TLS, zaznacz „Zastąp zapisane sekrety” i podaj dane uwierzytelniające.');
  }

  const authMode = form.elements.auth_mode?.value || config.defaultAuth;
  const secrets = {};
  if (authMode === 'token') {
    secrets.token_id = form.querySelector('[data-secret-key="token_id"]')?.value || '';
    secrets.token_secret = form.querySelector('[data-secret-key="token_secret"]')?.value || '';
    if (!secrets.token_id || !secrets.token_secret) throw new Error('Podaj Token ID i Token secret.');
  } else {
    secrets.password = form.querySelector('[data-secret-key="password"]')?.value || '';
    if (!secrets.password) throw new Error('Podaj hasło Proxmox.');
  }

  return api('/credentials/proxmox/test', {
    method: 'POST',
    body: {
      name: String(data.get('name') || 'Test Proxmox').trim() || 'Test Proxmox',
      type: 'proxmox',
      endpoint,
      username,
      verify_ssl: verifySsl,
      expires_at: null,
      rotation_due_at: null,
      secrets,
    },
  });
}

function sshBootstrapHostKeyPanel() {
  const knownHosts = node('input', { type: 'hidden', name: 'ssh_bootstrap_known_hosts' });
  const fingerprint = node('strong', { class: 'mono', text: 'Nie pobrano' });
  const keyType = node('span', { class: 'muted', text: '—' });
  const confirm = checkboxField('Potwierdzam, że odcisk należy do tego serwera', 'ssh_host_key_confirmed', false);
  const confirmInput = confirm.querySelector('input');
  confirmInput.disabled = true;

  const fetchButton = button('Pobierz odcisk hosta', async event => {
    const control = event.currentTarget;
    const form = control.closest('form');
    const endpoint = String(form?.elements.endpoint?.value || '').trim();
    if (!endpoint) {
      toast('Najpierw podaj endpoint SSH, np. ssh://server.example.com:22.', 'error');
      return;
    }
    const previous = control.textContent;
    control.disabled = true;
    control.textContent = 'Pobieranie…';
    confirmInput.checked = false;
    confirmInput.disabled = true;
    knownHosts.value = '';
    fingerprint.textContent = 'Pobieranie…';
    keyType.textContent = '';
    try {
      const result = await api('/credentials/ssh/host-key', { method: 'POST', body: { endpoint } });
      knownHosts.value = result.known_hosts;
      fingerprint.textContent = result.fingerprint;
      keyType.textContent = result.key_type + ' · ' + result.host + ':' + result.port;
      confirmInput.disabled = false;
    } catch (error) {
      fingerprint.textContent = 'Nie udało się pobrać';
      keyType.textContent = error.message;
      toast(error.message, 'error');
    } finally {
      control.disabled = false;
      control.textContent = previous;
    }
  }, 'ghost');

  return node('section', { class: 'ssh-key-bootstrap-panel wide' },
    node('div', { class: 'ssh-key-bootstrap-head' },
      node('div', {},
        node('strong', { text: 'Automatyczne wygenerowanie i wgranie klucza SSH' }),
        node('p', { class: 'muted', text: 'Cloudportal pobierze klucz hosta, po Twoim potwierdzeniu wygeneruje Ed25519, zaloguje się jednorazowo hasłem i dopisze klucz publiczny do ~/.ssh/authorized_keys.' })),
      fetchButton),
    node('div', { class: 'ssh-host-fingerprint' },
      node('span', { text: 'Odcisk hosta' }),
      fingerprint,
      keyType),
    confirm,
    knownHosts,
    node('p', { class: 'field-help', text: 'Hasło służy tylko do instalacji klucza. Po udanej weryfikacji zapisywany jest wyłącznie zaszyfrowany klucz prywatny i zweryfikowany known_hosts.' }));
}

function renderCredentialDynamic(container, type, item) {
  const config = CREDENTIAL_TYPE_CONFIG[type] || CREDENTIAL_TYPE_CONFIG.other;
  const sameType = Boolean(item && item.type === type);
  const mustReplace = Boolean(item && !sameType);
  const identity = node('div', { class: 'form-grid credential-identity' });
  if (config.endpoint) {
    if (type === 'proxmox' && config.port) {
      const parsedEndpoint = splitProxmoxEndpoint(sameType ? item.endpoint : '');
      const endpointField = field(config.endpoint.label, 'endpoint', {
        value: parsedEndpoint.address,
        required: config.endpoint.required,
        placeholder: config.endpoint.placeholder,
        help: config.endpoint.help,
      });
      const portField = field(config.port.label, 'port', {
        type: 'number',
        value: parsedEndpoint.port || config.port.default,
        required: true,
        min: config.port.min,
        max: config.port.max,
        help: config.port.help,
      });
      const endpointInput = endpointField.querySelector('input');
      const portInput = portField.querySelector('input');
      endpointInput.addEventListener('change', () => {
        const parsed = splitProxmoxEndpoint(endpointInput.value);
        endpointInput.value = parsed.address;
        portInput.value = String(parsed.port || config.port.default);
      });
      identity.append(node('div', { class: 'credential-endpoint-row wide' }, endpointField, portField));
    } else {
      identity.append(field(config.endpoint.label, 'endpoint', {
        value: sameType ? item.endpoint : '', required: config.endpoint.required,
        placeholder: config.endpoint.placeholder, wide: true, help: config.endpoint.help,
      }));
    }
  }
  let proxmoxIdentityRow = null;
  if (config.username) {
    if (type === 'proxmox' && config.realm) {
      const parsedIdentity = splitProxmoxUsername(sameType ? item.username : '');
      const usernameField = field(config.username.label, 'username', {
        value: parsedIdentity.username,
        required: config.username.required,
        placeholder: config.username.placeholder,
      });
      const realmField = field(config.realm.label, 'realm', {
        value: parsedIdentity.realm || config.realm.default,
        required: config.realm.required,
        placeholder: config.realm.placeholder,
        help: config.realm.help,
      });
      const usernameInput = usernameField.querySelector('input');
      const realmInput = realmField.querySelector('input');
      usernameInput.addEventListener('change', () => {
        const parsed = splitProxmoxUsername(usernameInput.value);
        usernameInput.value = parsed.username;
        if (parsed.realm) realmInput.value = parsed.realm;
      });
      proxmoxIdentityRow = node('div', { class: 'credential-user-row wide' }, usernameField, realmField);
    } else {
      identity.append(field(config.username.label, 'username', {
        value: sameType ? item.username : '', required: config.username.required, placeholder: config.username.placeholder,
      }));
    }
  }
  if (config.tls) {
    identity.append(node('div', { class: 'credential-tls-control' },
      checkboxField('Akceptuj certyfikat self-signed / niezaufany', 'accept_untrusted_tls', sameType ? !item.verify_ssl : false),
      node('div', { class: 'field-help', text: 'Włączenie tej opcji wyłącza weryfikację CA i nazwy hosta dla tych danych dostępowych.' })));
  }

  const typeCard = node('div', { class: 'credential-type-card' },
    node('div', {}, node('strong', { text: config.label }), badge(type, 'info')),
    node('p', { text: config.description }));
  const secretPanel = node('section', { class: 'credential-secret-panel' },
    node('div', { class: 'credential-secret-header' },
      node('div', {}, node('strong', { text: item ? 'Zapisany sekret' : 'Dane uwierzytelniające' }),
        node('p', { text: item ? 'Sekrety nie są odczytywane z backendu. Włącz wymianę tylko gdy chcesz je zastąpić.' : 'Pola są wysyłane wyłącznie przy zapisie.' })),
      badge(item?.configured ? 'configured' : 'new', item?.configured ? 'ok' : 'info')));

  let replaceInput = null;
  if (item) {
    const replace = checkboxField(mustReplace ? 'Zmiana typu wymaga nowych sekretów' : 'Zastąp zapisane sekrety', 'replace_secrets', mustReplace);
    replaceInput = replace.querySelector('input');
    if (mustReplace) replaceInput.disabled = true;
    secretPanel.append(replace);
  }
  const choices = Object.entries(config.authModes).filter(([, mode]) => !mode.createOnly || !item).map(([value, mode]) => ({ value, label: mode.label }));
  const authWrapper = selectField('Metoda uwierzytelnienia', 'auth_mode', choices, config.defaultAuth, { required: true, wide: true });
  const authSelect = authWrapper.querySelector('select');
  const secretGrid = node('div', { class: 'form-grid credential-secret-grid' });
  secretPanel.append(authWrapper);
  if (proxmoxIdentityRow) secretPanel.append(proxmoxIdentityRow);
  secretPanel.append(secretGrid);
  const refresh = () => {
    const enabled = !item || mustReplace || Boolean(replaceInput?.checked);
    authSelect.disabled = !enabled;
    const authMode = authSelect.value || config.defaultAuth;
    renderCredentialSecretFields(secretGrid, config, authMode, enabled);
    if (type === 'ssh' && authMode === 'generate_key' && enabled) {
      secretGrid.append(sshBootstrapHostKeyPanel());
    }
  };
  authSelect.addEventListener('change', refresh);
  replaceInput?.addEventListener('change', refresh);
  refresh();
  container.replaceChildren(typeCard, identity, secretPanel);
}

async function credentialsView() {
  const credentials = (await api('/credentials?limit=200')).items;
  const actions = allowed('credentials.create') ? [button('Dodaj dane dostępowe', () => credentialForm(), 'primary')] : [];
  dom.content.replaceChildren(heading('Sekrety są szyfrowane i nigdy nie wracają do przeglądarki.', actions),
    table([
      { label: 'Nazwa', value: item => node('strong', { text: item.name }) },
      { label: 'Typ', value: item => badge(credentialTypeLabel(item.type), 'info') },
      { label: 'Endpoint', value: item => node('span', { class: 'mono', text: item.endpoint || 'zarządzany przez dostawcę' }) },
      { label: 'Użytkownik', value: item => item.username || '—' },
      { label: 'Sekret', value: item => item.configured ? badge('configured', 'ok') : badge('missing', 'danger') },
      { label: 'Wygasa', value: item => item.expires_at ? formatDate(item.expires_at) : '—' },
      { label: 'Rotacja', value: item => item.rotation_due_at ? formatDate(item.rotation_due_at) : '—' },
      { label: 'Sekret zmieniono', value: item => item.secret_updated_at ? formatDate(item.secret_updated_at) : '—' },
    ], credentials, item => credentialActions(item)));
}

function credentialActions(item) {
  const actions = [];
  if (allowed('credentials.test') && item.type !== 'other' && (item.type !== 'ssh' || item.endpoint)) actions.push(button('Testuj', async () => {
    try {
      const result = await api('/credentials/' + item.id + '/test', { method: 'POST' });
      toast('Połączenie działa' + (result.version ? ' (' + result.version + ')' : '') + '.');
    } catch (error) { toast(error.message, 'error'); }
  }));
  if (allowed('credentials.update')) actions.push(button('Edytuj', () => credentialForm(item)));
  if (allowed('credentials.delete')) actions.push(button('Usuń', () => confirmAction('Usuń dane dostępowe', 'Pozycja „' + item.name + '” zostanie trwale usunięta.', async () => {
    await api('/credentials/' + item.id, { method: 'DELETE' }); toast('Dane dostępowe usunięte.'); navigate('credentials');
  }), 'danger'));
  return actions;
}

function credentialForm(item = null) {
  const dynamic = node('div', { class: 'credential-dynamic wide' });
  const typeField = selectField('Typ', 'type', credentialTypeChoices(), item?.type || 'proxmox', { required: true });
  const fields = node('div', { class: 'form-grid' },
    field('Nazwa', 'name', { required: true, value: item?.name || '' }), typeField,
    field('Dane dostępowe wygasają (opcjonalnie)', 'expires_at', { type: 'datetime-local', value: item?.expires_at ? toDateTimeLocal(item.expires_at) : '' }),
    field('Rotacja wymagana do (opcjonalnie)', 'rotation_due_at', { type: 'datetime-local', value: item?.rotation_due_at ? toDateTimeLocal(item.rotation_due_at) : '' }),
    dynamic);

  let connectionCheck = null;
  let connectionResult = null;
  let connectionButton = null;
  if (allowed('credentials.test')) {
    connectionResult = node('div', { class: 'credential-connection-result', hidden: true });
    connectionButton = button('Sprawdź połączenie', async event => {
      const control = event.currentTarget;
      const form = control.closest('dialog')?.querySelector('#modal-form') || document.querySelector('#modal-form');
      if (!form) return;
      const previous = control.textContent;
      control.disabled = true;
      control.textContent = 'Sprawdzanie…';
      connectionResult.hidden = false;
      connectionResult.className = 'credential-connection-result pending';
      connectionResult.replaceChildren(
        node('div', { class: 'credential-connection-result-header' },
          badge('Test', 'info'), node('strong', { text: 'Sprawdzanie połączenia z Proxmox VE…' })),
        node('p', { class: 'credential-connection-message', text: 'Weryfikuję endpoint, TLS i uwierzytelnienie bez zapisywania sekretu.' }));
      try {
        const result = await testProxmoxCredentialForm(item, form);
        renderProxmoxConnectionResult(connectionResult, result);
      } catch (error) {
        renderProxmoxConnectionResult(connectionResult, null, error);
      } finally {
        control.disabled = false;
        control.textContent = previous;
      }
    }, 'ghost');
    connectionCheck = node('section', { class: 'credential-connection-check wide' },
      node('div', { class: 'credential-connection-check-copy' },
        node('strong', { text: 'Test połączenia przed zapisem' }),
        node('p', { text: 'Sprawdza dostęp do API, uwierzytelnienie, wersję Proxmox VE, TLS i czas odpowiedzi. Sekret nie jest zapisywany.' })),
      node('div', { class: 'credential-connection-check-actions' }, connectionButton),
      connectionResult);
    fields.append(connectionCheck);
    fields.append(checkboxField('Po zapisaniu przetestuj połączenie', 'test_after_save', false));
  }

  const typeSelect = typeField.querySelector('select');
  const render = () => {
    renderCredentialDynamic(dynamic, typeSelect.value, item);
    if (connectionCheck) {
      connectionCheck.hidden = typeSelect.value !== 'proxmox';
      connectionResult.hidden = true;
      connectionResult.replaceChildren();
    }
  };
  typeSelect.addEventListener('change', render);
  render();

  openModal({
    title: item ? 'Edytuj dane dostępowe: ' + item.name : 'Nowe dane dostępowe',
    eyebrow: 'Sekrety infrastruktury', body: fields,
    submitLabel: item ? 'Zapisz zmiany' : 'Dodaj dane dostępowe', wide: true,
    onSubmit: async (data, form) => {
      const type = data.get('type');
      const config = CREDENTIAL_TYPE_CONFIG[type] || CREDENTIAL_TYPE_CONFIG.other;
      const sameType = Boolean(item && item.type === type);
      const endpoint = type === 'proxmox'
        ? buildProxmoxEndpoint(form.elements.endpoint?.value || '', form.elements.port?.value || config.port?.default)
        : (form.elements.endpoint ? form.elements.endpoint.value : (sameType ? item.endpoint : ''));
      const username = type === 'proxmox'
        ? buildProxmoxUsername(form.elements.username?.value || '', form.elements.realm?.value || config.realm?.default)
        : (form.elements.username ? form.elements.username.value : (sameType ? item.username : ''));
      const verifySsl = config.tls ? !data.has('accept_untrusted_tls') : (sameType ? item.verify_ssl : true);
      const replaceSecrets = !item || !sameType || data.has('replace_secrets');
      const identityChanged = Boolean(item && (item.type !== type || item.endpoint !== endpoint || item.username !== username || item.verify_ssl !== verifySsl));
      if (identityChanged && !replaceSecrets) throw new Error('Zmiana typu, endpointu, użytkownika lub TLS wymaga zastąpienia sekretów.');

      const payload = {
        name: data.get('name'), type, endpoint, username, verify_ssl: verifySsl,
        expires_at: data.get('expires_at') ? new Date(data.get('expires_at')).toISOString() : null,
        rotation_due_at: data.get('rotation_due_at') ? new Date(data.get('rotation_due_at')).toISOString() : null,
      };
      const authMode = form.elements.auth_mode?.value || config.defaultAuth;
      if (!item && type === 'proxmox' && authMode === 'generate_token') {
        const password = form.querySelector('[data-secret-key="password"]')?.value || '';
        const tokenName = form.querySelector('[data-secret-key="token_name"]')?.value || '';
        if (!password || !tokenName) throw new Error('Podaj hasło Proxmox i nazwę tokenu.');
        let saved;
        setProxmoxTokenVerificationState(
          form,
          true,
          'Tworzenie tokenu. Jeśli Proxmox zwróci HTTP 400, Cloudportal sprawdzi, czy nazwa tokenu jest duplikatem…'
        );
        try {
          saved = await api('/credentials/proxmox/bootstrap', { method: 'POST', body: {
            name: payload.name, endpoint, username, password, token_name: tokenName,
            verify_ssl: verifySsl, privilege_separation: false,
            expires_at: payload.expires_at, rotation_due_at: payload.rotation_due_at,
          }});
        } catch (error) {
          const duplicate = applyProxmoxDuplicateTokenSuggestion(form, error);
          if (duplicate) {
            const suggestion = duplicate.suggested
              ? ` Proponowana nowa nazwa: „${duplicate.suggested}”. Została wpisana do formularza — zatwierdź ponownie, aby jej użyć.`
              : ' Podaj inną nazwę tokenu i spróbuj ponownie.';
            throw new Error(duplicate.message + ' Duplikat został potwierdzony przez odczyt listy tokenów z Proxmox.' + suggestion);
          }
          const rejected = proxmoxTokenRejectedDetail(error);
          if (rejected?.code === 'proxmox_token_create_rejected') {
            throw new Error(rejected.message || 'Proxmox odrzucił utworzenie tokenu. Sprawdzono, że nie jest to duplikat nazwy.');
          }
          if (rejected?.code === 'proxmox_token_duplicate_check_failed') {
            throw new Error(rejected.message || 'Nie udało się potwierdzić, czy błąd HTTP 400 oznacza duplikat tokenu.');
          }
          throw error;
        } finally {
          setProxmoxTokenVerificationState(form, false);
        }
        if (data.has('test_after_save')) {
          try {
            const result = await api('/credentials/' + saved.id + '/test', { method: 'POST' });
            toast('Token Proxmox wygenerowany i zapisany. Test działa' + (result.version ? ' (' + result.version + ')' : '') + '.');
          } catch (error) { toast('Token wygenerowany i zapisany, ale test nie powiódł się: ' + error.message, 'error'); }
        } else toast('Token Proxmox wygenerowany i zapisany. Hasło nie zostało zachowane.');
        navigate('credentials');
        return;
      }
      if (!item && type === 'ssh' && authMode === 'generate_key') {
        const password = form.querySelector('[data-secret-key="password"]')?.value || '';
        const knownHosts = String(form.elements.ssh_bootstrap_known_hosts?.value || '').trim();
        const confirmed = Boolean(form.elements.ssh_host_key_confirmed?.checked);
        if (!endpoint || !endpoint.startsWith('ssh://')) throw new Error('Podaj endpoint SSH w formacie ssh://host:port.');
        if (!username) throw new Error('Podaj użytkownika SSH.');
        if (!password) throw new Error('Podaj hasło SSH używane do jednorazowego wgrania klucza.');
        if (!knownHosts || !confirmed) throw new Error('Pobierz odcisk hosta SSH i potwierdź go przed wygenerowaniem klucza.');

        const generated = await api('/credentials/ssh/bootstrap', { method: 'POST', body: {
          name: payload.name,
          endpoint,
          username,
          password,
          known_hosts: knownHosts,
          expires_at: payload.expires_at,
          rotation_due_at: payload.rotation_due_at,
        }});
        const saved = generated.credential;
        if (data.has('test_after_save')) {
          try {
            await api('/credentials/' + saved.id + '/test', { method: 'POST' });
            toast('Klucz SSH wygenerowany, wgrany i zweryfikowany. Hasło nie zostało zapisane.');
          } catch (error) {
            toast('Klucz został wgrany i zapisany, ale końcowy test nie powiódł się: ' + error.message, 'error');
          }
        } else {
          toast('Klucz SSH Ed25519 wygenerowany i wgrany na serwer. Hasło nie zostało zapisane.');
        }
        navigate('credentials');
        return;
      }
      if (replaceSecrets) {
        const secrets = {};
        form.querySelectorAll('[data-secret-key]').forEach(input => {
          if (!input.disabled && input.value !== '') secrets[input.dataset.secretKey] = input.value;
        });
        if (!Object.keys(secrets).length) throw new Error('Wprowadź wymagane dane uwierzytelniające.');
        payload.secrets = secrets;
      }
      const saved = await api(item ? '/credentials/' + item.id : '/credentials', { method: item ? 'PUT' : 'POST', body: payload });
      if (data.has('test_after_save') && type !== 'other' && (type !== 'ssh' || endpoint)) {
        try {
          const result = await api('/credentials/' + saved.id + '/test', { method: 'POST' });
          toast('Dane dostępowe zapisane. Test działa' + (result.version ? ' (' + result.version + ')' : '') + '.');
        } catch (error) { toast('Dane dostępowe zapisane, ale test nie powiódł się: ' + error.message, 'error'); }
      } else toast('Dane dostępowe zapisane.');
      navigate('credentials');
    },
  });
}

registerView({ id: 'credentials', label: 'Dane dostępowe', icon: 'K', permission: 'credentials.read', order: 40 }, credentialsView);
})();
