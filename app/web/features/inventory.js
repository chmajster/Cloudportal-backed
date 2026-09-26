'use strict';

(() => {
async function inventoryView() {
  const [vms, resources, providerResult] = await Promise.all([
    api('/inventory/vms?refresh=true&limit=200'),
    api('/inventory/resources?limit=200'),
    allowed('providers.read') ? api('/providers?limit=200') : Promise.resolve({ items: [] }),
  ]);
  const providerNames = new Map(providerResult.items.map(provider => [Number(provider.id), provider.name]));
  const visibleVms = (vms.items || []).filter(item => item.lifecycle_status !== 'destroyed');
  const actions = allowed('inventory.import') && allowed('providers.read') ? [button('Importuj istniejącą VM', () => navigate('/resources/import'), 'primary')] : [];
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
      ], visibleVms, item => inventoryVmActions(item))
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
      ], resources.items, item => [button('Szczegóły', () => navigate('/resources/managed/' + encodeURIComponent(item.id)))])
    )
  );
}

function inventoryVmActions(item) {
  const actions = [];
  if (allowed('vms.read') && item.lifecycle_status === 'active') actions.push(button('Szczegóły', () => navigate('/resources/vm/' + encodeURIComponent(item.id) + '/overview'), 'primary'));
  const canRecreate = item.lifecycle_status === 'active'
    && item.management_mode === 'terraform'
    && item.deployment_id
    && allowed('deployments.destroy')
    && allowed('deployments.create')
    && allowed('jobs.execute')
    && allowed('terraform.execute');
  if (canRecreate) actions.push(button('Odtwórz od zera', () => recreateVm(item), 'danger'));
  if (allowed('inventory.delete') && item.live === null && item.lifecycle_status !== 'destroyed') {
    actions.push(button('Usuń pozostałe dane', () => offerMissingVmCleanup(item, 'inventory'), 'danger'));
  }
  if (allowed('inventory.update')) actions.push(button('Odśwież stan', async () => {
    await api(`/inventory/vms/${item.id}/reconcile`, { method: 'POST' });
    toast('Stan zasobu odświeżony.');
    navigate('inventory');
  }));
  if (allowed('deployments.adopt') && allowed('terraform.read') && item.management_mode === 'external' && item.lifecycle_status === 'active' && !item.deployment_id) actions.push(button('Przejmij', () => navigate('/resources/vm/' + encodeURIComponent(item.id) + '/adopt')));
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

async function offerMissingVmCleanup(item, returnView = 'inventory') {
  let refreshed;
  try {
    refreshed = await api('/inventory/vms/' + encodeURIComponent(item.id) + '?refresh=true');
  } catch {
    return false;
  }

  if (refreshed.live !== null) return false;

  const label = item.name || ('VM ' + item.vm_id);
  const location = (item.node || 'Proxmox') + ' / VMID ' + item.vm_id;

  if (!allowed('inventory.delete')) {
    toast(
      label + ' (' + location + ') nie istnieje już w Proxmox. Brak uprawnienia inventory.delete do usunięcia nieaktualnego wpisu.',
      'warning'
    );
    return true;
  }

  openModal({
    title: 'VM nie istnieje w Proxmox',
    eyebrow: location,
    danger: true,
    submitLabel: 'Usuń pozostałe dane VM',
    body: node('div', { class: 'stack' },
      node('p', { text: label + ' nie została znaleziona na platformie Proxmox i ma status „Brak”.' }),
      node('p', { class: 'muted', text: 'Cloudportal ponownie potwierdzi brak VM w Proxmox, oznaczy powiązane wdrożenie jako zakończone/usunięte i usunie rekord VM oraz techniczny wpis inventory. Automatyczna synchronizacja nie odtworzy tego wpisu ze starego Terraform state. Historia wdrożenia, jobów i audytu pozostanie zachowana.' })),
    onSubmit: async () => {
      await api('/inventory/vms/' + encodeURIComponent(item.id) + '/missing?purge=true', { method: 'DELETE' });
      toast('Usunięto pozostałe dane brakującej VM z Cloudportalu.');
      await navigate(returnView);
    },
  });
  return true;
}

async function handleVmProviderFailure(item, error, returnView = 'inventory') {
  if (await offerMissingVmCleanup(item, returnView)) return true;
  toast(error.message, 'error');
  return false;
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

function recreateVm(item) {
  const name = item.name || ('VM ' + item.vm_id);
  confirmAction(
    'Odtwórz VM od zera',
    `VM „${name}” zostanie usunięta i utworzona ponownie z aktualnej definicji Terraform. Dane zapisane na dyskach tej VM mogą zostać bezpowrotnie utracone. Potwierdzić odtworzenie?`,
    async () => {
      const job = await api('/deployments/' + encodeURIComponent(item.deployment_id) + '/recreate', {
        method: 'POST',
        idempotent: true,
        body: {},
      });
      toast('Odtworzenie VM zostało zlecone jako zadanie ' + short(job.id, 18) + '.');
      await navigate('inventory');
    },
  );
}

function vmDetailActions(item, status = {}) {
  const base = vmBase(item);
  const runtime = vmRuntimeState(status, item);
  const groups = {
    power: [],
    tools: [],
    infrastructure: [],
    danger: [],
  };

  if (allowed('vms.power')) {
    if (runtime === 'running') {
      groups.power.push(
        button('Wyłącz', () => vmPower(item, 'shutdown'), 'primary'),
        button('Restart', () => vmPower(item, 'reboot'), 'ghost'),
        button('Wstrzymaj', () => vmPower(item, 'suspend'), 'ghost'),
      );
      groups.danger.push(
        button('Twardy reset', () => vmPower(item, 'reset'), 'danger'),
        button('Wymuś stop', () => vmPower(item, 'stop'), 'danger'),
      );
    } else if (runtime === 'paused' || runtime === 'suspended') {
      groups.power.push(button('Wznów', () => vmPower(item, 'resume'), 'primary'));
      groups.danger.push(button('Wymuś stop', () => vmPower(item, 'stop'), 'danger'));
    } else {
      groups.power.push(button('Uruchom', () => vmPower(item, 'start'), 'primary'));
    }
  }

  if (allowed('vms.console')) {
    groups.tools.push(button('Konsola', () => navigate('/resources/vm/' + encodeURIComponent(item.id) + '/console'), 'primary'));
  }
  if (allowed('snapshots.create')) {
    groups.tools.push(button('Snapshot', () => navigate('/resources/vm/' + encodeURIComponent(item.id) + '/snapshots/new')));
  }
  if (allowed('backups.create')) {
    groups.tools.push(button('Backup', () => navigate('/resources/vm/' + encodeURIComponent(item.id) + '/backups/new')));
  }

  if (allowed('vms.update')) {
    groups.infrastructure.push(
      button('CPU / RAM', () => navigate('/resources/vm/' + encodeURIComponent(item.id) + '/edit/compute')),
      button('Powiększ dysk', () => navigate('/resources/vm/' + encodeURIComponent(item.id) + '/edit/disk')),
    );
  }
  if (allowed('vms.migrate')) {
    groups.infrastructure.push(button('Migracja', () => navigate('/resources/vm/' + encodeURIComponent(item.id) + '/migrate')));
  }
  if (allowed('vms.clone')) {
    groups.infrastructure.push(button('Klonuj', () => navigate('/resources/vm/' + encodeURIComponent(item.id) + '/clone')));
  }
  if (allowed('vms.template')) {
    groups.infrastructure.push(button('→ Szablon', () => confirmAction(
      'Konwertuj do szablonu',
      'Operacja zmieni VM w szablon.',
      async () => {
        const result = await api(`${base}/template`, { method: 'POST', idempotent: true });
        await showProxmoxTask(item, result, 'Konwersja VM do szablonu');
        return false;
      },
    )));
  }

  const canRecreate = item.management_mode === 'terraform'
    && item.deployment_id
    && allowed('deployments.destroy')
    && allowed('deployments.create')
    && allowed('jobs.execute')
    && allowed('terraform.execute');
  if (canRecreate) groups.danger.push(button('Odtwórz od zera', () => recreateVm(item), 'danger'));
  if (allowed('vms.delete')) groups.danger.push(button('Usuń VM', () => deleteVm(item), 'danger'));

  return groups;
}

function vmActionGroup(title, description, actions, tone = '') {
  if (!actions?.length) return null;
  return node('section', { class: 'vm-action-group' + (tone ? ' ' + tone : '') },
    node('div', { class: 'vm-action-group-copy' },
      node('strong', { text: title }),
      node('small', { text: description })),
    node('div', { class: 'action-group vm-action-group-buttons' }, ...actions));
}

function vmUsagePercent(used, total) {
  const valid = Number.isFinite(Number(used)) && Number.isFinite(Number(total)) && Number(total) > 0;
  return valid ? Math.max(0, Math.min(100, Number(used) / Number(total) * 100)) : null;
}

function vmSummaryCard(label, value, detail, percent = null) {
  const safePercent = Number.isFinite(Number(percent)) ? Math.max(0, Math.min(100, Number(percent))) : null;
  return node('section', { class: 'vm-summary-card' },
    node('span', { class: 'vm-summary-label', text: label }),
    node('strong', { class: 'vm-summary-value', text: value }),
    safePercent === null ? null : node('div', { class: 'vm-summary-progress', 'aria-hidden': 'true' },
      node('span', { style: `width:${safePercent.toFixed(1)}%` })),
    node('small', { text: detail }));
}

function vmFact(label, value, mono = false) {
  return node('div', { class: 'vm-fact' },
    node('span', { text: label }),
    node('strong', { class: mono ? 'mono' : '', text: value || '—' }));
}

function vmTagChips(rawTags) {
  const tags = String(rawTags || '')
    .split(/[;,]+/)
    .map(value => value.trim())
    .filter(Boolean);
  if (!tags.length) return node('p', { class: 'muted', text: 'Brak tagów przypisanych do VM.' });
  return node('div', { class: 'vm-tag-list' },
    ...tags.map(tag => node('span', { class: 'vm-tag-chip mono', text: tag })));
}

function vmRuntimeState(status, item) {
  const qmpState = String(status.qmpstatus || '').trim().toLowerCase();
  if (qmpState === 'paused') return 'paused';
  return String(status.status || item.live?.status || 'unknown').trim().toLowerCase();
}

function vmRuntimeStateLabel(value) {
  const labels = {
    running: 'Uruchomiona',
    stopped: 'Wyłączona',
    paused: 'Wstrzymana',
    suspended: 'Wstrzymana',
  };
  return labels[value] || statusLabel(value);
}

function vmRuntimeStateKind(value) {
  if (value === 'running') return 'ok';
  if (value === 'paused' || value === 'suspended') return 'warning';
  if (value === 'stopped') return 'info';
  return statusKind(value);
}

function vmOverviewContent(item, status) {
  const primaryIp = status.primary_ip || item.primary_ip || '—';
  const cpuUsage = Number.isFinite(Number(status.cpu)) ? Math.max(0, Math.min(100, Number(status.cpu) * 100)) : null;
  const ramUsage = vmUsagePercent(status.mem, status.maxmem);
  const diskUsage = vmUsagePercent(status.disk, status.maxdisk);
  const cpuCount = status.cpus ?? status.maxcpu ?? '—';
  const ramValue = status.maxmem ? formatBytes(status.maxmem) : '—';
  const diskValue = status.maxdisk ? formatBytes(status.maxdisk) : '—';

  return node('div', { class: 'vm-detail-stack' },
    node('div', { class: 'vm-overview-grid' },
      vmSummaryCard('CPU', `${cpuCount} vCPU`,
        cpuUsage === null ? 'Brak telemetryki użycia CPU' : `Użycie ${Math.round(cpuUsage)}%`,
        cpuUsage),
      vmSummaryCard('RAM', ramValue,
        status.mem ? `${formatBytes(status.mem)} używane` : 'Brak telemetryki użycia RAM',
        ramUsage),
      vmSummaryCard('Dysk', diskValue,
        status.disk ? `${formatBytes(status.disk)} używane` : 'Brak telemetryki wykorzystania dysku',
        diskUsage),
      vmSummaryCard('Adres IP', primaryIp,
        primaryIp === '—' ? 'Brak adresu z inventory/QEMU Agent' : 'Adres podstawowy VM'),
      vmSummaryCard('Uptime', formatDuration(status.uptime),
        `${item.node || status.node || '—'} · VMID ${item.vm_id}`),
      vmSummaryCard('Zarządzanie', statusLabel(item.management_mode),
        item.deployment_id ? 'Deployment ' + short(item.deployment_id, 16) : 'Bez deploymentu')),

    node('div', { class: 'vm-overview-lower-grid' },
      node('section', { class: 'panel vm-facts-panel' },
        node('div', { class: 'panel-header' },
          node('div', {},
            node('h2', { text: 'Szczegóły maszyny' }),
            node('p', { class: 'muted', text: 'Tożsamość, lokalizacja i sposób zarządzania zasobem.' }))),
        node('div', { class: 'vm-facts-grid' },
          vmFact('Nazwa', status.name || item.name || `VM ${item.vm_id}`),
          vmFact('Node', item.node || status.node || '—'),
          vmFact('VMID', String(item.vm_id), true),
          vmFact('Adres IP', primaryIp, true),
          vmFact('Tryb zarządzania', statusLabel(item.management_mode)),
          vmFact('Lifecycle', statusLabel(item.lifecycle_status)),
          item.deployment_id ? vmFact('Deployment', item.deployment_id, true) : null,
          item.provider_id ? vmFact('Provider ID', String(item.provider_id), true) : null))),

      node('section', { class: 'panel vm-tags-panel' },
        node('div', { class: 'panel-header' },
          node('div', {},
            node('h2', { text: 'Klasyfikacja' }),
            node('p', { class: 'muted', text: 'Tagi synchronizowane z Proxmox i metadanymi CloudPortal.' }))),
        vmTagChips(status.tags)));
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
      allowed('vms.update') ? button('Edytuj CPU / RAM', () => navigate('/resources/vm/' + encodeURIComponent(item.id) + '/edit/compute')) : ''),
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
      allowed('snapshots.create') ? button('Nowy snapshot', () => navigate('/resources/vm/' + encodeURIComponent(item.id) + '/snapshots/new'), 'primary') : ''),
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
      ], rows, backup => allowed('backups.restore') ? [button('Przywróć', () => navigate('/resources/vm/' + encodeURIComponent(item.id) + '/backups/restore?archive=' + encodeURIComponent(backup.volid || '')), 'primary')] : []));
    } catch (error) {
      content.replaceChildren(node('p', { class: 'form-error', text: error.message }));
    }
  };
  const headerActions = [];
  if (allowed('backups.create')) headerActions.push(button('Nowy backup', () => navigate('/resources/vm/' + encodeURIComponent(item.id) + '/backups/new'), 'primary'));
  headerActions.push(button('Odśwież', load));
  shell.append(
    node('div', { class: 'panel-header' }, node('h2', { text: 'Backupy' }), node('div', { class: 'action-group' }, headerActions)),
    node('div', { class: 'vm-backup-toolbar' }, storageControl),
    content,
  );
  if (storages.length === 1) window.setTimeout(load, 0);
  return shell;
}

