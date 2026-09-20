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
  if (allowed('vms.read') && item.lifecycle_status === 'active') actions.push(button('Szczegóły', () => showVmDetailsPage(item), 'primary'));
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
  if (!(typeof window.modalSurfaceOpen === 'function' ? window.modalSurfaceOpen() : dom.modal.open)) dom.modal.showModal();

  const poll = async () => {
    if (nonce !== state.taskPollNonce || !(typeof window.modalSurfaceOpen === 'function' ? window.modalSurfaceOpen() : dom.modal.open)) return;
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

function vmDetailActions(item) {
  const base = vmBase(item);
  const actions = [];
  if (allowed('vms.power')) {
    [
      ['start', 'Uruchom', 'primary'],
      ['shutdown', 'Wyłącz', 'ghost'],
      ['reboot', 'Restart', 'ghost'],
      ['suspend', 'Wstrzymaj', 'ghost'],
      ['resume', 'Wznów', 'ghost'],
      ['reset', 'Twardy reset', 'danger'],
      ['stop', 'Wymuś stop', 'danger'],
    ].forEach(([action, label, kind]) => actions.push(button(label, () => vmPower(item, action), kind)));
  }
  if (allowed('vms.console')) actions.push(button('Konsola', () => showVmConsole(item), 'primary'));
  if (allowed('snapshots.create')) actions.push(button('Snapshot', () => createVmSnapshot(item)));
  if (allowed('backups.create')) actions.push(button('Backup', () => backupVm(item)));
  if (allowed('vms.update')) {
    actions.push(button('CPU / RAM', () => configureVm(item)));
    actions.push(button('Powiększ dysk', () => resizeVmDisk(item)));
  }
  if (allowed('vms.migrate')) actions.push(button('Migracja', () => migrateVm(item)));
  if (allowed('vms.clone')) actions.push(button('Klonuj', () => cloneVm(item)));
  if (allowed('vms.template')) actions.push(button('→ Szablon', () => confirmAction(
    'Konwertuj do szablonu',
    'Operacja zmieni VM w szablon.',
    async () => {
      const result = await api(`${base}/template`, { method: 'POST', idempotent: true });
      await showProxmoxTask(item, result, 'Konwersja VM do szablonu');
      return false;
    },
  )));
  if (allowed('vms.delete')) actions.push(button('Usuń VM', () => deleteVm(item), 'danger'));
  return actions;
}

function vmUsageBar(label, used, total, formatter = value => String(value)) {
  const valid = Number.isFinite(Number(used)) && Number.isFinite(Number(total)) && Number(total) > 0;
  const percent = valid ? Math.max(0, Math.min(100, Number(used) / Number(total) * 100)) : 0;
  return node('div', { class: 'vm-usage-card' },
    node('div', { class: 'vm-usage-heading' },
      node('span', { text: label }),
      node('strong', { text: valid ? `${formatter(used)} / ${formatter(total)}` : '—' })),
    node('div', { class: 'vm-usage-track' },
      node('span', { class: 'vm-usage-fill', style: `width:${percent.toFixed(1)}%` })),
    node('small', { text: valid ? `${Math.round(percent)}%` : 'Brak danych' }));
}

function vmOverviewContent(item, status) {
  const stateValue = status.status || item.live?.status || 'unknown';
  return node('div', { class: 'vm-detail-stack' },
    node('div', { class: 'vm-overview-grid' },
      node('section', { class: 'vm-stat-card' }, node('span', { text: 'Stan' }), badge(statusLabel(stateValue), statusKind(stateValue)), node('small', { text: item.node || '—' })),
      node('section', { class: 'vm-stat-card' }, node('span', { text: 'CPU' }), node('strong', { text: String(status.cpus ?? status.cpu ?? '—') }), node('small', { text: 'vCPU' })),
      node('section', { class: 'vm-stat-card' }, node('span', { text: 'RAM' }), node('strong', { text: status.maxmem ? formatBytes(status.maxmem) : '—' }), node('small', { text: status.mem ? `używane ${formatBytes(status.mem)}` : 'brak telemetryki' })),
      node('section', { class: 'vm-stat-card' }, node('span', { text: 'Dysk' }), node('strong', { text: status.maxdisk ? formatBytes(status.maxdisk) : '—' }), node('small', { text: status.disk ? `używane ${formatBytes(status.disk)}` : 'brak telemetryki' })),
      node('section', { class: 'vm-stat-card' }, node('span', { text: 'Uptime' }), node('strong', { text: formatDuration(status.uptime) }), node('small', { text: `VMID ${item.vm_id}` })),
      node('section', { class: 'vm-stat-card' }, node('span', { text: 'Zarządzanie' }), badge(statusLabel(item.management_mode), item.management_mode === 'terraform' ? 'ok' : 'info'), node('small', { text: item.deployment_id ? short(item.deployment_id, 20) : 'bez deploymentu' }))),
    node('section', { class: 'panel vm-resource-panel' },
      node('div', { class: 'panel-header' }, node('h2', { text: 'Wykorzystanie zasobów' })),
      node('div', { class: 'vm-usage-grid' },
        vmUsageBar('RAM', status.mem, status.maxmem, formatBytes),
        vmUsageBar('Dysk', status.disk, status.maxdisk, formatBytes),
        vmUsageBar('CPU', Number(status.cpu || 0), 1, value => `${Math.round(Number(value) * 100)}%`))),
    node('section', { class: 'panel' },
      node('div', { class: 'panel-header' }, node('h2', { text: 'Informacje' })),
      node('div', { class: 'checks' },
        info('Nazwa', status.name || item.name || `VM ${item.vm_id}`),
        info('Node', item.node || status.node || '—'),
        info('VMID', String(item.vm_id)),
        info('Tagi', status.tags || '—'),
        info('Tryb zarządzania', statusLabel(item.management_mode)),
        info('Lifecycle', statusLabel(item.lifecycle_status)))));
}

function vmHardwareContent(item, status) {
  const rows = [
    ['CPU', status.cpus ?? status.cpu],
    ['CPU usage', Number.isFinite(Number(status.cpu)) ? `${Math.round(Number(status.cpu) * 100)}%` : null],
    ['RAM używany', status.mem ? formatBytes(status.mem) : null],
    ['RAM maksymalny', status.maxmem ? formatBytes(status.maxmem) : null],
    ['Dysk używany', status.disk ? formatBytes(status.disk) : null],
    ['Dysk maksymalny', status.maxdisk ? formatBytes(status.maxdisk) : null],
    ['Net IN', status.netin ? formatBytes(status.netin) : null],
    ['Net OUT', status.netout ? formatBytes(status.netout) : null],
    ['Disk read', status.diskread ? formatBytes(status.diskread) : null],
    ['Disk write', status.diskwrite ? formatBytes(status.diskwrite) : null],
    ['PID', status.pid],
    ['Uptime', formatDuration(status.uptime)],
    ['Tagi', status.tags],
  ].filter(([, value]) => value !== undefined && value !== null && value !== '');
  return node('section', { class: 'panel' },
    node('div', { class: 'panel-header' }, node('h2', { text: 'Hardware i telemetria' }),
      allowed('vms.update') ? button('Edytuj CPU / RAM', () => configureVm(item)) : ''),
    table([
      { label: 'Parametr', value: row => node('strong', { text: row[0] }) },
      { label: 'Wartość', value: row => row[1] },
    ], rows));
}

async function vmSnapshotsContent(item) {
  if (!allowed('snapshots.read')) return node('div', { class: 'empty', text: 'Brak uprawnienia snapshots.read.' });
  const base = vmBase(item);
  const snapshots = (await api(`${base}/snapshots`)).items || [];
  return node('section', { class: 'panel' },
    node('div', { class: 'panel-header' }, node('h2', { text: 'Snapshoty' }),
      allowed('snapshots.create') ? button('Nowy snapshot', () => createVmSnapshot(item), 'primary') : ''),
    table([
      { label: 'Snapshot', value: snap => node('strong', { text: snap.name }) },
      { label: 'Opis', value: snap => snap.description || '—' },
      { label: 'RAM', value: snap => snap.vmstate ? badge('Tak', 'ok') : 'Nie' },
    ], snapshots, snap => {
      const result = [];
      if (snap.name !== 'current' && allowed('snapshots.rollback')) result.push(button('Przywróć', () => confirmAction(
        'Przywróć snapshot',
        `VM zostanie przywrócona do snapshotu ${snap.name}.`,
        async () => {
          const operation = await api(`${base}/snapshots/${encodeURIComponent(snap.name)}/rollback`, { method: 'POST', idempotent: true });
          await showProxmoxTask(item, operation, 'Przywracanie snapshotu');
          return false;
        },
      ), 'danger'));
      if (snap.name !== 'current' && allowed('snapshots.delete')) result.push(button('Usuń', () => confirmAction(
        'Usuń snapshot',
        snap.name,
        async () => {
          const operation = await api(`${base}/snapshots/${encodeURIComponent(snap.name)}`, { method: 'DELETE', idempotent: true });
          await showProxmoxTask(item, operation, 'Usuwanie snapshotu');
          return false;
        },
      ), 'danger'));
      return result;
    }));
}

async function vmBackupsContent(item) {
  if (!allowed('backups.read')) return node('div', { class: 'empty', text: 'Brak uprawnienia backups.read.' });
  const shell = node('section', { class: 'panel' });
  const content = node('div', { class: 'vm-inline-content' });
  const storages = (await discoverVmOptions(item, 'storages', item.node))
    .filter(storage => storage.active !== 0 && String(storage.content || '').includes('backup'));
  const storageControl = storages.length
    ? selectField('Storage backupu', 'vm_backup_storage', storages.map(storage => ({ value: storage.storage, label: storageLabel(storage) })), storages[0].storage)
    : field('Storage backupu', 'vm_backup_storage', { placeholder: 'np. local', required: true });
  const select = storageControl.querySelector('select,input');
  const load = async () => {
    const storage = select.value.trim();
    if (!storage) {
      content.replaceChildren(node('div', { class: 'empty', text: 'Wybierz storage backupu.' }));
      return;
    }
    content.replaceChildren(node('div', { class: 'loading compact' }, node('div', { class: 'spinner' })));
    try {
      const rows = (await api(`${vmBase(item)}/backups?storage=${encodeURIComponent(storage)}`)).items || [];
      content.replaceChildren(table([
        { label: 'Wolumen', value: backup => node('span', { class: 'mono', text: backup.volid || '—' }) },
        { label: 'Format', value: backup => backup.format || '—' },
        { label: 'Rozmiar', value: backup => formatBytes(backup.size) },
        { label: 'Utworzono', value: backup => backup.ctime ? new Date(backup.ctime * 1000).toLocaleString('pl-PL') : '—' },
        { label: 'Chroniony', value: backup => backup.protected ? badge('Tak', 'ok') : 'Nie' },
      ], rows, backup => allowed('backups.restore') ? [button('Przywróć', () => restoreVmFromBackup(item, backup), 'primary')] : []));
    } catch (error) {
      content.replaceChildren(node('p', { class: 'form-error', text: error.message }));
    }
  };
  const headerActions = [];
  if (allowed('backups.create')) headerActions.push(button('Nowy backup', () => backupVm(item), 'primary'));
  headerActions.push(button('Odśwież', load));
  shell.append(
    node('div', { class: 'panel-header' }, node('h2', { text: 'Backupy' }), node('div', { class: 'action-group' }, headerActions)),
    node('div', { class: 'vm-backup-toolbar' }, storageControl),
    content,
  );
  if (storages.length === 1) window.setTimeout(load, 0);
  return shell;
}

async function vmAuditContent(item) {
  if (!allowed('audit.read')) return node('div', { class: 'empty', text: 'Brak uprawnienia audit.read.' });
  const target = `${item.provider_id}:${item.node}:${item.vm_id}`;
  const entries = (await api('/audit?limit=200')).items || [];
  const rows = entries.filter(entry => String(entry.resource_id || '').startsWith(target));
  return node('section', { class: 'panel' },
    node('div', { class: 'panel-header' }, node('h2', { text: 'Historia operacji' }), badge(String(rows.length), 'info')),
    table([
      { label: 'Czas', value: row => formatDate(row.timestamp) },
      { label: 'Akcja', value: row => row.action || '—' },
      { label: 'Użytkownik', value: row => row.actor_username || row.actor_user_id || '—' },
      { label: 'Request ID', class: 'mono', value: row => short(row.request_id, 18) },
    ], rows));
}

async function showVmDetailsPage(item, initialTab = 'overview', parentView = null) {
  const returnView = parentView || (state.view === 'my-resources' ? 'my-resources' : 'inventory');
  const returnLabel = returnView === 'my-resources' ? 'Moje zasoby' : 'Zasoby';
  try {
    const status = await api(`${vmBase(item)}/status`);
    state.view = returnView;
    location.hash = returnView;
    dom.pageEyebrow.textContent = `${returnLabel} / ${item.node || 'Proxmox'} / VMID ${item.vm_id}`;
    dom.pageTitle.textContent = status.name || item.name || `VM ${item.vm_id}`;
    dom.navigation.querySelectorAll('.nav-link').forEach(link => link.classList.toggle('active', link.dataset.route === returnView));

    const tabContent = node('div', { class: 'vm-tab-content' });
    const tabs = [
      ['overview', 'Przegląd'],
      ['hardware', 'Hardware'],
      ['snapshots', 'Snapshoty', 'snapshots.read'],
      ['backups', 'Backupy', 'backups.read'],
      ['audit', 'Historia', 'audit.read'],
    ].filter(([, , permission]) => allowed(permission));

    let activeTab = tabs.some(([id]) => id === initialTab) ? initialTab : 'overview';
    const tabBar = node('div', { class: 'vm-tabs', role: 'tablist', 'aria-label': 'Szczegóły VM' });

    const renderTab = async id => {
      activeTab = id;
      tabBar.querySelectorAll('.vm-tab').forEach(tab => {
        const active = tab.dataset.tab === id;
        tab.classList.toggle('active', active);
        tab.setAttribute('aria-selected', String(active));
      });
      tabContent.replaceChildren(node('div', { class: 'loading compact' }, node('div', { class: 'spinner' })));
      try {
        let result;
        if (id === 'overview') result = vmOverviewContent(item, status);
        else if (id === 'hardware') result = vmHardwareContent(item, status);
        else if (id === 'snapshots') result = await vmSnapshotsContent(item);
        else if (id === 'backups') result = await vmBackupsContent(item);
        else result = await vmAuditContent(item);
        tabContent.replaceChildren(result);
      } catch (error) {
        tabContent.replaceChildren(node('div', { class: 'panel' }, node('p', { class: 'form-error', text: error.message })));
      }
    };

    tabs.forEach(([id, label]) => tabBar.append(node('button', {
      class: 'vm-tab',
      type: 'button',
      role: 'tab',
      'aria-selected': String(id === activeTab),
      'data-tab': id,
      onClick: () => renderTab(id),
    }, label)));

    const header = node('section', { class: 'vm-detail-header' },
      node('div', { class: 'vm-breadcrumbs' },
        node('button', { type: 'button', class: 'button link', onClick: () => navigate(returnView) }, returnLabel),
        node('span', { text: '/' }),
        node('span', { text: item.node || 'Proxmox' }),
        node('span', { text: '/' }),
        node('strong', { text: `VM ${item.vm_id}` })),
      node('div', { class: 'vm-title-row' },
        node('div', {},
          node('div', { class: 'vm-title-meta' }, badge(statusLabel(status.status || item.live?.status || 'unknown'), statusKind(status.status || item.live?.status)), badge(`VMID ${item.vm_id}`, 'info')),
          node('p', { class: 'muted', text: `${item.node || '—'} · ${status.tags || 'bez tagów'}` })),
        node('div', { class: 'action-group vm-detail-actions' },
          button('Odśwież', () => showVmDetailsPage(item, activeTab, returnView)),
          ...vmDetailActions(item))));

    dom.content.replaceChildren(header, tabBar, tabContent);
    await renderTab(activeTab);
    dom.content.focus();
  } catch (error) {
    toast(error.message, 'error');
    navigate(returnView);
  }
}

async function openVmManager(item, initialTab = 'overview', parentView = null) {
  return showVmDetailsPage(item, initialTab, parentView);
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

registerCommand('inventory.openVm', showVmDetailsPage);
registerView({ id: 'inventory', label: 'Zasoby', icon: 'V', permission: 'inventory.read', order: 100 }, inventoryView);
})();
