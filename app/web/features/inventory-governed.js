'use strict';

(() => {
const POWER_ACTIONS = Object.freeze({
  start: 'power_on',
  stop: 'power_off',
  shutdown: 'shutdown',
  reboot: 'reboot',
  reset: 'reset',
  suspend: 'suspend',
  resume: 'resume',
});

function applies(item) {
  const scope = globalThis.CPProjectContext?.current?.();
  return Boolean(
    scope?.tenant_id
    && scope?.project_id
    && item?.deployment_id
    && ['terraform', 'proxmox'].includes(String(item?.management_mode || ''))
  );
}

function resourceBase(item) {
  return `/resources/${encodeURIComponent(item.id)}`;
}

function confirmationName(item) {
  return String(item?.name || ('vm-' + item?.vm_id));
}

function normalizeStatus(item, payload) {
  const live = payload?.live || {};
  const config = live.configuration || {};
  const memoryMb = Number(live.memory_mb ?? config.memory);
  return {
    vmid: live.vm_id ?? item.vm_id,
    name: live.name || item.name || '',
    status: live.power_state || item.live?.status || item.lifecycle_status || 'unknown',
    qmpstatus: null,
    cpu: null,
    cpus: live.cpu_cores ?? config.cores ?? null,
    mem: null,
    maxmem: Number.isFinite(memoryMb) && memoryMb > 0 ? memoryMb * 1024 * 1024 : null,
    disk: null,
    maxdisk: null,
    uptime: null,
    pid: null,
    lock: null,
    template: null,
    tags: Array.isArray(live.tags) ? live.tags.join(';') : String(config.tags || ''),
    primary_ip: live.primary_ip || item.primary_ip || null,
    node: live.node || item.node || '',
  };
}

async function status(item, rawBase) {
  if (!applies(item)) return api(`${rawBase}/status`);
  return normalizeStatus(item, await api(`${resourceBase(item)}/day2-state`));
}

async function catalog(item) {
  if (!applies(item)) return null;
  return api(`${resourceBase(item)}/actions`);
}

function action(catalogData, actionId) {
  return (catalogData?.actions || []).find(row => row.id === actionId) || null;
}

function supported(item, catalogData, actionId) {
  return !applies(item) || action(catalogData, actionId)?.supported === true;
}

async function execute(item, actionId, parameters = {}, reason = '') {
  return api(`${resourceBase(item)}/actions/${encodeURIComponent(actionId)}`, {
    method: 'POST',
    idempotent: true,
    body: { parameters, reason },
  });
}

function submitted(label, result) {
  const suffix = result?.job_id ? ' · job ' + short(result.job_id, 18) : '';
  toast(label + ' zostało zlecone' + suffix + '.');
}

async function snapshotInfo(item, rawBase, catalogData = null) {
  if (!applies(item)) return api(`${rawBase}/snapshots`);
  const [snapshotResult, effectiveCatalog] = await Promise.all([
    api(`${resourceBase(item)}/snapshots`),
    catalogData ? Promise.resolve(catalogData) : catalog(item),
  ]);
  return {
    items: (snapshotResult.items || []).map(snapshot => ({
      ...snapshot,
      snaptime: snapshot.created,
      vmstate: snapshot.include_memory,
    })),
    capability: effectiveCatalog?.capabilities?.snapshot_capability || {},
  };
}

async function powerRequest(item, rawBase, powerAction, reason = 'Sterowanie zasilaniem VM z widoku Moje zasoby') {
  if (!applies(item)) {
    return {
      mode: 'provider',
      result: await api(`${rawBase}/power`, { method: 'POST', idempotent: true, body: { action: powerAction } }),
    };
  }
  const actionId = POWER_ACTIONS[powerAction];
  if (!actionId) throw new Error('Nieobsługiwana akcja zasilania VM.');
  return { mode: 'day2', result: await execute(item, actionId, {}, reason) };
}

function consolePath(item, rawBase) {
  return applies(item) ? `${resourceBase(item)}/console` : `${rawBase}/console`;
}

async function createSnapshot(item, data) {
  const result = await execute(item, 'create_snapshot', {
    name: data.get('snapname'),
    description: data.get('description'),
    include_memory: data.has('include_ram'),
  }, 'Utworzenie snapshotu z widoku Moje zasoby');
  submitted('Tworzenie snapshotu', result);
}

async function restoreSnapshot(item, snapName) {
  const result = await execute(item, 'restore_snapshot', {
    name: snapName,
    confirmation: confirmationName(item),
  }, 'Przywrócenie snapshotu z widoku Moje zasoby');
  submitted('Przywracanie snapshotu', result);
}

async function deleteSnapshot(item, snapName) {
  const result = await execute(item, 'delete_snapshot', {
    name: snapName,
    confirmation: confirmationName(item),
  }, 'Usunięcie snapshotu z widoku Moje zasoby');
  submitted('Usuwanie snapshotu', result);
}

async function configureCompute(item, currentStatus) {
  const fields = node('div', { class: 'form-grid' },
    field('Rdzenie CPU', 'cores', { type: 'number', min: 1, value: currentStatus.cpus ?? '' }),
    field('RAM (MiB)', 'memory', {
      type: 'number',
      min: 512,
      value: currentStatus.maxmem ? Math.round(currentStatus.maxmem / 1024 / 1024) : '',
    }));
  openModal({
    title: 'CPU / RAM',
    eyebrow: item.name || String(item.vm_id),
    body: fields,
    onSubmit: async data => {
      const parameters = {};
      if (data.get('cores') && Number(data.get('cores')) !== Number(currentStatus.cpus)) {
        parameters.cpu_cores = Number(data.get('cores'));
      }
      const currentMemory = currentStatus.maxmem ? Math.round(currentStatus.maxmem / 1024 / 1024) : null;
      if (data.get('memory') && Number(data.get('memory')) !== currentMemory) {
        parameters.memory_mb = Number(data.get('memory'));
      }
      if (!Object.keys(parameters).length) throw new Error('Nie wykryto żadnej zmiany.');
      const result = await execute(item, 'resize_compute', parameters, 'Zmiana CPU / RAM z widoku Moje zasoby');
      submitted('Zmiana CPU / RAM', result);
      return false;
    },
  });
}

async function resizeDisk(item, device, growGib) {
  const disks = (await api(`${resourceBase(item)}/disks`)).items || [];
  const current = disks.find(row => row.device === device);
  if (!current || !Number.isFinite(Number(current.size_gib))) {
    throw new Error('Nie można ustalić bieżącego rozmiaru dysku.');
  }
  const result = await execute(item, 'resize_disk', {
    device,
    new_size_gib: Math.ceil(Number(current.size_gib) + Number(growGib)),
  }, 'Powiększenie dysku z widoku Moje zasoby');
  submitted('Powiększanie dysku', result);
}

async function migrate(item, data) {
  const result = await execute(item, 'migrate_vm', {
    target_node: data.get('target'),
    online: data.has('online'),
    with_local_disks: data.has('with_local_disks'),
  }, 'Migracja VM z widoku Moje zasoby');
  submitted('Migracja VM', result);
}

async function clone(item, data) {
  const parameters = {
    new_vm_id: Number(data.get('new_vm_id')),
    name: data.get('name'),
    full: data.has('full'),
  };
  if (data.get('target')) parameters.target_node = data.get('target');
  if (data.get('storage')) parameters.target_storage = data.get('storage');
  const result = await execute(item, 'clone_vm', parameters, 'Klonowanie VM z widoku Moje zasoby');
  submitted('Klonowanie VM', result);
}

globalThis.InventoryGovernance = Object.freeze({
  applies,
  resourceBase,
  status,
  catalog,
  supported,
  execute,
  submitted,
  snapshotInfo,
  powerRequest,
  consolePath,
  createSnapshot,
  restoreSnapshot,
  deleteSnapshot,
  configureCompute,
  resizeDisk,
  migrate,
  clone,
});

registerExtension('inventory-governed-day2', () => {});
})();