function vmMonitorNumeric(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : 0;
}

function vmMonitorPercent(value) {
  return `${Math.round(vmMonitorNumeric(value) * 100)}%`;
}

function vmMonitorRate(value) {
  return `${formatBytes(vmMonitorNumeric(value))}/s`;
}

function vmMonitorChart(title, rows, valueFor, formatter, ceiling = null) {
  const valid = rows.filter(row => Number.isFinite(Number(valueFor(row))));
  if (!valid.length) {
    return node('section', { class: 'vm-monitor-chart' },
      node('div', { class: 'vm-monitor-chart-head' }, node('strong', { text: title })),
      node('div', { class: 'empty compact', text: 'Brak danych RRD dla wybranego zakresu.' }));
  }

  const step = Math.max(1, Math.ceil(valid.length / 72));
  const samples = valid.filter((_, index) => index % step === 0);
  const last = valid[valid.length - 1];
  if (samples[samples.length - 1] !== last) samples.push(last);

  const values = samples.map(row => Math.max(0, vmMonitorNumeric(valueFor(row))));
  const maxValue = ceiling && Number(ceiling) > 0
    ? Number(ceiling)
    : Math.max(1, ...values);
  const latestValue = values[values.length - 1];
  const bars = node('div', { class: 'vm-monitor-bars', role: 'img', 'aria-label': title });

  samples.forEach((row, index) => {
    const value = values[index];
    const height = value > 0 ? Math.max(2, Math.min(100, (value / maxValue) * 100)) : 0;
    const when = row.time
      ? new Date(Number(row.time) * 1000).toLocaleString('pl-PL')
      : 'brak czasu';
    bars.append(node('span', {
      class: 'vm-monitor-bar',
      style: `height:${height}%`,
      title: `${when}: ${formatter(value, row)}`,
    }));
  });

  return node('section', { class: 'vm-monitor-chart' },
    node('div', { class: 'vm-monitor-chart-head' },
      node('strong', { text: title }),
      node('span', { text: formatter(latestValue, last) })),
    bars,
    node('div', { class: 'vm-monitor-chart-axis' },
      node('span', { text: samples[0]?.time ? new Date(Number(samples[0].time) * 1000).toLocaleString('pl-PL') : '—' }),
      node('span', { text: last?.time ? new Date(Number(last.time) * 1000).toLocaleString('pl-PL') : '—' })));
}

