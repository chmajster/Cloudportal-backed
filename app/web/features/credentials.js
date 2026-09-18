'use strict';

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

function renderCredentialDynamic(container, type, item) {
  const config = CREDENTIAL_TYPE_CONFIG[type] || CREDENTIAL_TYPE_CONFIG.other;
  const sameType = Boolean(item && item.type === type);
  const mustReplace = Boolean(item && !sameType);
  const identity = node('div', { class: 'form-grid credential-identity' });
  if (config.endpoint) identity.append(field(config.endpoint.label, 'endpoint', {
    value: sameType ? item.endpoint : '', required: config.endpoint.required,
    placeholder: config.endpoint.placeholder, wide: true, help: config.endpoint.help,
  }));
  if (config.username) identity.append(field(config.username.label, 'username', {
    value: sameType ? item.username : '', required: config.username.required, placeholder: config.username.placeholder,
  }));
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
  secretPanel.append(authWrapper, secretGrid);
  const refresh = () => {
    const enabled = !item || mustReplace || Boolean(replaceInput?.checked);
    authSelect.disabled = !enabled;
    renderCredentialSecretFields(secretGrid, config, authSelect.value || config.defaultAuth, enabled);
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
  if (allowed('credentials.test')) fields.append(checkboxField('Po zapisaniu przetestuj połączenie', 'test_after_save', false));

  const typeSelect = typeField.querySelector('select');
  const render = () => renderCredentialDynamic(dynamic, typeSelect.value, item);
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
      const endpoint = form.elements.endpoint ? form.elements.endpoint.value : (sameType ? item.endpoint : '');
      const username = form.elements.username ? form.elements.username.value : (sameType ? item.username : '');
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
        const saved = await api('/credentials/proxmox/bootstrap', { method: 'POST', body: {
          name: payload.name, endpoint, username, password, token_name: tokenName,
          verify_ssl: verifySsl, privilege_separation: false,
          expires_at: payload.expires_at, rotation_due_at: payload.rotation_due_at,
        }});
        if (data.has('test_after_save')) {
          try {
            const result = await api('/credentials/' + saved.id + '/test', { method: 'POST' });
            toast('Token Proxmox wygenerowany i zapisany. Test działa' + (result.version ? ' (' + result.version + ')' : '') + '.');
          } catch (error) { toast('Token wygenerowany i zapisany, ale test nie powiódł się: ' + error.message, 'error'); }
        } else toast('Token Proxmox wygenerowany i zapisany. Hasło nie zostało zachowane.');
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
