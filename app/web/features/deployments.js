'use strict';

(() => {
let jobLogPollNonce = 0;
let myResourcesPollTimer = null;
let jobsPollTimer = null;

const MY_RESOURCES_VM_UI_KEY = 'cloudportal.my-resources.vms.ui.v1';
const myResourcesVmUi = (() => {
  const defaults = {
    view: 'cards',
    query: '',
    filtersOpen: false,
    filters: {
      apmid: '',
      environment: '',
      owner: '',
      project: '',
      tenant: '',
      status: '',
      provider: '',
      node: '',
      mine: false,
    },
  };
  try {
    const stored = JSON.parse(localStorage.getItem(MY_RESOURCES_VM_UI_KEY) || '{}');
    return {
      ...defaults,
      ...stored,
      view: stored.view === 'list' ? 'list' : 'cards',
      filtersOpen: false,
      filters: { ...defaults.filters, ...(stored.filters || {}) },
    };
  } catch {
    return defaults;
  }
})();

function saveMyResourcesVmUi() {
  try {
    localStorage.setItem(MY_RESOURCES_VM_UI_KEY, JSON.stringify({
      view: myResourcesVmUi.view,
      query: myResourcesVmUi.query,
      filters: myResourcesVmUi.filters,
    }));
  } catch {
    // Preferences remain available for the current page when storage is unavailable.
  }
}

function vmTagValues(deployment) {
  const raw = deployment?.variables?.tags || [];
  const tags = (Array.isArray(raw) ? raw : String(raw).split(/[;,\s]+/))
    .map(value => String(value || '').trim().toLowerCase())
    .filter(Boolean);
  const blueprintVariables = deployment?.workflow?.blueprint?.variables || {};
  return {
    apmid: (
      tags.find(value => value.startsWith('apmid-'))?.slice(6)
      || blueprintVariables.apmid
      || ''
    ).toString().trim().toUpperCase(),
    environment: (
      tags.find(value => value.startsWith('env-'))?.slice(4)
      || blueprintVariables.environment
      || ''
    ).toString().trim().toLowerCase(),
  };
}

function vmMetadata(item, deployment, providerNames, userNames, projectNames, tenantNames) {
  const classification = vmTagValues(deployment);
  const ownerId = String(deployment?.created_by ?? item.created_by ?? '');
  const projectId = String(item.project_id || deployment?.project_id || '');
  const tenantId = String(item.tenant_id || deployment?.tenant_id || '');
  const currentUserId = String(state.identity?.user?.id ?? '');
  const owner = ownerId
    ? (ownerId === currentUserId
        ? 'Ja · ' + (userNames.get(ownerId) || state.identity?.user?.username || ('#' + ownerId))
        : (userNames.get(ownerId) || ('Użytkownik #' + ownerId)))
    : '—';
  return {
    apmid: classification.apmid,
    environment: classification.environment,
    ownerId,
    owner,
    projectId,
    project: projectNames.get(projectId) || (projectId ? short(projectId, 12) : '—'),
    tenantId,
    tenant: tenantNames.get(tenantId) || (tenantId ? short(tenantId, 12) : '—'),
    status: String(item.live?.status || item.lifecycle_status || 'unknown').toLowerCase(),
    providerId: String(item.provider_id || ''),
    provider: providerNames.get(Number(item.provider_id)) || ('Platforma #' + item.provider_id),
    node: String(item.node || ''),
  };
}

function uniqueVmChoices(entries, valueKey, labelKey = valueKey) {
  const values = new Map();
  entries.forEach(({ meta }) => {
    const value = String(meta[valueKey] || '').trim();
    if (!value) return;
    values.set(value, String(meta[labelKey] || value));
  });
  return [...values.entries()]
    .sort((a, b) => a[1].localeCompare(b[1], 'pl', { sensitivity: 'base' }))
    .map(([value, label]) => ({ value, label }));
}

function activeVmFilterCount() {
  const filters = myResourcesVmUi.filters;
  return ['apmid', 'environment', 'owner', 'project', 'tenant', 'status', 'provider', 'node']
    .filter(name => Boolean(filters[name])).length
    + (filters.mine ? 1 : 0);
}

function vmMatchesFilters(entry) {
  const { item, meta } = entry;
  const filters = myResourcesVmUi.filters;
  const currentUserId = String(state.identity?.user?.id ?? '');
  if (filters.apmid && meta.apmid !== filters.apmid) return false;
  if (filters.environment && meta.environment !== filters.environment) return false;
  if (filters.owner && meta.ownerId !== filters.owner) return false;
  if (filters.project && meta.projectId !== filters.project) return false;
  if (filters.tenant && meta.tenantId !== filters.tenant) return false;
  if (filters.status && meta.status !== filters.status) return false;
  if (filters.provider && meta.providerId !== filters.provider) return false;
  if (filters.node && meta.node !== filters.node) return false;
  if (filters.mine && (!currentUserId || meta.ownerId !== currentUserId)) return false;

  const query = String(myResourcesVmUi.query || '').trim().toLocaleLowerCase('pl');
  if (!query) return true;
  const haystack = [
    item.name, item.vm_id, item.node, item.deployment_id,
    meta.apmid, meta.environment, meta.owner, meta.project, meta.tenant,
    meta.provider, meta.status, item.management_mode,
  ].map(value => String(value || '').toLocaleLowerCase('pl')).join(' ');
  return haystack.includes(query);
}

async function launchProductBlueprint(item) {
  if (!hasCommand('blueprints.execute')) {
    toast('Uruchamianie Blueprintu nie jest dostępne.', 'error');
    return;
  }
  await runCommand('blueprints.execute', item);
}

function productCard(item) {
  const deployment = item.deployment || {};
  const template = deployment.template || 'VM';
  const provider = deployment.provider || '';
  const meta = node('div', { class: 'product-card-meta' },
    badge('v' + item.version, 'info'),
    badge(template, ''),
    provider ? badge(provider, '') : null,
    item.requires_approval ? badge('Approval wg polityki globalnej', 'warning') : null);

  return node('article', { class: 'product-card' },
    node('div', { class: 'product-card-top' },
      node('span', { class: 'product-card-icon', 'aria-hidden': 'true' }, appIcon('box')),
      node('div', { class: 'product-card-copy' },
        node('strong', { text: item.name }),
        node('small', { class: 'mono muted', text: item.slug }))),
    node('p', {
      class: 'product-card-description',
      text: item.description || 'Gotowy Blueprint do utworzenia maszyny wirtualnej.',
    }),
    meta,
    node('div', { class: 'product-card-actions' },
      button('Utwórz VM', () => launchProductBlueprint(item), 'primary')));
}

function productsPanel(blueprints, canUseProducts) {
  const body = node('div', { class: 'product-grid' });
  if (!canUseProducts) {
    body.append(node('div', {
      class: 'product-catalog-empty',
      text: 'Brak uprawnień do uruchamiania produktów.',
    }));
  } else if (!blueprints.length) {
    body.append(node('div', {
      class: 'product-catalog-empty',
      text: 'Brak gotowych Blueprintów. Aktywuj Blueprint widoczny w panelu backendu, aby pojawił się jako produkt.',
    }));
  } else {
    blueprints.forEach(item => body.append(productCard(item)));
  }

  return node('section', { class: 'panel product-catalog-panel' },
    node('div', { class: 'product-catalog-header' },
      node('div', {},
        node('span', { class: 'product-catalog-kicker', text: 'Self-service' }),
        node('h2', { text: 'Produkty' }),
        node('p', { class: 'muted', text: 'Wybierz gotowy Blueprint. Formularz pokaże tylko parametry wymagane do utworzenia VM.' })),
      badge(String(blueprints.length) + ' dostępnych', blueprints.length ? 'ok' : '')),
    body);
}

async function deploymentsView() {
  const canUseProducts = allowed('blueprints.read')
    && allowed('blueprints.execute')
    && allowed('deployments.create')
    && allowed('terraform.read');

  const [blueprintResult, templateResult] = await Promise.all([
    canUseProducts ? api('/blueprints?available=true&limit=200') : Promise.resolve({ items: [] }),
    canUseProducts ? api('/templates') : Promise.resolve({ items: [] }),
  ]);

  const enabledTemplates = new Set(
    (templateResult.items || []).filter(item => item.enabled !== false).map(item => item.id)
  );
  const products = (blueprintResult.items || []).filter(item =>
    item.is_active
    && item.deployment?.template
    && enabledTemplates.has(item.deployment.template)
  );

  dom.content.replaceChildren(
    heading('Wybierz gotowy produkt i utwórz nową maszynę lub usługę. Lista istniejących zasobów znajduje się w sekcji „Moje zasoby” w menu bocznym.'),
    productsPanel(products, canUseProducts)
  );
}

function managedVmCard(item, providerNames, deploymentById, metadata = {}, onSelectionChange = null) {
  const liveStatus = item.live?.status || item.lifecycle_status || 'unknown';
  const active = item.lifecycle_status === 'active';
  const canOpen = allowed('vms.read') && active && hasCommand('inventory.openVm');
  const canConsole = allowed('vms.console') && active && hasCommand('inventory.consoleVm');
  const actions = [];
  if (canOpen) {
    actions.push(button('Zarządzaj VM', () => runCommand('inventory.openVm', item, 'overview', 'my-resources'), 'primary'));
  }
  if (canConsole) {
    actions.push(button('Konsola', () => runCommand('inventory.consoleVm', item), 'ghost'));
  }
  const deployment = item.deployment_id ? deploymentById.get(item.deployment_id) : null;
  const canRecreate = item.management_mode === 'terraform'
    && item.deployment_id
    && deployment?.status !== 'reconciliation_required'
    && hasCommand('inventory.recreateVm')
    && allowed('deployments.destroy')
    && allowed('deployments.create')
    && allowed('jobs.execute')
    && allowed('terraform.execute');
  if (canRecreate) {
    actions.push(button('Odtwórz od zera', () => runCommand('inventory.recreateVm', item), 'danger'));
  }
  if (deployment) actions.push(button('Wdrożenie', () => showDeploymentDetails(deployment), 'ghost'));

  const card = node('article', {
    class: 'my-resource-card my-resource-vm-card',
  },
    node('div', { class: 'my-resource-card-head' },
      node('span', { class: 'my-resource-card-icon', 'aria-hidden': 'true' }, appIcon('server')),
      node('div', { class: 'my-resource-card-title' },
        node('strong', { text: item.name || ('VM ' + item.vm_id) }),
        node('small', { class: 'mono muted', text: (item.node || '—') + ' / VMID ' + item.vm_id })),
      badge(statusLabel(liveStatus), statusKind(liveStatus))),
    node('div', { class: 'my-resource-card-meta' },
      node('span', { text: metadata.provider || providerNames.get(Number(item.provider_id)) || ('Platforma #' + item.provider_id) }),
      node('span', { text: statusLabel(item.management_mode) }),
      metadata.apmid ? node('span', { text: 'APMID: ' + metadata.apmid }) : null,
      metadata.environment ? node('span', { text: 'ENV: ' + metadata.environment.toUpperCase() }) : null,
      metadata.owner && metadata.owner !== '—' ? node('span', { text: 'Właściciel: ' + metadata.owner }) : null,
      metadata.project && metadata.project !== '—' ? node('span', { text: 'Projekt: ' + metadata.project }) : null),
    actions.length
      ? node('div', { class: 'my-resource-card-actions' }, ...actions)
      : node('small', { class: 'muted', text: 'Brak uprawnień do sterowania tą VM.' }));

  window.vmBulkActions?.decorateCard(card, item, onSelectionChange);
  return card;
}

function createVmBrowser(vms, providerNames, deploymentById, userNames, projectNames, tenantNames) {
  const entries = vms.map(item => ({
    item,
    meta: vmMetadata(
      item,
      item.deployment_id ? deploymentById.get(item.deployment_id) : null,
      providerNames,
      userNames,
      projectNames,
      tenantNames,
    ),
  }));

  const wrapper = node('div', { class: 'my-resources-vm-browser' });
  const controls = node('div', { class: 'my-resources-vm-controls' });
  const filterPanel = node('div', { class: 'my-resources-filter-panel' });
  const results = node('div', { class: 'my-resources-vm-results' });
  const resultSummary = node('span', { class: 'my-resources-vm-result-count muted' });

  const search = node('input', {
    type: 'search',
    class: 'my-resources-vm-search',
    value: myResourcesVmUi.query,
    placeholder: 'Szukaj VM, VMID, node, APMID…',
    'aria-label': 'Szukaj maszyn wirtualnych',
  });
  const filterButton = button('Filtry', () => {
    myResourcesVmUi.filtersOpen = !myResourcesVmUi.filtersOpen;
    filterPanel.hidden = !myResourcesVmUi.filtersOpen;
    filterButton.setAttribute('aria-expanded', String(myResourcesVmUi.filtersOpen));
  }, 'ghost');
  filterButton.classList.add('my-resources-filter-button');
  filterButton.setAttribute('aria-expanded', String(myResourcesVmUi.filtersOpen));

  const cardsButton = button('Kafelki', () => {
    myResourcesVmUi.view = 'cards';
    saveMyResourcesVmUi();
    renderResults();
  }, 'ghost');
  const listButton = button('Lista', () => {
    myResourcesVmUi.view = 'list';
    saveMyResourcesVmUi();
    renderResults();
  }, 'ghost');
  const viewToggle = node('div', { class: 'my-resources-vm-view-toggle', 'aria-label': 'Rodzaj wyświetlania' },
    node('span', { class: 'muted', text: 'Widok:' }),
    cardsButton,
    listButton);

  controls.append(search, filterButton, resultSummary, viewToggle);

  const selectFilter = (label, name, choices, value) => {
    const control = selectField(label, name, [
      { value: '', label: 'Wszystkie' },
      ...choices,
    ], value, { wide: false });
    const select = control.querySelector('select');
    select.addEventListener('change', () => {
      myResourcesVmUi.filters[name.replace('vm_filter_', '')] = select.value;
      saveMyResourcesVmUi();
      renderResults();
    });
    return control;
  };

  const currentUserId = String(state.identity?.user?.id ?? '');
  const ownerChoices = uniqueVmChoices(entries, 'ownerId', 'owner');
  const filterFields = node('div', { class: 'my-resources-filter-grid' },
    selectFilter('APMID', 'vm_filter_apmid', uniqueVmChoices(entries, 'apmid'), myResourcesVmUi.filters.apmid),
    selectFilter('Środowisko', 'vm_filter_environment', uniqueVmChoices(entries, 'environment').map(row => ({
      ...row,
      label: row.label.toUpperCase(),
    })), myResourcesVmUi.filters.environment),
    selectFilter('Właściciel', 'vm_filter_owner', ownerChoices, myResourcesVmUi.filters.owner),
    selectFilter('Projekt', 'vm_filter_project', uniqueVmChoices(entries, 'projectId', 'project'), myResourcesVmUi.filters.project),
    selectFilter('Tenant', 'vm_filter_tenant', uniqueVmChoices(entries, 'tenantId', 'tenant'), myResourcesVmUi.filters.tenant),
    selectFilter('Status', 'vm_filter_status', uniqueVmChoices(entries, 'status').map(row => ({
      ...row,
      label: statusLabel(row.value),
    })), myResourcesVmUi.filters.status),
    selectFilter('Platforma', 'vm_filter_provider', uniqueVmChoices(entries, 'providerId', 'provider'), myResourcesVmUi.filters.provider),
    selectFilter('Node', 'vm_filter_node', uniqueVmChoices(entries, 'node'), myResourcesVmUi.filters.node));

  const onlyMine = checkboxField('Tylko moje VM', 'vm_filter_mine', Boolean(myResourcesVmUi.filters.mine));
  onlyMine.querySelector('input').disabled = !currentUserId;
  onlyMine.querySelector('input').addEventListener('change', event => {
    myResourcesVmUi.filters.mine = event.currentTarget.checked;
    saveMyResourcesVmUi();
    renderResults();
  });

  const clearFilters = button('Wyczyść filtry', () => {
    Object.assign(myResourcesVmUi.filters, {
      apmid: '', environment: '', owner: '', project: '', tenant: '',
      status: '', provider: '', node: '', mine: false,
    });
    myResourcesVmUi.query = '';
    search.value = '';
    filterPanel.querySelectorAll('select').forEach(select => { select.value = ''; });
    const mine = filterPanel.querySelector('[name="vm_filter_mine"]');
    if (mine) mine.checked = false;
    saveMyResourcesVmUi();
    renderResults();
  }, 'ghost');
  const applyFilters = button('Zastosuj', () => {
    myResourcesVmUi.filtersOpen = false;
    filterPanel.hidden = true;
    filterButton.setAttribute('aria-expanded', 'false');
  }, 'primary');
  filterPanel.append(
    filterFields,
    node('div', { class: 'my-resources-filter-actions' }, onlyMine, clearFilters, applyFilters)
  );
  filterPanel.hidden = !myResourcesVmUi.filtersOpen;

  function renderResults() {
    const filtered = entries.filter(vmMatchesFilters);
    const filterCount = activeVmFilterCount();
    filterButton.textContent = filterCount ? 'Filtry (' + filterCount + ')' : 'Filtry';
    filterButton.classList.toggle('active', filterCount > 0);
    cardsButton.classList.toggle('active', myResourcesVmUi.view === 'cards');
    listButton.classList.toggle('active', myResourcesVmUi.view === 'list');
    resultSummary.textContent = filtered.length + ' z ' + entries.length + ' VM';

    if (!filtered.length) {
      results.replaceChildren(resourceEmptyState(
        'monitor',
        'Brak VM pasujących do filtrów',
        'Wyczyść filtry albo zmień kryteria wyszukiwania.'
      ));
      return;
    }

    let vmBulkControls = null;
    const grid = node('div', {
      class: 'my-resource-grid ' + (myResourcesVmUi.view === 'list' ? 'my-resource-grid-list' : 'my-resource-grid-cards'),
    });
    filtered.forEach(({ item, meta }) => grid.append(managedVmCard(
      item,
      providerNames,
      deploymentById,
      meta,
      () => vmBulkControls?.sync(),
    )));
    vmBulkControls = window.vmBulkActions?.toolbar(
      filtered.map(entry => entry.item),
      grid,
      () => myResourcesView(false)
    ) || null;
    if (vmBulkControls) results.replaceChildren(vmBulkControls.element, grid);
    else results.replaceChildren(grid);
  }

  search.addEventListener('input', () => {
    myResourcesVmUi.query = search.value;
    saveMyResourcesVmUi();
    renderResults();
  });

  wrapper.append(controls, filterPanel, results);
  renderResults();
  return wrapper;
}

function managedResourceCard(item, providerNames) {
  const details = {
    Typ: item.resource_type,
    Platforma: providerNames.get(Number(item.provider_id)) || item.provider,
    'External ID': item.external_id,
    IP: item.primary_ip || '—',
    Status: statusLabel(item.lifecycle_status),
    Wdrożenie: item.deployment_id,
    ...(item.metadata_json || {}),
  };
  return node('article', { class: 'my-resource-card' },
    node('div', { class: 'my-resource-card-head' },
      node('span', { class: 'my-resource-card-icon', 'aria-hidden': 'true' }, appIcon('box')),
      node('div', { class: 'my-resource-card-title' },
        node('strong', { text: item.name || item.resource_type || 'Zasób' }),
        node('small', { class: 'muted', text: item.resource_type || 'resource' })),
      badge(statusLabel(item.lifecycle_status), statusKind(item.lifecycle_status))),
    node('div', { class: 'my-resource-card-meta' },
      node('span', { text: providerNames.get(Number(item.provider_id)) || CREDENTIAL_TYPE_CONFIG[item.provider]?.label || item.provider || '—' }),
      item.primary_ip ? node('span', { class: 'mono', text: item.primary_ip }) : null),
    node('div', { class: 'my-resource-card-actions' },
      button('Szczegóły', () => showObjectDetails(item.name || 'Zasób', details, 'Zasób zarządzany'))));
}

function resourceSummaryCard(iconName, label, count, subtitle, tone, sectionId) {
  return node('button', {
    class: 'my-resources-summary-card my-resources-summary-card-' + tone,
    type: 'button',
    onClick: () => document.getElementById(sectionId)?.scrollIntoView({ behavior: 'smooth', block: 'start' }),
  },
    node('span', { class: 'my-resources-summary-icon', 'aria-hidden': 'true' }, appIcon(iconName)),
    node('span', { class: 'my-resources-summary-copy' },
      node('span', { class: 'my-resources-summary-label', text: label }),
      node('strong', { text: String(count) }),
      node('small', { text: subtitle })),
    node('span', { class: 'my-resources-summary-chevron', 'aria-hidden': 'true' }, appIcon('chevron-right')));
}

function resourceEmptyState(iconName, title, description) {
  return node('div', { class: 'my-resources-empty' },
    node('span', { class: 'my-resources-empty-icon', 'aria-hidden': 'true' }, appIcon(iconName)),
    node('strong', { text: title }),
    node('span', { class: 'muted', text: description }));
}

function resourceSection(id, iconName, title, description, count, content) {
  return node('section', { class: 'panel my-resources-section', id },
    node('div', { class: 'my-resources-section-head' },
      node('div', { class: 'my-resources-section-title' },
        node('span', { class: 'my-resources-section-icon', 'aria-hidden': 'true' }, appIcon(iconName)),
        node('div', {},
          node('h2', { text: title }),
          node('p', { class: 'muted', text: description }))),
      node('span', { class: 'my-resources-section-count', text: String(count) })),
    content);
}

async function myResourcesView(repairInventory = true) {
  if (myResourcesPollTimer) {
    clearTimeout(myResourcesPollTimer);
    myResourcesPollTimer = null;
  }
  const preserveScroll = Boolean(dom.content.querySelector('.my-resources-page-head'));
  const preservedScrollY = preserveScroll ? window.scrollY : 0;
  const canReadInventory = allowed('inventory.read');

  if (repairInventory && canReadInventory && allowed('inventory.update')) {
    try {
      const repaired = await api('/inventory/reconcile', { method: 'POST', body: {} });
      if (Number(repaired.repaired_count || 0) > 0) {
        toast('Odbudowano inventory dla ' + repaired.repaired_count + ' wdrożeń.');
      }
    } catch (error) {
      toast('Nie udało się automatycznie zsynchronizować inventory: ' + error.message, 'warning');
    }
  }

  const optionalItems = path => api(path).then(result => result.items || []).catch(() => []);
  const [deploymentResult, vmResult, resourceResult, providerResult, users, projects, tenants] = await Promise.all([
    allowed('deployments.read') ? api('/deployments?limit=200') : Promise.resolve({ items: [] }),
    canReadInventory ? api('/inventory/vms?limit=200') : Promise.resolve({ items: [] }),
    canReadInventory ? api('/inventory/resources?limit=200') : Promise.resolve({ items: [] }),
    allowed('providers.read') ? api('/providers?limit=200') : Promise.resolve({ items: [] }),
    allowed('users.read') ? optionalItems('/users?limit=200') : Promise.resolve([]),
    optionalItems('/projects?limit=200'),
    optionalItems('/tenants?limit=200'),
  ]);

  const deployments = deploymentResult.items || [];
  const vms = vmResult.items || [];
  const resources = (resourceResult.items || []).filter(item => item.resource_type !== 'vm');
  const providerNames = new Map((providerResult.items || []).map(provider => [Number(provider.id), provider.name]));
  const vmByDeployment = new Map(vms.filter(item => item.deployment_id).map(item => [item.deployment_id, item]));
  const deploymentById = new Map(deployments.map(item => [item.id, item]));
  const userNames = new Map(users.map(user => [
    String(user.id),
    [user.first_name, user.last_name].filter(Boolean).join(' ').trim() || user.username || ('#' + user.id),
  ]));
  if (state.identity?.user?.id) {
    const current = state.identity.user;
    userNames.set(String(current.id),
      [current.first_name, current.last_name].filter(Boolean).join(' ').trim()
      || current.username
      || ('#' + current.id));
  }
  const projectNames = new Map(projects.map(project => [String(project.id), project.name || project.slug || project.id]));
  const tenantNames = new Map(tenants.map(tenant => [String(tenant.id), tenant.name || tenant.slug || tenant.id]));

  const vmGrid = vms.length
    ? createVmBrowser(vms, providerNames, deploymentById, userNames, projectNames, tenantNames)
    : resourceEmptyState('monitor', 'Brak maszyn VM', 'Nie ma VM dostępnych dla bieżących uprawnień.');

  const resourceGrid = resources.length
    ? node('div', { class: 'my-resource-grid' }, ...resources.map(item => managedResourceCard(item, providerNames)))
    : resourceEmptyState('box', 'Brak innych zasobów', 'Nie ma innych zarządzanych zasobów dostępnych dla bieżących uprawnień.');

  const deploymentContent = deployments.length
    ? table([
        { label: 'Nazwa', value: item => node('div', {}, node('strong', { text: item.name }), node('div', { class: 'mono muted', text: short(item.id, 18) })) },
        { label: 'Platforma', value: item => providerNames.get(Number(item.provider_id)) || CREDENTIAL_TYPE_CONFIG[item.provider]?.label || ('#' + item.provider_id) },
        { label: 'Typ', value: item => vmByDeployment.has(item.id) ? badge('VM', 'info') : badge(item.template || 'Zasób', '') },
        { label: 'Status', value: item => badge(statusLabel(item.status), statusKind(item.status)) },
        { label: 'Aktualizacja', value: item => formatDate(item.updated_at) },
      ], deployments, item => {
        const actions = [];
        const vm = vmByDeployment.get(item.id);
        if (vm && allowed('vms.read') && hasCommand('inventory.openVm')) {
          actions.push(button('Zarządzaj VM', () => runCommand('inventory.openVm', vm, 'overview', 'my-resources'), 'primary'));
        }
        actions.push(...deploymentActions(item, 'my-resources'));
        return actions;
      })
    : resourceEmptyState('rocket', 'Brak wdrożeń', 'Nie ma wdrożeń dostępnych dla bieżących uprawnień.');

  const body = node('div', { class: 'my-resources-body' },
    node('div', { class: 'my-resources-summary' },
      resourceSummaryCard('server', 'VM', vms.length, 'Maszyny wirtualne', 'vm', 'my-resources-vms'),
      resourceSummaryCard('database', 'Inne zasoby', resources.length, 'Zasoby dodatkowe', 'resources', 'my-resources-other'),
      resourceSummaryCard('rocket', 'Wdrożenia', deployments.length, 'Aktywne wdrożenia', 'deployments', 'my-resources-deployments')),
    resourceSection('my-resources-vms', 'monitor', 'Maszyny wirtualne', 'Zaznacz VM, aby wykonać akcję masową, albo kliknij „Zarządzaj VM”, aby przejść do konsoli, snapshotów, backupów i konfiguracji.', vms.length, vmGrid),
    resourceSection('my-resources-other', 'box', 'Inne zasoby', 'Pozostałe zasoby zarządzane przez Cloudportal i dostępne przez bieżące uprawnienia.', resources.length, resourceGrid),
    resourceSection('my-resources-deployments', 'rocket', 'Wdrożenia i operacje', 'Historia i stan wdrożeń. Dla wdrożenia VM dostępny jest bezpośredni skrót do panelu maszyny.', deployments.length, deploymentContent));

  dom.content.replaceChildren(
    node('div', { class: 'my-resources-page-head' },
      node('p', {
        class: 'my-resources-intro',
        text: 'Zasoby i wdrożenia dostępne dla zalogowanego użytkownika. Wejście w VM otwiera panel sterowania zgodny z jego uprawnieniami.',
      })),
    body
  );

  if (preservedScrollY > 0) {
    requestAnimationFrame(() => window.scrollTo({ top: preservedScrollY, behavior: 'auto' }));
  }

  const provisioningActive = deployments.some(item =>
    item.active_job_id || ['waiting_approval', 'queued', 'running', 'cancelling', 'waiting_provider', 'recovery_queued'].includes(String(item.status || '')));
  if (provisioningActive) {
    myResourcesPollTimer = window.setTimeout(() => {
      if (state.view === 'my-resources' && dom.content.querySelector('.my-resources-page-head')) {
        myResourcesView(false).catch(error => toast(error.message, 'error'));
      }
    }, 2000);
  }
}

function deploymentActions(item, returnTo = 'my-resources') {
  const actions = [button('Szczegóły', () => showDeploymentDetails(item))];
  const reconciliationRequired = item.status === 'reconciliation_required';
  if (item.status === 'waiting_approval' && item.active_job_id && allowed('blueprints.approve')) {
    actions.push(button('Zatwierdź i uruchom', async () => {
      await api('/jobs/' + item.active_job_id + '/approve', { method: 'POST' });
      toast('Wdrożenie zatwierdzone i przekazane do kolejki.');
      navigate(returnTo);
    }, 'primary'));
  }
  if (item.active_job_id && allowed('jobs.cancel') && item.status !== 'cancelling') {
    actions.push(button('Anuluj', () => confirmAction(
      'Anuluj aktywne zadanie',
      'Backend przerwie Terraform/Ansible. Jeżeli worker już nie działa, dispatcher zamknie osierocony job automatycznie.',
      async () => {
        const cancelled = await api('/jobs/' + item.active_job_id + '/cancel', { method: 'POST' });
        toast(cancelled.status === 'cancelled' ? 'Zadanie anulowane.' : 'Anulowanie rozpoczęte.');
        navigate(returnTo);
      },
    ), 'danger'));
  }
  if (allowed('jobs.execute') && allowed('terraform.execute') && !item.active_job_id && item.status !== 'destroyed') {
    actions.push(button(
      reconciliationRequired ? 'Plan reconciliacyjny' : 'Plan',
      () => createTerraformJob(item, 'terraform.plan'),
      reconciliationRequired ? 'primary' : undefined,
    ));
    if (!reconciliationRequired) {
      actions.push(button('Zastosuj', () => createTerraformJob(item, 'terraform.apply')));
    }
  }
  if (allowed('deployments.destroy') && allowed('jobs.execute') && !item.active_job_id
      && item.status !== 'destroyed' && !reconciliationRequired) {
    actions.push(button('Usuń zasoby', () => confirmAction('Usuń zasoby wdrożenia', `Terraform usunie zasoby wdrożenia ${item.name}.`, async () => { await api(`/deployments/${item.id}/destroy`, { method: 'POST', body: {}, idempotent: true }); toast('Utworzono zadanie usuwania zasobów.'); navigate(returnTo); }), 'danger'));
  }
  return actions;
}

function showDeploymentDetails(item) {
  const variableRows = Object.entries(item.variables || {}).map(([key, value]) => ({ key, value }));
  const workflow = item.workflow || {};
  const overview = node('div', { class: 'checks' },
    info('Status', statusLabel(item.status)),
    info('Platforma', CREDENTIAL_TYPE_CONFIG[item.provider]?.label || item.provider),
    info('Szablon', item.template),
    info('Silnik IaC', item.executor),
    info('Utworzono', formatDate(item.created_at)),
    info('Aktualizacja', formatDate(item.updated_at)));
  const content = node('div', { class: 'stack' },
    overview,
    node('section', { class: 'detail-section' },
      node('h3', { text: 'Parametry wdrożenia' }),
      variableRows.length ? table([
        { label: 'Pole', value: row => FIELD_LABELS[row.key] || row.key.replaceAll('_', ' ') },
        { label: 'Wartość', value: row => displayValue(row.value) },
      ], variableRows) : node('p', { class: 'muted', text: 'Brak parametrów.' })));
  const ansibleRuns = Array.isArray(workflow.ansible_runs) && workflow.ansible_runs.length
    ? workflow.ansible_runs
    : (workflow.ansible ? [workflow.ansible] : []);
  if (ansibleRuns.length) {
    const orderedRuns = ansibleRuns.map((run, index) => ({ ...run, order: index + 1 }));
    content.append(node('section', { class: 'detail-section' },
      node('h3', { text: 'Konfiguracja Ansible' }),
      table([
        { label: '#', value: row => row.order },
        { label: 'Runbook / Playbook', value: row => row.playbook },
        { label: 'Dane dostępowe', value: row => `#${row.credentials_id}` },
      ], orderedRuns)));
  }
  dom.modal.classList.add('modal-wide');
  dom.modalTitle.textContent = item.name;
  dom.modalEyebrow.textContent = `Wdrożenie · ${short(item.id, 18)}`;
  dom.modalBody.replaceChildren(content);
  const actions = [button('Zamknij', closeModal)];
  if (item.status === 'waiting_approval' && item.active_job_id && allowed('blueprints.approve')) {
    actions.unshift(button('Zatwierdź i uruchom', async () => {
      await api('/jobs/' + item.active_job_id + '/approve', { method: 'POST' });
      toast('Wdrożenie zatwierdzone i przekazane do kolejki.');
      closeModal();
      navigate('my-resources');
    }, 'primary'));
  }
  if (item.active_job_id && allowed('jobs.read')) actions.unshift(button('Przejdź do zadań', () => navigate('jobs'), 'primary'));
  dom.modalActions.replaceChildren(...actions);
  if (!(typeof window.modalSurfaceOpen === 'function' ? window.modalSurfaceOpen() : dom.modal.open)) dom.modal.showModal();
}

async function createTerraformJob(item, operation) {
  try { await api('/jobs', { method: 'POST', body: { operation, deployment_id: item.id }, idempotent: true }); toast(`Utworzono zadanie: ${operationLabel(operation)}.`); navigate('jobs'); }
  catch (error) { toast(error.message, 'error'); }
}


function deploymentVariableValue(container, name) {
  return container.querySelector('[name="' + name + '"]')?.value || '';
}

function deploymentVariableWrapper(container, name) {
  return container.querySelector('[data-template-variable="' + name + '"]');
}

function deploymentVariableRequired(template, name) {
  return new Set(template?.variables_schema?.required || []).has(name);
}

function deploymentSetSelectChoices(select, choices, selected = '', placeholder = '') {
  select.replaceChildren();
  if (placeholder) select.append(node('option', { value: '', text: placeholder }));
  choices.forEach(choice => select.append(node('option', {
    value: choice.value,
    text: choice.label,
    selected: String(choice.value) === String(selected),
  })));
  if (!select.value && choices.length === 1) select.value = String(choices[0].value);
}

function deploymentReplaceVariableWithSelect(container, template, name, label, choices, selected, placeholder, help) {
  const current = deploymentVariableWrapper(container, name);
  if (!current) return null;
  const replacement = selectField(label, name, choices, selected, {
    required: deploymentVariableRequired(template, name),
    placeholder,
  });
  replacement.setAttribute('data-template-variable', name);
  if (help) replacement.append(node('small', { class: 'field-help', text: help }));
  current.replaceWith(replacement);
  return replacement.querySelector('select');
}

function createProxmoxTemplatePicker(container, rows, selectedId = '', selectedNode = '', onSelect = null) {
  const current = deploymentVariableWrapper(container, 'template_id');
  if (!current) return null;

  const hiddenId = node('input', { type: 'hidden', name: 'template_id', value: selectedId });
  const hiddenNode = node('input', { type: 'hidden', name: 'template_node', value: selectedNode });
  const search = node('input', {
    type: 'search',
    class: 'proxmox-template-search',
    placeholder: 'Szukaj po nazwie, VMID lub węźle…',
    'aria-label': 'Szukaj szablonu Proxmox',
  });
  const nodeFilter = node('select', { class: 'proxmox-template-node-filter', 'aria-label': 'Filtruj szablony po węźle' });
  const nodes = [...new Set(rows.map(row => String(row.node || '')).filter(Boolean))].sort((a, b) => a.localeCompare(b));
  nodeFilter.append(node('option', { value: '', text: 'Wszystkie węzły' }));
  nodes.forEach(value => nodeFilter.append(node('option', { value, text: value })));

  const summary = node('div', { class: 'proxmox-template-selection', 'aria-live': 'polite' });
  const grid = node('div', { class: 'proxmox-template-grid', role: 'listbox', 'aria-label': 'Szablony Proxmox' });

  const selectTemplate = row => {
    hiddenId.value = String(row?.vmid || '');
    hiddenNode.value = String(row?.node || '');
    render();
    if (typeof onSelect === 'function') onSelect(row);
  };

  const render = () => {
    const query = search.value.trim().toLowerCase();
    const filterNode = nodeFilter.value;
    const visible = rows.filter(row => {
      if (filterNode && String(row.node || '') !== filterNode) return false;
      if (!query) return true;
      return [row.name, row.vmid, row.node]
        .map(value => String(value || '').toLowerCase())
        .some(value => value.includes(query));
    });

    grid.replaceChildren();
    visible.forEach(row => {
      const selected = String(row.vmid) === String(hiddenId.value)
        && String(row.node || '') === String(hiddenNode.value || '');
      const facts = [];
      if (Number.isFinite(Number(row.maxcpu)) && Number(row.maxcpu) > 0) facts.push(String(row.maxcpu) + ' vCPU');
      if (Number.isFinite(Number(row.maxmem)) && Number(row.maxmem) > 0) facts.push(formatBytes(row.maxmem) + ' RAM');
      if (Number.isFinite(Number(row.maxdisk)) && Number(row.maxdisk) > 0) facts.push(formatBytes(row.maxdisk) + ' dysk');

      const card = node('button', {
        type: 'button',
        class: 'proxmox-template-card' + (selected ? ' selected' : ''),
        role: 'option',
        'aria-selected': String(selected),
      },
        node('span', { class: 'proxmox-template-card-icon' }, appIcon('box')),
        node('span', { class: 'proxmox-template-card-copy' },
          node('strong', { text: row.name || ('VM template ' + row.vmid) }),
          node('span', { class: 'proxmox-template-card-meta' },
            node('span', { class: 'badge info', text: 'VMID ' + row.vmid }),
            node('span', { class: 'badge', text: row.node || 'brak węzła' })),
          facts.length ? node('small', { text: facts.join(' · ') }) : null),
        selected ? node('span', { class: 'proxmox-template-card-check', 'aria-hidden': 'true' }, appIcon('check')) : null
      );
      card.addEventListener('click', () => selectTemplate(row));
      grid.append(card);
    });

    if (!visible.length) {
      grid.append(node('div', { class: 'proxmox-template-empty', text: rows.length
        ? 'Brak szablonów pasujących do filtra.'
        : 'Na tej platformie Proxmox nie znaleziono szablonów QEMU.' }));
    }

    const selected = rows.find(row =>
      String(row.vmid) === String(hiddenId.value)
      && String(row.node || '') === String(hiddenNode.value || ''));
    summary.replaceChildren(
      selected
        ? node('div', { class: 'proxmox-template-selected-summary' },
          node('span', { class: 'proxmox-template-selected-icon' }, appIcon('check')),
          node('span', {},
            node('strong', { text: selected.name || ('VM template ' + selected.vmid) }),
            node('small', { text: 'VMID ' + selected.vmid + ' · węzeł źródłowy ' + (selected.node || '—') })))
        : node('span', { class: 'muted', text: 'Kliknij kafelek, aby wybrać bazowy szablon VM. VMID i węzeł źródłowy zostaną ustawione automatycznie.' })
    );
  };

  search.addEventListener('input', render);
  nodeFilter.addEventListener('change', render);

  const picker = node('section', {
    class: 'proxmox-template-picker wide',
    'data-template-variable': 'template_id',
  },
    hiddenId,
    hiddenNode,
    node('div', { class: 'proxmox-template-picker-header' },
      node('div', {},
        node('strong', { text: 'Obraz / szablon Proxmox' }),
        node('small', { text: 'Bez ręcznego wpisywania VMID. Wybierz szablon bezpośrednio z aktualnego inventory Proxmox.' }))),
    node('div', { class: 'proxmox-template-toolbar' }, search, nodeFilter),
    summary,
    grid
  );

  current.replaceWith(picker);
  const oldTemplateNode = deploymentVariableWrapper(container, 'template_node');
  if (oldTemplateNode && oldTemplateNode !== picker) oldTemplateNode.remove();

  const exact = rows.find(row =>
    String(row.vmid) === String(selectedId)
    && (!selectedNode || String(row.node || '') === String(selectedNode)));
  if (exact) selectTemplate(exact);
  else if (rows.length === 1) selectTemplate(rows[0]);
  else render();

  return { picker, hiddenId, hiddenNode };
}

async function enhanceProxmoxDeploymentVariables(container, template, providerSelect) {
  if (template?.provider !== 'proxmox') return;

  const providerId = providerSelect.value;
  if (!providerId) return;

  const previous = {
    node: deploymentVariableValue(container, 'node'),
    templateId: deploymentVariableValue(container, 'template_id'),
    templateNode: deploymentVariableValue(container, 'template_node'),
    storage: deploymentVariableValue(container, 'storage'),
    network: deploymentVariableValue(container, 'network') || 'vmbr0',
  };

  const [nodeResult, templateResult] = await Promise.all([
    api('/providers/' + providerId + '/nodes'),
    api('/providers/' + providerId + '/templates'),
  ]);

  const nodeChoices = nodeResult.items
    .filter(item => item.node)
    .map(item => ({
      value: item.node,
      label: item.node + (item.status ? ' · ' + statusLabel(item.status) : ''),
    }));

  const nodeSelect = deploymentReplaceVariableWithSelect(
    container,
    template,
    'node',
    'Węzeł / lokalizacja',
    nodeChoices,
    previous.node,
    'Wybierz docelowy węzeł',
    'Lista jest pobierana bezpośrednio z wybranej platformy Proxmox.'
  );

  const storageSelect = deploymentReplaceVariableWithSelect(
    container,
    template,
    'storage',
    'Storage',
    [],
    previous.storage,
    'Najpierw wybierz węzeł',
    'Pokazywane są storage dostępne na wybranym węźle.'
  );
  const networkSelect = deploymentReplaceVariableWithSelect(
    container,
    template,
    'network',
    'Sieć / bridge',
    [],
    previous.network,
    'Najpierw wybierz węzeł',
    'Bridge jest wybierany z sieci wykrytych na wybranym węźle.'
  );

  const loadNodeResources = async () => {
    if (!nodeSelect?.value) {
      if (storageSelect) deploymentSetSelectChoices(storageSelect, [], '', 'Wybierz docelowy węzeł');
      if (networkSelect) deploymentSetSelectChoices(networkSelect, [], '', 'Wybierz docelowy węzeł');
      return;
    }

    const keepStorage = storageSelect?.value || previous.storage;
    const keepNetwork = networkSelect?.value || previous.network || 'vmbr0';
    const [storageResult, networkResult] = await Promise.all([
      api('/providers/' + providerId + '/storages?node=' + encodeURIComponent(nodeSelect.value)),
      api('/providers/' + providerId + '/networks?node=' + encodeURIComponent(nodeSelect.value)),
    ]);

    if (storageSelect) {
      const storages = storageResult.items
        .filter(item => {
          if (item.disable) return false;
          const content = Array.isArray(item.content) ? item.content.join(',') : String(item.content || '');
          return !content || content.includes('images');
        })
        .map(item => ({
          value: item.storage || item.id,
          label: (item.storage || item.id || 'storage')
            + (item.type ? ' [' + item.type + ']' : '')
            + (Number.isFinite(Number(item.avail)) ? ' · wolne ' + formatBytes(item.avail) : ''),
        }));
      deploymentSetSelectChoices(storageSelect, storages, keepStorage, storages.length ? 'Wybierz storage' : 'Brak storage dla VM');
    }

    if (networkSelect) {
      const networks = networkResult.items
        .filter(item => item.iface)
        .map(item => ({
          value: item.iface,
          label: item.iface
            + (item.type ? ' [' + item.type + ']' : '')
            + (item.active === 1 || item.active === true ? ' · aktywna' : ''),
        }));
      const preferred = networks.some(item => String(item.value) === String(keepNetwork))
        ? keepNetwork
        : (networks.some(item => String(item.value) === 'vmbr0') ? 'vmbr0' : '');
      deploymentSetSelectChoices(networkSelect, networks, preferred, networks.length ? 'Wybierz bridge' : 'Brak sieci na węźle');
    }
  };

  if (nodeSelect) {
    nodeSelect.addEventListener('change', () => {
      loadNodeResources().catch(error => toast(error.message, 'error'));
    });
  }

  const picker = createProxmoxTemplatePicker(
    container,
    templateResult.items || [],
    previous.templateId,
    previous.templateNode,
    row => {
      if (!nodeSelect || !row?.node) return;
      const matchingTargetNode = [...nodeSelect.options].some(option => String(option.value) === String(row.node));
      if (!matchingTargetNode) {
        toast('Węzeł szablonu nie jest dostępny jako węzeł docelowy. Wybierz węzeł ręcznie.', 'warning');
        return;
      }
      if (!nodeSelect.value) nodeSelect.value = String(row.node);
      loadNodeResources().catch(error => toast(error.message, 'error'));
    }
  );

  if (nodeSelect && !nodeSelect.value && picker?.hiddenNode?.value) {
    const templateNode = String(picker.hiddenNode.value);
    const matchingTargetNode = [...nodeSelect.options].some(option => String(option.value) === templateNode);
    if (matchingTargetNode) nodeSelect.value = templateNode;
  }

  await loadNodeResources();
}


function createAnsiblePlaybookPreview(playbookSelect) {
  const body = node('div', { class: 'ansible-playbook-preview-body' },
    node('div', { class: 'muted', text: 'Kliknij „Podgląd playbooka”, aby wczytać YAML.' }));
  const panel = node('section', { class: 'ansible-playbook-preview wide', hidden: true },
    node('div', { class: 'ansible-playbook-preview-header' },
      node('div', {},
        node('strong', { text: 'Podgląd playbooka Ansible' }),
        node('small', { text: 'Widok tylko do odczytu. Pokazywane są zatwierdzone pliki YAML używane przez backend.' })),
      button('Ukryj podgląd', () => { panel.hidden = true; }, 'ghost')),
    body);
  let loadedId = null;

  const roleLabel = role => ({
    main: 'Główny playbook',
    wait: 'Oczekiwanie',
    validate: 'Walidacja',
  }[role] || role || 'Playbook');

  const render = source => {
    const files = source?.files || [];
    if (!files.length) {
      body.replaceChildren(node('div', { class: 'muted', text: 'Brak plików YAML do wyświetlenia.' }));
      return;
    }

    const meta = node('div', { class: 'ansible-playbook-preview-meta' },
      node('span', { class: 'badge info', text: source.name || source.id }),
      node('span', { class: 'badge', text: 'v' + source.version }),
      node('span', { class: 'badge', text: String(source.transport || '').toUpperCase() }));
    const tabs = node('div', { class: 'ansible-playbook-tabs', role: 'tablist', 'aria-label': 'Pliki playbooka Ansible' });
    const role = node('div', { class: 'ansible-playbook-role muted' });
    const code = node('pre', { class: 'ansible-playbook-code mono', tabindex: '0' });

    const selectFile = (file, tab) => {
      tabs.querySelectorAll('button').forEach(value => {
        value.classList.toggle('active', value === tab);
        value.setAttribute('aria-selected', String(value === tab));
      });
      role.textContent = roleLabel(file.role);
      code.textContent = file.content || '';
      code.setAttribute('aria-label', 'Kod playbooka ' + file.name);
    };

    files.forEach((file, index) => {
      const tab = node('button', {
        type: 'button',
        class: 'ansible-playbook-tab' + (index === 0 ? ' active' : ''),
        role: 'tab',
        'aria-selected': String(index === 0),
        text: file.name,
      });
      tab.addEventListener('click', () => selectFile(file, tab));
      tabs.append(tab);
    });

    body.replaceChildren(meta, tabs, role, code);
    selectFile(files[0], tabs.querySelector('button'));
  };

  const load = async () => {
    const playbookId = playbookSelect.value;
    if (!playbookId) throw new Error('Wybierz playbook Ansible.');
    panel.hidden = false;
    if (loadedId === playbookId && body.querySelector('.ansible-playbook-code')) return;
    loadedId = playbookId;
    body.replaceChildren(node('div', { class: 'loading ansible-playbook-preview-loading' }, node('div', { class: 'spinner' })));
    try {
      const source = await api('/ansible/playbooks/' + encodeURIComponent(playbookId) + '/source');
      render(source);
    } catch (error) {
      loadedId = null;
      body.replaceChildren(node('div', { class: 'form-error', text: error.message }));
    }
  };

  const actions = node('div', { class: 'ansible-playbook-preview-actions wide' },
    button('Podgląd playbooka', () => load().catch(error => toast(error.message, 'error')), 'ghost'));

  playbookSelect.addEventListener('change', () => {
    loadedId = null;
    if (!panel.hidden) load().catch(error => toast(error.message, 'error'));
  });

  return { actions, panel };
}


async function createDeployment() {
  try {
    const [providerResult, credentialResult, templateResult, playbookResult] = await Promise.all([
      api('/providers?limit=200'),
      api('/credentials?limit=200'),
      api('/templates'),
      allowed('ansible.execute') && allowed('ansible.read') ? api('/ansible/playbooks') : Promise.resolve({ items: [] }),
    ]);
    const providers = providerResult.items;
    const credentials = credentialResult.items;
    const templates = templateResult.items.filter(item => item.enabled !== false);
    const playbooks = playbookResult.items.filter(item => item.enabled !== false);
    if (!providers.length) throw new Error('Najpierw dodaj provider infrastruktury.');
    if (!credentials.length) throw new Error('Najpierw dodaj credential infrastruktury.');
    if (!templates.length) throw new Error('Katalog nie zawiera żadnego szablonu wdrożenia.');

    const templateField = selectField('Szablon', 'template', templates.map(item => ({
      value: item.id, label: `${item.name} · v${item.version} · ${CREDENTIAL_TYPE_CONFIG[item.provider]?.label || item.provider}`,
    })), templates[0].id, { required: true });

    const terraformPreviewBody = node('div', { class: 'terraform-template-preview-body' },
      node('div', { class: 'muted', text: 'Kliknij „Podgląd Terraform”, aby wczytać pliki HCL wybranego szablonu.' }));
    const terraformPreview = node('section', { class: 'terraform-template-preview wide', hidden: true },
      node('div', { class: 'terraform-template-preview-header' },
        node('div', {},
          node('strong', { text: 'Podgląd szablonu Terraform / OpenTofu' }),
          node('small', { text: 'Widok tylko do odczytu. Pokazywany jest dokładny HCL używany przez backend.' })),
        button('Ukryj podgląd', () => { terraformPreview.hidden = true; }, 'ghost')),
      terraformPreviewBody);
    let terraformPreviewTemplateId = null;

    const renderTerraformPreview = source => {
      const files = source?.files || [];
      if (!files.length) {
        terraformPreviewBody.replaceChildren(node('div', { class: 'muted', text: 'Szablon nie zawiera plików .tf.' }));
        return;
      }
      const tabs = node('div', { class: 'terraform-template-tabs', role: 'tablist', 'aria-label': 'Pliki Terraform' });
      const code = node('pre', { class: 'terraform-template-code mono', tabindex: '0' });
      const meta = node('div', { class: 'terraform-template-preview-meta' },
        node('span', { class: 'badge info', text: source.name || source.id }),
        node('span', { class: 'badge', text: 'v' + source.version }),
        node('span', { class: 'badge', text: CREDENTIAL_TYPE_CONFIG[source.provider]?.label || source.provider }));

      const selectFile = (file, tab) => {
        tabs.querySelectorAll('button').forEach(value => {
          value.classList.toggle('active', value === tab);
          value.setAttribute('aria-selected', String(value === tab));
        });
        code.textContent = file.content || '';
        code.setAttribute('aria-label', 'Kod pliku ' + file.name);
      };

      files.forEach((file, index) => {
        const tab = node('button', {
          type: 'button',
          class: 'terraform-template-tab' + (index === 0 ? ' active' : ''),
          role: 'tab',
          'aria-selected': String(index === 0),
          text: file.name,
        });
        tab.addEventListener('click', () => selectFile(file, tab));
        tabs.append(tab);
      });

      terraformPreviewBody.replaceChildren(meta, tabs, code);
      selectFile(files[0], tabs.querySelector('button'));
    };

    const loadTerraformPreview = async () => {
      const templateId = templateField.querySelector('select').value;
      if (!templateId) throw new Error('Wybierz szablon Terraform.');
      terraformPreview.hidden = false;
      if (terraformPreviewTemplateId === templateId && terraformPreviewBody.querySelector('.terraform-template-code')) return;
      terraformPreviewTemplateId = templateId;
      terraformPreviewBody.replaceChildren(node('div', { class: 'loading terraform-template-preview-loading' }, node('div', { class: 'spinner' })));
      try {
        renderTerraformPreview(await api('/templates/' + encodeURIComponent(templateId) + '/source'));
      } catch (error) {
        terraformPreviewTemplateId = null;
        terraformPreviewBody.replaceChildren(node('div', { class: 'form-error', text: error.message }));
      }
    };

    const terraformPreviewButton = button('Podgląd Terraform', () => {
      loadTerraformPreview().catch(error => toast(error.message, 'error'));
    }, 'ghost');

    const providerField = selectField('Platforma', 'provider_id', [], '', { required: true });
    const credentialField = selectField('Dane dostępowe', 'credentials_id', [], '', { required: true });
    const variableFields = node('div', { class: 'form-grid wide template-variable-grid' });
    const ansibleFields = node('div', { class: 'form-grid wide ansible-fields' });
    const ansibleToggle = checkboxField('Po utworzeniu skonfiguruj system przez Ansible', 'ansible_enabled', false);
    const ansibleSection = formSection('Konfiguracja po wdrożeniu', 'Opcjonalny, zatwierdzony playbook uruchamiany po uzyskaniu adresu VM.', ansibleToggle, ansibleFields);

    const fields = node('div', { class: 'form-grid' },
      formSection('Podstawowe informacje', 'Wybierz szablon i miejsce wdrożenia.',
        node('div', { class: 'form-grid' },
          field('Nazwa wdrożenia', 'deployment_name', { required: true, placeholder: 'np. web-prod-01' }),
          templateField,
          node('div', { class: 'terraform-template-preview-actions wide' }, terraformPreviewButton),
          terraformPreview,
          providerField,
          credentialField,
          selectField('Silnik IaC', 'executor', [{ value: 'terraform', label: 'Terraform' }, { value: 'opentofu', label: 'OpenTofu' }], 'terraform'))),
      formSection('Konfiguracja zasobu', 'Pola są generowane automatycznie ze schematu wybranego szablonu.', variableFields),
      ansibleSection);

    const templateSelect = templateField.querySelector('select');
    const providerSelect = providerField.querySelector('select');
    const credentialSelect = credentialField.querySelector('select');

    const currentTemplate = () => templates.find(item => item.id === templateSelect.value);
    const refill = (select, rows, placeholder) => {
      const previous = select.value;
      select.replaceChildren(node('option', { value: '', text: placeholder }));
      rows.forEach(row => select.append(node('option', { value: row.id, text: row.label, selected: String(row.id) === String(previous) })));
      if (!select.value && rows.length === 1) select.value = String(rows[0].id);
    };

    const refreshCredentials = () => {
      const provider = providers.find(item => String(item.id) === String(providerSelect.value));
      const matching = provider
        ? credentials.filter(item => Number(item.id) === Number(provider.credentials_id)).map(item => ({ id: item.id, label: item.name }))
        : [];
      refill(credentialSelect, matching, matching.length ? 'Dane dostępowe platformy' : 'Wybierz provider');
    };

    const renderAnsible = () => {
      const template = currentTemplate();
      const supported = template?.provider === 'proxmox' && playbooks.length > 0;
      ansibleSection.hidden = !supported;
      if (!supported) {
        ansibleToggle.querySelector('input').checked = false;
        ansibleFields.replaceChildren();
        return;
      }
      const enabled = ansibleToggle.querySelector('input').checked;
      const playbookField = selectField('Playbook', 'ansible_playbook', playbooks.map(playbook => ({
        value: playbook.id,
        label: `${playbook.name} · v${playbook.version}`,
      })), playbooks[0]?.id || '', { required: enabled });
      const playbookSelect = playbookField.querySelector('select');
      const playbookPreview = createAnsiblePlaybookPreview(playbookSelect);
      const credentialField = selectField('Systemowe dane dostępowe', 'ansible_credentials_id', [], '', { required: enabled });
      const variablesContainer = node('div', { class: 'form-grid wide' });
      ansibleFields.replaceChildren(playbookField, playbookPreview.actions, playbookPreview.panel, credentialField, variablesContainer);
      ansibleFields.querySelectorAll('input,select,textarea').forEach(control => { control.disabled = !enabled; });

      const refreshPlaybook = () => {
        const playbook = playbooks.find(value => value.id === playbookSelect.value) || playbooks[0];
        const matchingCredentials = credentials.filter(value => value.type === playbook?.transport);
        const select = credentialField.querySelector('select');
        refill(select, matchingCredentials.map(value => ({ id: value.id, label: `${value.name} · ${credentialTypeLabel(value.type)}` })),
          matchingCredentials.length ? 'Wybierz systemowe dane dostępowe' : `Brak danych dostępowych typu ${playbook?.transport || ''}`);
        variablesContainer.replaceChildren();
        (playbook?.variables || []).forEach(name => variablesContainer.append(
          field(FIELD_LABELS[name] || name.replaceAll('_', ' '), `ansible_var_${name}`, {
            help: 'Opcjonalna zmienna zatwierdzonego playbooka.',
          })));
      };
      playbookSelect.addEventListener('change', refreshPlaybook);
      refreshPlaybook();
    };

    const refreshTemplate = async () => {
      const template = currentTemplate();
      if (!template) return;
      const matchingProviders = providers.filter(item => item.type === template.provider).map(item => ({ id: item.id, label: item.name }));
      refill(providerSelect, matchingProviders, matchingProviders.length ? 'Wybierz provider' : 'Brak połączenia z tą platformą');
      refreshCredentials();
      renderTemplateVariables(variableFields, template);
      await enhanceProxmoxDeploymentVariables(variableFields, template, providerSelect);
      renderAnsible();
    };

    templateSelect.addEventListener('change', () => {
      terraformPreviewTemplateId = null;
      if (!terraformPreview.hidden) {
        loadTerraformPreview().catch(error => toast(error.message, 'error'));
      }
      refreshTemplate().catch(error => toast(error.message, 'error'));
    });
    providerSelect.addEventListener('change', () => {
      refreshCredentials();
      enhanceProxmoxDeploymentVariables(variableFields, currentTemplate(), providerSelect)
        .catch(error => toast(error.message, 'error'));
    });
    ansibleToggle.querySelector('input').addEventListener('change', renderAnsible);
    await refreshTemplate();

    openModal({
      title: 'Ręczne wdrożenie (zaawansowane)',
      eyebrow: 'Terraform / OpenTofu',
      body: fields,
      submitLabel: 'Utwórz i uruchom',
      wide: true,
      onSubmit: async (_data, form) => {
        const template = currentTemplate();
        if (!providerSelect.value) throw new Error('Brak providera zgodnego z wybranym szablonem.');
        if (!credentialSelect.value) throw new Error('Brak credentiala zgodnego z wybraną platformą.');
        if (template?.provider === 'proxmox' && !form.elements.template_id?.value) {
          throw new Error('Wybierz bazowy szablon Proxmox z kreatora.');
        }
        const body = {
          name: form.elements.deployment_name.value,
          provider_id: Number(providerSelect.value),
          template: template.id,
          credentials_id: Number(credentialSelect.value),
          executor: form.elements.executor.value,
          variables: readTemplateVariables(form, template),
        };
        if (form.elements.ansible_enabled?.checked) {
          const playbook = playbooks.find(value => value.id === form.elements.ansible_playbook?.value);
          if (!playbook) throw new Error('Wybierz playbook Ansible.');
          if (!form.elements.ansible_credentials_id?.value) throw new Error('Wybierz systemowe dane dostępowe dla Ansible.');
          const variables = {};
          (playbook.variables || []).forEach(name => {
            const value = form.elements[`ansible_var_${name}`]?.value?.trim();
            if (value) variables[name] = value;
          });
          body.ansible = {
            playbook: playbook.id,
            credentials_id: Number(form.elements.ansible_credentials_id.value),
            variables,
          };
        }
        await api('/deployments', { method: 'POST', idempotent: true, body });
        toast('Wdrożenie zapisano w lokalnej bazie i dodano do kolejki. Jeśli Proxmox jest niedostępny, provisioning uruchomi się automatycznie po odzyskaniu połączenia.');
        navigate('deployments');
      },
    });
  } catch (error) { toast(error.message, 'error'); }
}

async function jobsView() {
  jobLogPollNonce += 1;
  if (jobsPollTimer) {
    clearTimeout(jobsPollTimer);
    jobsPollTimer = null;
  }
  const preservedScrollY = window.scrollY;
  const jobs = (await api('/jobs?limit=200')).items;
  const actions = [];
  if (allowed('jobs.execute') && allowed('ansible.execute') && allowed('ansible.read') && allowed('credentials.read')) {
    actions.push(button('Uruchom Ansible', runStandaloneAnsible, 'primary'));
  }
  dom.content.replaceChildren(heading('Historia i bieżący stan wykonania. Logi są redagowane po stronie backendu.', actions),
    table([
      { label: 'ID', class: 'mono', value: item => short(item.id, 18) }, { label: 'Operacja', value: item => operationLabel(item.operation) },
      { label: 'Status', value: item => badge(item.provider_waiting ? 'Oczekuje na Proxmox' : statusLabel(item.status), item.provider_waiting ? 'warning' : statusKind(item.status)) },
      { label: 'Etap', value: item => window.JobStageUI.cell(item) },
      { label: 'Synchronizacja', value: item => item.provider_waiting
        ? node('span', { class: 'muted', text: 'Próba ' + item.provider_retry_attempts + ' · kolejna ' + formatDate(item.provider_next_retry_at) })
        : '—' },
      { label: 'Źródło', value: item => item.source }, { label: 'Wdrożenie', class: 'mono', value: item => short(item.deployment_id, 14) },
      { label: 'Utworzono', value: item => formatDate(item.created_at) }, { label: 'Błąd', value: item => node('span', { class: item.error ? 'form-error' : 'muted', text: item.error || '—' }) },
    ], jobs, item => {
      const actions = [button('Logi', () => navigate('/jobs/' + encodeURIComponent(item.id)))];
      if (allowed('jobs.cancel') && ['queued', 'running'].includes(item.status) && !item.cancel_requested) {
        actions.push(button('Anuluj', () => confirmAction(
          'Anuluj zadanie',
          `Zadanie ${short(item.id)} zostanie zatrzymane. Trwający Terraform/Ansible otrzyma przerwanie.`,
          async () => {
            const result = await api(`/jobs/${item.id}/cancel`, { method: 'POST' });
            toast(result.status === 'cancelled' ? 'Zadanie anulowane.' : 'Anulowanie rozpoczęte.');
            navigate('jobs');
          },
        ), 'danger'));
      }
      if (item.status === 'cancelling' || item.cancel_requested && item.status === 'running') {
        actions.push(badge('Anulowanie…', 'warning'));
      }
      if (allowed('jobs.execute') && ['failed', 'cancelled'].includes(item.status)) actions.push(button('Ponów', async () => {
        await api(`/jobs/${item.id}/retry`, { method: 'POST', idempotent: true });
        toast('Utworzono ponowienie zadania.');
        navigate('jobs');
      }));
      return actions;
    }));

  if (preservedScrollY > 0) {
    requestAnimationFrame(() => window.scrollTo({ top: preservedScrollY, behavior: 'auto' }));
  }

  if (jobs.some(item => ['waiting_approval', 'queued', 'running', 'cancelling'].includes(item.status) || item.provider_waiting)) {
    jobsPollTimer = window.setTimeout(() => {
      if (state.view === 'jobs') jobsView().catch(error => toast(error.message, 'error'));
    }, 2000);
  }
}

async function runStandaloneAnsible(initialPlaybookId = null) {
  try {
    const canSelectManagedVms = allowed('inventory.read');
    const [playbookResult, credentialResult, vmResult, resourceResult] = await Promise.all([
      api('/ansible/playbooks'),
      api('/credentials?limit=200'),
      canSelectManagedVms ? api('/inventory/vms?lifecycle_status=active&limit=200') : Promise.resolve({ items: [] }),
      canSelectManagedVms ? api('/inventory/resources?lifecycle_status=active&limit=200') : Promise.resolve({ items: [] }),
    ]);
    const playbooks = playbookResult.items.filter(item => item.enabled !== false);
    const credentials = credentialResult.items;
    const vmResourcesByDeployment = new Map(
      (resourceResult.items || [])
        .filter(item => item.resource_type === 'vm' && item.deployment_id)
        .map(item => [item.deployment_id, item])
    );
    const managedVmTargets = (vmResult.items || [])
      .map(vm => ({
        ...vm,
        target_address: vm.deployment_id ? (vmResourcesByDeployment.get(vm.deployment_id)?.primary_ip || '') : '',
      }))
      .sort((a, b) => String(a.name || a.vm_id).localeCompare(String(b.name || b.vm_id), 'pl'));
    if (!playbooks.length) throw new Error('Katalog nie zawiera aktywnych playbooków Ansible.');

    const categories = [...new Set(playbooks.map(playbook => playbook.category || 'Inne'))].sort((a, b) => a.localeCompare(b));
    const categoryField = selectField('Kategoria', 'playbook_category', [
      { value: '', label: 'Wszystkie kategorie' },
      ...categories.map(value => ({ value, label: value })),
    ], '');
    const playbookField = selectField('Playbook', 'playbook', [], '', { required: true });
    const playbookInfo = node('div', { class: 'ansible-playbook-info wide' });
    const playbookSelect = playbookField.querySelector('select');
    const playbookPreview = createAnsiblePlaybookPreview(playbookSelect);
    const credentialField = selectField('Dane dostępowe hosta', 'credentials_id', [], '', { required: true });
    const hostsField = field('Adresy IP hostów', 'hosts', {
      tag: 'textarea',
      required: true,
      wide: true,
      placeholder: '10.0.0.10\n10.0.0.11',
      help: 'Wybierz VM powyżej albo wpisz adres IPv4/IPv6 ręcznie. Jeden adres w wierszu lub adresy oddzielone przecinkami.',
    });
    const hostsInput = hostsField.querySelector('textarea');
    let selectedVmAddresses = new Set();

    const vmTargetSelect = node('select', {
      name: 'managed_vm_targets',
      multiple: true,
      size: Math.min(7, Math.max(3, managedVmTargets.length || 3)),
      disabled: !managedVmTargets.length,
    });
    if (!managedVmTargets.length) {
      vmTargetSelect.append(node('option', {
        value: '',
        text: canSelectManagedVms ? 'Brak aktywnych VM w Twoim zakresie dostępu' : 'Brak uprawnienia inventory.read',
        disabled: true,
      }));
    } else {
      managedVmTargets.forEach(vm => {
        const address = vm.target_address;
        vmTargetSelect.append(node('option', {
          value: vm.id,
          text: [
            vm.name || ('VM ' + vm.vm_id),
            address || 'brak znanego IP',
            (vm.node || '—') + ' / VMID ' + vm.vm_id,
          ].join(' · '),
          disabled: !address,
          'data-address': address || null,
        }));
      });
    }

    const vmTargetField = node('label', { class: 'wide' },
      formFieldLabel('VM z Moich zasobów', false),
      vmTargetSelect,
      node('span', {
        class: 'field-help',
        text: canSelectManagedVms
          ? 'Możesz zaznaczyć wiele VM. Lista respektuje Twój zakres RBAC. VM bez znanego adresu IP są wyszarzone.'
          : 'Wybór VM wymaga uprawnienia inventory.read. Adres IP nadal możesz podać ręcznie.',
      }));

    vmTargetSelect.addEventListener('change', () => {
      const manualHosts = splitValues(hostsInput.value).filter(address => !selectedVmAddresses.has(address));
      selectedVmAddresses = new Set(
        Array.from(vmTargetSelect.selectedOptions)
          .map(option => option.dataset.address)
          .filter(Boolean)
      );
      hostsInput.value = [...new Set([...selectedVmAddresses, ...manualHosts])].join('\n');
    });

    const variables = node('div', { class: 'form-grid wide' });
    const fields = node('div', { class: 'form-grid' },
      formSection('Playbook', 'Wybierz zatwierdzoną operację, którą backend uruchomi na zdalnej VM.',
        node('div', { class: 'form-grid' }, categoryField, playbookField, playbookInfo, playbookPreview.actions, playbookPreview.panel)),
      formSection('Cel', 'Wybierz dostępne VM lub podaj adresy IP hostów, na których ma zostać uruchomiony zatwierdzony playbook.',
        node('div', { class: 'form-grid' },
          credentialField,
          vmTargetField,
          hostsField)),
      formSection('Zmienne playbooka', 'Puste pola opcjonalne nie są wysyłane.', variables));

    const categorySelect = categoryField.querySelector('select');

    if (initialPlaybookId) {
      const initial = playbooks.find(value => value.id === initialPlaybookId);
      if (initial) categorySelect.value = initial.category || 'Inne';
    }

    const fillPlaybooks = () => {
      const selected = playbookSelect.value || initialPlaybookId || '';
      const category = categorySelect.value;
      const matching = playbooks.filter(value => !category || (value.category || 'Inne') === category);
      playbookSelect.replaceChildren();
      matching.forEach(playbook => playbookSelect.append(node('option', {
        value: playbook.id,
        text: `${playbook.name} · ${playbook.transport.toUpperCase()} · v${playbook.version}`,
        selected: playbook.id === selected,
      })));
      if (!playbookSelect.value && matching.length) playbookSelect.value = matching[0].id;
    };

    const refresh = () => {
      const playbook = playbooks.find(value => value.id === playbookSelect.value) || playbooks[0];
      playbookInfo.replaceChildren(
        node('div', { class: 'ansible-playbook-info-copy' },
          node('div', { class: 'ansible-playbook-info-title' },
            node('strong', { text: playbook.name }),
            badge(playbook.category || 'Inne', 'info'),
            badge(playbook.transport.toUpperCase())),
          node('p', { text: playbook.description || 'Zatwierdzony playbook Ansible.' }),
          node('small', { class: 'mono muted', text: playbook.id })));
      const matching = credentials.filter(value => value.type === playbook.transport);
      const select = credentialField.querySelector('select');
      select.replaceChildren(node('option', { value: '', text: matching.length ? 'Wybierz dane dostępowe' : `Brak danych typu ${playbook.transport}` }));
      matching.forEach(value => select.append(node('option', { value: value.id, text: value.name })));
      if (matching.length === 1) select.value = String(matching[0].id);
      variables.replaceChildren();
      const defaults = {
        service_state: 'restarted',
        service_enabled: 'true',
        user_shell: '/bin/bash',
        service_start_mode: 'auto',
      };
      const choices = {
        service_state: ['started', 'stopped', 'restarted', 'reloaded'],
        service_enabled: ['true', 'false'],
        service_start_mode: ['auto', 'delayed', 'manual', 'disabled'],
      };
      (playbook.variables || []).forEach(name => {
        const required = (playbook.required_variables || []).includes(name);
        const label = FIELD_LABELS[name] || name.replaceAll('_', ' ');
        const inputName = `ansible_job_var_${name}`;
        if (choices[name]) {
          variables.append(selectField(label, inputName,
            choices[name].map(value => ({ value, label: value })),
            defaults[name] || choices[name][0],
            { required }));
          return;
        }
        variables.append(field(label, inputName, {
          required,
          value: defaults[name] || '',
          help: required ? 'Pole wymagane przez wybrany playbook.' : 'Opcjonalna zmienna zatwierdzonego playbooka.',
        }));
      });
    };
    categorySelect.addEventListener('change', () => {
      fillPlaybooks();
      refresh();
    });
    playbookSelect.addEventListener('change', refresh);
    fillPlaybooks();
    refresh();

    openModal({
      title: 'Uruchom Ansible',
      eyebrow: 'Zadanie jednorazowe',
      body: fields,
      submitLabel: 'Dodaj do kolejki',
      wide: true,
      onSubmit: async (_data, form) => {
        const playbook = playbooks.find(value => value.id === form.elements.playbook.value);
        if (!playbook) throw new Error('Wybierz playbook.');
        if (!form.elements.credentials_id.value) throw new Error('Wybierz dane dostępowe do hosta.');
        const hosts = splitValues(form.elements.hosts.value);
        if (!hosts.length) throw new Error('Podaj co najmniej jeden adres IP hosta.');
        const jobVariables = {};
        (playbook.variables || []).forEach(name => {
          const value = form.elements[`ansible_job_var_${name}`]?.value?.trim();
          if (value) jobVariables[name] = value;
        });
        await api('/jobs', {
          method: 'POST',
          idempotent: true,
          body: {
            operation: 'ansible.execute',
            ansible: {
              playbook: playbook.id,
              credentials_id: Number(form.elements.credentials_id.value),
              inventory: { hosts },
              variables: jobVariables,
            },
          },
        });
        toast('Zadanie Ansible zostało dodane do kolejki.');
        navigate('jobs');
      },
    });
  } catch (error) { toast(error.message, 'error'); }
}

function jobLogIdFromRoute() {
  const route = String(location.hash.slice(1) || '').split('/page/')[0].replace(/\/+$/, '');
  const match = route.match(/^\/?jobs\/([^/]+)$/);
  if (!match) throw new Error('Brak ID zadania w adresie URL.');
  try {
    return decodeURIComponent(match[1]);
  } catch {
    throw new Error('ID zadania w adresie URL jest nieprawidłowe.');
  }
}

async function jobLogView() {
  if (jobsPollTimer) {
    clearTimeout(jobsPollTimer);
    jobsPollTimer = null;
  }

  const jobId = jobLogIdFromRoute();
  const nonce = ++jobLogPollNonce;
  const statusHost = node('div', { class: 'job-log-status' });
  const metaHost = node('dl', { class: 'job-log-page-meta' });
  const inventoryState = node('div', { class: 'job-log-inventory-state muted' });
  const actionHost = node('div', { class: 'action-group job-log-page-actions' });
  const logOutput = node('div', { class: 'log-output mono job-live-log', text: 'Oczekiwanie na logi…' });

  const copyLink = button('Kopiuj link', async () => {
    try {
      await copyText(location.href);
      toast('Skopiowano bezpośredni link do logów.');
    } catch (error) {
      toast(error.message, 'error');
    }
  }, 'ghost');

  dom.content.replaceChildren(
    heading('Logi zadania ' + short(jobId, 18), [
      button('← Zadania', () => navigate('jobs'), 'ghost'),
      copyLink,
    ]),
    node('section', { class: 'panel job-log-page' },
      metaHost,
      statusHost,
      inventoryState,
      actionHost,
      logOutput)
  );

  let firstRender = true;

  const renderLogs = rows => {
    const previousHeight = logOutput.scrollHeight;
    const previousTop = logOutput.scrollTop;
    const atBottom = firstRender || previousHeight - previousTop - logOutput.clientHeight <= 24;
    const text = rows.map(item => `[${formatDate(item.timestamp)}] ${item.message}`).join('\n') || 'Brak logów.';
    logOutput.textContent = text;
    if (atBottom) logOutput.scrollTop = logOutput.scrollHeight;
    else logOutput.scrollTop = Math.min(previousTop, Math.max(0, logOutput.scrollHeight - logOutput.clientHeight));
    firstRender = false;
  };

  const terminal = value => ['successful', 'failed', 'cancelled'].includes(value);

  const poll = async () => {
    if (nonce !== jobLogPollNonce || state.view !== 'job-log' || !logOutput.isConnected) return;
    try {
      const [current, logs] = await Promise.all([
        api('/jobs/' + encodeURIComponent(jobId)),
        api('/jobs/' + encodeURIComponent(jobId) + '/logs?limit=200'),
      ]);

      metaHost.replaceChildren(
        node('dt', { text: 'Operacja' }), node('dd', { text: operationLabel(current.operation) }),
        node('dt', { text: 'ID zadania' }), node('dd', { class: 'mono', text: current.id }),
        node('dt', { text: 'Wdrożenie' }), node('dd', { class: 'mono', text: current.deployment_id || '—' }),
        node('dt', { text: 'Źródło' }), node('dd', { text: current.source || '—' }),
        node('dt', { text: 'Utworzono' }), node('dd', { text: formatDate(current.created_at) })
      );

      statusHost.replaceChildren(
        node('div', { class: 'action-group' },
          badge(statusLabel(current.status), statusKind(current.status)),
          current.provider_waiting ? badge('Oczekuje na Proxmox', 'warning') : null,
          node('span', {
            class: 'muted',
            text: current.error || (terminal(current.status) ? 'Zadanie zakończone.' : 'Log jest odświeżany automatycznie.'),
          }))
      );
      renderLogs(logs.items || []);

      const actions = [];

      if (allowed('jobs.cancel') && ['queued', 'running'].includes(current.status) && !current.cancel_requested) {
        actions.push(button('Anuluj zadanie', () => confirmAction(
          'Anuluj zadanie',
          'Trwający Terraform/Ansible zostanie przerwany. Stan zasobu zostanie zachowany do weryfikacji.',
          async () => {
            const result = await api('/jobs/' + current.id + '/cancel', { method: 'POST' });
            toast(result.status === 'cancelled' ? 'Zadanie anulowane.' : 'Anulowanie rozpoczęte.');
            return false;
          },
        ), 'danger'));
      }

      if (current.operation === 'terraform.apply' && current.deployment_id && current.status === 'successful') {
        let managedVm = null;
        if (allowed('inventory.read')) {
          const inventory = await api('/inventory/vms?limit=200');
          managedVm = (inventory.items || []).find(item => item.deployment_id === current.deployment_id) || null;
        }
        if (managedVm) {
          inventoryState.className = 'job-log-inventory-state form-error success';
          inventoryState.textContent = `VM zsynchronizowana z inventory: ${managedVm.node} / VMID ${managedVm.vm_id}.`;
          if (allowed('vms.read') && hasCommand('inventory.openVm')) {
            actions.unshift(button('Otwórz VM', async () => {
              jobLogPollNonce += 1;
              await runCommand('inventory.openVm', managedVm, 'overview', 'my-resources');
            }, 'primary'));
          }
        } else if (allowed('inventory.read')) {
          inventoryState.className = 'job-log-inventory-state form-error';
          inventoryState.textContent = 'Terraform zakończył się sukcesem, ale VM nie jest jeszcze widoczna w inventory. Odświeżenie zostanie wykonane po ponownym zastosowaniu lub synchronizacji backendu.';
        }
        actions.unshift(button('Moje zasoby', () => {
          jobLogPollNonce += 1;
          navigate('my-resources');
        }, managedVm ? 'ghost' : 'primary'));
      } else {
        inventoryState.className = 'job-log-inventory-state muted';
        inventoryState.textContent = current.operation === 'terraform.apply' && !terminal(current.status)
          ? (current.status === 'cancelling' || current.cancel_requested
            ? 'Anulowanie w toku. Backend kończy proces Terraform/Ansible i porządkuje status wdrożenia.'
            : 'Po zakończeniu Terraform backend automatycznie doda VM do „Moje zasoby”.')
          : '';
      }

      actionHost.replaceChildren(...actions);

      if (!terminal(current.status)) {
        window.setTimeout(poll, 1500);
      }
    } catch (error) {
      statusHost.replaceChildren(node('p', { class: 'form-error', text: error.message }));
      if (nonce === jobLogPollNonce && state.view === 'job-log' && logOutput.isConnected) {
        window.setTimeout(poll, 3000);
      }
    }
  };

  await poll();
}

registerCommand('deployments.create', createDeployment);
registerCommand('deployments.open', showDeploymentDetails);
registerCommand('ansible.run', runStandaloneAnsible);
registerView({ id: 'deployments', label: 'Produkty', iconName: 'box', permission: 'deployments.read', order: 110 }, deploymentsView);
registerView({ id: 'my-resources', label: 'Moje zasoby', iconName: 'server', permission: null, order: 111 }, myResourcesView);
registerView({ id: 'jobs', label: 'Zadania', icon: 'J', permission: 'jobs.read', order: 120 }, jobsView);
registerView({
  id: 'job-log',
  label: 'Logi zadania',
  permission: 'jobs.read',
  order: 121,
  navigation: false,
  navigationParent: 'jobs',
}, jobLogView);
})();