function vmMonitorCurrent(item, current) {
  const cpu = vmMonitorNumeric(current.cpu);
  const ramUsed = vmMonitorNumeric(current.mem);
  const ramMax = vmMonitorNumeric(current.maxmem);
  return node('div', { class: 'vm-monitor-current-grid' },
    node('section', { class: 'vm-stat-card' },
      node('span', { text: 'Stan' }),
      badge(statusLabel(current.status || item.live?.status || 'unknown'), statusKind(current.status || item.live?.status)),
      node('small', { text: current.qmpstatus || item.node || '—' })),
    node('section', { class: 'vm-stat-card' },
      node('span', { text: 'CPU' }),
      node('strong', { text: vmMonitorPercent(cpu) }),
      node('small', { text: `${current.cpus ?? '—'} vCPU` })),
    node('section', { class: 'vm-stat-card' },
      node('span', { text: 'RAM' }),
      node('strong', { text: ramMax ? `${formatBytes(ramUsed)} / ${formatBytes(ramMax)}` : formatBytes(ramUsed) }),
      node('small', { text: ramMax ? `${Math.round((ramUsed / ramMax) * 100)}% użycia` : 'brak limitu' })),
    node('section', { class: 'vm-stat-card' },
      node('span', { text: 'Net IN' }),
      node('strong', { text: formatBytes(vmMonitorNumeric(current.netin)) }),
      node('small', { text: 'licznik od startu VM' })),
    node('section', { class: 'vm-stat-card' },
      node('span', { text: 'Net OUT' }),
      node('strong', { text: formatBytes(vmMonitorNumeric(current.netout)) }),
      node('small', { text: 'licznik od startu VM' })),
    node('section', { class: 'vm-stat-card' },
      node('span', { text: 'Disk read' }),
      node('strong', { text: formatBytes(vmMonitorNumeric(current.diskread)) }),
      node('small', { text: 'licznik od startu VM' })),
    node('section', { class: 'vm-stat-card' },
      node('span', { text: 'Disk write' }),
      node('strong', { text: formatBytes(vmMonitorNumeric(current.diskwrite)) }),
      node('small', { text: `uptime ${formatDuration(current.uptime)}` })));
}

