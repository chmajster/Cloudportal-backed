'use strict';

(() => {
function formatBytes(value) {
  const bytes = Number(value || 0);
  if (!bytes) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let size = bytes;
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  return (index ? size.toFixed(size >= 10 ? 1 : 2) : String(size)) + ' ' + units[index];
}

async function multipartFetch(path, options = {}, canRefresh = true) {
  const headers = {
    'X-Request-ID': crypto.randomUUID(),
    'X-Portal-Source': 'Cloudportal-backed',
    ...(options.headers || {}),
  };
  if (state.session?.access_token) headers.Authorization = 'Bearer ' + state.session.access_token;
  const response = await fetch(API + path, { ...options, headers });
  if (response.status === 401 && canRefresh && state.session?.refresh_token) {
    await window.cloudportalHttp.refreshSession();
    return multipartFetch(path, options, false);
  }
  return response;
}

async function responseError(response) {
  const data = await response.json().catch(() => ({}));
  throw new ApiError(response.status, data);
}

function setChoices(select, rows, selected = '', placeholder = 'Wybierz') {
  select.replaceChildren(
    node('option', { value: '', text: placeholder }),
    ...rows.map(row => node('option', { value: String(row.value), text: row.label }))
  );
  if (selected && rows.some(row => String(row.value) === String(selected))) {
    select.value = String(selected);
  } else if (rows.length === 1) {
    select.value = String(rows[0].value);
  }
}

function contentTypes(storage) {
  const raw = storage?.content || '';
  return new Set(Array.isArray(raw)
    ? raw.map(String)
    : String(raw).split(',').map(value => value.trim()).filter(Boolean));
}

function fileField() {
  const input = node('input', {
    type: 'file',
    name: 'file',
    accept: '.ova,application/x-tar,application/octet-stream',
    required: true,
  });
  const info = node('small', {
    class: 'field-help',
    text: 'OVA zostanie sprawdzone, dyski VMDK zostaną przekonwertowane do QCOW2 i wysłane do storage Proxmox typu Import.',
  });
  return {
    input,
    wrapper: node('label', { class: 'field wide' },
      node('span', { class: 'field-label', text: 'Plik appliance OVA' }),
      input,
      info),
  };
}

function defaultSlug(name) {
  return String(name || '')
    .toLowerCase()
    .normalize('NFKD')
    .replace(/[^a-z0-9_.-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 63) || 'appliance-ova';
}

async function openOvaImport() {
  if (!allowed('blueprints.create') || !allowed('terraform.execute') || !allowed('providers.read')) {
    toast('Brak uprawnień blueprints.create, terraform.execute lub providers.read.', 'error');
    return;
  }

  const providerResult = await api('/providers?limit=200');
  const providers = (providerResult.items || []).filter(item => item.type === 'proxmox');
  if (!providers.length) throw new Error('Najpierw skonfiguruj provider Proxmox.');

  const providerField = selectField(
    'Provider Proxmox', 'provider_id',
    providers.map(item => ({ value: item.id, label: item.name + ' (#' + item.id + ')' })),
    providers[0].id, { required: true }
  );
  const nodeField = selectField('Node Proxmox', 'node', [], '', { required: true, placeholder: 'Wybierz node' });
  const importStorageField = selectField(
    'Storage źródłowy OVA / Import', 'import_storage', [], '',
    { required: true, wide: true, help: 'Storage musi mieć w Proxmox włączony Content: Import.' }
  );
  const targetStorageField = selectField(
    'Storage dysków VM', 'storage', [], '',
    { required: true, wide: true, help: 'Tu trafią dyski każdej VM tworzonej z Blueprintu.' }
  );
  const networkField = selectField('Bridge / sieć', 'network', [], '', { required: true, placeholder: 'Wybierz bridge' });
  const diskBusField = selectField('Kontroler dysków', 'disk_bus', [
    { value: 'scsi', label: 'SCSI' },
    { value: 'virtio', label: 'VirtIO' },
    { value: 'sata', label: 'SATA' },
  ], 'scsi', { required: true });

  const nameField = field('Nazwa Blueprintu', 'name', {
    required: true,
    maxlength: 100,
    value: '',
    placeholder: 'np. FortiGate Appliance',
  });
  const slugField = field('Slug Blueprintu', 'slug', {
    required: true,
    maxlength: 63,
    value: '',
    placeholder: 'np. fortigate-appliance',
  });
  const vlanField = field('VLAN ID', 'vlan_id', {
    type: 'number',
    min: 1,
    max: 4094,
    placeholder: 'opcjonalnie',
  });
  const upload = fileField();
  const status = node('div', { class: 'callout info wide' },
    node('strong', { text: 'OVA → Blueprint → wiele VM' }),
    node('p', {
      text: 'Upload wykonywany jest tylko podczas tworzenia appliance. Późniejsze uruchomienia Blueprintu korzystają z obrazów Import zapisanych w Proxmox i nie wymagają ponownego wysyłania OVA.',
    }));

  const body = node('div', { class: 'form-grid' },
    nameField,
    slugField,
    upload.wrapper,
    providerField,
    nodeField,
    importStorageField,
    targetStorageField,
    networkField,
    diskBusField,
    vlanField,
    status
  );

  const providerSelect = providerField.querySelector('select');
  const nodeSelect = nodeField.querySelector('select');
  const importStorageSelect = importStorageField.querySelector('select');
  const targetStorageSelect = targetStorageField.querySelector('select');
  const networkSelect = networkField.querySelector('select');
  const nameInput = nameField.querySelector('input');
  const slugInput = slugField.querySelector('input');
  let slugTouched = false;

  slugInput.addEventListener('input', () => { slugTouched = true; });
  nameInput.addEventListener('input', () => {
    if (!slugTouched) slugInput.value = defaultSlug(nameInput.value);
  });

  async function loadNodeResources() {
    const providerId = providerSelect.value;
    const nodeName = nodeSelect.value;
    if (!providerId || !nodeName) return;
    const [storageResult, networkResult] = await Promise.all([
      api('/providers/' + providerId + '/storages?node=' + encodeURIComponent(nodeName)),
      api('/providers/' + providerId + '/networks?node=' + encodeURIComponent(nodeName)),
    ]);
    const storages = (storageResult.items || []).filter(item => !item.disable);
    const importStorages = storages.filter(item => contentTypes(item).has('import'));
    const imageStorages = storages.filter(item => contentTypes(item).has('images'));
    setChoices(importStorageSelect, importStorages.map(item => ({
      value: item.storage,
      label: item.storage + (item.type ? ' [' + item.type + ']' : ''),
    })), importStorageSelect.value, importStorages.length ? 'Wybierz storage Import' : 'Brak storage z Content: Import');
    setChoices(targetStorageSelect, imageStorages.map(item => ({
      value: item.storage,
      label: item.storage + (item.type ? ' [' + item.type + ']' : ''),
    })), targetStorageSelect.value, imageStorages.length ? 'Wybierz storage VM' : 'Brak storage images');
    const networks = (networkResult.items || []).filter(item => item.iface);
    setChoices(networkSelect, networks.map(item => ({
      value: item.iface,
      label: item.iface + (item.type ? ' [' + item.type + ']' : ''),
    })), networkSelect.value || 'vmbr0', 'Wybierz bridge');
  }

  async function loadProvider() {
    const providerId = providerSelect.value;
    const nodeResult = await api('/providers/' + providerId + '/nodes');
    const nodes = (nodeResult.items || []).filter(item => item.node);
    setChoices(nodeSelect, nodes.map(item => ({ value: item.node, label: item.node })), nodeSelect.value, 'Wybierz node');
    await loadNodeResources();
  }

  providerSelect.addEventListener('change', () => loadProvider().catch(error => toast(error.message, 'error')));
  nodeSelect.addEventListener('change', () => loadNodeResources().catch(error => toast(error.message, 'error')));
  await loadProvider();

  openModal({
    title: 'Importuj OVA jako Blueprint',
    eyebrow: 'Appliance / Proxmox',
    body,
    submitLabel: 'Uploaduj OVA i utwórz Blueprint',
    wide: true,
    onSubmit: async (_data, form) => {
      const file = upload.input.files?.[0];
      if (!file || !file.name.toLowerCase().endsWith('.ova')) {
        throw new Error('Wybierz plik .ova.');
      }
      if (!importStorageSelect.value) {
        throw new Error('Wybrany node nie ma storage z włączonym Content: Import.');
      }
      if (!targetStorageSelect.value) {
        throw new Error('Wybierz storage docelowy obsługujący Disk image.');
      }

      const query = new URLSearchParams({
        provider_id: providerSelect.value,
        node: nodeSelect.value,
        import_storage: importStorageSelect.value,
        storage: targetStorageSelect.value,
        network: networkSelect.value,
        slug: form.elements.slug.value.trim(),
        name: form.elements.name.value.trim(),
        disk_bus: form.elements.disk_bus.value,
      });
      const vlan = form.elements.vlan_id.value.trim();
      if (vlan) query.set('vlan_id', vlan);

      const payload = new FormData();
      payload.append('file', file, file.name);
      toast('Wysyłam ' + file.name + ' (' + formatBytes(file.size) + ') i przygotowuję appliance…');
      const response = await multipartFetch('/appliances/ova-blueprints?' + query.toString(), {
        method: 'POST',
        body: payload,
      });
      if (!response.ok) await responseError(response);
      const blueprint = await response.json();
      toast('Utworzono appliance Blueprint: ' + blueprint.name + '.');
      navigate('appliances');
    },
  });
}

async function appliancesView() {
  const result = await api('/blueprints?limit=200');
  const appliances = (result.items || []).filter(item => item.deployment?.template === 'proxmox-appliance');
  const actions = [];
  if (allowed('blueprints.create') && allowed('terraform.execute') && allowed('providers.read')) {
    actions.push(button('Importuj OVA jako Blueprint', () => openOvaImport().catch(error => toast(error.message, 'error')), 'primary'));
  }
  actions.push(button('Blueprinty', () => navigate('blueprints')));

  dom.content.replaceChildren(
    heading('Appliance OVA', actions),
    node('p', {
      class: 'muted',
      text: 'OVA jest importowane raz. Utworzony Blueprint może następnie tworzyć dowolną liczbę VM z zapisanych w Proxmox obrazów Import.',
    }),
    appliances.length ? table([
      { label: 'Appliance', value: item => node('div', {}, node('strong', { text: item.name }), node('div', { class: 'mono muted', text: item.slug + ' · v' + item.version })) },
      { label: 'Node', value: item => item.deployment?.variables?.node || '—' },
      { label: 'Storage VM', value: item => item.deployment?.variables?.storage || '—' },
      { label: 'Dyski źródłowe', value: item => (item.deployment?.variables?.import_file_ids || []).length },
      { label: 'Status', value: item => badge(item.is_active ? 'Aktywny' : 'Nieaktywny', item.is_active ? 'ok' : 'warning') },
      { label: 'Aktualizacja', value: item => formatDate(item.updated_at) },
    ], appliances, item => [
      hasCommand('blueprints.execute')
        ? button('Utwórz VM', () => runCommand('blueprints.execute', item), 'primary')
        : button('Produkty', () => navigate('deployments'), 'primary'),
      button('Blueprint', () => navigate('blueprints')),
    ]) : node('div', { class: 'empty-state' },
      node('strong', { text: 'Brak appliance OVA' }),
      node('p', { class: 'muted', text: 'Zaimportuj pierwszy plik OVA, aby utworzyć Blueprint appliance.' }))
  );
}

window.ApplianceBlueprintUI = Object.freeze({ open: openOvaImport });
registerExtension('appliance-blueprints', () => {});
registerView({
  id: 'appliances',
  label: 'Appliance OVA',
  iconName: 'box',
  permission: 'blueprints.read',
  order: 116,
}, appliancesView);
})();
