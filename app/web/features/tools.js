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


function apmidTool(config) {
  const apmids = config?.apmids || [];
  return node('article', { class: 'panel tool-card' },
    node('div', { class: 'tool-card-head' },
      node('div', { class: 'tool-icon', 'aria-hidden': 'true' }, appIcon('list-check')),
      node('div', { class: 'tool-title' },
        node('span', { class: 'tool-category', text: 'Klasyfikacja VM' }),
        node('h2', { text: 'APMID' }),
        node('p', { class: 'muted', text: 'Zarządzaj listą APMID w formie prostej tabeli. Wartości są używane przez kreator VM i jako tagi Proxmox.' })),
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

function normalizeApmid(value) {
  return String(value || '').trim().toUpperCase();
}

function validateApmid(value) {
  return /^[A-Z0-9][A-Z0-9_-]{0,62}$/.test(value);
}

async function apmidView() {
  const config = await api('/settings/vm-classification');
  const canEdit = allowed('settings.update');
  const rows = node('tbody');

  function currentValues() {
    const values = [];
    rows.querySelectorAll('input[data-apmid-value]').forEach(input => {
      const value = normalizeApmid(input.value);
      if (!value) return;
      if (!validateApmid(value)) throw new Error('APMID może zawierać litery, cyfry, _ oraz -, maksymalnie 63 znaki.');
      if (values.includes(value)) throw new Error('APMID „' + value + '” występuje więcej niż raz.');
      values.push(value);
    });
    return values;
  }

  function addRow(value = '') {
    const input = node('input', {
      type: 'text',
      value: value,
      placeholder: 'np. IAASTEAM',
      maxlength: 63,
      'data-apmid-value': '',
      disabled: !canEdit,
      autocomplete: 'off',
      spellcheck: 'false',
    });
    input.addEventListener('blur', () => { input.value = normalizeApmid(input.value); });
    input.addEventListener('keydown', event => {
      if (!canEdit || event.key !== 'Enter') return;
      event.preventDefault();
      input.value = normalizeApmid(input.value);
      const row = addRow('');
      row.querySelector('input[data-apmid-value]')?.focus();
    });

    const row = node('tr', {},
      node('td', { class: 'apmid-sheet-index mono' }),
      node('td', {}, input),
      node('td', { class: 'apmid-sheet-actions' },
        canEdit ? button('Usuń', () => { row.remove(); renumber(); }, 'ghost') : null));
    rows.append(row);
    renumber();
    return row;
  }

  function renumber() {
    [...rows.children].forEach((row, index) => {
      const cell = row.querySelector('.apmid-sheet-index');
      if (cell) cell.textContent = String(index + 1);
    });
  }

  (config.apmids || []).forEach(value => addRow(value));
  if (!rows.children.length) addRow('');

  const addButton = button('Dodaj wiersz', () => {
    const row = addRow('');
    row.querySelector('input[data-apmid-value]')?.focus();
  }, 'ghost');

  const saveButton = button('Zapisz listę APMID', async () => {
    try {
      const apmids = currentValues();
      await api('/settings/vm-classification', {
        method: 'PUT',
        body: {
          environments: {
            test: config.environments?.test !== false,
            dev: config.environments?.dev !== false,
            nonprod: config.environments?.nonprod !== false,
            prod: config.environments?.prod !== false,
          },
          apmids,
        },
      });
      toast('Lista APMID zapisana.');
      await apmidView();
    } catch (error) {
      toast(error.message, 'error');
    }
  }, 'primary');

  const sheet = node('section', { class: 'panel apmid-sheet-panel' },
    node('div', { class: 'apmid-sheet-head' },
      node('div', {},
        node('span', { class: 'tools-eyebrow', text: 'Lista APMID' }),
        node('h2', { text: 'APMID' }),
        node('p', { class: 'muted', text: 'Każdy APMID jest osobnym wierszem. Edytuj komórki bezpośrednio jak w prostym arkuszu.' })),
      badge(String((config.apmids || []).length) + ' zapisanych', 'info')),
    node('div', { class: 'apmid-sheet-wrap' },
      node('table', { class: 'apmid-sheet' },
        node('thead', {},
          node('tr', {},
            node('th', { text: '#' }),
            node('th', { text: 'APMID' }),
            node('th', { text: 'Akcje' }))),
        rows)),
    canEdit
      ? node('div', { class: 'apmid-sheet-footer' }, addButton, saveButton)
      : node('div', { class: 'callout info' },
          node('strong', { text: 'Tryb tylko do odczytu' }),
          node('p', { text: 'Do edycji listy APMID wymagane jest uprawnienie settings.update.' }))
  );

  dom.content.replaceChildren(
    heading('Zarządzanie listą APMID.', [button('← Narzędzia', () => navigate('tools'))]),
    sheet
  );
}

async function toolsView() {
  const cards = [];

  if (allowed('settings.read')) {
    try {
      const vmClassification = await api('/settings/vm-classification');
      cards.push(apmidTool(vmClassification));
    } catch (error) {
      cards.push(node('article', { class: 'panel tool-card' },
        node('div', { class: 'tool-card-head' },
          node('div', { class: 'tool-icon', 'aria-hidden': 'true' }, appIcon('list-check')),
          node('div', { class: 'tool-title' },
            node('span', { class: 'tool-category', text: 'Klasyfikacja VM' }),
            node('h2', { text: 'APMID' }),
            node('p', { class: 'muted', text: 'Lista identyfikatorów aplikacji używanych przez kreator VM.' })),
          badge('Niedostępny', 'warning')),
        node('p', { class: 'tool-error muted', text: error?.message || 'Nie udało się pobrać listy APMID.' })));
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

  if (allowed('ansible.read')) cards.push(ansibleHostEntryTool());

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
  id: 'apmid',
  label: 'APMID',
  iconName: 'list-check',
  navigation: false,
  navigationParent: 'tools',
  permission: 'settings.read',
  order: 156,
}, apmidView);
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