async function vmMonitorContent(item) {
  const timeframeField = selectField('Zakres danych', 'vm_monitor_timeframe', [
    { value: 'hour', label: 'Ostatnia godzina' },
    { value: 'day', label: 'Ostatnie 24 godziny' },
    { value: 'week', label: 'Ostatnie 7 dni' },
    { value: 'month', label: 'Ostatni miesiąc' },
    { value: 'year', label: 'Ostatni rok' },
  ], 'hour');
  const timeframe = timeframeField.querySelector('select');
  const content = node('div', { class: 'vm-detail-stack' });
  const shell = node('div', { class: 'vm-detail-stack' });

  const load = async () => {
    content.replaceChildren(node('div', { class: 'loading compact' }, node('div', { class: 'spinner' })));
    try {
      const data = await api(`${vmBase(item)}/monitor?timeframe=${encodeURIComponent(timeframe.value)}`);
      const current = data.current || {};
      const rows = data.series || [];
      const ramCeiling = vmMonitorNumeric(current.maxmem)
        || Math.max(0, ...rows.map(row => vmMonitorNumeric(row.maxmem)));
      content.replaceChildren(
        vmMonitorCurrent(item, current),
        node('section', { class: 'panel' },
          node('div', { class: 'panel-header' },
            node('div', {},
              node('h2', { text: 'Metryki historyczne' }),
              node('p', { class: 'muted', text: 'Źródło: Proxmox API / RRD. Wykresy pokazują próbki zwrócone przez Proxmox.' })),
            badge(`${rows.length} próbek`, rows.length ? 'ok' : 'warning')),
          node('div', { class: 'vm-monitor-grid' },
            vmMonitorChart('CPU', rows, row => row.cpu, value => vmMonitorPercent(value), 1),
            vmMonitorChart('RAM', rows, row => row.mem, value => formatBytes(value), ramCeiling || null),
            vmMonitorChart('Network IN', rows, row => row.netin, value => vmMonitorRate(value)),
            vmMonitorChart('Network OUT', rows, row => row.netout, value => vmMonitorRate(value)),
            vmMonitorChart('Disk read', rows, row => row.diskread, value => vmMonitorRate(value)),
            vmMonitorChart('Disk write', rows, row => row.diskwrite, value => vmMonitorRate(value)))),
      );
    } catch (error) {
      content.replaceChildren(node('div', { class: 'panel' }, node('p', { class: 'form-error', text: error.message })));
    }
  };

  const refresh = button('Odśwież', load, 'primary');
  timeframe.addEventListener('change', load);
  shell.append(
    node('section', { class: 'panel vm-monitor-toolbar-panel' },
      node('div', { class: 'panel-header' },
        node('div', {},
          node('h2', { text: 'Monitor Proxmox' }),
          node('p', { class: 'muted', text: `${item.node || '—'} / VMID ${item.vm_id}` })),
        refresh),
      node('div', { class: 'vm-monitor-toolbar' }, timeframeField)),
    content,
  );
  await load();
  return shell;
}

