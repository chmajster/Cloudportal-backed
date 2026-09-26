'use strict';

(() => {
const REQUIRED_PERMISSIONS = ['providers.read', 'vms.read', 'vms.clone', 'vms.template'];

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
    item.name || ('VM ' + item.vmid),
    (item.node || '—') + '/VMID ' + item.vmid,
    statusLabel(item.status || 'unknown'),
  ].join(' · ');
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

async function waitProxmoxTask(providerId, nodeName, upid, onPoll, timeoutMs = 60 * 60 * 1000) {
  if (!upid) return { status: 'stopped', exitstatus: 'OK' };
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const task = await api(
      '/providers/' + encodeURIComponent(providerId)
      + '/tasks/' + encodeURIComponent(nodeName)
      + '/' + encodeURIComponent(String(upid))
    );
    if (typeof onPoll === 'function') onPoll(task);
    const stopped = String(task.status || '').toLowerCase() === 'stopped' || Boolean(task.exitstatus);
    if (stopped) {
      if (String(task.exitstatus || '') !== 'OK') {
        throw new Error('Task Proxmox zakończył się błędem: ' + (task.exitstatus || 'nieznany błąd'));
      }
      return task;
    }
    await new Promise(resolve => window.setTimeout(resolve, 2000));
  }
  throw new Error('Przekroczono czas oczekiwania na task Proxmox.');
}

async function waitForTemplate(providerId, targetVmid, onPoll, timeoutMs = 20 * 60 * 1000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const result = await api('/providers/' + encodeURIComponent(providerId) + '/templates');
    const template = (result.items || []).find(item => Number(item.vmid) === Number(targetVmid));
    if (template) return template;
    if (typeof onPoll === 'function') onPoll();
    await new Promise(resolve => window.setTimeout(resolve, 3000));
  }
  throw new Error(
    'Klon został utworzony, ale nie potwierdzono konwersji do template w wymaganym czasie. '
    + 'Sprawdź VMID ' + targetVmid + ' bezpośrednio w Proxmox.'
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
  const sourceSelect = sourceField.querySelector('select');
  const targetNodeSelect = targetNodeField.querySelector('select');
  const storageSelect = storageField.querySelector('select');
  const targetVmidInput = targetVmidField.querySelector('input');
  const templateNameInput = templateNameField.querySelector('input');

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
    state.vms = (vmResult.items || []).filter(item => Number(item.template || 0) !== 1);
    state.templates = templateResult.items || [];
    state.nodes = nodeResult.items || [];

    replaceSelectOptions(
      sourceSelect,
      state.vms.map(item => ({ value: item.vmid, label: sourceVmLabel(item) })),
      state.vms.length ? null : 'Brak dostępnych VM'
    );
    replaceSelectOptions(
      targetNodeSelect,
      state.nodes.map(item => ({
        value: item.node,
        label: item.node + (item.status ? ' · ' + statusLabel(item.status) : ''),
      })),
      state.nodes.length ? null : 'Brak node'
    );
    sourceSelect.disabled = !state.vms.length;
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
    let cloneFinished = false;
    try {
      setBadge(stepClone, '1. Klonowanie', 'warning');
      setBadge(stepConvert, '2. Oczekuje', 'info');
      setBadge(stepVerify, '3. Oczekuje', 'info');
      detail.textContent = 'Źródłowa VM pozostaje bez zmian. Tworzę pełny klon VMID ' + targetVmid + '…';

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

      await waitProxmoxTask(
        state.providerId,
        source.node,
        result.task,
        task => {
          detail.textContent = 'Klonowanie: ' + statusLabel(task.status || 'running')
            + (task.exitstatus ? ' · ' + task.exitstatus : '');
        }
      );
      cloneFinished = true;
      setBadge(stepClone, '1. Klon gotowy', 'ok');
      setBadge(stepConvert, '2. Konwersja', 'warning');

      if (result.follow_up_tracked === false) {
        detail.textContent = 'Backend nie zapisał follow-upu w kolejce. Kończę konwersję bezpośrednio z tego widoku.';
        const templateTask = await api(
          '/providers/' + encodeURIComponent(state.providerId)
          + '/vms/' + encodeURIComponent(targetNode)
          + '/' + encodeURIComponent(targetVmid)
          + '/template',
          { method: 'POST', idempotent: true }
        );
        await waitProxmoxTask(state.providerId, targetNode, templateTask.task);
      }

      detail.textContent = 'Klon jest gotowy. Backend konwertuje VMID ' + targetVmid + ' do template…';
      await waitForTemplate(state.providerId, targetVmid, () => {
        detail.textContent = 'Oczekiwanie na pojawienie się template VMID ' + targetVmid + ' w Proxmox…';
      });
      setBadge(stepConvert, '2. Template gotowy', 'ok');

      const refreshed = await api('/providers/' + encodeURIComponent(state.providerId) + '/vms');
      const originalExists = (refreshed.items || []).some(item => Number(item.vmid) === Number(source.vmid));
      if (!originalExists) throw new Error(
        'Template został utworzony, ale nie udało się potwierdzić obecności źródłowej VMID ' + source.vmid + '.'
      );
      setBadge(stepVerify, '3. Oryginał dostępny', 'ok');
      detail.textContent = 'Gotowe. Template VMID ' + targetVmid
        + ' utworzony z pełnego klona. Oryginalna VMID ' + source.vmid + ' nadal istnieje i nie została przekonwertowana.';
      toast('Template utworzony z klona. Oryginalna VM pozostała dostępna.');
      await loadProvider();
      setBadge(stepClone, '1. Klon gotowy', 'ok');
      setBadge(stepConvert, '2. Template gotowy', 'ok');
      setBadge(stepVerify, '3. Oryginał dostępny', 'ok');
    } catch (error) {
      if (cloneFinished) setBadge(stepConvert, '2. Wymaga sprawdzenia', 'danger');
      else setBadge(stepClone, '1. Błąd', 'danger');
      detail.textContent = error.message;
      toast(error.message, 'error');
    } finally {
      runButton.disabled = false;
    }
  }, 'primary');

  providerSelect.addEventListener('change', () => loadProvider().catch(error => toast(error.message, 'error')));
  sourceSelect.addEventListener('change', () => syncSourceDefaults().catch(error => toast(error.message, 'error')));
  targetNodeSelect.addEventListener('change', () => loadStorages().catch(error => toast(error.message, 'error')));

  const formPanel = node('section', { class: 'panel' },
    node('div', { class: 'panel-header' },
      node('div', {},
        node('span', { class: 'tools-eyebrow', text: 'Proxmox' }),
        node('h2', { text: 'Klon VM → Template' }),
        node('p', {
          class: 'muted',
          text: 'Cloudportal tworzy pełny klon do nowego VMID. Dopiero klon jest konwertowany do template; VM źródłowa nie jest zmieniana.',
        }))),
    node('div', { class: 'form-grid' },
      providerField,
      sourceField,
      targetVmidField,
      templateNameField,
      targetNodeField,
      storageField),
    node('div', { class: 'checks wide' },
      info('Typ klona', 'Full clone — niezależny od oryginału'),
      info('Źródłowa VM', 'Pozostaje zwykłą VM'),
      info('Konwersja', 'Dotyczy wyłącznie nowego VMID')),
    node('div', { class: 'row-actions' }, runButton)
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
