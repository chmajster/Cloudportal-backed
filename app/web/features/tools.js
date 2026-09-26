'use strict';

(() => {
function toolStatus(status) {
  const map = {
    idle: ['Gotowy', ''],
    checking: ['Sprawdzanie', 'warning'],
    update_available: ['Dostępna aktualizacja', 'info'],
    running: ['Aktualizacja trwa', 'warning'],
    success: ['Ostatnia aktualizacja OK', 'ok'],
    failed: ['Wymaga uwagi', 'danger'],
    up_to_date: ['Aktualny', 'ok'],
    local_ahead: ['Lokalny commit nowszy', 'ok'],
  };
  return map[status] || ['Status nieznany', ''];
}

function toolMeta(label, value, mono = false) {
  return node('div', { class: 'tool-meta-item' },
    node('span', { text: label }),
    node('strong', { class: mono ? 'mono' : '', text: value || '—' }));
}

function toolStatusDot(status) {
  if (status === 'failed') return 'bad';
  if (['checking', 'running', 'update_available'].includes(status)) return 'warn';
  return 'ok';
}

function toolHealthText(status, updateAvailable) {
  if (status === 'failed') return 'Updater wymaga uwagi';
  if (status === 'checking') return 'Sprawdzanie repozytorium';
  if (status === 'running') return 'Aktualizacja jest w toku';
  if (updateAvailable) return 'Nowszy commit jest dostępny';
  return 'Updater gotowy';
}

function autoUpdateTool(status) {
  const stateInfo = toolStatus(status?.status);
  const current = status?.current_version || 'nieznany';
  const target = status?.target_version || '—';
  const updateAvailable = Boolean(status?.update_available);

  return node('article', { class: 'panel tool-card tool-card-featured' },
    node('div', { class: 'tool-card-head' },
      node('div', { class: 'tool-icon', 'aria-hidden': 'true' }, appIcon('refresh')),
      node('div', { class: 'tool-title' },
        node('span', { class: 'tool-category', text: 'System' }),
        node('h2', { text: 'Auto-update' }),
        node('p', { class: 'muted', text: 'Sprawdzaj nowsze commity, uruchamiaj aktualizację i obserwuj cały proces wdrożenia.' })),
      badge(stateInfo[0], stateInfo[1])),
    node('div', { class: 'tool-meta-grid' },
      toolMeta('Commit zainstalowany', current, true),
      toolMeta('Commit kanału', target, true),
      toolMeta('Kanał Git', status?.ref || 'main'),
      toolMeta('Tryb', status?.automatic ? 'Automatyczny' : 'Ręczny')),
    node('div', { class: 'tool-card-footer' },
      node('span', { class: 'tool-health' },
        node('span', { class: 'status-dot ' + toolStatusDot(status?.status) }),
        toolHealthText(status?.status, updateAvailable)),
      button(updateAvailable ? 'Otwórz i zaktualizuj' : 'Otwórz Auto-update', () => navigate('updates'), 'primary'))
  );
}

function unavailableUpdateTool(error) {
  return node('article', { class: 'panel tool-card' },
    node('div', { class: 'tool-card-head' },
      node('div', { class: 'tool-icon', 'aria-hidden': 'true' }, appIcon('refresh')),
      node('div', { class: 'tool-title' },
        node('span', { class: 'tool-category', text: 'System' }),
        node('h2', { text: 'Auto-update' }),
        node('p', { class: 'muted', text: 'Sprawdzanie i instalacja nowych commitów Cloudportal.' })),
      badge('Status niedostępny', 'warning')),
    node('p', { class: 'tool-error muted', text: error?.message || 'Nie udało się odczytać bieżącego stanu usługi aktualizacji.' }),
    node('div', { class: 'tool-card-footer' },
      node('span', { class: 'tool-health' }, node('span', { class: 'status-dot' }), 'Stan serwisu nieznany'),
      button('Otwórz Auto-update', () => navigate('updates'), 'primary'))
  );
}

function hostnameGeneratorTool(schemes = []) {
  const active = schemes.filter(item => item.is_active);
  const next = active[0] || schemes[0] || null;
  return node('article', { class: 'panel tool-card tool-card-featured' },
    node('div', { class: 'tool-card-head' },
      node('div', { class: 'tool-icon', 'aria-hidden': 'true' }, appIcon('network')),
      node('div', { class: 'tool-title' },
        node('span', { class: 'tool-category', text: 'Automatyzacja VM' }),
        node('h2', { text: 'Generator hostname' }),
        node('p', { class: 'muted', text: 'Twórz wzorce nazw hostów, numeruj je automatycznie i przypisuj patterny do Blueprintów VM.' })),
      badge(active.length ? 'Gotowy' : 'Konfiguracja', active.length ? 'ok' : 'warning')),
    node('div', { class: 'tool-meta-grid' },
      toolMeta('Aktywne patterny', active.length),
      toolMeta('Wszystkie patterny', schemes.length),
      toolMeta('Przykładowy pattern', next?.pattern || '—', true),
      toolMeta('Następny numer', next?.next_number ?? '—')),
    node('div', { class: 'tool-card-footer' },
      node('span', { class: 'tool-health' },
        node('span', { class: 'status-dot ' + (active.length ? 'ok' : 'warn') }),
        active.length ? 'Pattern może być użyty w Blueprint' : 'Utwórz pierwszy pattern hostname'),
      button(active.length ? 'Otwórz generator' : 'Skonfiguruj generator', () => navigate('hostnames'), 'primary'))
  );
}

function ansibleHostEntryTool() {
  return node('article', { class: 'panel tool-card' },
    node('div', { class: 'tool-card-head' },
      node('div', { class: 'tool-icon', 'aria-hidden': 'true' }, appIcon('file-text')),
      node('div', { class: 'tool-title' },
        node('span', { class: 'tool-category', text: 'Ansible' }),
        node('h2', { text: 'Generator wpisu hosta' }),
        node('p', { class: 'muted', text: 'Zbuduj poprawny wpis hosta do inventory Ansible dla SSH albo WinRM i skopiuj wynik w formacie INI lub YAML.' })),
      badge('Lokalnie', 'ok')),
    node('div', { class: 'tool-meta-grid' },
      toolMeta('Formaty', 'INI / YAML'),
      toolMeta('Połączenia', 'SSH / WinRM'),
      toolMeta('Walidacja', 'alias, grupa, host_vars'),
      toolMeta('Sekrety', 'nie są zapisywane')),
    node('div', { class: 'tool-card-footer' },
      node('span', { class: 'tool-health' },
        node('span', { class: 'status-dot ok' }),
        'Generator działa wyłącznie w przeglądarce'),
      button('Otwórz generator', () => navigate('ansible-host-entry'), 'primary'))
  );
}

function ansibleInventoryToken(value) {
  const text = String(value ?? '');
  if (/^[A-Za-z0-9_./:@+\-]+$/.test(text)) return text;
  return JSON.stringify(text);
}

function ansibleYamlScalar(value) {
  return JSON.stringify(String(value ?? ''));
}

function parseAnsibleHostVariables(raw) {
  const rows = [];
  String(raw || '').split('\n').forEach((line, index) => {
    const text = line.trim();
    if (!text || text.startsWith('#')) return;
    const separator = text.indexOf('=');
    if (separator < 1) throw new Error(`Host vars, wiersz ${index + 1}: użyj formatu klucz=wartość.`);
    const key = text.slice(0, separator).trim();
    const value = text.slice(separator + 1).trim();
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) {
      throw new Error(`Host vars, wiersz ${index + 1}: „${key}” nie jest poprawną nazwą zmiennej Ansible.`);
    }
    rows.push([key, value]);
  });
  return rows;
}

function ansibleHostEntryModel(form) {
  const data = new FormData(form);
  const alias = String(data.get('alias') || '').trim();
  const address = String(data.get('address') || '').trim();
  const group = String(data.get('group') || '').trim();
  const connection = String(data.get('connection') || 'ssh');
  const username = String(data.get('username') || '').trim();
  const port = Number.parseInt(String(data.get('port') || ''), 10);

  if (!/^[A-Za-z0-9_.-]+$/.test(alias)) throw new Error('Alias hosta może zawierać tylko litery, cyfry, kropkę, podkreślenie i myślnik.');
  if (!address || /\s/.test(address)) throw new Error('Podaj adres IP lub nazwę DNS bez spacji.');
  if (group && !/^[A-Za-z0-9_.-]+$/.test(group)) throw new Error('Nazwa grupy może zawierać tylko litery, cyfry, kropkę, podkreślenie i myślnik.');
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('Port musi być liczbą od 1 do 65535.');

  return {
    alias,
    address,
    group,
    connection,
    username,
    port,
    privateKey: String(data.get('private_key') || '').trim(),
    become: data.get('become') === 'on',
    winrmTransport: String(data.get('winrm_transport') || 'ntlm'),
    ignoreWinrmCertificate: data.get('ignore_winrm_certificate') === 'on',
    variables: parseAnsibleHostVariables(data.get('host_vars')),
  };
}

function ansibleHostIni(model) {
  const parts = [
    model.alias,
    `ansible_host=${ansibleInventoryToken(model.address)}`,
  ];
  if (model.username) parts.push(`ansible_user=${ansibleInventoryToken(model.username)}`);
  parts.push(`ansible_port=${model.port}`);

  if (model.connection === 'winrm') {
    parts.push('ansible_connection=winrm');
    parts.push(`ansible_winrm_transport=${ansibleInventoryToken(model.winrmTransport)}`);
    if (model.ignoreWinrmCertificate) parts.push('ansible_winrm_server_cert_validation=ignore');
  } else {
    parts.push('ansible_connection=ssh');
    if (model.privateKey) parts.push(`ansible_ssh_private_key_file=${ansibleInventoryToken(model.privateKey)}`);
    if (model.become) parts.push('ansible_become=true');
  }

  model.variables.forEach(([key, value]) => parts.push(`${key}=${ansibleInventoryToken(value)}`));
  const entry = parts.join(' ');
  return model.group ? `[${model.group}]\n${entry}` : entry;
}

function ansibleHostYaml(model) {
  const root = model.group
    ? ['all:', '  children:', `    ${model.group}:`, '      hosts:', `        ${model.alias}:`]
    : ['all:', '  hosts:', `    ${model.alias}:`];
  const indent = model.group ? '          ' : '      ';
  root.push(`${indent}ansible_host: ${ansibleYamlScalar(model.address)}`);
  if (model.username) root.push(`${indent}ansible_user: ${ansibleYamlScalar(model.username)}`);
  root.push(`${indent}ansible_port: ${model.port}`);

  if (model.connection === 'winrm') {
    root.push(`${indent}ansible_connection: "winrm"`);
    root.push(`${indent}ansible_winrm_transport: ${ansibleYamlScalar(model.winrmTransport)}`);
    if (model.ignoreWinrmCertificate) root.push(`${indent}ansible_winrm_server_cert_validation: "ignore"`);
  } else {
    root.push(`${indent}ansible_connection: "ssh"`);
    if (model.privateKey) root.push(`${indent}ansible_ssh_private_key_file: ${ansibleYamlScalar(model.privateKey)}`);
    if (model.become) root.push(`${indent}ansible_become: true`);
  }

  model.variables.forEach(([key, value]) => root.push(`${indent}${key}: ${ansibleYamlScalar(value)}`));
  return root.join('\n');
}

function ansibleHostEntryView() {
  const alias = field('Alias hosta', 'alias', { required: true, value: 'server01', placeholder: 'np. web01' });
  const address = field('Adres IP / DNS', 'address', { required: true, value: '192.168.1.10', placeholder: 'np. 10.20.30.40' });
  const group = field('Grupa inventory', 'group', { value: 'linux', placeholder: 'np. linux' });
  const connection = selectField('Typ połączenia', 'connection', [
    { value: 'ssh', label: 'Linux / Unix — SSH' },
    { value: 'winrm', label: 'Windows — WinRM' },
  ], 'ssh');
  const username = field('Użytkownik', 'username', { value: 'ansible', placeholder: 'np. ansible' });
  const port = field('Port', 'port', { type: 'number', min: 1, max: 65535, value: 22, required: true });
  const format = selectField('Format wyniku', 'format', [
    { value: 'ini', label: 'INI — inventory.ini' },
    { value: 'yaml', label: 'YAML — inventory.yml' },
  ], 'ini');
  const hostVars = field('Dodatkowe host_vars', 'host_vars', {
    tag: 'textarea',
    wide: true,
    placeholder: 'environment=dev\napp_role=frontend',
    help: 'Jedna zmienna w wierszu, format klucz=wartość. Linie zaczynające się od # są pomijane.',
  });

  const sshOptions = node('section', { class: 'ansible-entry-options' },
    node('div', { class: 'ansible-entry-options-head' },
      node('strong', { text: 'Opcje SSH' }),
      node('span', { class: 'muted', text: 'Linux / Unix' })),
    node('div', { class: 'form-grid' },
      field('Ścieżka do klucza prywatnego', 'private_key', {
        value: '',
        placeholder: '~/.ssh/id_ed25519',
        wide: true,
        help: 'Opcjonalnie. Generator nie odczytuje ani nie zapisuje zawartości klucza.',
      }),
      checkboxField('Użyj privilege escalation (ansible_become=true)', 'become', true)));

  const winrmOptions = node('section', { class: 'ansible-entry-options', hidden: true },
    node('div', { class: 'ansible-entry-options-head' },
      node('strong', { text: 'Opcje WinRM' }),
      node('span', { class: 'muted', text: 'Windows' })),
    node('div', { class: 'form-grid' },
      selectField('Transport WinRM', 'winrm_transport', [
        { value: 'ntlm', label: 'NTLM' },
        { value: 'kerberos', label: 'Kerberos' },
        { value: 'credssp', label: 'CredSSP' },
        { value: 'basic', label: 'Basic' },
      ], 'ntlm'),
      checkboxField('Ignoruj walidację certyfikatu WinRM', 'ignore_winrm_certificate', true)));

  const form = node('form', { class: 'panel ansible-entry-form', autocomplete: 'off' },
    node('div', { class: 'ansible-entry-form-head' },
      node('div', {},
        node('span', { class: 'tools-eyebrow', text: 'Inventory Ansible' }),
        node('h2', { text: 'Parametry hosta' }),
        node('p', { class: 'muted', text: 'Uzupełnij dane. Podgląd po prawej aktualizuje się automatycznie.' }))),
    node('div', { class: 'form-grid' }, alias, address, group, connection, username, port, format, hostVars),
    sshOptions,
    winrmOptions);

  const preview = node('pre', { class: 'ansible-entry-code', 'aria-live': 'polite' });
  const validation = node('div', { class: 'ansible-entry-validation' });
  const copy = button('Kopiuj wpis', async () => {
    try {
      await copyText(preview.textContent);
      toast('Wpis Ansible skopiowany do schowka.');
    } catch (error) {
      toast(error.message, 'error');
    }
  }, 'primary');
  const reset = button('Przywróć przykład', () => {
    form.reset();
    form.elements.port.value = '22';
    syncConnection();
  });

  const resultPanel = node('section', { class: 'panel ansible-entry-preview' },
    node('div', { class: 'ansible-entry-preview-head' },
      node('div', {},
        node('span', { class: 'tools-eyebrow', text: 'Gotowy wpis' }),
        node('h2', { text: 'Podgląd inventory' }),
        node('p', { class: 'muted', text: 'Wynik można wkleić bezpośrednio do inventory.ini albo inventory.yml.' })),
      badge('Na żywo', 'ok')),
    preview,
    validation,
    node('div', { class: 'ansible-entry-preview-actions' }, reset, copy));

  function sync() {
    try {
      const model = ansibleHostEntryModel(form);
      const output = form.elements.format.value === 'yaml' ? ansibleHostYaml(model) : ansibleHostIni(model);
      preview.textContent = output;
      validation.className = 'ansible-entry-validation ok';
      validation.textContent = 'Wpis jest poprawny składniowo dla generatora.';
      copy.disabled = false;
    } catch (error) {
      preview.textContent = '# Uzupełnij lub popraw pola, aby wygenerować wpis.';
      validation.className = 'ansible-entry-validation error';
      validation.textContent = error.message;
      copy.disabled = true;
    }
  }

  function syncConnection() {
    const isWinrm = form.elements.connection.value === 'winrm';
    const currentPort = String(form.elements.port.value || '');
    sshOptions.hidden = isWinrm;
    winrmOptions.hidden = !isWinrm;
    if (isWinrm && (!currentPort || currentPort === '22')) form.elements.port.value = '5986';
    if (!isWinrm && (!currentPort || currentPort === '5986' || currentPort === '5985')) form.elements.port.value = '22';
    sync();
  }

  connection.querySelector('select').addEventListener('change', syncConnection);
  form.addEventListener('input', sync);
  form.addEventListener('change', sync);
  form.addEventListener('submit', event => event.preventDefault());

  dom.content.replaceChildren(
    heading('Generator pojedynczego wpisu hosta do inventory Ansible.', [
      button('← Narzędzia', () => navigate('tools')),
    ]),
    node('div', { class: 'ansible-entry-layout' }, form, resultPanel),
    node('section', { class: 'panel ansible-entry-help' },
      node('h2', { text: 'Co jest generowane' }),
      node('p', { class: 'muted', text: 'SSH dodaje ansible_connection=ssh, opcjonalny klucz prywatny i ansible_become. WinRM dodaje ansible_connection=winrm, transport oraz opcjonalne wyłączenie walidacji certyfikatu. Hasła i zawartość kluczy nie są przechowywane ani generowane.' }))
  );
  syncConnection();
}


function environmentTool(config) {
  const environments = config?.environments || {};
  const labels = { test: 'TEST', dev: 'DEV', nonprod: 'NONPROD', prod: 'PROD' };
  const enabled = Object.keys(labels).filter(key => environments[key] !== false);
  return node('article', { class: 'panel tool-card' },
    node('div', { class: 'tool-card-head' },
      node('div', { class: 'tool-icon', 'aria-hidden': 'true' }, appIcon('server')),
      node('div', { class: 'tool-title' },
        node('span', { class: 'tool-category', text: 'Klasyfikacja VM' }),
        node('h2', { text: 'Środowiska' }),
        node('p', { class: 'muted', text: 'Zarządzaj środowiskami dostępnymi przy tworzeniu Blueprintów i maszyn wirtualnych.' })),
      badge(enabled.length + '/4 aktywne', enabled.length ? 'ok' : 'warning')),
    node('div', { class: 'tool-meta-grid' },
      ...Object.entries(labels).map(([key, label]) => toolMeta(label, environments[key] !== false ? 'Włączone' : 'Wyłączone'))),
    node('div', { class: 'tool-card-footer' },
      node('span', { class: 'tool-health' },
        node('span', { class: 'status-dot ' + (enabled.length ? 'ok' : 'warn') }),
        enabled.length ? 'Środowiska są dostępne w kreatorze VM' : 'Włącz co najmniej jedno środowisko'),
      button('Zarządzaj środowiskami', () => navigate('environments'), 'primary'))
  );
}

async function environmentsView() {
  const config = await api('/settings/vm-classification');
  const canEdit = allowed('settings.update');
  const labels = {
    test: ['TEST', 'Środowisko testowe'],
    dev: ['DEV', 'Środowisko deweloperskie'],
    nonprod: ['NONPROD', 'Środowisko przedprodukcyjne'],
    prod: ['PROD', 'Środowisko produkcyjne'],
  };

  const controls = node('div', { class: 'environment-manager-grid' },
    ...Object.entries(labels).map(([key, [label, description]]) => {
      const input = node('input', {
        type: 'checkbox',
        name: 'environment_' + key,
        checked: config.environments?.[key] !== false,
        disabled: !canEdit,
      });
      return node('label', { class: 'environment-manager-card' },
        input,
        node('span', { class: 'environment-manager-copy' },
          node('strong', { text: label }),
          node('small', { class: 'muted', text: description })),
        node('span', { class: 'environment-manager-state', text: input.checked ? 'Włączone' : 'Wyłączone' }));
    }));

  controls.querySelectorAll('input').forEach(input => input.addEventListener('change', () => {
    const stateLabel = input.closest('.environment-manager-card')?.querySelector('.environment-manager-state');
    if (stateLabel) stateLabel.textContent = input.checked ? 'Włączone' : 'Wyłączone';
  }));

  const form = node('form', { class: 'panel environment-manager-panel' },
    node('div', { class: 'environment-manager-head' },
      node('div', {},
        node('span', { class: 'tools-eyebrow', text: 'Klasyfikacja VM' }),
        node('h2', { text: 'Środowiska' }),
        node('p', { class: 'muted', text: 'Włączone środowiska są dostępne w kreatorze Blueprintów i podczas tworzenia VM.' })),
      canEdit ? badge('Edycja', 'ok') : badge('Tylko odczyt', 'info')),
    controls,
    canEdit
      ? node('div', { class: 'environment-manager-actions' },
          node('button', { class: 'button primary', type: 'submit', text: 'Zapisz środowiska' }))
      : node('div', { class: 'callout info' },
          node('strong', { text: 'Tryb tylko do odczytu' }),
          node('p', { text: 'Do zmiany środowisk wymagane jest uprawnienie settings.update.' }))
  );

  form.addEventListener('submit', async event => {
    event.preventDefault();
    const data = new FormData(form);
    const environments = {
      test: data.has('environment_test'),
      dev: data.has('environment_dev'),
      nonprod: data.has('environment_nonprod'),
      prod: data.has('environment_prod'),
    };
    if (!Object.values(environments).some(Boolean)) {
      toast('Włącz co najmniej jedno środowisko.', 'error');
      return;
    }
    await api('/settings/vm-classification', {
      method: 'PUT',
      body: {
        environments,
        apmids: config.apmids || [],
        hostname_defaults: config.hostname_defaults || { location: 'wro', role: 'server' },
      },
    });
    toast('Środowiska zapisane.');
    await environmentsView();
  });

  dom.content.replaceChildren(
    heading('Zarządzanie środowiskami.', [button('← Narzędzia', () => navigate('tools'))]),
    form
  );
}


function hostnameDefaultsTool(config) {
  const defaults = config?.hostname_defaults || {};
  return node('article', { class: 'panel tool-card' },
    node('div', { class: 'tool-card-head' },
      node('div', { class: 'tool-icon', 'aria-hidden': 'true' }, appIcon('network')),
      node('div', { class: 'tool-title' },
        node('span', { class: 'tool-category', text: 'Automatyzacja VM' }),
        node('h2', { text: 'Location i Role' }),
        node('p', { class: 'muted', text: 'Ustaw globalne wartości {location} i {role} używane automatycznie przez patterny hostname.' })),
      badge('Automatyczne', 'ok')),
    node('div', { class: 'tool-meta-grid' },
      toolMeta('Location', defaults.location || 'wro', true),
      toolMeta('Role', defaults.role || 'server', true)),
    node('div', { class: 'tool-card-footer' },
      node('span', { class: 'tool-health' },
        node('span', { class: 'status-dot ok' }),
        'Blueprint nie pyta o te wartości'),
      button('Konfiguruj Location i Role', () => navigate('hostname-defaults'), 'primary'))
  );
}

async function hostnameDefaultsView() {
  const config = await api('/settings/vm-classification');
  const defaults = config.hostname_defaults || { location: 'wro', role: 'server' };
  const canEdit = allowed('settings.update');

  const locationField = field('Location', 'location', {
    value: defaults.location || 'wro',
    required: true,
    placeholder: 'np. wro',
    help: 'Globalna wartość używana automatycznie dla tokenu {location}.',
  });
  const roleField = field('Role', 'role', {
    value: defaults.role || 'server',
    required: true,
    placeholder: 'np. web',
    help: 'Globalna wartość używana automatycznie dla tokenu {role}.',
  });
  if (!canEdit) {
    locationField.querySelector('input').disabled = true;
    roleField.querySelector('input').disabled = true;
  }

  const form = node('form', { class: 'panel hostname-defaults-form' },
    node('div', { class: 'hostname-defaults-head' },
      node('div', {},
        node('span', { class: 'tools-eyebrow', text: 'Domyślne tokeny hostname' }),
        node('h2', { text: 'Location i Role' }),
        node('p', { class: 'muted', text: 'Te wartości są wstawiane automatycznie do patternów hostname. Kreator Blueprintu nie wymaga ich ręcznego podawania.' }))),
    node('div', { class: 'form-grid' }, locationField, roleField),
    canEdit
      ? node('div', { class: 'hostname-defaults-actions' },
          node('button', { class: 'button primary', type: 'submit', text: 'Zapisz ustawienia' }))
      : node('div', { class: 'callout info' },
          node('strong', { text: 'Tryb tylko do odczytu' }),
          node('p', { text: 'Do zmiany Location i Role wymagane jest uprawnienie settings.update.' }))
  );

  form.addEventListener('submit', async event => {
    event.preventDefault();
    const data = new FormData(form);
    const locationValue = String(data.get('location') || '').trim().toLowerCase();
    const roleValue = String(data.get('role') || '').trim().toLowerCase();
    const valid = value => /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(value);
    if (!valid(locationValue)) {
      toast('Location musi być poprawnym fragmentem hostname: litery, cyfry i myślnik.', 'error');
      return;
    }
    if (!valid(roleValue)) {
      toast('Role musi być poprawnym fragmentem hostname: litery, cyfry i myślnik.', 'error');
      return;
    }
    await api('/settings/vm-classification', {
      method: 'PUT',
      body: {
        environments: config.environments,
        apmids: config.apmids || [],
        hostname_defaults: { location: locationValue, role: roleValue },
      },
    });
    toast('Domyślne Location i Role zapisane.');
    await hostnameDefaultsView();
  });

  dom.content.replaceChildren(
    heading('Domyślne wartości hostname.', [button('← Narzędzia', () => navigate('tools'))]),
    form
  );
}


function apmidTool(config) {
  const apmids = config?.apmids || [];
  return node('article', { class: 'panel tool-card' },
    node('div', { class: 'tool-card-head' },
      node('div', { class: 'tool-icon', 'aria-hidden': 'true' }, appIcon('list-check')),
      node('div', { class: 'tool-title' },
        node('span', { class: 'tool-category', text: 'Klasyfikacja VM' }),
        node('h2', { text: 'APMID' }),
        node('p', { class: 'muted', text: 'Zarządzaj listą APMID używaną przez kreator VM i automatyczne tagi Proxmox.' })),
      badge(apmids.length ? String(apmids.length) + ' pozycji' : 'Pusta lista', apmids.length ? 'ok' : 'warning')),
    node('div', { class: 'tool-meta-grid' },
      toolMeta('Liczba APMID', String(apmids.length)),
      toolMeta('Przykład', apmids[0] || '—', true)),
    node('div', { class: 'tool-card-footer' },
      node('span', { class: 'tool-health' },
        node('span', { class: 'status-dot ' + (apmids.length ? 'ok' : 'warn') }),
        apmids.length ? 'Lista gotowa do użycia' : 'Dodaj pierwszy APMID'),
      button('Otwórz listę APMID', () => navigate('apmid'), 'primary'))
  );
}

const IMMUTABLE_APMIDS = new Set(['LEO']);

function normalizeApmid(value) {
  return String(value || '').trim().toUpperCase();
}

function validateApmid(value) {
  return /^[A-Z0-9][A-Z0-9_-]{0,62}$/.test(value);
}

async function saveApmids(config, apmids, message) {
  const requested = apmids.map(normalizeApmid).filter(Boolean);
  requested.forEach(value => {
    if (!validateApmid(value)) {
      throw new Error('APMID może zawierać litery, cyfry, _ oraz -, maksymalnie 63 znaki.');
    }
  });
  if (new Set(requested).size !== requested.length) {
    throw new Error('Lista APMID zawiera duplikaty.');
  }
  const normalized = [
    'LEO',
    ...requested.filter(value => !IMMUTABLE_APMIDS.has(value)),
  ];

  await api('/settings/vm-classification', {
    method: 'PUT',
    body: {
      environments: {
        test: config.environments?.test !== false,
        dev: config.environments?.dev !== false,
        nonprod: config.environments?.nonprod !== false,
        prod: config.environments?.prod !== false,
      },
      apmids: normalized,
    },
  });
  toast(message);
  await apmidView();
}

function apmidInputForm(initialValue, submitLabel, onSubmit, onCancel) {
  const input = node('input', {
    type: 'text',
    name: 'apmid',
    value: initialValue || '',
    placeholder: 'np. IAASTEAM',
    maxlength: 63,
    autocomplete: 'off',
    spellcheck: 'false',
    required: true,
  });
  const form = node('form', { class: 'apmid-inline-form' },
    input,
    node('div', { class: 'apmid-inline-actions' },
      button('Anuluj', onCancel, 'ghost'),
      node('button', { class: 'button primary', type: 'submit', text: submitLabel })));

  form.addEventListener('submit', async event => {
    event.preventDefault();
    try {
      const value = normalizeApmid(input.value);
      if (!validateApmid(value)) {
        throw new Error('APMID może zawierać litery, cyfry, _ oraz -, maksymalnie 63 znaki.');
      }
      await onSubmit(value);
    } catch (error) {
      toast(error.message, 'error');
    }
  });

  requestAnimationFrame(() => input.focus());
  return form;
}

async function apmidView() {
  const config = await api('/settings/vm-classification');
  const canEdit = allowed('settings.update');
  const apmids = [...(config.apmids || [])];
  const list = node('div', { class: 'apmid-list' });
  const addArea = node('div', { class: 'apmid-add-area', hidden: true });

  const showAddForm = () => {
    addArea.hidden = false;
    addArea.replaceChildren(apmidInputForm('', 'Dodaj APMID', async value => {
      if (apmids.includes(value)) throw new Error('APMID „' + value + '” już istnieje.');
      await saveApmids(config, [...apmids, value], 'APMID dodany.');
    }, () => {
      addArea.hidden = true;
      addArea.replaceChildren();
    }));
  };

  const renderList = () => {
    list.replaceChildren();
    if (!apmids.length) {
      list.append(node('div', { class: 'apmid-empty' },
        node('strong', { text: 'Brak APMID' }),
        node('span', { class: 'muted', text: 'Dodaj pierwszy APMID, aby był dostępny w kreatorze VM.' })));
      return;
    }

    apmids.forEach((value, index) => {
      const locked = IMMUTABLE_APMIDS.has(value);
      const valueBox = node('div', { class: 'apmid-list-value' },
        node('strong', { class: 'mono', text: value }),
        node('small', { class: 'muted', text: locked
          ? 'Domyślny APMID systemowy — nie można edytować ani usunąć'
          : 'Dostępny w kreatorze VM' }));
      const actions = node('div', { class: 'apmid-list-actions' });

      const row = node('div', { class: 'apmid-list-row' },
        node('div', { class: 'apmid-list-index mono', text: String(index + 1) }),
        valueBox,
        actions);

      if (canEdit && !locked) {
        actions.append(
          button('Edytuj', () => {
            valueBox.replaceChildren(apmidInputForm(value, 'Zapisz', async nextValue => {
              const duplicate = apmids.some((item, itemIndex) => itemIndex !== index && item === nextValue);
              if (duplicate) throw new Error('APMID „' + nextValue + '” już istnieje.');
              const next = [...apmids];
              next[index] = nextValue;
              await saveApmids(config, next, 'APMID zaktualizowany.');
            }, () => renderList()));
          }, 'ghost'),
          button('Usuń', () => confirmAction(
            'Usuń APMID',
            'Usunąć APMID „' + value + '” z listy?',
            async () => {
              await saveApmids(config, apmids.filter((_, itemIndex) => itemIndex !== index), 'APMID usunięty.');
            },
            'danger'
          ), 'ghost')
        );
      }

      if (locked) actions.append(badge('Domyślny · zablokowany', 'info'));

      list.append(row);
    });
  };

  renderList();

  const panel = node('section', { class: 'panel apmid-list-panel' },
    node('div', { class: 'apmid-list-head' },
      node('div', {},
        node('span', { class: 'tools-eyebrow', text: 'Lista APMID' }),
        node('h2', { text: 'APMID' }),
        node('p', { class: 'muted', text: 'LEO jest domyślnym APMID systemowym i jest zablokowany. Pozostałe APMID możesz dodawać, edytować i usuwać.' })),
      canEdit ? button('Dodaj APMID', showAddForm, 'primary') : badge('Tylko odczyt', 'info')),
    addArea,
    list
  );

  dom.content.replaceChildren(
    heading('Zarządzanie listą APMID.', [button('← Narzędzia', () => navigate('tools'))]),
    panel
  );
}


function blueprintAvatarPreview(item, className = 'blueprint-avatar-preview') {
  if (!item?.data_uri) {
    return node('span', { class: className + ' empty', 'aria-hidden': 'true' }, appIcon('box'));
  }
  return node('span', { class: className },
    node('img', {
      src: item.data_uri,
      alt: '',
      loading: 'lazy',
      decoding: 'async',
    }));
}

function blueprintAvatarTool(avatars = []) {
  return node('article', { class: 'panel tool-card' },
    node('div', { class: 'tool-card-head' },
      node('div', { class: 'tool-icon', 'aria-hidden': 'true' },
        avatars[0] ? blueprintAvatarPreview(avatars[0], 'tool-avatar-preview') : appIcon('box')),
      node('div', { class: 'tool-title' },
        node('span', { class: 'tool-category', text: 'Blueprinty' }),
        node('h2', { text: 'Awatary Blueprintów' }),
        node('p', { class: 'muted', text: 'Zarządzaj ikonami ICO używanymi na kartach produktów i w kreatorze Blueprintu.' })),
      badge(avatars.length ? String(avatars.length) + ' ikon' : 'Brak ikon', avatars.length ? 'ok' : 'warning')),
    node('div', { class: 'tool-meta-grid' },
      toolMeta('Dostępne awatary', String(avatars.length)),
      toolMeta('Format', 'image/x-icon · base64')),
    node('div', { class: 'tool-card-footer' },
      node('span', { class: 'tool-health' },
        node('span', { class: 'status-dot ' + (avatars.length ? 'ok' : 'warn') }),
        avatars.length ? 'Awatary gotowe do użycia' : 'Dodaj pierwszy avatar Blueprintu'),
      button('Zarządzaj awatarami', () => navigate('blueprint-avatars'), 'primary')));
}

function blueprintAvatarForm(item = null) {
  const editing = Boolean(item);
  const idField = field('ID awatara', 'id', {
    required: true,
    value: item?.id || '',
    placeholder: 'ubuntu',
    help: 'Stały identyfikator używany przez Blueprint, np. ubuntu lub windows-server.',
  });
  if (editing) idField.querySelector('input').disabled = true;

  const nameField = field('Nazwa', 'name', {
    required: true,
    value: item?.name || '',
    placeholder: 'Ubuntu',
  });
  const dataField = field('x-icon;base64', 'data_uri', {
    tag: 'textarea',
    required: true,
    wide: true,
    value: item?.data_uri || '',
    placeholder: 'data:image/x-icon;base64,AAACAA...',
    help: 'Wklej pełny data URI: data:image/x-icon;base64,... Maksymalny rozmiar po dekodowaniu: 128 KiB.',
  });
  const dataInput = dataField.querySelector('textarea');
  dataInput.rows = 8;

  const previewImage = node('img', {
    class: 'blueprint-avatar-form-image',
    alt: 'Podgląd awatara',
    hidden: true,
  });
  const previewStatus = node('span', { class: 'muted', text: 'Wklej poprawny data:image/x-icon;base64,...' });
  const preview = node('div', { class: 'blueprint-avatar-form-preview wide' },
    node('div', { class: 'blueprint-avatar-form-preview-box' }, previewImage),
    node('div', {},
      node('strong', { text: 'Podgląd' }),
      previewStatus));

  const refreshPreview = () => {
    const value = String(dataInput.value || '').trim();
    const validPrefix = /^(?:data:)?image\/(?:x-icon|vnd\.microsoft\.icon);base64,/i.test(value);
    if (!validPrefix) {
      previewImage.hidden = true;
      previewImage.removeAttribute('src');
      previewStatus.textContent = 'Niepoprawny prefix. Użyj data:image/x-icon;base64,...';
      return;
    }
    previewImage.hidden = false;
    previewImage.src = value.startsWith('data:') ? value : 'data:' + value;
    previewStatus.textContent = 'Podgląd z danych base64.';
  };
  dataInput.addEventListener('input', refreshPreview);
  refreshPreview();

  const body = node('div', { class: 'form-grid' },
    idField,
    nameField,
    dataField,
    preview);

  openModal({
    title: editing ? 'Edytuj avatar Blueprintu' : 'Dodaj avatar Blueprintu',
    eyebrow: 'Narzędzia · Awatary Blueprintów',
    body,
    submitLabel: editing ? 'Zapisz avatar' : 'Dodaj avatar',
    wide: true,
    onSubmit: async data => {
      const id = editing ? item.id : String(data.get('id') || '').trim();
      const payload = {
        id,
        name: String(data.get('name') || '').trim(),
        data_uri: String(data.get('data_uri') || '').trim(),
      };
      await api('/settings/blueprint-avatars' + (editing ? '/' + encodeURIComponent(item.id) : ''), {
        method: editing ? 'PUT' : 'POST',
        body: payload,
      });
      toast(editing ? 'Avatar Blueprintu zaktualizowany.' : 'Avatar Blueprintu dodany.');
      navigate('blueprint-avatars');
    },
  });
}

async function blueprintAvatarsView() {
  const result = await api('/settings/blueprint-avatars');
  const avatars = result.items || [];
  const canEdit = allowed('settings.update');

  const list = node('div', { class: 'blueprint-avatar-list' });
  if (!avatars.length) {
    list.append(node('div', { class: 'apmid-empty' },
      node('strong', { text: 'Brak awatarów Blueprintów' }),
      node('span', { class: 'muted', text: 'Dodaj pierwszy plik ICO w formacie data:image/x-icon;base64,...' })));
  } else {
    avatars.forEach(item => {
      const actions = [];
      if (canEdit) {
        actions.push(
          button('Edytuj', () => blueprintAvatarForm(item), 'ghost'),
          button('Usuń', () => confirmAction(
            'Usuń avatar Blueprintu',
            'Avatar „' + item.name + '” zostanie usunięty. Jeśli jest przypisany do Blueprintu, backend zablokuje operację.',
            async () => {
              await api('/settings/blueprint-avatars/' + encodeURIComponent(item.id), { method: 'DELETE' });
              toast('Avatar Blueprintu usunięty.');
              await blueprintAvatarsView();
            }
          ), 'danger')
        );
      }
      list.append(node('div', { class: 'blueprint-avatar-list-row' },
        blueprintAvatarPreview(item),
        node('div', { class: 'blueprint-avatar-list-copy' },
          node('strong', { text: item.name }),
          node('small', { class: 'mono muted', text: item.id }),
          node('small', { class: 'muted', text: 'image/x-icon · base64' })),
        node('div', { class: 'blueprint-avatar-list-actions' }, ...actions)));
    });
  }

  const panel = node('section', { class: 'panel blueprint-avatar-manager' },
    node('div', { class: 'apmid-list-head' },
      node('div', {},
        node('span', { class: 'tools-eyebrow', text: 'Katalog ikon' }),
        node('h2', { text: 'Awatary Blueprintów' }),
        node('p', { class: 'muted', text: 'Awatary są globalnym katalogiem ikon. Blueprint przechowuje tylko ID wybranego awatara.' })),
      canEdit ? button('Dodaj avatar', () => blueprintAvatarForm(), 'primary') : badge('Tylko odczyt', 'info')),
    list);

  dom.content.replaceChildren(
    heading('Awatary dostępne w kreatorze Blueprintu.', [button('← Narzędzia', () => navigate('tools'))]),
    panel
  );
}


function instanceBackupTool() {
  return node('article', { class: 'panel tool-card' },
    node('div', { class: 'tool-card-head' },
      node('div', { class: 'tool-icon', 'aria-hidden': 'true' }, appIcon('file-text')),
      node('div', { class: 'tool-title' },
        node('span', { class: 'tool-category', text: 'Ciągłość działania' }),
        node('h2', { text: 'Backup i migracja' }),
        node('p', { class: 'muted', text: 'Utwórz pełny backup Cloudportal-backed lub przenieś instancję na inny serwer.' })),
      badge('WEB UI', 'ok')),
    node('div', { class: 'tool-card-footer' },
      node('span', { class: 'tool-health' },
        node('span', { class: 'status-dot' }),
        'Eksport i restore .cpb'),
      button('Otwórz', () => navigate('instance-backup'), 'primary')));
}


async function toolsView() {
  const cards = [];

  if (allowed('settings.read')) {
    try {
      const vmClassification = await api('/settings/vm-classification');
      cards.push(environmentTool(vmClassification));
      cards.push(apmidTool(vmClassification));
      cards.push(hostnameDefaultsTool(vmClassification));
    } catch (error) {
      cards.push(node('article', { class: 'panel tool-card' },
        node('div', { class: 'tool-card-head' },
          node('div', { class: 'tool-icon', 'aria-hidden': 'true' }, appIcon('list-check')),
          node('div', { class: 'tool-title' },
            node('span', { class: 'tool-category', text: 'Klasyfikacja VM' }),
            node('h2', { text: 'Klasyfikacja VM' }),
            node('p', { class: 'muted', text: 'Środowiska, lista APMID oraz globalne Location i Role.' })),
          badge('Niedostępny', 'warning')),
        node('p', { class: 'tool-error muted', text: error?.message || 'Nie udało się pobrać ustawień klasyfikacji VM.' })));
    }
  }

  if (allowed('settings.read')) {
    try {
      const avatars = await api('/settings/blueprint-avatars');
      cards.push(blueprintAvatarTool(avatars.items || []));
    } catch (error) {
      cards.push(node('article', { class: 'panel tool-card' },
        node('div', { class: 'tool-card-head' },
          node('div', { class: 'tool-icon', 'aria-hidden': 'true' }, appIcon('box')),
          node('div', { class: 'tool-title' },
            node('span', { class: 'tool-category', text: 'Blueprinty' }),
            node('h2', { text: 'Awatary Blueprintów' }),
            node('p', { class: 'muted', text: 'Katalog ikon ICO dla produktów self-service.' })),
          badge('Niedostępny', 'warning')),
        node('p', { class: 'tool-error muted', text: error?.message || 'Nie udało się pobrać awatarów Blueprintów.' })));
    }
  }

  if (allowed('hostnames.read')) {
    try {
      const schemes = await api('/hostname-schemes?limit=200');
      cards.push(hostnameGeneratorTool(schemes.items || []));
    } catch (error) {
      cards.push(node('article', { class: 'panel tool-card' },
        node('div', { class: 'tool-card-head' },
          node('div', { class: 'tool-icon', 'aria-hidden': 'true' }, appIcon('network')),
          node('div', { class: 'tool-title' },
            node('span', { class: 'tool-category', text: 'Automatyzacja VM' }),
            node('h2', { text: 'Generator hostname' }),
            node('p', { class: 'muted', text: 'Tworzenie i zarządzanie patternami hostname dla Blueprintów.' })),
          badge('Niedostępny', 'warning')),
        node('p', { class: 'tool-error muted', text: error?.message || 'Nie udało się pobrać patternów hostname.' }),
        node('div', { class: 'tool-card-footer' },
          node('span', { class: 'tool-health' }, node('span', { class: 'status-dot' }), 'Stan generatora nieznany'),
          button('Otwórz generator', () => navigate('hostnames'), 'primary'))));
    }
  }

  if (
    allowed('providers.read')
    && allowed('vms.read')
    && allowed('vms.clone')
    && allowed('vms.template')
    && window.ProxmoxTemplateTool?.card
  ) {
    cards.push(window.ProxmoxTemplateTool.card());
  }

  if (allowed('ansible.read')) cards.push(ansibleHostEntryTool());
  if (allowed('instance_backups.read')) cards.push(instanceBackupTool());

  if (allowed('updates.read')) {
    try {
      const status = await api('/updates/status');
      cards.push(autoUpdateTool(status));
    } catch (error) {
      cards.push(unavailableUpdateTool(error));
    }
  }

  dom.content.replaceChildren(
    heading('Narzędzia administracyjne i serwisowe Cloudportal.'),
    node('section', { class: 'tools-hero' },
      node('div', {},
        node('span', { class: 'tools-eyebrow', text: 'Centrum narzędzi' }),
        node('h2', { text: 'Narzędzia' }),
        node('p', { class: 'muted', text: 'Operacje systemowe i automatyzacja infrastruktury dostępne dla bieżącego użytkownika.' })),
      node('div', { class: 'tools-count' },
        node('strong', { text: String(cards.length) }),
        node('span', { text: cards.length === 1 ? 'narzędzie' : 'narzędzia' }))),
    cards.length
      ? node('div', { class: 'tools-grid' }, cards)
      : node('div', { class: 'empty', text: 'Brak narzędzi dostępnych dla bieżących uprawnień.' })
  );
}

registerView({ id: 'tools', label: 'Narzędzia', iconName: 'wrench', order: 155 }, toolsView);
registerView({
  id: 'environments',
  label: 'Środowiska',
  iconName: 'server',
  navigation: false,
  navigationParent: 'tools',
  permission: 'settings.read',
  order: 156,
}, environmentsView);
registerView({
  id: 'apmid',
  label: 'APMID',
  iconName: 'list-check',
  navigation: false,
  navigationParent: 'tools',
  permission: 'settings.read',
  order: 156,
}, apmidView);
registerView({
  id: 'hostname-defaults',
  label: 'Location i Role',
  iconName: 'network',
  navigation: false,
  navigationParent: 'tools',
  permission: 'settings.read',
  order: 157,
}, hostnameDefaultsView);
registerView({
  id: 'blueprint-avatars',
  label: 'Awatary Blueprintów',
  iconName: 'box',
  navigation: false,
  navigationParent: 'tools',
  permission: 'settings.read',
  order: 157,
}, blueprintAvatarsView);
registerView({
  id: 'ansible-host-entry',
  label: 'Generator hosta Ansible',
  iconName: 'file-text',
  navigation: false,
  navigationParent: 'tools',
  permission: 'ansible.read',
  order: 156,
}, ansibleHostEntryView);
})();