function vmHistoryKindLabel(kind) {
  return ({
    lifecycle: 'Lifecycle',
    deployment: 'Deployment',
    job: 'Job',
    provisioning: 'Provisioning',
    day2: 'Day-2',
    audit: 'Audit',
  })[kind] || kind || 'Inne';
}

function vmHistoryKindTone(kind) {
  if (kind === 'provisioning' || kind === 'job') return 'info';
  if (kind === 'day2') return 'warning';
  if (kind === 'lifecycle' || kind === 'deployment') return 'ok';
  return 'info';
}

function vmHistoryStatusTone(status) {
  const value = String(status || '').toLowerCase();
  if (['successful', 'success', 'succeeded', 'succeeded_with_warning', 'completed', 'active'].includes(value)) return 'ok';
  if (['failed', 'failure', 'cancelled', 'destroyed', 'missing'].includes(value)) return 'danger';
  if (['running', 'queued', 'cancelling', 'requested', 'waiting_approval'].includes(value)) return 'warning';
  return 'info';
}

async function vmAuditContent(item) {
  const rows = (await api(`/inventory/vms/${encodeURIComponent(item.id)}/history?limit=500`)).items || [];
  return node('section', { class: 'panel' },
    node('div', { class: 'panel-header' },
      node('div', {},
        node('h2', { text: 'Historia VM' }),
        node('p', { class: 'muted', text: 'Lifecycle, provisioning, joby, operacje Day-2 i audyt dostępny dla bieżących uprawnień.' })),
      badge(String(rows.length), 'info')),
    rows.length ? table([
      { label: 'Czas', value: row => formatDate(row.timestamp) },
      { label: 'Typ', value: row => badge(vmHistoryKindLabel(row.kind), vmHistoryKindTone(row.kind)) },
      { label: 'Zdarzenie', value: row => node('strong', { text: row.title || '—' }) },
      { label: 'Status', value: row => row.status ? badge(statusLabel(row.status), vmHistoryStatusTone(row.status)) : '—' },
      { label: 'Szczegóły', value: row => row.detail || '—' },
      { label: 'Użytkownik', value: row => row.actor_user_id || '—' },
      { label: 'ID', value: row => node('span', { class: 'mono', text: [
        row.job_id ? `job ${short(row.job_id, 12)}` : null,
        row.request_id ? `req ${short(row.request_id, 12)}` : null,
      ].filter(Boolean).join(' · ') || '—' }) },
    ], rows, row => row.job_id && allowed('jobs.read')
      ? [button('Logi', () => navigate('/jobs/' + encodeURIComponent(row.job_id)), 'ghost')]
      : []) : node('div', { class: 'empty', text: 'Brak zapisanej historii dla tej VM.' }));
}

