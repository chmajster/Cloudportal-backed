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

async function composeProvisioningVms({ deployments = [], vms = [], jobs = [] }) {
  const rawVms = [...vms];
  const allJobs = [...jobs];
  const deploymentById = new Map(deployments.map(item => [item.id, item]));
  const jobById = new Map(allJobs.map(item => [item.id, item]));

  if (allowed('jobs.read')) {
    const missingActiveJobIds = [...new Set(
      deployments
        .map(item => item.active_job_id)
        .filter(id => id && !jobById.has(id))
    )];
    const activeJobs = await Promise.all(
      missingActiveJobIds.map(id => api('/jobs/' + encodeURIComponent(id)).catch(() => null))
    );
    activeJobs.filter(Boolean).forEach(job => {
      allJobs.push(job);
      jobById.set(job.id, job);
    });
  }

  const latestLifecycleJobByDeployment = new Map();
  allJobs
    .filter(item => item.deployment_id && ['terraform.apply', 'terraform.destroy'].includes(item.operation))
    .sort((left, right) => (Date.parse(right.created_at || '') || 0) - (Date.parse(left.created_at || '') || 0))
    .forEach(item => {
      if (!latestLifecycleJobByDeployment.has(item.deployment_id)) {
        latestLifecycleJobByDeployment.set(item.deployment_id, item);
      }
    });

  const provisioningJobForDeployment = deployment => {
    if (!deployment) return null;
    const active = deployment.active_job_id ? jobById.get(deployment.active_job_id) : null;
    if (active && ['terraform.apply', 'terraform.destroy'].includes(active.operation)) return active;
    return latestLifecycleJobByDeployment.get(deployment.id) || null;
  };

  function provisionalBlueprintVm(deployment) {
    const variables = deployment.variables || {};
    const vmId = variables.vm_id ?? variables.vmid ?? variables.target_vmid ?? variables.new_vmid ?? null;
    return {
      id: 'provisioning:' + deployment.id,
      tenant_id: deployment.tenant_id,
      project_id: deployment.project_id,
      provider_id: deployment.provider_id,
      deployment_id: deployment.id,
      node: variables.node || variables.target_node || '',
      vm_id: vmId,
      name: deployment.name,
      management_mode: 'terraform',
      lifecycle_status: 'provisioning',
      created_by: deployment.created_by,
      created_at: deployment.created_at,
      updated_at: deployment.updated_at,
      destroyed_at: null,
      provisioning_placeholder: true,
      provisioning_job: provisioningJobForDeployment(deployment),
    };
  }

  const managedDeploymentIds = new Set(rawVms.filter(item => item.deployment_id).map(item => item.deployment_id));
  const result = rawVms.map(item => {
    const deployment = item.deployment_id ? deploymentById.get(item.deployment_id) : null;
    const provisioningJob = provisioningJobForDeployment(deployment);
    return provisioningJob && provisioningJob.status !== 'successful'
      ? { ...item, provisioning_job: provisioningJob }
      : item;
  });

  deployments
    .filter(item => item.provider === 'proxmox'
      && Boolean(item.workflow?.blueprint)
      && item.status !== 'destroyed'
      && !managedDeploymentIds.has(item.id))
    .forEach(item => result.push(provisionalBlueprintVm(item)));

  result.sort((left, right) => (Date.parse(right.created_at || '') || 0) - (Date.parse(left.created_at || '') || 0));
  return result;
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
    status: String(item.provisioning_job?.status || item.live?.status || item.lifecycle_status || 'unknown').toLowerCase(),
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
    item.provisioning_job?.operation, item.provisioning_job?.current_stage, item.provisioning_job?.error,
  ].map(value => String(value || '').toLocaleLowerCase('pl')).join(' ');
  return haystack.includes(query);
}

function emptyVmState(title, description) {
  return node('div', { class: 'my-resources-empty' },
    node('span', { class: 'my-resources-empty-icon', 'aria-hidden': 'true' }, appIcon('monitor')),
    node('strong', { text: title }),
    node('span', { class: 'muted', text: description }));
}

