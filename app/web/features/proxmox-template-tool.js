'use strict';

(() => {
const REQUIRED_PERMISSIONS = ['providers.read', 'vms.read', 'vms.clone', 'vms.template', 'jobs.execute'];

function toolMetaItem(label, value, mono = false) {
  return node('div', { class: 'tool-meta-item' },
    node('span', { text: label }),
    node('strong', { class: mono ? 'mono' : '', text: value || '—' }));
}

function progressRow(label, valueNode) {
  return node('div', { class: 'check' },
    node('span', { text: label }),
    valueNode);
}

function replaceSelectOptions(select, options, placeholder = null) {
  const rows = [];
  if (placeholder !== null) rows.push(node('option', { value: '', text: placeholder }));
  options.forEach(option => rows.push(node('option', {
    value: String(option.value),
    text: option.label,
  })));
  select.replaceChildren(...rows);
}

function nextFreeVmid(vms, templates) {
  const used = new Set(
    [...vms, ...templates]
      .map(item => Number(item.vmid))
      .filter(value => Number.isInteger(value) && value >= 100)
  );
  let candidate = 100;
  while (used.has(candidate) && candidate < 999999999) candidate += 1;
  return candidate;
}

function sourceVmLabel(item) {
  return [
    String(item.vmid),
    item.name || ('vm-' + item.vmid),
    item.node || '—',
    statusLabel(item.status || 'unknown'),
  ].join(' - ');
}

function sourceVmSearchText(item) {
  return [
    item.vmid,
    'vmid ' + item.vmid,
    item.name,
    item.node,
    item.status,
    statusLabel(item.status || 'unknown'),
  ].filter(Boolean).join(' ').toLocaleLowerCase('pl-PL');
}

function cloneTemplateToolCard() {
  return node('article', { class: 'panel tool-card tool-card-featured' },
    node('div', { class: 'tool-card-head' },
      node('div', { class: 'tool-icon', 'aria-hidden': 'true' }, appIcon('server')),
      node('div', { class: 'tool-title' },
        node('span', { class: 'tool-category', text: 'Proxmox' }),
        node('h2', { text: 'VM → Template' }),
        node('p', {
          class: 'muted',
          text: 'Utwórz pełny klon istniejącej VM i automatycznie przekonwertuj tylko klona do template. Oryginalna VM pozostaje bez zmian.',
        })),
      badge('Pełny clone', 'ok')),
    node('div', { class: 'tool-meta-grid' },
      toolMetaItem('Źródło', 'Istniejąca VM'),
      toolMetaItem('Klon', 'Nowy VMID'),
      toolMetaItem('Tryb', 'Full clone'),
      toolMetaItem('Wynik', 'Proxmox template')),
    node('div', { class: 'tool-card-footer' },
      node('span', { class: 'tool-health' },
        node('span', { class: 'status-dot ok' }),
        'Oryginalny VMID nie jest konwertowany'),
      button('Utwórz template z klona', () => navigate('proxmox-template-clone'), 'primary'))
  );
}

async function proxmoxTemplateCloneView() {
  const missingPermissions = REQUIRED_PERMISSIONS.filter(permission => !allowed(permission));
  if (missingPermissions.length) {
    dom.content.replaceChildren(
      heading('VM → Template', [button('← Narzędzia', () => navigate('tools'))]),
      node('section', { class: 'panel' },
        node('h2', { text: 'Brak wymaganych uprawnień' }),
        node('p', { class: 'muted', text: missingPermissions.join(', ') }))
    );
    return;
  }

  const providers = (await api('/providers?limit=200')).items
    .filter(item => item.type === 'proxmox');
  if (!providers.length) {
    dom.content.replaceChildren(
      heading('VM → Template', [button('← Narzędzia', () => navigate('tools'))]),
      node('section', { class: 'panel empty', text: 'Brak dostępnych providerów Proxmox.' })
    );
    return;
  }

  const state = {
    providerId: String(providers[0].id),
    vms: [],
    templates: [],
    nodes: [],
    storages: [],
  };

  const providerField = selectField(
    'Provider Proxmox',
    'provider_id',
    providers.map(item => ({ value: item.id, label: item.name + ' (#' + item.id + ')' })),
    state.providerId,
    { required: true }
  );
  const sourceSearchField = field('Szukaj VM', 'source_vm_filter', {
    type: 'search',
    wide: true,
    autocomplete: 'off',
    placeholder: 'Wpisz VMID, hostname, node albo status…',
    help: 'Przykład: 102, AnsibleTower, chris, zatrzymany.',
  });
  const sourceField = selectField('Źródłowa VM', 'source_vm', [], '', {
    required: true,
    wide: true,
    help: 'Źródłowa VM nie jest konwertowana do template i nie jest usuwana.',
  });
  const targetVmidField = field('Nowy VMID klona/template', 'target_vmid', {
    type: 'number', min: 100, max: 999999999, required: true,
  });
  const templateNameField = field('Nazwa nowego template', 'template_name', {
    required: true, placeholder: 'np. ubuntu-2404-template',
  });
  const targetNodeField = selectField('Node docelowy', 'target_node', [], '', { required: true });
  const storageField = selectField('Storage docelowy', 'storage', [], '', {
    help: 'Puste = Proxmox zachowa domyślne/źródłowe rozmieszczenie storage.',
  });

  const providerSelect = providerField.querySelector('select');
  const sourceSearchInput = sourceSearchField.querySelector('input');
  const sourceSelect = sourceField.querySelector('select');
  const targetNodeSelect = targetNodeField.querySelector('select');
  const storageSelect = storageField.querySelector('select');
  const targetVmidInput = targetVmidField.querySelector('input');
  const templateNameInput = templateNameField.querySelector('input');

  const sourceMatchCount = badge('0 VM', 'info');
  const stepClone = badge('1. Oczekuje', 'info');
  const stepConvert = badge('2. Oczekuje', 'info');
  const stepVerify = badge('3. Oczekuje', 'info');
  const detail = node('p', { class: 'muted', text: 'Wybierz źródłową VM i parametry klona.' });
  const progressPanel = node('section', { class: 'panel' },
    node('div', { class: 'panel-header' },
      node('h2', { text: 'Przebieg' }),
      badge('Gotowy', 'info')),
    node('div', { class: 'checks' },
      progressRow('Klonowanie VM', stepClone),
      progressRow('Konwersja klona do template', stepConvert),
      progressRow('Weryfikacja oryginału', stepVerify)),
    detail
  );

  function setBadge(target, text, kind) {
    target.textContent = text;
    target.className = 'badge ' + (kind || '');
  }

  function selectedSource() {
    const vmid = Number(sourceSelect.value);
    return state.vms.find(item => Number(item.vmid) === vmid) || null;
  }

  function filteredSourceVms() {
    const query = String(sourceSearchInput.value || '').trim().toLocaleLowerCase('pl-PL');
    if (!query) return state.vms;
    return state.vms.filter(item => sourceVmSearchText(item).includes(query));
  }

  function renderSourceOptions({ preserveSelection = true } = {}) {
    const previous = preserveSelection ? String(sourceSelect.value || '') : '';
    const filtered = filteredSourceVms();
    replaceSelectOptions(
      sourceSelect,
      filtered.map(item => ({ value: item.vmid, label: sourceVmLabel(item) })),
      filtered.length ? null : 'Brak VM pasujących do wyszukiwania'
    );
    sourceSelect.disabled = !filtered.length;

    if (previous && filtered.some(item => String(item.vmid) === previous)) {
      sourceSelect.value = previous;
    } else if (filtered.length) {
      sourceSelect.value = String(filtered[0].vmid);
    } else {
      sourceSelect.value = '';
    }

    sourceMatchCount.textContent = filtered.length === state.vms.length
      ? filtered.length + ' VM'
      : filtered.length + ' / ' + state.vms.length + ' VM';
    sourceMatchCount.className = 'badge ' + (filtered.length ? 'info' : 'warning');
    return previous !== String(sourceSelect.value || '');
  }

  async function loadStorages() {
    const nodeName = targetNodeSelect.value;
    if (!nodeName) {
      replaceSelectOptions(storageSelect, [], 'Automatycznie');
      return;
    }
    try {
      const result = await api(
        '/providers/' + encodeURIComponent(state.providerId)
        + '/storages?node=' + encodeURIComponent(nodeName)
      );
      state.storages = (result.items || []).filter(item => item.disable !== 1 && item.enabled !== false);
      replaceSelectOptions(
        storageSelect,
        state.storages.map(item => ({
          value: item.storage,
          label: item.storage + (item.type ? ' · ' + item.type : ''),
        })),
        'Automatycznie / storage źródłowy'
      );
    } catch (error) {
      state.storages = [];
      replaceSelectOptions(storageSelect, [], 'Automatycznie / storage źródłowy');
      toast('Nie udało się pobrać storage: ' + error.message, 'warning');
    }
  }

  async function syncSourceDefaults() {
    const source = selectedSource();
    if (!source) return;
    const nodeExists = state.nodes.some(item => String(item.node) === String(source.node));
    if (nodeExists) targetNodeSelect.value = String(source.node);
    templateNameInput.value = String(source.name || ('vm-' + source.vmid)) + '-template';
    targetVmidInput.value = String(nextFreeVmid(state.vms, state.templates));
    await loadStorages();
    detail.textContent = 'Źródło: ' + sourceVmLabel(source)
      + '. Operacja utworzy niezależny full clone; źródłowa VM pozostanie dostępna.';
  }

  async function loadProvider() {
    state.providerId = String(providerSelect.value);
    setBadge(stepClone, '1. Oczekuje', 'info');
    setBadge(stepConvert, '2. Oczekuje', 'info');
    setBadge(stepVerify, '3. Oczekuje', 'info');
    detail.textContent = 'Pobieranie VM i zasobów Proxmox…';

    const [vmResult, templateResult, nodeResult] = await Promise.all([
      api('/providers/' + encodeURIComponent(state.providerId) + '/vms'),
      api('/providers/' + encodeURIComponent(state.providerId) + '/templates'),
      api('/providers/' + encodeURIComponent(state.providerId) + '/nodes'),
    ]);
    state.vms = (vmResult.items || [])
      .filter(item => Number(item.template || 0) !== 1)
      .sort((left, right) => Number(left.vmid) - Number(right.vmid));
    state.templates = templateResult.items || [];
    state.nodes = nodeResult.items || [];

    sourceSearchInput.value = '';
    renderSourceOptions({ preserveSelection: false });
    replaceSelectOptions(
      targetNodeSelect,
      state.nodes.map(item => ({
        value: item.node,
        label: item.node + (item.status ? ' · ' + statusLabel(item.status) : ''),
      })),
      state.nodes.length ? null : 'Brak node'
    );
    targetNodeSelect.disabled = !state.nodes.length;
    await syncSourceDefaults();
  }

  const runButton = button('Klonuj i utwórz template', async () => {
    const source = selectedSource();
    if (!source) {
      toast('Wybierz źródłową VM.', 'error');
      return;
    }
    const targetVmid = Number(targetVmidInput.value);
    const templateName = String(templateNameInput.value || '').trim();
    const targetNode = String(targetNodeSelect.value || '').trim();
    const storage = String(storageSelect.value || '').trim();
    const used = new Set([...state.vms, ...state.templates].map(item => Number(item.vmid)));

    if (!Number.isInteger(targetVmid) || targetVmid < 100) {
      toast('Podaj poprawny docelowy VMID.', 'error');
      return;
    }
    if (targetVmid === Number(source.vmid)) {
      toast('Docelowy VMID musi różnić się od VMID źródłowej VM.', 'error');
      return;
    }
    if (used.has(targetVmid)) {
      toast('Docelowy VMID jest już zajęty w Proxmox.', 'error');
      return;
    }
    if (!templateName || !targetNode) {
      toast('Podaj nazwę template i node docelowy.', 'error');
      return;
    }

    runButton.disabled = true;
    try {
      setBadge(stepClone, '1. Kolejka', 'warning');
      setBadge(stepConvert, '2. W zadaniu', 'info');
      setBadge(stepVerify, '3. W zadaniu', 'info');
      detail.textContent = 'Tworzę zadanie w tle. Po zapisaniu cały postęp będzie widoczny w Zadaniach.';

      const result = await api(
        '/providers/' + encodeURIComponent(state.providerId)
        + '/vms/' + encodeURIComponent(source.node)
        + '/' + encodeURIComponent(source.vmid)
        + '/clone',
        {
          method: 'POST',
          idempotent: true,
          body: {
            new_vm_id: targetVmid,
            name: templateName,
            target: targetNode,
            full: true,
            storage: storage || null,
            pool: null,
            convert_to_template: true,
          },
        }
      );

      if (!result.job?.id) throw new Error('Backend nie zwrócił identyfikatora zadania.');
      setBadge(stepClone, '1. Dodano do kolejki', 'ok');
      setBadge(stepConvert, '2. Śledź w Zadaniach', 'info');
      setBadge(stepVerify, '3. Śledź w Zadaniach', 'info');
      detail.textContent = 'Zadanie ' + short(result.job.id, 18)
        + ' działa w tle. Możesz opuścić ten widok; klonowanie, konwersja i weryfikacja będą kontynuowane przez workera.';
      toast('Klon VM → Template dodano do zadań.');
      navigate('jobs');
    } catch (error) {
      setBadge(stepClone, '1. Błąd kolejki', 'danger');
      detail.textContent = error.message;
      toast(error.message, 'error');
      runButton.disabled = false;
    }
  }, 'primary');

  providerSelect.addEventListener('change', () => loadProvider().catch(error => toast(error.message, 'error')));
  sourceSearchInput.addEventListener('input', () => {
    const selectionChanged = renderSourceOptions();
    if (selectionChanged) syncSourceDefaults().catch(error => toast(error.message, 'error'));
  });
  sourceSelect.addEventListener('change', () => syncSourceDefaults().catch(error => toast(error.message, 'error')));
  targetNodeSelect.addEventListener('change', () => loadStorages().catch(error => toast(error.message, 'error')));

  const sourcePicker = node('section', { class: 'proxmox-template-section proxmox-template-source' },
    node('div', { class: 'proxmox-template-section-head' },
      node('div', {},
        node('span', { class: 'tools-eyebrow', text: 'Krok 1' }),
        node('h3', { text: 'Wybierz źródłową VM' }),
        node('p', { class: 'muted', text: 'Wyszukaj VM po numerze, hostname, node lub statusie.' })),
      sourceMatchCount),
    node('div', { class: 'proxmox-template-source-grid' },
      providerField,
      sourceSearchField,
      sourceField));

  const targetConfig = node('section', { class: 'proxmox-template-section' },
    node('div', { class: 'proxmox-template-section-head' },
      node('div', {},
        node('span', { class: 'tools-eyebrow', text: 'Krok 2' }),
        node('h3', { text: 'Parametry nowego template' }),
        node('p', { class: 'muted', text: 'Powstanie nowy, niezależny full clone. Oryginalna VM pozostanie bez zmian.' }))),
    node('div', { class: 'form-grid' },
      targetVmidField,
      templateNameField,
      targetNodeField,
      storageField));

  const formPanel = node('section', { class: 'panel proxmox-template-tool' },
    node('div', { class: 'panel-header proxmox-template-tool-head' },
      node('div', {},
        node('span', { class: 'tools-eyebrow', text: 'Proxmox' }),
        node('h2', { text: 'Klon VM → Template' }),
        node('p', {
          class: 'muted',
          text: 'Najpierw powstaje pełny klon VM. Dopiero nowy VMID jest konwertowany do template.',
        }))),
    sourcePicker,
    targetConfig,
    node('div', { class: 'checks proxmox-template-safety' },
      info('Typ klona', 'Full clone — niezależny od oryginału'),
      info('Źródłowa VM', 'Pozostaje zwykłą VM'),
      info('Konwersja', 'Dotyczy wyłącznie nowego VMID')),
    node('div', { class: 'proxmox-template-actions' }, runButton)
  );

  dom.content.replaceChildren(
    heading('Utwórz Proxmox template z kopii istniejącej VM.', [
      button('← Narzędzia', () => navigate('tools')),
    ]),
    formPanel,
    progressPanel
  );

  await loadProvider();
}

registerExtension('proxmox-template-tool-card', () => {
  window.ProxmoxTemplateTool = Object.freeze({ card: cloneTemplateToolCard });
});

registerView({
  id: 'proxmox-template-clone',
  label: 'VM → Template',
  iconName: 'server',
  navigation: false,
  navigationParent: 'tools',
  permission: 'providers.read',
  order: 158,
}, proxmoxTemplateCloneView);
})();