async function showVmDetailsPage(item, initialTab = 'overview', parentView = null) {
  const returnView = parentView || (viewIs('my-resources') ? 'my-resources' : 'inventory');
  const returnLabel = 'Moje zasoby';
  const routedDetails = state.view === 'routed-form'
    && matchRoutedForm()?.route?.id === 'inventory-vm-details';
  try {
    const status = await api(`${vmBase(item)}/status`);
    if (!routedDetails) {
      state.view = returnView;
      location.hash = typeof window.uiRoutePath === 'function' ? window.uiRoutePath(returnView) : returnView;
    }
    dom.pageEyebrow.textContent = `${returnLabel} / ${item.node || 'Proxmox'} / VMID ${item.vm_id}`;
    dom.pageTitle.textContent = status.name || item.name || `VM ${item.vm_id}`;
    dom.navigation.querySelectorAll('.nav-link').forEach(link => link.classList.toggle('active', link.dataset.route === returnView));

    const tabContent = node('div', { class: 'vm-tab-content' });
    const tabs = [
      ['overview', 'Przegląd'],
      ['monitor', 'Monitor'],
      ['hardware', 'Hardware'],
      ['snapshots', 'Snapshoty', 'snapshots.read'],
      ['backups', 'Backupy', 'backups.read'],
      ['audit', 'Historia'],
    ].filter(([, , permission]) => allowed(permission));

    let activeTab = tabs.some(([id]) => id === initialTab) ? initialTab : 'overview';
    const tabBar = node('div', { class: 'vm-tabs', role: 'tablist', 'aria-label': 'Szczegóły VM' });

    const renderTab = async id => {
      activeTab = id;
      if (routedDetails) {
        const tabPath = '/resources/vm/' + encodeURIComponent(item.id) + '/' + encodeURIComponent(id);
        state.routePath = tabPath;
        history.replaceState(history.state, '', '#' + tabPath);
      }
      tabBar.querySelectorAll('.vm-tab').forEach(tab => {
        const active = tab.dataset.tab === id;
        tab.classList.toggle('active', active);
        tab.setAttribute('aria-selected', String(active));
      });
      tabContent.replaceChildren(node('div', { class: 'loading compact' }, node('div', { class: 'spinner' })));
      try {
        let result;
        if (id === 'overview') result = vmOverviewContent(item, status);
        else if (id === 'monitor') result = await vmMonitorContent(item);
        else if (id === 'hardware') result = vmHardwareContent(item, status);
        else if (id === 'snapshots') result = await vmSnapshotsContent(item);
        else if (id === 'backups') result = await vmBackupsContent(item);
        else if (id === 'audit') result = await vmAuditContent(item);
        else result = vmOverviewContent(item, status);
        tabContent.replaceChildren(result);
      } catch (error) {
        if (await offerMissingVmCleanup(item, returnView)) return;
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

    const runtimeState = vmRuntimeState(status, item);
    const primaryIp = status.primary_ip || item.primary_ip || '—';
    const actionGroups = vmDetailActions(item, status);
    const header = node('section', { class: 'vm-detail-header' },
      node('div', { class: 'vm-breadcrumbs' },
        node('button', { type: 'button', class: 'button link', onClick: () => navigate(returnView) }, returnLabel),
        node('span', { text: '/' }),
        node('span', { text: item.node || 'Proxmox' }),
        node('span', { text: '/' }),
        node('strong', { text: `VM ${item.vm_id}` })),

      node('div', { class: 'vm-hero' },
        node('div', { class: 'vm-hero-main' },
          node('div', { class: 'vm-hero-icon', 'aria-hidden': 'true' }, appIcon('server')),
          node('div', { class: 'vm-hero-copy' },
            node('div', { class: 'vm-title-meta' },
              badge(vmRuntimeStateLabel(runtimeState), vmRuntimeStateKind(runtimeState)),
              badge(`VMID ${item.vm_id}`, 'info'),
              badge(statusLabel(item.management_mode), item.management_mode === 'terraform' ? 'ok' : 'info')),
            node('strong', { class: 'vm-hero-name', text: status.name || item.name || `VM ${item.vm_id}` }),
            node('div', { class: 'vm-hero-facts' },
              node('span', { text: item.node || status.node || '—' }),
              node('span', { class: 'mono', text: primaryIp }),
              item.deployment_id ? node('span', { class: 'mono', text: 'deployment ' + short(item.deployment_id, 14) }) : null))),
        node('div', { class: 'vm-hero-actions' },
          button('Odśwież', () => showVmDetailsPage(item, activeTab, returnView), 'ghost'))),

      node('div', { class: 'vm-command-center' },
        vmActionGroup('Zasilanie', 'Codzienne sterowanie stanem VM.', actionGroups.power),
        vmActionGroup('Operacje', 'Konsola, snapshoty i backupy.', actionGroups.tools),
        vmActionGroup('Infrastruktura', 'Zmiany parametrów i relokacja VM.', actionGroups.infrastructure),
        vmActionGroup('Strefa ryzyka', 'Operacje mogące przerwać pracę lub usunąć dane.', actionGroups.danger, 'danger')));

    dom.content.replaceChildren(header, tabBar, tabContent);
    await renderTab(activeTab);
    dom.content.focus();
  } catch (error) {
    const handled = await handleVmProviderFailure(item, error, returnView);
    if (!handled) navigate(returnView);
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
  } catch (error) {
    await handleVmProviderFailure(item, error, viewIs('my-resources') ? 'my-resources' : 'inventory');
  }
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
      ], rows, backup => allowed('backups.restore') ? [button('Przywróć', () => navigate('/resources/vm/' + encodeURIComponent(item.id) + '/backups/restore?archive=' + encodeURIComponent(backup.volid || '')), 'primary')] : []);
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
    const result = await api(`${vmBase(item)}/console`, { method: 'POST' });
    const rfbModule = result.local_rfb_module || result.rfb_module;
    const modulePathValid = result.local_rfb_module
      ? result.local_rfb_module === '/ui/vendor/novnc/core/rfb.js'
      : result.rfb_module?.startsWith('/api/v1/console-sessions/');
    if (
      result.mode !== 'novnc'
      || !modulePathValid
      || !result.ws_path?.startsWith('/api/v1/console-sessions/')
    ) {
      throw new Error('Backend nie zwrócił poprawnej sesji noVNC.');
    }

    const screen = node('div', { class: 'novnc-screen' });
    const status = node('p', { class: 'muted novnc-status', text: 'Łączenie z konsolą przez Cloudportal-backed…' });
    const toolbar = node('div', { class: 'novnc-toolbar', role: 'toolbar', 'aria-label': 'Sterowanie maszyną wirtualną' });
    let ctrlAltDelButton = null;

    const consolePower = async (action, label, trigger) => {
      trigger.disabled = true;
      try {
        await api(`${vmBase(item)}/power`, { method: 'POST', idempotent: true, body: { action } });
        toast(`${label}: polecenie zostało wysłane.`);
      } catch (error) {
        toast(error.message, 'error');
      } finally {
        trigger.disabled = false;
      }
    };

    if (allowed('vms.power')) {
      const powerOnButton = button('Power ON', () => consolePower('start', 'Uruchamianie VM', powerOnButton), 'primary');
      const powerOffButton = button('Power OFF', () => consolePower('shutdown', 'Wyłączanie VM', powerOffButton), 'danger');
      powerOnButton.title = 'Uruchom VM';
      powerOffButton.title = 'Bezpiecznie wyłącz VM (ACPI)';
      toolbar.append(powerOnButton, powerOffButton);
    }

    ctrlAltDelButton = button('CTRL+ALT+DEL', () => {
      const activeRfb = state.consoleRfb;
      if (!activeRfb) {
        toast('Konsola noVNC nie jest połączona.', 'error');
        return;
      }
      activeRfb.sendCtrlAltDel();
      toast('Wysłano CTRL+ALT+DEL do VM.');
    }, 'ghost');
    ctrlAltDelButton.disabled = true;
    ctrlAltDelButton.title = 'Wyślij CTRL+ALT+DEL do systemu gościa';
    toolbar.append(ctrlAltDelButton);

    dom.modalTitle.textContent = 'Konsola noVNC';
    dom.modalEyebrow.textContent = item.name || `${item.node} / ${item.vm_id}`;
    dom.modalBody.replaceChildren(toolbar, status, screen);
    dom.modalActions.replaceChildren(button('Zamknij', closeModal));
    dom.modal.classList.add('modal-console');
    dom.modal.showModal();

    // The RFB implementation is shipped with Cloudportal, so module loading
    // cannot depend on Proxmox static-file MIME, redirects or noVNC patch level.
    const module = await import(rfbModule);
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
    rfb.addEventListener('connect', () => {
      status.textContent = 'Połączono przez backend proxy.';
      ctrlAltDelButton.disabled = false;
    });
    rfb.addEventListener('disconnect', event => {
      if (state.consoleRfb === rfb) state.consoleRfb = null;
      ctrlAltDelButton.disabled = true;
      status.textContent = event.detail?.clean ? 'Konsola rozłączona.' : 'Połączenie konsoli zostało przerwane.';
    });
    rfb.addEventListener('credentialsrequired', () => {
      rfb.sendCredentials({ password: result.password });
    });
  } catch (error) {
    state.consoleRfb = null;
    if (typeof window.modalSurfaceOpen === 'function' ? window.modalSurfaceOpen() : dom.modal.open) closeModal();
    await handleVmProviderFailure(item, error, viewIs('my-resources') ? 'my-resources' : 'inventory');
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
  } catch (error) {
    await handleVmProviderFailure(item, error, viewIs('my-resources') ? 'my-resources' : 'inventory');
  }
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

