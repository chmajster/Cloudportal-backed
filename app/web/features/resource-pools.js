'use strict';

(() => {
  const STRATEGIES = [
    ['BALANCED', 'Balanced — CPU + RAM + storage + provisioning'],
    ['LEAST_USED', 'Least Used'],
    ['MOST_FREE_MEMORY', 'Most Free Memory'],
    ['MOST_FREE_CPU', 'Most Free CPU'],
    ['ROUND_ROBIN', 'Round Robin'],
    ['WEIGHTED', 'Weighted'],
    ['PRIORITY', 'Priority'],
    ['RANDOM', 'Random'],
  ];

  const TABS = [
    ['overview', 'Overview'],
    ['members', 'Members'],
    ['rules', 'Placement Rules'],
    ['capacity', 'Capacity'],
    ['reservations', 'Reservations'],
    ['deployments', 'Deployments'],
    ['history', 'History'],
    ['settings', 'Settings'],
  ];

  function scopeHeaders(scope) {
    if (!scope?.tenant_id || !scope?.project_id) return {};
    return {
      'X-Tenant-ID': String(scope.tenant_id),
      'X-Project-ID': String(scope.project_id),
    };
  }

  async function currentScope() {
    const result = await api('/project-context');
    const selected = result?.selected || null;
    if (!selected) throw new Error('Wybierz Organizację i Projekt.');
    return {
      tenant_id: selected.tenant_id,
      project_id: selected.id,
      tenant_name: result.tenant_name || selected.tenant_id,
      project_name: selected.name || selected.slug || selected.id,
      headers: scopeHeaders({ tenant_id: selected.tenant_id, project_id: selected.id }),
    };
  }

  function can(result, permission) {
    return allowed(permission) || (result?.permissions || []).includes(permission);
  }

  function healthBadge(status) {
    const value = String(status || 'UNKNOWN').toUpperCase();
    const kind = value === 'HEALTHY' ? 'ok'
      : value === 'UNAVAILABLE' ? 'danger'
        : ['DEGRADED', 'CAPACITY_WARNING', 'MAINTENANCE'].includes(value) ? 'warning' : 'info';
    return badge(value, kind);
  }

  function fmt(value, digits = 0) {
    const number = Number(value || 0);
    return Number.isFinite(number) ? number.toLocaleString('pl-PL', { maximumFractionDigits: digits }) : '0';
  }

  function gibFromMb(value) {
    return fmt(Number(value || 0) / 1024, 1) + ' GiB';
  }

  function dateTime(value) {
    if (!value) return '—';
    try { return new Date(value).toLocaleString('pl-PL'); } catch { return String(value); }
  }

  async function mapLimit(rows, limit, callback) {
    const pending = [...rows];
    const result = new Map();
    await Promise.all(Array.from({ length: Math.min(limit, pending.length) }, async () => {
      while (pending.length) {
        const row = pending.shift();
        try { result.set(row.id, await callback(row)); }
        catch (error) { result.set(row.id, { health: 'UNAVAILABLE', reason: error.message }); }
      }
    }));
    return result;
  }

  function poolForm(item, scope, onSaved) {
    const body = node('div', { class: 'form-grid resource-pool-form' },
      field('Nazwa', 'name', { value: item?.name || '', required: true, maxlength: 100 }),
      selectField('Strategia', 'strategy', STRATEGIES.map(([value, label]) => ({ value, label })),
        item?.strategy || 'BALANCED', { required: true }),
      field('Opis', 'description', { tag: 'textarea', value: item?.description || '', wide: true, maxlength: 4000 }),
      field('Retry placement', 'retry_limit', { type: 'number', min: 1, max: 10, value: item?.retry_limit ?? 3, required: true }),
      field('TTL rezerwacji [s]', 'reservation_ttl_seconds', {
        type: 'number', min: 30, max: 86400, value: item?.reservation_ttl_seconds ?? 600, required: true,
        help: 'Po TTL osierocona rezerwacja nie blokuje capacity. Worker wykona ponowny placement przed provisioningiem.',
      }),
      field('CPU warning [%]', 'cpu_warning', { type: 'number', min: 0, max: 100, value: item?.thresholds?.cpu_warning ?? 80 }),
      field('CPU hard limit [%]', 'cpu_hard_limit', { type: 'number', min: 0, max: 100, value: item?.thresholds?.cpu_hard_limit ?? 95 }),
      field('RAM warning [%]', 'ram_warning', { type: 'number', min: 0, max: 100, value: item?.thresholds?.ram_warning ?? 80 }),
      field('RAM hard limit [%]', 'ram_hard_limit', { type: 'number', min: 0, max: 100, value: item?.thresholds?.ram_hard_limit ?? 95 }),
      field('Storage warning [%]', 'storage_warning', { type: 'number', min: 0, max: 100, value: item?.thresholds?.storage_warning ?? 80 }),
      field('Storage hard limit [%]', 'storage_hard_limit', { type: 'number', min: 0, max: 100, value: item?.thresholds?.storage_hard_limit ?? 95 }),
      checkboxField('Pula aktywna', 'enabled', item ? item.enabled !== false : true));

    openModal({
      title: item ? 'Edytuj pulę zasobów' : 'Nowa pula zasobów',
      eyebrow: 'Placement Engine',
      body,
      wide: true,
      submitLabel: item ? 'Zapisz' : 'Utwórz pulę',
      onSubmit: async data => {
        const number = name => Number(data.get(name));
        const payload = {
          name: String(data.get('name') || '').trim(),
          description: String(data.get('description') || '').trim(),
          strategy: String(data.get('strategy') || 'BALANCED'),
          enabled: data.has('enabled'),
          retry_limit: number('retry_limit'),
          reservation_ttl_seconds: number('reservation_ttl_seconds'),
          thresholds: {
            cpu_warning: number('cpu_warning'), cpu_hard_limit: number('cpu_hard_limit'),
            ram_warning: number('ram_warning'), ram_hard_limit: number('ram_hard_limit'),
            storage_warning: number('storage_warning'), storage_hard_limit: number('storage_hard_limit'),
          },
          metadata: item?.metadata || {},
        };
        await api('/resource-pools' + (item ? '/' + encodeURIComponent(item.id) : ''), {
          method: item ? 'PUT' : 'POST', body: payload, headers: scope.headers,
        });
        toast(item ? 'Pula zasobów zaktualizowana.' : 'Pula zasobów utworzona.');
        await onSaved?.();
      },
    });
  }

  async function memberForm(pool, item, scope, onSaved) {
    const providers = (await api('/providers?limit=200', { headers: scope.headers })).items || [];
    const providerField = searchableSelectField(
      'Platforma', 'provider_id',
      providers.map(provider => ({ value: provider.id, label: provider.name + ' · ' + provider.type })),
      item?.provider_id || providers[0]?.id || '',
      { required: true, wide: true, placeholder: 'Wpisz nazwę platformy…' }
    );
    const nodeField = searchableSelectField('Node', 'node', [], item?.node || '', {
      wide: true, placeholder: 'Wpisz nazwę node…',
      help: 'Puste = cały provider. Placement Engine rozwinie member do aktualnych targetów providera.',
    });
    const storageField = searchableSelectField('Storage', 'storage', [], item?.storage || '', {
      wide: true, placeholder: 'Wpisz nazwę storage…',
    });
    const networkField = searchableSelectField('Network', 'network', [], item?.network || '', {
      wide: true, placeholder: 'Wpisz nazwę sieci…',
    });
    const status = node('div', { class: 'resource-pool-discovery-status wide muted', text: 'Pobieranie zasobów providera…' });

    const body = node('div', { class: 'form-grid resource-pool-form' },
      providerField, status, nodeField, storageField, networkField,
      field('Cluster', 'cluster', { value: item?.cluster || '', placeholder: 'opcjonalnie' }),
      field('Datacenter / location', 'datacenter', { value: item?.datacenter || '', placeholder: 'np. WRO-DC1' }),
      field('Priority', 'priority', { type: 'number', min: 0, max: 10000, value: item?.priority ?? 100, required: true }),
      field('Weight', 'weight', { type: 'number', min: 1, max: 10000, value: item?.weight ?? 100, required: true }),
      field('Max VM', 'max_vm_count', { type: 'number', min: 1, value: item?.max_vm_count ?? '' }),
      field('Max CPU [%]', 'max_cpu_usage', { type: 'number', min: 0, max: 100, value: item?.max_cpu_usage ?? '' }),
      field('Max RAM [%]', 'max_memory_usage', { type: 'number', min: 0, max: 100, value: item?.max_memory_usage ?? '' }),
      field('Min free RAM [MiB]', 'min_free_memory_mb', { type: 'number', min: 0, value: item?.min_free_memory_mb ?? '' }),
      field('Min free storage [GiB]', 'min_free_storage_gb', { type: 'number', min: 0, value: item?.min_free_storage_gb ?? '' }),
      field('Tagi membera', 'tags', { value: (item?.tags || []).join(', '), wide: true, placeholder: 'prod, gpu, dc1' }),
      checkboxField('Member aktywny', 'enabled', item ? item.enabled !== false : true),
      checkboxField('Maintenance mode', 'maintenance_mode', Boolean(item?.maintenance_mode)));

    let discoveryGeneration = 0;
    async function loadProvider(providerId, preferredNode = '') {
      const generation = ++discoveryGeneration;
      const provider = providers.find(value => String(value.id) === String(providerId));
      if (!provider) return;
      status.textContent = 'Pobieranie node’ów…';
      try {
        const nodes = (await api('/providers/' + encodeURIComponent(provider.id) + '/nodes', { headers: scope.headers })).items || [];
        if (generation !== discoveryGeneration) return;
        nodeField.searchableSelect.setChoices(
          [{ value: '', label: 'Cała platforma / automatyczny node' }, ...nodes.map(row => ({
            value: row.node || row.id,
            label: (row.node || row.id) + (row.status ? ' · ' + row.status : ''),
          }))],
          preferredNode
        );
        status.textContent = 'Zasoby providera pobrane z API.';
        await loadNode(provider.id, nodeField.searchableSelect.value(), item);
      } catch (error) {
        if (generation !== discoveryGeneration) return;
        status.textContent = 'Błąd discovery: ' + error.message;
        nodeField.searchableSelect.setChoices([{ value: '', label: 'Cała platforma / automatyczny node' }], '');
        storageField.searchableSelect.setChoices([{ value: '', label: 'Automatyczny storage' }], '');
        networkField.searchableSelect.setChoices([{ value: '', label: 'Automatyczna sieć' }], '');
      }
    }

    async function loadNode(providerId, nodeValue, preferred = null) {
      const generation = ++discoveryGeneration;
      const query = nodeValue ? '?node=' + encodeURIComponent(nodeValue) : '';
      status.textContent = nodeValue ? 'Pobieranie storage i network…' : 'Cała platforma — storage/network będą rozwiązywane per target.';
      if (!nodeValue) {
        storageField.searchableSelect.setChoices([{ value: '', label: 'Automatyczny storage' }], '');
        networkField.searchableSelect.setChoices([{ value: '', label: 'Automatyczna sieć' }], '');
        return;
      }
      try {
        const [storages, networks] = await Promise.all([
          api('/providers/' + encodeURIComponent(providerId) + '/storages' + query, { headers: scope.headers }),
          api('/providers/' + encodeURIComponent(providerId) + '/networks' + query, { headers: scope.headers }),
        ]);
        if (generation !== discoveryGeneration) return;
        storageField.searchableSelect.setChoices(
          [{ value: '', label: 'Automatyczny storage' }, ...(storages.items || []).map(row => ({
            value: row.storage || row.id,
            label: (row.storage || row.id) + (row.content ? ' · ' + String(row.content) : ''),
          }))],
          preferred?.storage || ''
        );
        networkField.searchableSelect.setChoices(
          [{ value: '', label: 'Automatyczna sieć' }, ...(networks.items || []).filter(row => row.iface || row.network || row.id).map(row => ({
            value: row.iface || row.network || row.id,
            label: (row.iface || row.network || row.id) + (row.type ? ' · ' + row.type : ''),
          }))],
          preferred?.network || ''
        );
        status.textContent = 'Node, storage i network zweryfikowane przez API providera.';
      } catch (error) {
        if (generation !== discoveryGeneration) return;
        status.textContent = 'Błąd discovery: ' + error.message;
      }
    }

    providerField.searchableSelect.onChange(value => loadProvider(value, ''));
    nodeField.searchableSelect.onChange(value => loadNode(providerField.searchableSelect.value(), value, null));
    await loadProvider(providerField.searchableSelect.value(), item?.node || '');

    openModal({
      title: item ? 'Edytuj member puli' : 'Dodaj member do puli',
      eyebrow: pool.name,
      body,
      wide: true,
      submitLabel: item ? 'Zapisz' : 'Dodaj member',
      onSubmit: async data => {
        const nullableNumber = name => String(data.get(name) || '').trim() === '' ? null : Number(data.get(name));
        const payload = {
          provider_id: Number(data.get('provider_id')),
          cluster: String(data.get('cluster') || '').trim() || null,
          node: String(data.get('node') || '').trim() || null,
          datacenter: String(data.get('datacenter') || '').trim() || null,
          storage: String(data.get('storage') || '').trim() || null,
          network: String(data.get('network') || '').trim() || null,
          enabled: data.has('enabled'),
          priority: Number(data.get('priority')),
          weight: Number(data.get('weight')),
          maintenance_mode: data.has('maintenance_mode'),
          max_vm_count: nullableNumber('max_vm_count'),
          max_cpu_usage: nullableNumber('max_cpu_usage'),
          max_memory_usage: nullableNumber('max_memory_usage'),
          min_free_memory_mb: nullableNumber('min_free_memory_mb'),
          min_free_storage_gb: nullableNumber('min_free_storage_gb'),
          tags: String(data.get('tags') || '').split(/[,
]+/).map(value => value.trim()).filter(Boolean),
          metadata: item?.metadata || {},
        };
        const path = '/resource-pools/' + encodeURIComponent(pool.id) + '/members'
          + (item ? '/' + encodeURIComponent(item.id) : '');
        await api(path, { method: item ? 'PUT' : 'POST', body: payload, headers: scope.headers });
        toast(item ? 'Member zaktualizowany.' : 'Member dodany.');
        await onSaved?.();
      },
    });
  }

  function parseJson(value, fallback = {}) {
    const text = String(value || '').trim();
    if (!text) return fallback;
    const parsed = JSON.parse(text);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('Wymagany jest obiekt JSON.');
    return parsed;
  }

  function ruleForm(pool, item, scope, onSaved) {
    const exampleConditions = item?.conditions || {
      all: [
        { field: 'environment', operator: 'equals', value: 'prod' },
      ],
    };
    const exampleActions = item?.actions || {
      preferred_location: 'DC1',
      fallback_locations: ['DC2'],
    };
    const body = node('div', { class: 'form-grid resource-pool-form' },
      field('Nazwa reguły', 'name', { value: item?.name || '', required: true, maxlength: 100 }),
      field('Priority', 'priority', { type: 'number', min: -10000, max: 10000, value: item?.priority ?? 100, required: true }),
      field('Conditions JSON', 'conditions', {
        tag: 'textarea', wide: true, value: JSON.stringify(exampleConditions, null, 2),
        help: 'Pola: tenant, organization, project, apmid/APMID, environment/ENV, blueprint, operating_system, vm_size, cpu, ram, disk_size, tags, provider_type, location, metadata. Operatory: equals, not_equals, in, not_in, contains, starts_with, greater_than, less_than, greater_or_equal, less_or_equal.',
      }),
      field('Actions JSON', 'actions', {
        tag: 'textarea', wide: true, value: JSON.stringify(exampleActions, null, 2),
        help: 'Hard: allowed_provider_types/ids, allowed_nodes/locations, required_network, required_storage_class, required_tags, hard_constraints. Soft: preferred_location, fallback_locations, soft_preferences, affinity.',
      }),
      checkboxField('Reguła aktywna', 'enabled', item ? item.enabled !== false : true),
      checkboxField('Stop processing', 'stop_processing', Boolean(item?.stop_processing)));

    openModal({
      title: item ? 'Edytuj Placement Rule' : 'Nowa Placement Rule',
      eyebrow: pool.name,
      body,
      wide: true,
      submitLabel: item ? 'Zapisz' : 'Dodaj regułę',
      onSubmit: async data => {
        let conditions;
        let actions;
        try {
          conditions = parseJson(data.get('conditions'));
          actions = parseJson(data.get('actions'));
        } catch (error) {
          throw new Error('Niepoprawny JSON reguły: ' + error.message);
        }
        const payload = {
          name: String(data.get('name') || '').trim(),
          priority: Number(data.get('priority')),
          enabled: data.has('enabled'),
          stop_processing: data.has('stop_processing'),
          conditions, actions,
        };
        const path = '/resource-pools/' + encodeURIComponent(pool.id) + '/rules'
          + (item ? '/' + encodeURIComponent(item.id) : '');
        await api(path, { method: item ? 'PUT' : 'POST', body: payload, headers: scope.headers });
        toast(item ? 'Reguła zaktualizowana.' : 'Reguła utworzona.');
        await onSaved?.();
      },
    });
  }

  function metricCard(label, used, total, reserved, formatter = value => fmt(value, 1)) {
    const available = Math.max(0, Number(total || 0) - Number(used || 0) - Number(reserved || 0));
    const pct = total ? Math.min(100, ((Number(used || 0) + Number(reserved || 0)) / Number(total)) * 100) : 0;
    return node('article', { class: 'resource-pool-metric' },
      node('div', { class: 'resource-pool-metric-head' },
        node('span', { text: label }),
        node('strong', { text: fmt(pct, 1) + '%' })),
      node('div', { class: 'resource-pool-meter' },
        node('span', { style: 'width:' + pct + '%' })),
      node('dl', {},
        node('div', {}, node('dt', { text: 'Used' }), node('dd', { text: formatter(used) })),
        node('div', {}, node('dt', { text: 'Reserved' }), node('dd', { text: formatter(reserved) })),
        node('div', {}, node('dt', { text: 'Available' }), node('dd', { text: formatter(available) })),
        node('div', {}, node('dt', { text: 'Total' }), node('dd', { text: formatter(total) }))));
  }

  function capacityPanel(capacity) {
    return node('div', { class: 'resource-pool-capacity' },
      node('div', { class: 'resource-pool-status-line' },
        healthBadge(capacity.health),
        node('span', { text: capacity.reason || '' })),
      node('div', { class: 'resource-pool-metrics-grid' },
        metricCard('CPU', capacity.cpu_used, capacity.cpu_total, capacity.cpu_reserved, value => fmt(value, 1) + ' vCPU'),
        metricCard('RAM', capacity.memory_mb_used, capacity.memory_mb_total, capacity.memory_mb_reserved, gibFromMb),
        metricCard('Storage', capacity.storage_gb_used, capacity.storage_gb_total, capacity.storage_gb_reserved, value => fmt(value, 1) + ' GiB')),
      node('div', { class: 'resource-pool-health-grid' },
        node('div', {}, node('span', { text: 'Nodes online' }), node('strong', { text: String(capacity.nodes_online ?? 0) })),
        node('div', {}, node('span', { text: 'Nodes offline' }), node('strong', { text: String(capacity.nodes_offline ?? 0) })),
        node('div', {}, node('span', { text: 'Maintenance' }), node('strong', { text: String(capacity.nodes_maintenance ?? 0) })),
        node('div', {}, node('span', { text: 'Members' }), node('strong', { text: String(capacity.members_total ?? 0) }))));
  }

  async function simulator(scope, pools) {
    const poolField = searchableSelectField(
      'Resource Pool', 'pool_id',
      [{ value: '', label: 'Policy based — wybierz pulę regułami' }, ...pools.map(pool => ({ value: pool.id, label: pool.name }))],
      '', { wide: true }
    );
    const body = node('div', { class: 'form-grid resource-pool-form' },
      selectField('Placement Mode', 'placement_mode', [
        { value: 'POLICY', label: 'POLICY — pula wybierana przez Placement Rules' },
        { value: 'POOL', label: 'POOL — wskazana pula' },
      ], 'POLICY'),
      poolField,
      field('Organization', 'organization', { value: scope.tenant_name }),
      field('Project', 'project', { value: scope.project_name }),
      field('APMID', 'apmid', { value: 'LEO-131' }),
      selectField('ENV', 'environment', ['prod', 'nonprod', 'dev', 'test'].map(value => ({ value, label: value.toUpperCase() })), 'prod'),
      field('CPU', 'cpu', { type: 'number', min: 1, value: 4 }),
      field('RAM [MiB]', 'ram_mb', { type: 'number', min: 1, value: 8192 }),
      field('Disk [GiB]', 'disk_gb', { type: 'number', min: 1, value: 100 }),
      field('Logical Network', 'logical_network', { value: '', placeholder: 'opcjonalnie, np. PROD' }),
      field('Storage Class', 'storage_class', { value: '', placeholder: 'opcjonalnie, np. FAST' }),
      field('Tagi', 'tags', { value: '', wide: true, placeholder: 'critical, database' }));

    openModal({
      title: 'Placement Simulator',
      eyebrow: 'Dry run — bez tworzenia VM',
      body,
      wide: true,
      submitLabel: 'Symuluj placement',
      onSubmit: async data => {
        const mode = String(data.get('placement_mode') || 'POLICY');
        const poolId = String(data.get('pool_id') || '');
        if (mode === 'POOL' && !poolId) throw new Error('W trybie POOL wybierz Resource Pool.');
        const payload = {
          placement_mode: mode,
          pool_id: mode === 'POOL' ? poolId : null,
          organization: String(data.get('organization') || '').trim() || null,
          project: String(data.get('project') || '').trim() || null,
          apmid: String(data.get('apmid') || '').trim() || null,
          environment: String(data.get('environment') || '').trim() || null,
          cpu: Number(data.get('cpu')),
          ram_mb: Number(data.get('ram_mb')),
          disk_gb: Number(data.get('disk_gb')),
          logical_network: String(data.get('logical_network') || '').trim() || null,
          storage_class: String(data.get('storage_class') || '').trim() || null,
          tags: String(data.get('tags') || '').split(/[,
]+/).map(value => value.trim()).filter(Boolean),
          reserve: false,
        };
        const result = await api('/placement/simulate', { method: 'POST', body: payload, headers: scope.headers });
        const selected = result.selected || {};
        const selectedPanel = node('section', { class: 'resource-pool-simulator-result' },
          node('h3', { text: 'Selected' }),
          node('dl', { class: 'resource-pool-decision-grid' },
            ...[
              ['Pool', selected.pool], ['Platform', selected.platform], ['Node', selected.node],
              ['Storage', selected.storage], ['Network', selected.network], ['Score', selected.score],
            ].map(([label, value]) => node('div', {}, node('dt', { text: label }), node('dd', { text: String(value ?? '—') })))),
          node('p', { class: 'muted', text: result.decision_reason || '' }),
          table([
            { label: 'Candidate', value: row => [row.platform, row.node].filter(Boolean).join(' / ') || String(row.provider_id || '—') },
            { label: 'Status', value: row => badge(row.status || 'AVAILABLE', row.status === 'REJECTED' ? 'danger' : row.status === 'SELECTED' ? 'ok' : 'info') },
            { label: 'Score', value: row => fmt(row.score, 2) },
            { label: 'Reason', value: row => (row.reasons || []).join('; ') || '—' },
          ], result.candidates || []));
        body.replaceChildren(selectedPanel);
        const modalActions = document.querySelector('#modal-actions');
        if (modalActions) modalActions.replaceChildren(button('Zamknij', () => document.querySelector('#modal')?.close(), 'primary'));
        return false;
      },
    });
  }

  async function poolListView() {
    const scope = await currentScope();
    const result = await api('/resource-pools?limit=200', { headers: scope.headers });
    const pools = result.items || [];
    const capacities = await mapLimit(pools, 6, pool =>
      api('/resource-pools/' + encodeURIComponent(pool.id) + '/capacity', { headers: scope.headers }));

    const actions = [];
    if (can(result, 'resource_pool.create')) actions.push(button('Nowa pula', () => poolForm(null, scope, poolListView), 'primary'));
    if (can(result, 'placement.simulate')) actions.push(button('Placement Simulator', () => simulator(scope, pools)));

    const content = node('div', { class: 'stack resource-pools-view' },
      heading('Logiczne pule infrastruktury z provider-agnostic Placement Engine.', actions),
      node('section', { class: 'panel resource-pool-context' },
        node('div', { class: 'panel-header' },
          node('div', {}, node('h2', { text: 'Zakres' }), node('p', { class: 'muted', text: 'Pule są izolowane per Organizacja + Projekt.' }))),
        node('div', { class: 'tool-meta-grid' },
          node('div', { class: 'tool-meta-item' }, node('span', { text: 'Organizacja' }), node('strong', { text: scope.tenant_name })),
          node('div', { class: 'tool-meta-item' }, node('span', { text: 'Projekt' }), node('strong', { text: scope.project_name })),
          node('div', { class: 'tool-meta-item' }, node('span', { text: 'Pule' }), node('strong', { text: String(pools.length) })))),
      table([
        { label: 'Name', value: pool => node('button', { class: 'link-button', type: 'button', onClick: () => navigate('/resource-pools/' + encodeURIComponent(pool.id) + '/overview') }, node('strong', { text: pool.name })) },
        { label: 'Type', value: pool => pool.members > 1 ? 'Multi-target' : 'Single / empty' },
        { label: 'Members', value: pool => pool.members },
        { label: 'Strategy', value: pool => pool.strategy },
        { label: 'Status', value: pool => healthBadge(capacities.get(pool.id)?.health) },
        { label: 'Active deployments', value: pool => pool.active_deployments ?? 0 },
        { label: 'Available capacity', value: pool => {
          const cap = capacities.get(pool.id) || {};
          return 'CPU ' + fmt(cap.cpu_available, 1) + ' / RAM ' + gibFromMb(cap.memory_mb_available) + ' / Storage ' + fmt(cap.storage_gb_available, 1) + ' GiB';
        }},
        { label: 'Created', value: pool => dateTime(pool.created_at) },
      ], pools, pool => [
        button('Otwórz', () => navigate('/resource-pools/' + encodeURIComponent(pool.id) + '/overview')),
        can(result, 'resource_pool.edit') ? button('Edytuj', () => poolForm(pool, scope, poolListView)) : null,
        can(result, 'resource_pool.delete') ? button('Usuń', async () => {
          if (!confirm('Usunąć pulę ' + pool.name + '?')) return;
          await api('/resource-pools/' + encodeURIComponent(pool.id), { method: 'DELETE', headers: scope.headers });
          toast('Pula usunięta.');
          await poolListView();
        }, 'danger') : null,
      ].filter(Boolean)));

    dom.content.replaceChildren(content);
  }

  async function renderMembers(pool, scope, permissions) {
    const result = await api('/resource-pools/' + encodeURIComponent(pool.id) + '/members', { headers: scope.headers });
    const members = result.items || [];
    return node('section', { class: 'panel stack' },
      heading('Targety fizyczne należące do puli.', can(permissions, 'resource_pool.edit')
        ? [button('Add member', () => memberForm(pool, null, scope, () => poolDetails(pool.id, 'members')), 'primary')]
        : []),
      table([
        { label: 'Platform', value: row => node('div', {}, node('strong', { text: row.platform_name || String(row.provider_id) }), node('small', { class: 'muted', text: row.provider_type })) },
        { label: 'Cluster / Node', value: row => [row.cluster, row.node || 'cała platforma'].filter(Boolean).join(' / ') },
        { label: 'Storage', value: row => row.storage || 'auto' },
        { label: 'Network', value: row => row.network || 'auto' },
        { label: 'Priority', value: row => row.priority },
        { label: 'Weight', value: row => row.weight },
        { label: 'Status', value: row => row.maintenance_mode ? badge('MAINTENANCE', 'warning') : row.enabled ? badge('ENABLED', 'ok') : badge('DISABLED', 'danger') },
      ], members, row => can(permissions, 'resource_pool.edit') ? [
        button('Edytuj', () => memberForm(pool, row, scope, () => poolDetails(pool.id, 'members'))),
        button(row.maintenance_mode ? 'Wyłącz maintenance' : 'Włącz maintenance', async () => {
          await api('/resource-pools/' + encodeURIComponent(pool.id) + '/members/' + encodeURIComponent(row.id)
            + '/maintenance?enabled=' + String(!row.maintenance_mode), { method: 'POST', headers: scope.headers });
          toast(row.maintenance_mode ? 'Maintenance wyłączony.' : 'Maintenance włączony.');
          await poolDetails(pool.id, 'members');
        }, row.maintenance_mode ? 'primary' : 'ghost'),
        button('Usuń', async () => {
          if (!confirm('Usunąć member ' + (row.platform_name || row.provider_id) + ' / ' + (row.node || 'all') + '?')) return;
          await api('/resource-pools/' + encodeURIComponent(pool.id) + '/members/' + encodeURIComponent(row.id), {
            method: 'DELETE', headers: scope.headers,
          });
          await poolDetails(pool.id, 'members');
        }, 'danger'),
      ] : []));
  }

  async function renderRules(pool, scope, permissions) {
    const result = await api('/resource-pools/' + encodeURIComponent(pool.id) + '/rules', { headers: scope.headers });
    const rules = result.items || [];
    return node('section', { class: 'panel stack' },
      heading('Reguły są oceniane przed live capacity i scoringiem.', can(permissions, 'resource_pool.edit')
        ? [button('Nowa reguła', () => ruleForm(pool, null, scope, () => poolDetails(pool.id, 'rules')), 'primary')]
        : []),
      table([
        { label: 'Name', value: row => node('strong', { text: row.name }) },
        { label: 'Priority', value: row => row.priority },
        { label: 'Status', value: row => row.enabled ? badge('ENABLED', 'ok') : badge('DISABLED', 'danger') },
        { label: 'Conditions', value: row => node('code', { text: JSON.stringify(row.conditions) }) },
        { label: 'Actions', value: row => node('code', { text: JSON.stringify(row.actions) }) },
        { label: 'Stop', value: row => row.stop_processing ? 'Tak' : 'Nie' },
      ], rules, row => can(permissions, 'resource_pool.edit') ? [
        button('Edytuj', () => ruleForm(pool, row, scope, () => poolDetails(pool.id, 'rules'))),
        button('Usuń', async () => {
          if (!confirm('Usunąć regułę ' + row.name + '?')) return;
          await api('/resource-pools/' + encodeURIComponent(pool.id) + '/rules/' + encodeURIComponent(row.id), {
            method: 'DELETE', headers: scope.headers,
          });
          await poolDetails(pool.id, 'rules');
        }, 'danger'),
      ] : []));
  }

  async function renderReservations(pool, scope) {
    const result = await api('/resource-pools/' + encodeURIComponent(pool.id) + '/reservations?limit=500', { headers: scope.headers });
    return node('section', { class: 'panel stack' },
      heading('Rezerwacje CPU/RAM/storage uwzględniane przez równoległe deploymenty.'),
      table([
        { label: 'Status', value: row => badge(row.status, row.status === 'RESERVED' ? 'warning' : row.status === 'RELEASED' ? 'ok' : 'info') },
        { label: 'Platform / Node', value: row => [row.platform, row.node].filter(Boolean).join(' / ') },
        { label: 'CPU', value: row => row.cpu },
        { label: 'RAM', value: row => gibFromMb(row.memory_mb) },
        { label: 'Storage', value: row => fmt(row.storage_gb) + ' GiB' },
        { label: 'Deployment', value: row => row.deployment_id || '—' },
        { label: 'Expires', value: row => dateTime(row.expires_at) },
      ], result.items || []));
  }

  async function renderHistory(pool, scope, deploymentsOnly = false) {
    const result = await api('/resource-pools/' + encodeURIComponent(pool.id) + '/placements?limit=500', { headers: scope.headers });
    let rows = result.items || [];
    if (deploymentsOnly) rows = rows.filter(row => row.deployment_id);
    return node('section', { class: 'panel stack' },
      heading(deploymentsOnly ? 'Deploymenty, które przeszły przez tę pulę.' : 'Explainable Placement — pełna historia decyzji.'),
      table([
        { label: 'Created', value: row => dateTime(row.created_at) },
        { label: 'Deployment', value: row => row.deployment_id || 'dry-run' },
        { label: 'Mode', value: row => row.placement_mode },
        { label: 'Platform / Node', value: row => [row.selected_platform, row.selected_node].filter(Boolean).join(' / ') },
        { label: 'Storage / Network', value: row => [row.selected_storage, row.selected_network].filter(Boolean).join(' / ') },
        { label: 'Score', value: row => fmt(row.score, 2) },
        { label: 'Reason', value: row => row.decision_reason || '—' },
      ], rows));
  }

  function logicalClassForm(kind, scope, onSaved) {
    const isNetwork = kind === 'network';
    const body = node('div', { class: 'form-grid' },
      field('Nazwa', 'name', { required: true, placeholder: isNetwork ? 'PROD' : 'FAST' }),
      field('Opis', 'description', { tag: 'textarea', wide: true }),
      checkboxField('Aktywne', 'enabled', true));
    openModal({
      title: isNetwork ? 'Nowa Logical Network' : 'Nowa Storage Class',
      eyebrow: 'Abstrakcja placement',
      body,
      submitLabel: 'Utwórz',
      onSubmit: async data => {
        await api(isNetwork ? '/logical-networks' : '/storage-classes', {
          method: 'POST',
          body: {
            name: String(data.get('name') || '').trim(),
            description: String(data.get('description') || '').trim(),
            enabled: data.has('enabled'),
            metadata: {},
          },
          headers: scope.headers,
        });
        await onSaved?.();
      },
    });
  }

  async function mappingForm(kind, logical, scope, onSaved) {
    const providers = (await api('/providers?limit=200', { headers: scope.headers })).items || [];
    const providerField = searchableSelectField(
      'Platforma', 'provider_id',
      providers.map(row => ({ value: row.id, label: row.name + ' · ' + row.type })),
      providers[0]?.id || '', { required: true, wide: true }
    );
    const nodeField = searchableSelectField('Node', 'node', [], '', { wide: true });
    const targetField = searchableSelectField(kind === 'network' ? 'Network' : 'Storage', 'target', [], '', {
      required: true, wide: true,
    });
    const body = node('div', { class: 'form-grid' }, providerField, nodeField, targetField);
    async function loadProvider(providerId) {
      const nodes = (await api('/providers/' + encodeURIComponent(providerId) + '/nodes', { headers: scope.headers })).items || [];
      nodeField.searchableSelect.setChoices(
        [{ value: '', label: 'Wszystkie node’y' }, ...nodes.map(row => ({ value: row.node || row.id, label: row.node || row.id }))],
        ''
      );
      await loadTarget(providerId, nodeField.searchableSelect.value());
    }
    async function loadTarget(providerId, nodeValue) {
      if (!nodeValue) {
        targetField.searchableSelect.setChoices([], '');
        return;
      }
      const path = '/providers/' + encodeURIComponent(providerId) + '/' + (kind === 'network' ? 'networks' : 'storages')
        + '?node=' + encodeURIComponent(nodeValue);
      const rows = (await api(path, { headers: scope.headers })).items || [];
      targetField.searchableSelect.setChoices(rows.map(row => ({
        value: kind === 'network' ? (row.iface || row.network || row.id) : (row.storage || row.id),
        label: kind === 'network' ? (row.iface || row.network || row.id) : (row.storage || row.id),
      })), '');
    }
    providerField.searchableSelect.onChange(value => loadProvider(value));
    nodeField.searchableSelect.onChange(value => loadTarget(providerField.searchableSelect.value(), value));
    await loadProvider(providerField.searchableSelect.value());

    openModal({
      title: 'Mapowanie ' + logical.name,
      eyebrow: kind === 'network' ? 'Logical Network' : 'Storage Class',
      body,
      submitLabel: 'Dodaj mapowanie',
      onSubmit: async data => {
        await api(
          (kind === 'network' ? '/logical-networks/' : '/storage-classes/')
          + encodeURIComponent(logical.id) + '/mappings',
          {
            method: 'POST',
            body: {
              provider_id: Number(data.get('provider_id')),
              node: String(data.get('node') || '').trim() || null,
              target: String(data.get('target') || '').trim(),
              metadata: {},
            },
            headers: scope.headers,
          }
        );
        await onSaved?.();
      },
    });
  }

  async function renderSettings(pool, scope, permissions) {
    const [networks, storageClasses] = await Promise.all([
      api('/logical-networks', { headers: scope.headers }),
      api('/storage-classes', { headers: scope.headers }),
    ]);
    const mappingTable = (kind, rows) => node('section', { class: 'resource-pool-mapping-section' },
      node('div', { class: 'panel-header' },
        node('div', {},
          node('h3', { text: kind === 'network' ? 'Logical Networks' : 'Storage Classes' }),
          node('p', { class: 'muted', text: kind === 'network'
            ? 'Blueprint używa nazwy logicznej, provider otrzymuje właściwy bridge/DVPG.'
            : 'Blueprint używa klasy logicznej, provider otrzymuje właściwy datastore/storage.' })),
        can(permissions, 'resource_pool.edit')
          ? button('Dodaj', () => logicalClassForm(kind, scope, () => poolDetails(pool.id, 'settings')), 'primary')
          : null),
      table([
        { label: 'Name', value: row => node('strong', { text: row.name }) },
        { label: 'Status', value: row => row.enabled ? badge('ENABLED', 'ok') : badge('DISABLED', 'danger') },
        { label: 'Mappings', value: row => (row.mappings || []).map(mapping =>
          [
            String(mapping.provider_id),
            mapping.node || '*',
            kind === 'network' ? mapping.network : mapping.storage,
          ].join(' / ')).join('; ') || '—' },
      ], rows, row => can(permissions, 'resource_pool.edit')
        ? [button('Dodaj mapping', () => mappingForm(kind, row, scope, () => poolDetails(pool.id, 'settings')))]
        : []));

    return node('div', { class: 'stack' },
      node('section', { class: 'panel' },
        heading('Konfiguracja puli.', can(permissions, 'resource_pool.edit')
          ? [button('Edytuj pulę', () => poolForm(pool, scope, () => poolDetails(pool.id, 'settings')), 'primary')]
          : [])),
      mappingTable('network', networks.items || []),
      mappingTable('storage', storageClasses.items || []));
  }

  async function poolDetails(poolId, activeTab = 'overview') {
    const scope = await currentScope();
    const listResult = await api('/resource-pools?limit=200', { headers: scope.headers });
    const pool = (listResult.items || []).find(row => String(row.id) === String(poolId))
      || await api('/resource-pools/' + encodeURIComponent(poolId), { headers: scope.headers });
    const permissions = listResult;
    const capacity = await api('/resource-pools/' + encodeURIComponent(pool.id) + '/capacity', { headers: scope.headers });
    const tab = TABS.some(([id]) => id === activeTab) ? activeTab : 'overview';

    const tabs = node('nav', { class: 'resource-pool-tabs', 'aria-label': 'Resource Pool' },
      ...TABS.map(([id, label]) => node('button', {
        type: 'button',
        class: 'resource-pool-tab' + (id === tab ? ' active' : ''),
        onClick: () => navigate('/resource-pools/' + encodeURIComponent(pool.id) + '/' + id),
      }, label)));

    let panel;
    if (tab === 'overview') {
      panel = node('div', { class: 'stack' },
        capacityPanel(capacity),
        node('section', { class: 'panel' },
          node('dl', { class: 'resource-pool-decision-grid' },
            node('div', {}, node('dt', { text: 'Strategy' }), node('dd', { text: pool.strategy })),
            node('div', {}, node('dt', { text: 'Members' }), node('dd', { text: String(pool.members ?? capacity.members_total ?? 0) })),
            node('div', {}, node('dt', { text: 'Active deployments' }), node('dd', { text: String(pool.active_deployments ?? 0) })),
            node('div', {}, node('dt', { text: 'Reservation TTL' }), node('dd', { text: String(pool.reservation_ttl_seconds) + ' s' })),
            node('div', {}, node('dt', { text: 'Retry limit' }), node('dd', { text: String(pool.retry_limit) })),
            node('div', {}, node('dt', { text: 'Created' }), node('dd', { text: dateTime(pool.created_at) })))));
    } else if (tab === 'members') panel = await renderMembers(pool, scope, permissions);
    else if (tab === 'rules') panel = await renderRules(pool, scope, permissions);
    else if (tab === 'capacity') panel = capacityPanel(capacity);
    else if (tab === 'reservations') panel = await renderReservations(pool, scope);
    else if (tab === 'deployments') panel = await renderHistory(pool, scope, true);
    else if (tab === 'history') panel = await renderHistory(pool, scope, false);
    else panel = await renderSettings(pool, scope, permissions);

    dom.content.replaceChildren(node('div', { class: 'stack resource-pools-view' },
      heading(pool.description || 'Resource Pool', [
        can(permissions, 'placement.simulate') ? button('Symuluj placement', () => simulator(scope, [pool])) : null,
        button('← Pule zasobów', () => navigate('/resource-pools')),
      ].filter(Boolean)),
      node('section', { class: 'resource-pool-hero' },
        node('div', {},
          node('span', { class: 'eyebrow', text: 'Resource Pool' }),
          node('h2', { text: pool.name }),
          node('div', { class: 'resource-pool-hero-meta' },
            healthBadge(capacity.health),
            badge(pool.strategy, 'info'),
            pool.enabled ? badge('ENABLED', 'ok') : badge('DISABLED', 'danger'))),
        node('div', { class: 'resource-pool-hero-capacity' },
          node('strong', { text: 'CPU ' + fmt(capacity.cpu_usage_pct, 1) + '%' }),
          node('span', { text: 'RAM ' + fmt(capacity.ram_usage_pct, 1) + '%' }),
          node('span', { text: 'Storage ' + fmt(capacity.storage_usage_pct, 1) + '%' }))),
      tabs,
      panel));
  }

  registerRoutedForm({
    id: 'resource-pools-details',
    pattern: /^\/resource-pools\/(?<id>[0-9a-fA-F-]{36})(?:\/(?<tab>overview|members|rules|capacity|reservations|deployments|history|settings))?$/,
    parent: 'resource-pools',
    permission: 'resource_pool.view',
    label: 'Pule zasobów',
  }, match => poolDetails(match.params.id, match.params.tab || 'overview'));

  registerView({
    id: 'resource-pools',
    label: 'Pule zasobów',
    iconName: 'server',
    permission: 'resource_pool.view',
    order: 55,
  }, poolListView);
})();