function managedVmCard(item, providerNames, deploymentById, metadata = {}, onSelectionChange = null, onRefresh = null) {
  const deployment = item.deployment_id ? deploymentById.get(item.deployment_id) : null;
  const createdAt = deployment?.created_at || item.created_at || '';
  const provisioningJob = item.provisioning_job || null;
  const destroyJob = provisioningJob?.operation === 'terraform.destroy';
  const provisioningVisible = Boolean(
    item.provisioning_placeholder
    || (provisioningJob && provisioningJob.status !== 'successful')
  );
  const lifecycleFailed = Boolean(
    provisioningJob && ['failed', 'cancelled'].includes(provisioningJob.status)
  );
  const provisioningFailed = lifecycleFailed && !destroyJob;
  const destroyFailed = lifecycleFailed && destroyJob;
  const destroyInProgress = Boolean(
    destroyJob && !['successful', 'failed', 'cancelled'].includes(provisioningJob.status)
  );
  const liveStatus = item.live?.status || item.lifecycle_status || 'unknown';
  const active = item.lifecycle_status === 'active'
    && !item.provisioning_placeholder
    && !destroyInProgress;
  const canOpen = allowed('vms.read') && active && hasCommand('inventory.openVm');
  const canConsole = allowed('vms.console') && active && hasCommand('inventory.consoleVm');
  const actions = [];

  if ((provisioningFailed || destroyFailed) && deployment && !deployment.active_job_id
      && deployment.status !== 'reconciliation_required') {
    if (provisioningFailed
        && allowed('jobs.execute') && allowed('terraform.execute')
        && allowed('deployments.create') && allowed('blueprints.execute')) {
      actions.push(button('Ponów', async () => {
        await api(`/jobs/${provisioningJob.id}/retry`, { method: 'POST', idempotent: true });
        toast('Provisioning został ponowiony.');
        if (typeof onRefresh === 'function') await onRefresh();
        else navigate('my-resources');
      }, 'primary'));
    }
    if (allowed('deployments.destroy') && allowed('jobs.execute') && allowed('terraform.execute')) {
      actions.push(button(destroyFailed ? 'Ponów usuwanie' : 'Usuń', () => confirmAction(
        destroyFailed ? 'Ponów usuwanie zasobów' : 'Usuń nieudany provisioning',
        destroyFailed
          ? 'Terraform ponownie spróbuje usunąć zasoby tego wdrożenia.'
          : 'Terraform usunie zasoby utworzone przed błędem. Po zakończeniu wpis zniknie z aktywnych VM.',
        async () => {
          await api(`/deployments/${deployment.id}/destroy`, { method: 'POST', body: {}, idempotent: true });
          toast(destroyFailed
            ? 'Utworzono ponowne zadanie usuwania zasobów.'
            : 'Utworzono zadanie usuwania nieudanego provisioningu.');
          if (typeof onRefresh === 'function') await onRefresh();
          else navigate('my-resources');
        },
      ), 'danger'));
    }
  }

  if (provisioningJob?.id && allowed('jobs.read')) {
    actions.push(button('Logi', () => navigate('/jobs/' + encodeURIComponent(provisioningJob.id)), 'ghost'));
  }

  if (canOpen) {
    actions.push(button('Zarządzaj VM', () => runCommand('inventory.openVm', item, 'overview', 'my-resources'), 'primary'));
  }
  if (canConsole) {
    actions.push(button('Konsola', () => runCommand('inventory.consoleVm', item), 'ghost'));
  }
  if (item.lifecycle_status === 'missing'
      && allowed('inventory.delete')
      && hasCommand('inventory.cleanupMissingVm')) {
    actions.push(button('Usuń pozostałe dane', () => runCommand(
      'inventory.cleanupMissingVm',
      item,
      'my-resources'
    ), 'danger'));
  }
  const canRecreate = !provisioningVisible
    && item.management_mode === 'terraform'
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

  const stage = provisioningVisible
    ? (provisioningJob && window.JobStageUI
        ? window.JobStageUI.cell(provisioningJob)
        : node('span', { text: statusLabel(provisioningJob?.status || deployment?.status || 'queued') }))
    : null;
  const activityTitle = destroyJob ? 'Usuwanie' : 'Provisioning';
  const statusText = provisioningVisible
    ? (lifecycleFailed ? activityTitle + ': błąd' : activityTitle)
    : statusLabel(liveStatus);
  const statusKindValue = provisioningVisible
    ? (lifecycleFailed ? 'danger' : 'warning')
    : statusKind(liveStatus);
  const vmIdLabel = item.vm_id === null || item.vm_id === undefined || item.vm_id === ''
    ? 'oczekuje'
    : item.vm_id;

  const card = node('article', {
    class: 'my-resource-card my-resource-vm-card',
  },
    node('div', { class: 'my-resource-card-head' },
      node('span', { class: 'my-resource-card-icon', 'aria-hidden': 'true' }, appIcon('server')),
      node('div', { class: 'my-resource-card-title' },
        node('strong', {
          text: item.name || ('VM ' + vmIdLabel),
          title: item.name || ('VM ' + vmIdLabel),
        }),
        node('small', {
          class: 'mono muted',
          text: (item.node || '—') + ' / VMID ' + vmIdLabel,
          title: (item.node || '—') + ' / VMID ' + vmIdLabel,
        })),
      badge(statusText, statusKindValue)),
    node('div', { class: 'my-resource-card-meta' },
      node('span', { text: metadata.provider || providerNames.get(Number(item.provider_id)) || ('Platforma #' + item.provider_id) }),
      node('span', { text: statusLabel(item.management_mode) }),
      metadata.apmid ? node('span', { text: 'APMID: ' + metadata.apmid }) : null,
      metadata.environment ? node('span', { text: 'ENV: ' + metadata.environment.toUpperCase() }) : null,
      metadata.owner && metadata.owner !== '—' ? node('span', { text: 'Właściciel: ' + metadata.owner }) : null,
      metadata.project && metadata.project !== '—' ? node('span', { text: 'Projekt: ' + metadata.project }) : null),
    node('div', { class: 'my-resource-card-created' },
      node('span', { class: 'my-resource-card-created-label', text: 'Data utworzenia' }),
      node('span', {
        class: 'my-resource-card-created-value',
        text: createdAt ? formatDate(createdAt) : '—',
      })),
    provisioningVisible ? node('div', { class: 'my-resource-provisioning-state' },
      node('div', { class: 'my-resource-provisioning-head' },
        node('strong', { text: activityTitle }),
        badge(statusLabel(provisioningJob?.status || deployment?.status || 'queued'),
          lifecycleFailed ? 'danger' : 'warning')),
      node('div', { class: 'my-resource-provisioning-stage' },
        node('span', { class: 'muted', text: 'Etap' }),
        stage),
      provisioningJob?.error
        ? node('div', { class: 'form-error my-resource-provisioning-error', text: provisioningJob.error })
        : null,
      deployment?.status === 'reconciliation_required'
        ? node('small', { class: 'muted', text: 'Wymagana rekonsyliacja Terraform przed ponowieniem lub usunięciem.' })
        : null)
      : null,
    actions.length
      ? node('div', { class: 'my-resource-card-actions' }, ...actions)
      : node('small', {
          class: 'muted',
          text: provisioningVisible
            ? (destroyJob ? 'Usuwanie zasobów trwa.' : 'Provisioning trwa.')
            : 'Brak uprawnień do sterowania tą VM.',
        }));

  if (!item.provisioning_placeholder) {
    window.vmBulkActions?.decorateCard(card, item, onSelectionChange);
  }
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
      onRefresh,
    )));
    const listHeader = myResourcesVmUi.view === 'list'
      ? node('div', { class: 'my-resources-vm-list-header', 'aria-hidden': 'true' },
        node('span', { text: 'Maszyna' }),
        node('span', { text: 'Informacje' }),
        node('span', { text: 'Data utworzenia' }),
        node('span', { class: 'my-resources-vm-list-header-actions', text: 'Akcje' }))
      : null;
    const bulkItems = filtered
      .map(entry => entry.item)
      .filter(item => !item.provisioning_placeholder && item.lifecycle_status === 'active');
    vmBulkControls = bulkItems.length ? (window.vmBulkActions?.toolbar(
      bulkItems,
      grid,
      () => typeof onRefresh === 'function' ? onRefresh() : undefined
    ) || null) : null;
    const renderedContent = listHeader ? [listHeader, grid] : [grid];
    if (vmBulkControls) results.replaceChildren(vmBulkControls.element, ...renderedContent);
    else results.replaceChildren(...renderedContent);
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
    composeProvisioning: composeProvisioningVms,
    create: createVmBrowser,
  });
});
})();