async function routedVmItem(match) {
  const rows = (await api('/inventory/vms?limit=200')).items || [];
  const item = rows.find(value => String(value.id) === String(match.params.id));
  if (!item) throw new Error('Nie znaleziono VM w inventory.');
  return item;
}

registerRoutedForm({
  id: 'managed-resource-details',
  pattern: /^\/resources\/managed\/(?<id>[^/]+)$/,
  parent: 'my-resources',
  permission: 'inventory.read',
  label: 'Moje zasoby',
}, async match => {
  const rows = (await api('/inventory/resources?limit=200')).items || [];
  const item = rows.find(value => String(value.id) === String(match.params.id));
  if (!item) throw new Error('Nie znaleziono zasobu zarządzanego.');
  showObjectDetails(item.name || item.resource_type || 'Zasób', {
    Typ: item.resource_type,
    Platforma: item.provider,
    'External ID': item.external_id,
    IP: item.primary_ip || '—',
    Status: statusLabel(item.lifecycle_status),
    Wdrożenie: item.deployment_id,
    ...(item.metadata_json || {}),
  }, 'Zasób zarządzany');
});

registerRoutedForm({
  id: 'inventory-vm-details',
  pattern: /^\/resources\/vm\/(?<id>[^/]+)\/(?<tab>overview|hardware|snapshots|backups|audit)$/,
  parent: 'my-resources',
  permission: 'vms.read',
  label: 'Moje zasoby',
  surface: false,
}, async match => showVmDetailsPage(await routedVmItem(match), match.params.tab, 'my-resources'));

