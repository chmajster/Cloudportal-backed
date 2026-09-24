'use strict';

(() => {
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

function emptyVmState(title, description) {
  return node('div', { class: 'my-resources-empty' },
    node('span', { class: 'my-resources-empty-icon', 'aria-hidden': 'true' }, appIcon('monitor')),
    node('strong', { text: title }),
    node('span', { class: 'muted', text: description }));
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
  if (deployment && hasCommand('deployments.open')) {
    actions.push(button('Wdrożenie', () => runCommand('deployments.open', deployment), 'ghost'));
  }

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

function createVmBrowser({
  vms,
  providerNames,
  deploymentById,
  userNames,
  projectNames,
  tenantNames,
  onRefresh,
}) {
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
      results.replaceChildren(emptyVmState(
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
      () => typeof onRefresh === 'function' ? onRefresh() : undefined
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

registerExtension('deployments-vm-browser', () => {
  window.MyResourcesVmBrowser = Object.freeze({
    create: createVmBrowser,
  });
});
})();
