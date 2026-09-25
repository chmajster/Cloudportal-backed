'use strict';

(() => {
async function providersView() {
  const [providerResult, credentialResult] = await Promise.all([
    api('/providers?limit=200'),
    allowed('credentials.read') ? api('/credentials?limit=200') : Promise.resolve({ items: [] }),
  ]);
  const providers = providerResult.items;
  const credentialNames = new Map(credentialResult.items.map(item => [Number(item.id), item.name]));
  const actions = allowed('providers.create') && allowed('credentials.read') ? [button('Dodaj platformę', () => navigate('/providers/new'), 'primary')] : [];
  dom.content.replaceChildren(heading('Połączenia z platformami infrastruktury. Każda platforma korzysta z przypisanych, zaszyfrowanych danych dostępowych.', actions),
    table([
      { label: 'Nazwa', value: item => node('strong', { text: item.name }) },
      { label: 'Platforma', value: item => badge(CREDENTIAL_TYPE_CONFIG[item.type]?.label || item.type, 'info') },
      { label: 'Dane dostępowe', value: item => credentialNames.get(Number(item.credentials_id)) || `#${item.credentials_id}` },
      { label: 'Aktualizacja', value: item => formatDate(item.updated_at) },
    ], providers, item => {
      const actions = [];
      actions.push(button('Przeglądaj zasoby', () => discoverProvider(item)));
      if (allowed('providers.update') && allowed('credentials.read')) actions.push(button('Edytuj', () => navigate('/providers/edit/' + encodeURIComponent(item.id) + '/' + encodeURIComponent(item.name || 'provider'))));
      if (allowed('providers.delete')) actions.push(button('Usuń', () => confirmAction('Usuń platformę', `Platforma „${item.name}” zostanie usunięta. Zasoby po stronie platformy nie zostaną skasowane.`, async () => {
        await api('/providers/' + item.id, { method: 'DELETE' });
        toast('Platforma usunięta.');
        navigate('providers');
      }), 'danger'));
      return actions;
    }));
}

async function providerForm(item = null) {
  try {
    const credentials = (await api('/credentials?limit=200')).items;
    const providerTypes = ['proxmox', 'vmware', 'aws', 'azure', 'openstack'];
    const typeField = selectField('Typ platformy', 'type', providerTypes.map(value => ({
      value, label: CREDENTIAL_TYPE_CONFIG[value]?.label || value,
    })), item?.type || 'proxmox', { required: true });
    const credentialField = selectField('Dane dostępowe', 'credentials_id', [], item?.credentials_id || '', {
      required: true, placeholder: 'Wybierz dane dostępowe tego samego typu',
    });
    const credentialSelect = credentialField.querySelector('select');
    const typeSelect = typeField.querySelector('select');

    const refreshCredentials = () => {
      const selectedType = typeSelect.value;
      const matching = credentials.filter(value => value.type === selectedType);
      const previous = String(item?.credentials_id || credentialSelect.value || '');
      credentialSelect.replaceChildren(node('option', { value: '', text: 'Wybierz dane dostępowe' }));
      matching.forEach(value => credentialSelect.append(node('option', {
        value: value.id,
        text: value.name + ' (#' + value.id + ')',
        selected: String(value.id) === previous,
      })));
      if (!matching.length) credentialSelect.append(node('option', { value: '', text: 'Brak danych dostępowych tego typu', disabled: true }));
    };
    typeSelect.addEventListener('change', refreshCredentials);
    refreshCredentials();

    const fields = node('div', { class: 'form-grid' },
      field('Nazwa', 'name', { required: true, value: item?.name || '' }),
      typeField,
      credentialField,
      node('div', { class: 'wide field-help', text: 'Platforma i dane dostępowe muszą mieć ten sam typ. Przegląd zasobów jest tylko do odczytu, a provisioning wykonuje zatwierdzony katalog Terraform.' })
    );

    openModal({
      title: item ? 'Edytuj platformę' : 'Nowa platforma',
      eyebrow: 'Infrastruktura',
      body: fields,
      onSubmit: async data => {
        if (!data.get('credentials_id')) throw new Error('Wybierz dane dostępowe zgodne z typem platformy.');
        await api(item ? '/providers/' + item.id : '/providers', {
          method: item ? 'PUT' : 'POST',
          body: {
            name: data.get('name'),
            type: data.get('type'),
            credentials_id: Number(data.get('credentials_id')),
          },
        });
        toast('Platforma zapisana.');
        navigate('providers');
      },
    });
  } catch (error) { toast(error.message, 'error'); }
}

async function discoverProvider(provider) {
  const allResources = [
    { value: 'vms', label: 'Maszyny / instancje' },
    { value: 'templates', label: 'Szablony / obrazy' },
    { value: 'networks', label: 'Sieci' },
    { value: 'storages', label: 'Storage / wolumeny' },
    { value: 'nodes', label: 'Węzły / lokalizacje' },
    { value: 'pools', label: 'Pule / grupy' },
  ];
  const unsupported = provider.type === 'openstack' ? new Set(['nodes', 'pools']) : new Set();
  const resources = allResources.filter(item => !unsupported.has(item.value));
  const fields = node('div', { class: 'form-grid' },
    selectField('Typ zasobu', 'resource', resources, provider.type === 'proxmox' ? 'nodes' : 'vms', { required: true }));
  if (provider.type === 'aws') fields.append(field('Region AWS', 'node', { value: 'eu-central-1', required: true, placeholder: 'eu-central-1' }));

  openModal({
    title: 'Zasoby: ' + provider.name,
    eyebrow: CREDENTIAL_TYPE_CONFIG[provider.type]?.label || provider.type,
    body: fields,
    submitLabel: 'Pobierz zasoby',
    wide: true,
    onSubmit: async data => {
      const resource = data.get('resource');
      const scope = data.get('node');
      const suffix = scope ? '?node=' + encodeURIComponent(scope) : '';
      const rows = (await api('/providers/' + provider.id + '/' + resource + suffix)).items;
      const preferred = ['name', 'id', 'vmid', 'node', 'status', 'type', 'cidr', 'storage', 'total', 'avail', 'active'];
      const keys = [...new Set(rows.flatMap(row => Object.keys(row)))];
      keys.sort((a, b) => {
        const ai = preferred.indexOf(a), bi = preferred.indexOf(b);
        return (ai < 0 ? 999 : ai) - (bi < 0 ? 999 : bi);
      });
      const columns = keys.slice(0, 8).map(key => ({
        label: FIELD_LABELS[key] || key.replaceAll('_', ' '),
        value: row => {
          if (['total', 'used', 'avail', 'size', 'mem', 'maxmem', 'disk', 'maxdisk'].includes(key) && Number.isFinite(Number(row[key]))) return formatBytes(row[key]);
          if (key === 'uptime') return formatDuration(row[key]);
          if (key === 'status') return badge(statusLabel(row[key]), statusKind(row[key]));
          return displayValue(row[key]);
        },
      }));
      dom.modalTitle.textContent = 'Zasoby: ' + provider.name;
      dom.modalEyebrow.textContent = resources.find(item => item.value === resource)?.label || resource;
      dom.modalBody.replaceChildren(rows.length
        ? table(columns, rows)
        : node('div', { class: 'empty', text: 'Nie znaleziono zasobów tego typu.' }));
      dom.modalActions.replaceChildren(button('Zamknij', closeModal));
      return false;
    },
  });
}

registerRoutedForm({
  id: 'providers-create',
  pattern: /^\/providers\/new$/,
  parent: 'providers',
  permission: 'providers.create',
  label: 'Platformy',
}, () => providerForm());
registerRoutedForm({
  id: 'providers-edit',
  pattern: /^\/providers\/edit\/(?<id>\d+)(?:\/[^/]+)?$/,
  parent: 'providers',
  permission: 'providers.update',
  label: 'Platformy',
}, async match => {
  const providers = (await api('/providers?limit=200')).items;
  const item = providers.find(value => Number(value.id) === Number(match.params.id));
  if (!item) throw new Error('Nie znaleziono platformy.');
  await providerForm(item);
});
registerCommand('providers.create', () => navigate('/providers/new'));
registerView({ id: 'providers', label: 'Platformy', icon: 'P', permission: 'providers.read', order: 50 }, providersView);
})();