registerRoutedForm({
  id: 'inventory-import',
  pattern: /^\/resources\/import$/,
  parent: 'my-resources',
  permission: 'inventory.import',
  label: 'Moje zasoby',
}, () => importInventoryVm());
registerRoutedForm({
  id: 'inventory-adopt',
  pattern: /^\/resources\/vm\/(?<id>[^/]+)\/adopt$/,
  parent: 'my-resources',
  permission: 'deployments.adopt',
  label: 'Moje zasoby',
}, async match => adoptInventoryVm(await routedVmItem(match)));
registerRoutedForm({
  id: 'inventory-console',
  pattern: /^\/resources\/vm\/(?<id>[^/]+)\/console$/,
  parent: 'my-resources',
  permission: 'vms.console',
  label: 'Moje zasoby',
}, async match => showVmConsole(await routedVmItem(match)));
registerRoutedForm({
  id: 'inventory-snapshot-create',
  pattern: /^\/resources\/vm\/(?<id>[^/]+)\/snapshots\/new$/,
  parent: 'my-resources',
  permission: 'snapshots.create',
  label: 'Moje zasoby',
}, async match => createVmSnapshot(await routedVmItem(match)));
registerRoutedForm({
  id: 'inventory-backup-create',
  pattern: /^\/resources\/vm\/(?<id>[^/]+)\/backups\/new$/,
  parent: 'my-resources',
  permission: 'backups.create',
  label: 'Moje zasoby',
}, async match => backupVm(await routedVmItem(match)));
registerRoutedForm({
  id: 'inventory-backup-restore',
  pattern: /^\/resources\/vm\/(?<id>[^/]+)\/backups\/restore$/,
  parent: 'my-resources',
  permission: 'backups.restore',
  label: 'Moje zasoby',
}, async match => {
  const archive = match.searchParams.get('archive') || '';
  if (!archive) throw new Error('Brak identyfikatora backupu.');
  restoreVmFromBackup(await routedVmItem(match), { volid: archive });
});
registerRoutedForm({
  id: 'inventory-compute-edit',
  pattern: /^\/resources\/vm\/(?<id>[^/]+)\/edit\/compute$/,
  parent: 'my-resources',
  permission: 'vms.update',
  label: 'Moje zasoby',
}, async match => configureVm(await routedVmItem(match)));
registerRoutedForm({
  id: 'inventory-disk-edit',
  pattern: /^\/resources\/vm\/(?<id>[^/]+)\/edit\/disk$/,
  parent: 'my-resources',
  permission: 'vms.update',
  label: 'Moje zasoby',
}, async match => resizeVmDisk(await routedVmItem(match)));
registerRoutedForm({
  id: 'inventory-migrate',
  pattern: /^\/resources\/vm\/(?<id>[^/]+)\/migrate$/,
  parent: 'my-resources',
  permission: 'vms.migrate',
  label: 'Moje zasoby',
}, async match => migrateVm(await routedVmItem(match)));
registerRoutedForm({
  id: 'inventory-clone',
  pattern: /^\/resources\/vm\/(?<id>[^/]+)\/clone$/,
  parent: 'my-resources',
  permission: 'vms.clone',
  label: 'Moje zasoby',
}, async match => cloneVm(await routedVmItem(match)));

registerCommand('inventory.openVm', (item, initialTab = 'overview') => navigate('/resources/vm/' + encodeURIComponent(item.id) + '/' + encodeURIComponent(initialTab || 'overview')));
registerCommand('inventory.consoleVm', item => navigate('/resources/vm/' + encodeURIComponent(item.id) + '/console'));
registerCommand('inventory.recreateVm', recreateVm);
registerCommand('inventory.cleanupMissingVm', (item, returnView = 'my-resources') => offerMissingVmCleanup(item, returnView));
registerView({ id: 'inventory', label: 'Moje zasoby', icon: 'V', permission: 'inventory.read', order: 100 }, inventoryView);
})();
