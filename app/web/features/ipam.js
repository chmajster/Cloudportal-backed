'use strict';

(() => {
async function ipamView() {
  const [pools, allocations] = await Promise.all([
    api('/ipam/pools?limit=200'),
    api('/ipam/allocations?limit=200'),
  ]);
  const actions = allowed('ipam.create') ? [button('Nowa pula', () => navigate('/ipam/pools/new'), 'primary')] : [];
  dom.content.replaceChildren(
    heading('Centralne pule IPv4, rezerwacje i przypisania do wdrożeń.', actions),
    node('section', { class: 'panel' },
      node('div', { class: 'panel-header' }, node('h2', { text: 'Pule adresowe' })),
      table([
        { label: 'Nazwa', value: item => node('strong', { text: item.name }) },
        { label: 'CIDR', class: 'mono', value: item => item.cidr },
        { label: 'Gateway', class: 'mono', value: item => item.gateway || '—' },
        { label: 'DNS', value: item => (item.dns_servers || []).join(', ') || '—' },
        { label: 'Status', value: item => badge(statusLabel(item.is_active ? 'active' : 'inactive'), item.is_active ? 'ok' : 'danger') },
      ], pools.items, item => {
        const result = [];
        if (allowed('ipam.allocate') && item.is_active) result.push(button('Przydziel IP', () => allocateIp(item), 'primary'));
        if (allowed('ipam.update')) result.push(button('Edytuj', () => navigate('/ipam/pools/edit/' + encodeURIComponent(item.id) + '/' + encodeURIComponent(item.name || 'pool'))));
        if (allowed('ipam.delete')) result.push(button('Usuń', () => confirmAction('Usuń pulę IPAM', `Pula ${item.name} zostanie usunięta tylko jeśli nie ma historii alokacji.`, async () => {
          await api(`/ipam/pools/${item.id}`, { method: 'DELETE' });
          navigate('ipam');
        }), 'danger'));
        return result;
      })
    ),
    node('section', { class: 'panel' },
      node('div', { class: 'panel-header' }, node('h2', { text: 'Alokacje' })),
      table([
        { label: 'Adres', class: 'mono', value: item => `${item.address}/${item.prefix_length}` },
        { label: 'Status', value: item => badge(statusLabel(item.status), statusKind(item.status)) },
        { label: 'Nazwa hosta', value: item => item.hostname || '—' },
        { label: 'Zasób', class: 'mono', value: item => short(item.resource_id, 18) },
        { label: 'Utworzono', value: item => formatDate(item.created_at) },
      ], allocations.items, item => {
        const result = [];
        if (allowed('ipam.allocate') && item.status === 'reserved') result.push(button('Przypisz', () => assignIpAllocation(item), 'primary'));
        if (allowed('ipam.release') && item.status !== 'released') result.push(button('Zwolnij', () => confirmAction('Zwolnij adres', `${item.address} wróci do puli.`, async () => {
          await api(`/ipam/allocations/${item.id}/release`, { method: 'POST' });
          toast('Adres IP zwolniony.');
          navigate('ipam');
        }), 'danger'));
        return result;
      })
    )
  );
}

async function assignIpAllocation(item) {
  try {
    const [deploymentResult, resourceResult] = await Promise.all([
      allowed('deployments.read') ? api('/deployments?limit=200') : Promise.resolve({ items: [] }),
      allowed('inventory.read') ? api('/inventory/resources?limit=200') : Promise.resolve({ items: [] }),
    ]);
    const choices = [
      ...deploymentResult.items.map(row => ({ value: row.id, label: `Wdrożenie: ${row.name}` })),
      ...resourceResult.items.map(row => ({ value: row.id, label: `Zasób: ${row.name || row.external_id}` })),
    ];
    const fields = node('div', { class: 'form-grid' });
    if (choices.length) fields.append(selectField('Zasób', 'known_resource_id', [{ value: '', label: 'Inny identyfikator' }, ...choices], ''));
    fields.append(
      field('Identyfikator zasobu', 'resource_id', {
        required: !choices.length,
        help: choices.length ? 'Wybierz z listy albo wpisz własny identyfikator.' : 'Podaj identyfikator zasobu.',
      }),
      field('Hostname (opcjonalnie)', 'hostname', { value: item.hostname || '' }));
    openModal({
      title: `Przypisz ${item.address}`,
      eyebrow: 'IPAM',
      body: fields,
      submitLabel: 'Przypisz adres',
      onSubmit: async data => {
        const resourceId = data.get('known_resource_id') || data.get('resource_id')?.trim();
        if (!resourceId) throw new Error('Wybierz lub podaj identyfikator zasobu.');
        await api(`/ipam/allocations/${item.id}/assign`, {
          method: 'POST',
          body: { resource_id: resourceId, hostname: data.get('hostname')?.trim() || null },
        });
        toast('Adres IP przypisany.');
        navigate('ipam');
      },
    });
  } catch (error) { toast(error.message, 'error'); }
}

function ipamPoolForm(item = null) {
  const fields = node('div', { class: 'form-grid' },
    field('Nazwa', 'name', { required: true, value: item?.name || '' }),
    field('CIDR', 'cidr', { required: true, value: item?.cidr || '192.0.2.0/24' }),
    field('Gateway', 'gateway', { value: item?.gateway || '' }),
    field('DNS (przecinki lub nowe linie)', 'dns_servers', { tag: 'textarea', value: (item?.dns_servers || []).join('\n') }),
    field('Wykluczenia IP/CIDR', 'excluded_addresses', { tag: 'textarea', wide: true, value: (item?.excluded_addresses || []).join('\n') }),
    checkboxField('Aktywna', 'is_active', item?.is_active ?? true));
  openModal({ title: item ? 'Edytuj pulę IPAM' : 'Nowa pula IPAM', eyebrow: 'IPAM', body: fields, onSubmit: async data => {
    await api(item ? `/ipam/pools/${item.id}` : '/ipam/pools', {
      method: item ? 'PUT' : 'POST',
      body: {
        name: data.get('name'), cidr: data.get('cidr'), gateway: data.get('gateway') || null,
        dns_servers: splitValues(data.get('dns_servers')), excluded_addresses: splitValues(data.get('excluded_addresses')),
        is_active: data.has('is_active'),
      },
    });
    toast('Pula IPAM zapisana.');
    navigate('ipam');
  }});
}

function allocateIp(pool) {
  const fields = node('div', { class: 'form-grid' },
    field('Preferowany adres (opcjonalnie)', 'preferred_address'),
    field('Hostname (opcjonalnie)', 'hostname'));
  openModal({ title: `Przydziel IP z ${pool.name}`, eyebrow: pool.cidr, body: fields, submitLabel: 'Rezerwuj', onSubmit: async data => {
    const result = await api(`/ipam/pools/${pool.id}/allocate`, { method: 'POST', idempotent: true, body: {
      preferred_address: data.get('preferred_address') || null, hostname: data.get('hostname') || null,
    } });
    toast(`Zarezerwowano ${result.address}.`);
    navigate('ipam');
  }});
}

registerRoutedForm({
  id: 'ipam-pool-create',
  pattern: /^\/ipam\/pools\/new$/,
  parent: 'ipam',
  permission: 'ipam.create',
  label: 'IPAM',
}, () => ipamPoolForm());
registerRoutedForm({
  id: 'ipam-pool-edit',
  pattern: /^\/ipam\/pools\/edit\/(?<id>\d+)(?:\/[^/]+)?$/,
  parent: 'ipam',
  permission: 'ipam.update',
  label: 'IPAM',
}, async match => {
  const pools = (await api('/ipam/pools?limit=200')).items;
  const item = pools.find(value => Number(value.id) === Number(match.params.id));
  if (!item) throw new Error('Nie znaleziono puli IPAM.');
  ipamPoolForm(item);
});
registerView({ id: 'ipam', label: 'IPAM', icon: 'I', permission: 'ipam.read', order: 90 }, ipamView);
})();
