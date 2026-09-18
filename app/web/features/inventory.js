'use strict';

(() => {
async function inventoryView() {
  const [vms, resources, providerResult] = await Promise.all([
    api('/inventory/vms?refresh=true&limit=200'),
    api('/inventory/resources?limit=200'),
    allowed('providers.read') ? api('/providers?limit=200') : Promise.resolve({ items: [] }),
  ]);
  const providerNames = new Map(providerResult.items.map(provider => [Number(provider.id), provider.name]));
  const actions = allowed('inventory.import') && allowed('providers.read') ? [button('Importuj istniejącą VM', importInventoryVm, 'primary')] : [];
  dom.content.replaceChildren(
    heading('Katalog zasobów odkrytych i zarządzanych przez Terraform. Przejęcie zarządzania zawsze wykonuje import i tylko plan — bez automatycznego zastosowania zmian.', actions),
    node('section', { class: 'panel' },
      node('div', { class: 'panel-header' }, node('h2', { text: 'Maszyny Proxmox' })),
      table([
        { label: 'VM', value: item => node('div', {}, node('strong', { text: item.name || `VM ${item.vm_id}` }), node('div', { class: 'mono muted', text: `${item.node}/${item.vm_id}` })) },
        { label: 'Tryb', value: item => badge(statusLabel(item.management_mode), item.management_mode === 'terraform' ? 'ok' : 'info') },
        { label: 'Stan', value: item => badge(statusLabel(item.lifecycle_status), statusKind(item.lifecycle_status)) },
        { label: 'Platforma', value: item => providerNames.get(Number(item.provider_id)) || `#${item.provider_id}` },
        { label: 'Stan w platformie', value: item => item.live ? badge(statusLabel(item.live.status || 'present'), statusKind(item.live.status)) : badge(statusLabel('missing'), 'danger') },
      ], vms.items, item => inventoryVmActions(item))
    ),
    node('section', { class: 'panel' },
      node('div', { class: 'panel-header' }, node('h2', { text: 'Zasoby zarządzane' })),
      table([
        { label: 'Nazwa', value: item => node('strong', { text: item.name }) },
        { label: 'Platforma', value: item => badge(CREDENTIAL_TYPE_CONFIG[item.provider]?.label || item.provider, 'info') },
        { label: 'External ID', class: 'mono', value: item => short(item.external_id, 26) },
        { label: 'IP', class: 'mono', value: item => item.primary_ip || '—' },
        { label: 'Status', value: item => badge(statusLabel(item.lifecycle_status), statusKind(item.lifecycle_status)) },
        { label: 'Wdrożenie', class: 'mono', value: item => short(item.deployment_id, 18) },
      ], resources.items, item => [button('Szczegóły', () => showObjectDetails(item.name, item.metadata_json || {}, 'Zasób zarządzany'))])
    )
  );
}

function inventoryVmActions(item) {
  const actions = [];
  if (allowed('vms.read') && item.lifecycle_status === 'active') actions.push(button('VM', () => openVmManager(item), 'primary'));
  if (allowed('inventory.update')) actions.push(button('Odśwież stan', async () => {
    await api(`/inventory/vms/${item.id}/reconcile`, { method: 'POST' });
    toast('Stan zasobu odświeżony.');
    navigate('inventory');
  }));
  if (allowed('deployments.adopt') && allowed('terraform.read') && item.management_mode === 'external' && item.lifecycle_status === 'active' && !item.deployment_id) actions.push(button('Przejmij', () => adoptInventoryVm(item)));
  if (allowed('inventory.delete') && (item.management_mode !== 'terraform' || item.lifecycle_status === 'destroyed')) actions.push(button('Usuń z katalogu', () => confirmAction('Usuń z katalogu', 'Zasób nie zostanie usunięty z platformy źródłowej.', async () => {
    await api(`/inventory/vms/${item.id}`, { method: 'DELETE' });
    navigate('inventory');
  }), 'danger'));
  return actions;
}

async function importInventoryVm() {
  const providers = (await api('/providers?limit=200')).items.filter(item => item.type === 'proxmox');
  const fields = node('div', { class: 'form-grid' },
    selectField('Platforma Proxmox', 'provider_id', providers.map(item => ({ value: item.id, label: `${item.name} (#${item.id})` })), '', { required: true, placeholder: 'Wybierz platformę' }),
    field('VMID', 'vm_id', { type: 'number', min: 100, required: true }));
  openModal({ title: 'Importuj istniejącą VM', eyebrow: 'Zasoby', body: fields, submitLabel: 'Dodaj do katalogu', onSubmit: async data => {
    await api('/inventory/vms/import', { method: 'POST', idempotent: true, body: {
      provider_id: Number(data.get('provider_id')), vm_id: Number(data.get('vm_id')),
    } });
    toast('VM dodana do katalogu jako zasób zewnętrzny.');
    navigate('inventory');
  }});
}

async function adoptInventoryVm(item) {
  try {
    const [preview, templateResult] = await Promise.all([
      api(`/inventory/vms/${item.id}/adoption-preview`),
      api('/templates'),
    ]);
    const template = templateResult.items.find(value => value.id === preview.template.id);
    if (!template) throw new Error('Szablon wymagany do adopcji nie jest dostępny w katalogu.');

    const variables = node('div', { class: 'form-grid wide template-variable-grid' });
    renderTemplateVariables(variables, template, preview.suggested_variables || {});
    const liveEntries = Object.entries(preview.live || {}).slice(0, 16);
    const liveGrid = node('div', { class: 'checks wide' },
      ...liveEntries.map(([key, value]) => info(FIELD_LABELS[key] || key.replaceAll('_', ' '), displayValue(value))));

    const fields = node('div', { class: 'form-grid' },
      formSection('Import do Terraform', 'Operacja wykona import do Terraform oraz plan. Zastosowanie zmian nie zostanie uruchomione automatycznie.',
        node('div', { class: 'form-grid' },
          field('Szablon', 'template', { value: template.name + ' · v' + template.version, wide: true }),
          selectField('Silnik IaC', 'executor', [{ value: 'terraform', label: 'Terraform' }, { value: 'opentofu', label: 'OpenTofu' }], 'terraform'))),
      formSection('Stan docelowy', 'Sprawdź wartości przed importem. Po planie będzie można ocenić drift.', variables),
      formSection('Aktualna konfiguracja', 'Podgląd odczytany bezpośrednio z Proxmox.', liveGrid));
    fields.querySelector('[name="template"]').readOnly = true;

    openModal({
      title: `Przejmij zarządzanie: ${item.name || item.vm_id}`,
      eyebrow: 'Terraform import + plan',
      body: fields,
      submitLabel: 'Importuj stan i wykonaj plan',
      wide: true,
      onSubmit: async (_data, form) => {
        const result = await api(`/inventory/vms/${item.id}/adopt`, {
          method: 'POST',
          idempotent: true,
          body: {
            template: template.id,
            executor: form.elements.executor.value,
            variables: readTemplateVariables(form, template),
          },
        });
        toast(`Utworzono zadanie importu ${short(result.job.id)}.`);
        navigate('jobs');
      },
    });
  } catch (error) { toast(error.message, 'error'); }
}

function vmBase(item) {
  return `/providers/${item.provider_id}/vms/${encodeURIComponent(item.node)}/${item.vm_id}`;
}

async function showProxmoxTask(item, result, title) {
  if (!result?.task) {
    toast(`${title}: operacja została przyjęta.`);
    return;
  }

  stopTaskPolling();
  const nonce = state.taskPollNonce;
  const statusValue = node('strong', { text: 'Uruchamianie…' });
  const exitValue = node('strong', { text: '—' });
  const typeValue = node('strong', { text: '—' });
  const startedValue = node('strong', { text: '—' });
  const progress = node('div', { class: 'task-progress' },
    node('div', { class: 'spinner', 'aria-hidden': 'true' }),
    node('span', { text: 'Oczekiwanie na status zadania Proxmox…' }));

  dom.modal.classList.remove('modal-console');
  dom.modalTitle.textContent = title;
  dom.modalEyebrow.textContent = item.name || `${item.node} / VMID ${item.vm_id}`;
  dom.modalBody.replaceChildren(
    progress,
    node('div', { class: 'checks task-checks' },
      node('div', { class: 'check' }, node('span', { text: 'Status' }), statusValue),
      node('div', { class: 'check' }, node('span', { text: 'Wynik' }), exitValue),
      node('div', { class: 'check' }, node('span', { text: 'Typ operacji' }), typeValue),
      node('div', { class: 'check' }, node('span', { text: 'Start' }), startedValue)),
    node('details', { class: 'task-id-details' },
      node('summary', { text: 'Identyfikator zadania' }),
      node('div', { class: 'secret-box mono', text: String(result.task) })));
  dom.modalActions.replaceChildren(button('Zamknij', closeModal));
  if (!dom.modal.open) dom.modal.showModal();

  const poll = async () => {
    if (nonce !== state.taskPollNonce || !dom.modal.open) return;
    try {
      const task = await api(`/providers/${item.provider_id}/tasks/${encodeURIComponent(item.node)}/${encodeURIComponent(String(result.task))}`);
      const stopped = task.status === 'stopped' || Boolean(task.exitstatus);
      statusValue.textContent = stopped ? 'Zakończone' : statusLabel(task.status || 'running');
      exitValue.textContent = task.exitstatus || (stopped ? '—' : 'W trakcie');
      typeValue.textContent = task.type || '—';
      startedValue.textContent = task.starttime ? new Date(task.starttime * 1000).toLocaleString('pl-PL') : '—';

      if (stopped) {
        const success = !task.exitstatus || task.exitstatus === 'OK';
        progress.replaceChildren(
          badge(success ? 'Zakończono pomyślnie' : 'Operacja zakończona błędem', success ? 'ok' : 'danger'));
        if (success) toast(`${title}: zakończono.`);
        else toast(`${title}: ${task.exitstatus || 'błąd operacji'}.`, 'error');
        state.taskPollTimer = null;
        return;
      }
      state.taskPollTimer = window.setTimeout(poll, 1500);
    } catch (error) {
      progress.replaceChildren(node('span', { class: 'form-error', text: 'Nie udało się odświeżyć statusu: ' + error.message }));
      state.taskPollTimer = window.setTimeout(poll, 4000);
    }
  };

  await poll();
}

async function discoverVmOptions(item, resource, node = null) {
  if (!allowed('providers.read')) return [];
  try {
    const query = node ? '?node=' + encodeURIComponent(node) : '';
    return (await api(`/providers/${item.provider_id}/${resource}${query}`)).items || [];
  } catch {
    return [];
  }
}

function storageLabel(storage) {
  const parts = [storage.storage || storage.id || 'storage'];
  if (storage.avail !== undefined) parts.push(`wolne ${formatBytes(storage.avail)}`);
  if (storage.content) parts.push(Array.isArray(storage.content) ? storage.content.join(', ') : String(storage.content));
  return parts.join(' · ');
}

async function openVmManager(item) {
  try {
    const base = vmBase(item);
    const status = await api(`${base}/status`);
    const snapshots = allowed('snapshots.read') ? (await api(`${base}/snapshots`)).items : [];
    dom.modalTitle.textContent = item.name || `VM ${item.vm_id}`;
    dom.modalEyebrow.textContent = `${item.node} / VMID ${item.vm_id}`;
    const statusPanel = node('div', { class: 'checks' },
      info('Status', statusLabel(status.status || '—')), info('CPU', status.cpus ?? status.cpu ?? '—'),
      info('RAM', status.mem && status.maxmem ? `${Math.round(status.mem / 1024 / 1024)} / ${Math.round(status.maxmem / 1024 / 1024)} MiB` : '—'),
      info('Czas działania', formatDuration(status.uptime)));
    const snapshotTable = allowed('snapshots.read') ? table([
      { label: 'Snapshot', value: snap => snap.name },
      { label: 'Opis', value: snap => snap.description || '—' },
      { label: 'RAM', value: snap => snap.vmstate ? 'Tak' : 'Nie' },
    ], snapshots, snap => allowed('snapshots.delete') && snap.name !== 'current' ? [button('Usuń', () => confirmAction('Usuń snapshot', snap.name, async () => {
      const result = await api(`${base}/snapshots/${encodeURIComponent(snap.name)}`, { method: 'DELETE', idempotent: true });
      await showProxmoxTask(item, result, 'Usuwanie snapshotu');
      return false;
    }), 'danger'), ...(allowed('snapshots.rollback') ? [button('Przywróć', () => confirmAction('Przywróć snapshot', `VM zostanie przywrócona do snapshotu ${snap.name}.`, async () => {
      const result = await api(`${base}/snapshots/${encodeURIComponent(snap.name)}/rollback`, { method: 'POST', idempotent: true });
      await showProxmoxTask(item, result, 'Przywracanie snapshotu');
      return false;
    }), 'danger')] : [])] : []) : node('p', { class: 'muted', text: 'Brak uprawnienia do snapshotów.' });
    dom.modalBody.replaceChildren(node('div', { class: 'stack' }, statusPanel, node('h3', { text: 'Snapshoty' }), snapshotTable));
    const actions = [button('Zamknij', closeModal)];
    if (allowed('vms.power')) {
      const powerActions = [
        ['start', 'Uruchom'], ['shutdown', 'Wyłącz bezpiecznie'], ['reboot', 'Restart'],
        ['suspend', 'Wstrzymaj'], ['resume', 'Wznów'], ['reset', 'Twardy reset'], ['stop', 'Wymuś stop'],
      ];
      for (const [action, label] of powerActions) {
        actions.push(button(label, () => vmPower(item, action), ['stop', 'reset'].includes(action) ? 'danger' : 'ghost'));
      }
    }
    if (allowed('snapshots.create')) actions.push(button('Snapshot', () => createVmSnapshot(item)));
    if (allowed('backups.create')) actions.push(button('Backup', () => backupVm(item)));
    if (allowed('backups.read')) actions.push(button('Backupy', () => listVmBackups(item)));
    if (allowed('vms.console')) actions.push(button('Konsola', () => showVmConsole(item), 'primary'));
    if (allowed('vms.update')) {
      actions.push(button('CPU/RAM', () => configureVm(item)));
      actions.push(button('Dysk +', () => resizeVmDisk(item)));
    }
    if (allowed('vms.migrate')) actions.push(button('Migracja', () => migrateVm(item)));
    if (allowed('vms.clone')) actions.push(button('Klonuj', () => cloneVm(item)));
    if (allowed('vms.template')) actions.push(button('→ Szablon', () => confirmAction('Konwertuj do szablonu', 'Operacja zmieni VM w szablon.', async () => {
      const result = await api(`${base}/template`, { method: 'POST', idempotent: true });
      await showProxmoxTask(item, result, 'Konwersja VM do szablonu');
      return false;
    })));
    if (allowed('vms.delete')) actions.push(button('Usuń VM', () => deleteVm(item), 'danger'));
    dom.modalActions.replaceChildren(...actions);
    if (!dom.modal.open) dom.modal.showModal();
  } catch (error) { toast(error.message, 'error'); }
}

async function vmPower(item, action) {
  const labels = {
    start: 'Uruchamianie VM', shutdown: 'Bezpieczne wyłączanie VM', reboot: 'Restart VM',
    suspend: 'Wstrzymywanie VM', resume: 'Wznawianie VM', reset: 'Twardy reset VM', stop: 'Zatrzymywanie VM',
  };
  try {
    const result = await api(`${vmBase(item)}/power`, { method: 'POST', idempotent: true, body: { action } });
    await showProxmoxTask(item, result, labels[action] || 'Operacja zasilania');
  } catch (error) { toast(error.message, 'error'); }
}

function deleteVm(item) {
  const fields = node('div', { class: 'form-grid' },
    node('p', { class: 'wide field-help', text: 'Operacja usuwa VM bezpośrednio w Proxmox. Dla zasobów zarządzanych przez Terraform używaj akcji „Usuń zasoby” na wdrożeniu.' }),
    checkboxField('Purge z konfiguracji HA/backup/replication', 'purge'),
    checkboxField('Usuń niepodpięte dyski', 'destroy_unreferenced_disks'));
  openModal({
    title: 'Usuń VM', eyebrow: `${item.node} / VMID ${item.vm_id}`, body: fields,
    submitLabel: 'Usuń VM', danger: true,
    onSubmit: async data => {
      const query = new URLSearchParams({
        purge: data.has('purge') ? 'true' : 'false',
        destroy_unreferenced_disks: data.has('destroy_unreferenced_disks') ? 'true' : 'false',
      });
      const result = await api(`${vmBase(item)}?${query.toString()}`, { method: 'DELETE', idempotent: true });
      await showProxmoxTask(item, result, 'Usuwanie VM');
      return false;
    },
  });
}

function createVmSnapshot(item) {
  const fields = node('div', { class: 'form-grid' },
    field('Nazwa snapshotu', 'snapname', { required: true }),
    field('Opis', 'description', { wide: true }),
    checkboxField('Dołącz stan RAM', 'include_ram'));
  openModal({ title: 'Nowy snapshot', eyebrow: item.name || String(item.vm_id), body: fields, onSubmit: async data => {
    const result = await api(`${vmBase(item)}/snapshots`, { method: 'POST', idempotent: true, body: {
      snapname: data.get('snapname'), description: data.get('description'), include_ram: data.has('include_ram'),
    } });
    await showProxmoxTask(item, result, 'Tworzenie snapshotu');
    return false;
  }});
}

async function backupVm(item) {
  try {
    const storages = (await discoverVmOptions(item, 'storages', item.node))
      .filter(storage => storage.active !== 0 && storage.enabled !== 0 && String(storage.content || '').includes('backup'));
    const storageField = storages.length
      ? selectField('Miejsce backupu', 'storage', storages.map(storage => ({ value: storage.storage, label: storageLabel(storage) })), storages[0].storage, { required: true })
      : field('Miejsce backupu', 'storage', { required: true, help: 'Nie udało się pobrać listy storage — wpisz nazwę ręcznie.' });
    const fields = node('div', { class: 'form-grid' },
      storageField,
      selectField('Tryb backupu', 'mode', [
        { value: 'snapshot', label: 'Snapshot — bez przestoju, jeśli wspierany' },
        { value: 'suspend', label: 'Wstrzymaj VM na czas backupu' },
        { value: 'stop', label: 'Zatrzymaj VM na czas backupu' },
      ], 'snapshot'),
      selectField('Kompresja', 'compress', [{ value: 'zstd', label: 'Zstandard (zalecane)' }, { value: 'lzo', label: 'LZO' }, { value: 'gzip', label: 'Gzip' }], 'zstd'),
      field('Notatka', 'notes', { wide: true }));
    openModal({ title: 'Utwórz backup VM', eyebrow: item.name || String(item.vm_id), body: fields, submitLabel: 'Uruchom backup', onSubmit: async data => {
      const result = await api(`${vmBase(item)}/backups`, { method: 'POST', idempotent: true, body: {
        storage: data.get('storage'), mode: data.get('mode'), compress: data.get('compress'), notes: data.get('notes') || null,
      } });
      await showProxmoxTask(item, result, 'Tworzenie backupu');
      return false;
    }});
  } catch (error) { toast(error.message, 'error'); }
}

async function listVmBackups(item) {
  try {
    const storages = (await discoverVmOptions(item, 'storages', item.node))
      .filter(storage => storage.active !== 0 && String(storage.content || '').includes('backup'));
    const storageField = storages.length
      ? selectField('Miejsce backupu', 'storage', storages.map(storage => ({ value: storage.storage, label: storageLabel(storage) })), storages[0].storage, { required: true })
      : field('Miejsce backupu', 'storage', { required: true, help: 'Nie udało się pobrać listy storage — wpisz nazwę ręcznie.' });
    openModal({ title: 'Backupy VM', eyebrow: item.name || String(item.vm_id), body: storageField, submitLabel: 'Pokaż backupy', onSubmit: async data => {
      const storage = data.get('storage');
      const result = await api(`${vmBase(item)}/backups?storage=${encodeURIComponent(storage)}`);
      dom.modalTitle.textContent = 'Backupy VM';
      dom.modalEyebrow.textContent = storage;
      const rows = result.items || [];
      const backupTable = table([
        { label: 'Wolumen', value: backup => node('span', { class: 'mono', text: backup.volid || '—' }) },
        { label: 'Format', value: backup => backup.format || '—' },
        { label: 'Rozmiar', value: backup => formatBytes(backup.size) },
        { label: 'Utworzono', value: backup => backup.ctime ? new Date(backup.ctime * 1000).toLocaleString('pl-PL') : '—' },
        { label: 'Chroniony', value: backup => backup.protected ? badge('Tak', 'ok') : 'Nie' },
      ], rows, backup => allowed('backups.restore') ? [button('Przywróć', () => restoreVmFromBackup(item, backup), 'primary')] : []);
      dom.modalBody.replaceChildren(rows.length ? backupTable : node('p', { class: 'muted', text: 'Brak backupów dla tej VM.' }));
      dom.modalActions.replaceChildren(button('Zamknij', closeModal));
      return false;
    }});
  } catch (error) { toast(error.message, 'error'); }
}

function restoreVmFromBackup(item, backup) {
  if (!backup?.volid) { toast('Backup nie ma identyfikatora volume.', 'error'); return; }
  const fields = node('div', { class: 'form-grid' },
    field('Wolumen backupu', 'archive', { value: backup.volid, required: true, wide: true }),
    field('Nowy VMID', 'vm_id', { type: 'number', min: 100, required: true }),
    field('Docelowy storage (opcjonalnie)', 'storage'),
    checkboxField('Nadaj unikalne parametry urządzeń / MAC', 'unique', true));
  fields.querySelector('[name="archive"]').readOnly = true;
  openModal({
    title: 'Przywróć VM z backupu', eyebrow: item.node, body: fields, submitLabel: 'Uruchom przywracanie',
    onSubmit: async data => {
      const payload = {
        vm_id: Number(data.get('vm_id')),
        archive: backup.volid,
        unique: data.has('unique'),
      };
      if (data.get('storage')) payload.storage = data.get('storage');
      const result = await api(`/providers/${item.provider_id}/restore/${encodeURIComponent(item.node)}`, {
        method: 'POST', idempotent: true, body: payload,
      });
      await showProxmoxTask(item, result, `Przywracanie VMID ${payload.vm_id}`);
      return false;
    },
  });
}

async function showVmConsole(item) {
  try {
    closeModal();
    const result = await api(`${vmBase(item)}/console`, { method: 'POST' });
    if (result.mode !== 'novnc' || !result.rfb_module?.startsWith('/api/v1/') || !result.ws_path?.startsWith('/api/v1/')) {
      throw new Error('Backend nie zwrócił poprawnej sesji noVNC.');
    }

    const screen = node('div', { class: 'novnc-screen' });
    const status = node('p', { class: 'muted', text: 'Łączenie z konsolą przez Cloudportal-backed…' });
    dom.modalTitle.textContent = 'Konsola noVNC';
    dom.modalEyebrow.textContent = item.name || `${item.node} / ${item.vm_id}`;
    dom.modalBody.replaceChildren(status, screen);
    dom.modalActions.replaceChildren(button('Zamknij', closeModal));
    dom.modal.classList.add('modal-console');
    dom.modal.showModal();

    const module = await import(result.rfb_module);
    const RFB = module.default;
    if (typeof RFB !== 'function') throw new Error('Moduł noVNC nie udostępnia klienta RFB.');
    const websocketScheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const rfb = new RFB(
      screen,
      `${websocketScheme}//${window.location.host}${result.ws_path}`,
      { credentials: { password: result.password } },
    );
    state.consoleRfb = rfb;
    rfb.scaleViewport = true;
    rfb.resizeSession = true;
    rfb.addEventListener('connect', () => { status.textContent = 'Połączono przez backend proxy.'; });
    rfb.addEventListener('disconnect', event => {
      if (state.consoleRfb === rfb) state.consoleRfb = null;
      status.textContent = event.detail?.clean ? 'Konsola rozłączona.' : 'Połączenie konsoli zostało przerwane.';
    });
    rfb.addEventListener('credentialsrequired', () => {
      rfb.sendCredentials({ password: result.password });
    });
  } catch (error) {
    state.consoleRfb = null;
    if (dom.modal.open) closeModal();
    toast(error.message, 'error');
  }
}

async function configureVm(item) {
  try {
    const status = await api(`${vmBase(item)}/status`);
    const fields = node('div', { class: 'form-grid' },
      field('Nazwa VM', 'name', { value: status.name || item.name || '' }),
      field('Rdzenie CPU', 'cores', { type: 'number', min: 1, value: status.cpus ?? '' }),
      field('RAM (MiB)', 'memory', { type: 'number', min: 512, value: status.maxmem ? Math.round(status.maxmem / 1024 / 1024) : '' }),
      field('Tagi', 'tags', { value: status.tags || '' }),
      selectField('Startuj automatycznie z hostem', 'onboot', [
        { value: '', label: 'Bez zmiany' },
        { value: 'true', label: 'Tak' },
        { value: 'false', label: 'Nie' },
      ], ''));
    openModal({ title: 'Konfiguracja VM', eyebrow: item.name || String(item.vm_id), body: fields, onSubmit: async data => {
      const payload = {};
      if (data.get('name') && data.get('name') !== status.name) payload.name = data.get('name');
      if (data.get('cores') && Number(data.get('cores')) !== Number(status.cpus)) payload.cores = Number(data.get('cores'));
      const currentMemory = status.maxmem ? Math.round(status.maxmem / 1024 / 1024) : null;
      if (data.get('memory') && Number(data.get('memory')) !== currentMemory) payload.memory = Number(data.get('memory'));
      if (data.get('tags') !== (status.tags || '')) payload.tags = data.get('tags');
      if (data.get('onboot')) payload.onboot = data.get('onboot') === 'true';
      if (!Object.keys(payload).length) throw new Error('Nie wykryto żadnej zmiany.');
      const result = await api(`${vmBase(item)}/config`, { method: 'PUT', idempotent: true, body: payload });
      await showProxmoxTask(item, result, 'Zmiana konfiguracji VM');
      return false;
    }});
  } catch (error) { toast(error.message, 'error'); }
}

function resizeVmDisk(item) {
  const fields = node('div', { class: 'form-grid' },
    field('Dysk', 'disk', { value: 'scsi0', required: true }),
    field('Powiększ o GiB', 'grow_gib', { type: 'number', min: 1, required: true }));
  openModal({ title: 'Powiększ dysk', eyebrow: item.name || String(item.vm_id), body: fields, onSubmit: async data => {
    const result = await api(`${vmBase(item)}/disk`, { method: 'PUT', idempotent: true, body: { disk: data.get('disk'), grow_gib: Number(data.get('grow_gib')) } });
    await showProxmoxTask(item, result, 'Powiększanie dysku');
    return false;
  }});
}

async function migrateVm(item) {
  try {
    const nodes = (await discoverVmOptions(item, 'nodes')).filter(node => node.node && node.node !== item.node);
    const targetField = nodes.length
      ? selectField('Docelowy węzeł', 'target', nodes.map(node => ({
        value: node.node,
        label: `${node.node}${node.status ? ' · ' + statusLabel(node.status) : ''}`,
      })), '', { required: true, placeholder: 'Wybierz węzeł' })
      : field('Docelowy węzeł', 'target', { required: true, help: 'Nie udało się pobrać listy węzłów — wpisz nazwę ręcznie.' });
    const fields = node('div', { class: 'form-grid' },
      targetField,
      checkboxField('Migracja online', 'online'),
      checkboxField('Przenieś lokalne dyski', 'with_local_disks'));
    openModal({ title: 'Migracja VM', eyebrow: item.name || String(item.vm_id), body: fields, submitLabel: 'Uruchom migrację', onSubmit: async data => {
      const result = await api(`${vmBase(item)}/migrate`, { method: 'POST', idempotent: true, body: {
        target: data.get('target'), online: data.has('online'), with_local_disks: data.has('with_local_disks'),
      } });
      await showProxmoxTask(item, result, 'Migracja VM');
      return false;
    }});
  } catch (error) { toast(error.message, 'error'); }
}

async function cloneVm(item) {
  try {
    const [nodes, pools] = await Promise.all([
      discoverVmOptions(item, 'nodes'),
      discoverVmOptions(item, 'pools'),
    ]);
    const targetField = nodes.length
      ? selectField('Docelowy węzeł', 'target', [{ value: '', label: `Bez zmiany — ${item.node}` }].concat(nodes.map(node => ({
        value: node.node, label: `${node.node}${node.status ? ' · ' + statusLabel(node.status) : ''}`,
      }))), '')
      : field('Docelowy węzeł (opcjonalnie)', 'target');
    const storageHolder = node('div', { class: 'wide' });
    const poolField = pools.length
      ? selectField('Pula (opcjonalnie)', 'pool', [{ value: '', label: 'Bez puli' }].concat(pools.map(pool => ({
        value: pool.poolid, label: pool.comment ? `${pool.poolid} · ${pool.comment}` : pool.poolid,
      }))), '')
      : field('Pula (opcjonalnie)', 'pool');

    const fields = node('div', { class: 'form-grid' },
      field('Nowy VMID', 'new_vm_id', { type: 'number', min: 100, required: true }),
      field('Nazwa nowej VM', 'name', { required: true, value: item.name ? item.name + '-clone' : '' }),
      targetField,
      storageHolder,
      poolField,
      checkboxField('Pełny klon', 'full', true));

    const targetControl = targetField.querySelector('select,input');
    const refreshStorages = async () => {
      const target = targetControl.value || item.node;
      const storages = (await discoverVmOptions(item, 'storages', target)).filter(storage => storage.active !== 0 && storage.enabled !== 0);
      if (storages.length) {
        storageHolder.replaceChildren(selectField('Docelowy storage (opcjonalnie)', 'storage',
          [{ value: '', label: 'Domyślny' }].concat(storages.map(storage => ({ value: storage.storage, label: storageLabel(storage) }))), ''));
      } else {
        storageHolder.replaceChildren(field('Docelowy storage (opcjonalnie)', 'storage', {
          help: 'Lista storage jest niedostępna — pole można zostawić puste.',
        }));
      }
    };
    targetControl.addEventListener('change', () => { refreshStorages(); });
    await refreshStorages();

    openModal({ title: 'Klonuj VM', eyebrow: item.name || String(item.vm_id), body: fields, submitLabel: 'Uruchom klonowanie', onSubmit: async data => {
      const result = await api(`${vmBase(item)}/clone`, { method: 'POST', idempotent: true, body: {
        new_vm_id: Number(data.get('new_vm_id')),
        name: data.get('name'),
        target: data.get('target') || null,
        storage: data.get('storage') || null,
        pool: data.get('pool') || null,
        full: data.has('full'),
      } });
      await showProxmoxTask(item, result, 'Klonowanie VM');
      return false;
    }});
  } catch (error) { toast(error.message, 'error'); }
}

registerCommand('inventory.openVm', openVmManager);
registerView({ id: 'inventory', label: 'Zasoby', icon: 'V', permission: 'inventory.read', order: 100 }, inventoryView);
})();
