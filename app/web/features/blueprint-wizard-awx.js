'use strict';

(() => {
  const parts = window.BlueprintWizardParts = window.BlueprintWizardParts || {};

  function enabled(state) {
    return Boolean(state.awxEnabled);
  }

  function toggleStep(state, checked) {
    state.awxEnabled = checked;
    if (checked && state.providerType === 'proxmox' && parts.cloudInit && !parts.cloudInit.enabled(state)) {
      parts.cloudInit.toggleStep(state, true);
    }
    if (!state.advancedWorkflow) return;

    const awxSteps = state.workflow.filter(step => step.type === 'register_awx');
    if (!checked) {
      const removed = new Set(awxSteps.map(step => step.id));
      state.workflow = state.workflow.filter(step => !removed.has(step.id)).map(step => ({
        ...step,
        depends_on: (step.depends_on || []).filter(id => !removed.has(id)),
      }));
      return;
    }
    if (awxSteps.length) return;

    let ipStep = state.workflow.find(step => step.type === 'wait_for_ip');
    if (!ipStep) {
      const apply = state.workflow.find(step => step.type === 'terraform_apply');
      const ids = new Set(state.workflow.map(step => step.id));
      let ipId = 'guest_ip';
      for (let index = 2; ids.has(ipId); index += 1) ipId = 'guest_ip_' + index;
      ipStep = {
        id: ipId,
        type: 'wait_for_ip',
        depends_on: apply ? [apply.id] : [],
        conditions: {},
        retry: 0,
        timeout: 180,
        rollback: null,
      };
      state.workflow.push(ipStep);
    }

    const ids = new Set(state.workflow.map(step => step.id));
    let awxId = 'awx';
    for (let index = 2; ids.has(awxId); index += 1) awxId = 'awx_' + index;
    state.workflow.push({
      id: awxId,
      type: 'register_awx',
      depends_on: [ipStep.id],
      conditions: {},
      retry: Number(state.awxRetry ?? 3),
      timeout: Number(state.awxTimeout ?? 300),
      rollback: null,
    });
  }

  async function discover(state, credentialId) {
    state.awxDiscovery = null;
    state.awxDiscoveryError = '';
    if (!credentialId) return;
    try {
      state.awxDiscovery = await api('/credentials/' + Number(credentialId) + '/awx/discovery', {
        headers: parts.core.scopeHeaders(state),
      });
    } catch (error) {
      state.awxDiscoveryError = error.message;
    }
  }

  function validate(state) {
    const errors = {};
    if (!enabled(state)) return errors;
    if (state.providerType !== 'proxmox') {
      errors.awx_enabled = 'Onboarding AWX jest obecnie dostępny tylko dla Blueprintów Proxmox.';
      return errors;
    }
    if (!parts.cloudInit?.enabled(state)) {
      errors.awx_enabled = 'Onboarding AWX wymaga włączonego Cloud-init.';
    }
    if (!state.awxCredentialId) {
      errors.awx_credential_id = 'Wybierz połączenie AWX dla tego Blueprintu.';
    }
    if (state.advancedWorkflow && !state.workflow.some(step => step.type === 'register_awx')) {
      errors.awx_enabled = 'Włączony AWX wymaga kroku register_awx w workflow.';
    }

    const discovery = state.awxDiscovery;
    if (!discovery) return errors;

    const organizationId = String(state.awxOrganizationId || '');
    const projectId = String(state.awxProjectId || '');
    const inventoryId = String(state.awxInventoryId || '');
    const jobTemplateId = String(state.awxJobTemplateId || '');

    const organizations = discovery.organizations || [];
    const projects = discovery.projects || [];
    const inventories = discovery.inventories || [];
    const templates = discovery.job_templates || [];

    if (organizationId && !organizations.some(row => String(row.id) === organizationId)) {
      errors.awx_organization_id = 'Wybrana organizacja AWX nie jest już dostępna.';
    }
    const project = projects.find(row => String(row.id) === projectId);
    if (projectId && !project) {
      errors.awx_project_id = 'Wybrany projekt AWX nie jest już dostępny.';
    } else if (project && organizationId && String(project.organization || '') !== organizationId) {
      errors.awx_project_id = 'Wybrany projekt AWX nie należy do wybranej organizacji.';
    }
    const inventory = inventories.find(row => String(row.id) === inventoryId);
    if (inventoryId && !inventory) {
      errors.awx_inventory_id = 'Wybrane inventory AWX nie jest już dostępne.';
    } else if (inventory && organizationId && String(inventory.organization || '') !== organizationId) {
      errors.awx_inventory_id = 'Wybrane inventory AWX nie należy do wybranej organizacji.';
    }
    const jobTemplate = templates.find(row => String(row.id) === jobTemplateId);
    if (jobTemplateId && !jobTemplate) {
      errors.awx_job_template_id = 'Wybrany Job Template AWX nie jest już dostępny.';
    } else if (jobTemplate && projectId && String(jobTemplate.project || '') !== projectId) {
      errors.awx_job_template_id = 'Wybrany Job Template nie należy do wybranego projektu AWX.';
    }

    return errors;
  }

  function labelWithMeta(row, extras = []) {
    return [row.name, ...extras.filter(Boolean)].join(' · ');
  }

  function render({ state, data, rerender, capture }) {
    const supported = state.providerType === 'proxmox';
    if (!supported && state.awxEnabled) toggleStep(state, false);

    const awxCredentials = (data.credentials || []).filter(row => row.type === 'awx');
    const active = enabled(state);
    const toggle = checkboxField(
      'Używaj AWX / Automation Controller dla maszyn z tego Blueprintu',
      'awx_enabled',
      active
    );
    const toggleControl = toggle.querySelector('input');
    toggleControl.disabled = !supported;
    toggleControl.addEventListener('change', event => {
      capture();
      toggleStep(state, event.currentTarget.checked);
      rerender();
    });

    const panel = node('section', { class: 'blueprint-wizard-inline-panel wide', 'data-awx-onboarding': 'true' },
      node('div', { class: 'blueprint-wizard-section-heading wide' },
        node('strong', { text: 'AWX / Automation Controller' }),
        node('span', { class: 'muted', text: supported
          ? 'Po utworzeniu VM CloudPortal może zarejestrować host w AWX i opcjonalnie uruchomić wybrany Job Template.'
          : 'Automatyczny onboarding AWX jest obecnie obsługiwany dla Blueprintów Proxmox.' })),
      toggle
    );
    if (!supported || !active) return panel;

    const credential = selectField('Połączenie AWX', 'awx_credential_id', [
      { value: '', label: awxCredentials.length ? 'Wybierz AWX' : 'Brak zapisanych połączeń AWX' },
      ...awxCredentials.map(row => ({ value: row.id, label: row.name + ' · ' + row.endpoint })),
    ], state.awxCredentialId, { required: true, wide: true });
    credential.querySelector('select').addEventListener('change', async event => {
      capture();
      state.awxCredentialId = event.currentTarget.value;
      state.awxOrganizationId = '';
      state.awxProjectId = '';
      state.awxInventoryId = '';
      state.awxJobTemplateId = '';
      await discover(state, state.awxCredentialId);
      rerender();
    });
    panel.append(credential);

    if (!state.awxCredentialId) {
      panel.append(node('p', { class: 'muted', text: 'Najpierw wybierz Credential typu AWX. Zasoby kontrolera zostaną pobrane dynamicznie.' }));
      return panel;
    }

    const discovery = state.awxDiscovery || {};
    const organizations = discovery.organizations || [];
    const projects = discovery.projects || [];
    const inventories = discovery.inventories || [];
    const jobTemplates = discovery.job_templates || [];
    const organizationId = String(state.awxOrganizationId || '');
    const projectId = String(state.awxProjectId || '');
    const inventoryId = String(state.awxInventoryId || '');
    const projectById = new Map(projects.map(row => [String(row.id), row]));

    const filteredProjects = projects.filter(row =>
      !organizationId || String(row.organization || '') === organizationId);
    const filteredInventories = inventories.filter(row =>
      !organizationId || String(row.organization || '') === organizationId);
    const filteredJobTemplates = jobTemplates.filter(row => {
      if (projectId) return String(row.project || '') === projectId;
      if (!organizationId || !row.project) return true;
      return String(projectById.get(String(row.project))?.organization || '') === organizationId;
    });

    const organization = selectField('Organizacja AWX', 'awx_organization_id', [
      { value: '', label: 'Automatycznie / bez ograniczenia do organizacji' },
      ...organizations.map(row => ({ value: row.id, label: row.name })),
    ], state.awxOrganizationId, {
      wide: true,
      help: 'Jeżeli inventory będzie tworzone automatycznie, zostanie utworzone w tej organizacji.',
    });
    organization.querySelector('select').addEventListener('change', event => {
      state.awxOrganizationId = event.currentTarget.value;
      const selectedProject = projects.find(row => String(row.id) === String(state.awxProjectId));
      if (selectedProject && state.awxOrganizationId
          && String(selectedProject.organization || '') !== String(state.awxOrganizationId)) {
        state.awxProjectId = '';
        state.awxJobTemplateId = '';
      }
      const selectedInventory = inventories.find(row => String(row.id) === String(state.awxInventoryId));
      if (selectedInventory && state.awxOrganizationId
          && String(selectedInventory.organization || '') !== String(state.awxOrganizationId)) {
        state.awxInventoryId = '';
      }
      rerender();
    });

    const project = selectField('Projekt AWX', 'awx_project_id', [
      { value: '', label: 'Bez przypisanego projektu / nie filtruj Job Template' },
      ...filteredProjects.map(row => ({
        value: row.id,
        label: labelWithMeta(row, [
          row.scm_type ? 'SCM: ' + row.scm_type : '',
          row.status ? 'status: ' + row.status : '',
        ]),
      })),
    ], state.awxProjectId, {
      wide: true,
      help: 'Projekt jest zapisany w Blueprintcie i ogranicza listę Job Template do właściwego projektu AWX.',
    });
    project.querySelector('select').addEventListener('change', event => {
      state.awxProjectId = event.currentTarget.value;
      const selectedTemplate = jobTemplates.find(row => String(row.id) === String(state.awxJobTemplateId));
      if (selectedTemplate && state.awxProjectId
          && String(selectedTemplate.project || '') !== String(state.awxProjectId)) {
        state.awxJobTemplateId = '';
      }
      rerender();
    });

    const inventory = selectField('Inventory AWX', 'awx_inventory_id', [
      { value: '', label: 'Automatycznie — użyj/utwórz „' + (state.awxInventoryName || 'CloudPortal') + '”' },
      ...filteredInventories.map(row => ({ value: row.id, label: row.name })),
    ], state.awxInventoryId, {
      wide: true,
      help: 'Możesz wskazać istniejące inventory albo pozwolić CloudPortalowi utworzyć je automatycznie.',
    });
    inventory.querySelector('select').addEventListener('change', event => {
      state.awxInventoryId = event.currentTarget.value;
      rerender();
    });

    const inventoryName = field('Nazwa inventory tworzonego automatycznie', 'awx_inventory_name', {
      value: state.awxInventoryName || 'CloudPortal',
      wide: true,
      help: 'Pole jest używane tylko wtedy, gdy nie wskażesz istniejącego inventory.',
    });
    inventoryName.querySelector('input').addEventListener('input', event => {
      state.awxInventoryName = event.currentTarget.value;
    });

    const groupEnv = checkboxField('Twórz/przypisuj grupę env-<environment>', 'awx_group_environment', state.awxGroupByEnvironment);
    groupEnv.querySelector('input').addEventListener('change', event => {
      state.awxGroupByEnvironment = event.currentTarget.checked;
    });
    const groupApmid = checkboxField('Twórz/przypisuj grupę apmid-<APMID>', 'awx_group_apmid', state.awxGroupByApmid);
    groupApmid.querySelector('input').addEventListener('change', event => {
      state.awxGroupByApmid = event.currentTarget.checked;
    });

    const jobTemplate = selectField('Job Template po onboardingu', 'awx_job_template_id', [
      { value: '', label: 'Nie uruchamiaj automatycznie' },
      ...filteredJobTemplates.map(row => ({
        value: row.id,
        label: labelWithMeta(row, [
          row.playbook ? 'playbook: ' + row.playbook : '',
          row.project ? 'projekt: ' + (projectById.get(String(row.project))?.name || ('#' + row.project)) : '',
        ]),
      })),
    ], state.awxJobTemplateId, {
      wide: true,
      help: 'Po rejestracji hosta CloudPortal może uruchomić Job Template należący do wybranego projektu AWX.',
    });
    jobTemplate.querySelector('select').addEventListener('change', event => {
      state.awxJobTemplateId = event.currentTarget.value;
    });

    const retry = field('Liczba ponowień AWX', 'awx_retry', {
      type: 'number', min: 0, max: 10, value: state.awxRetry ?? 3,
      help: 'Dotyczy kroku register_awx.',
    });
    retry.querySelector('input').addEventListener('input', event => {
      state.awxRetry = Number(event.currentTarget.value || 0);
    });
    const timeout = field('Timeout AWX (s)', 'awx_timeout', {
      type: 'number', min: 10, max: 3600, value: state.awxTimeout ?? 300,
      help: 'Maksymalny czas pojedynczej próby onboardingu.',
    });
    timeout.querySelector('input').addEventListener('input', event => {
      state.awxTimeout = Number(event.currentTarget.value || 300);
    });

    const removeOnDestroy = checkboxField(
      'Usuń host z AWX po usunięciu VM',
      'awx_remove_on_destroy',
      state.awxRemoveOnDestroy !== false
    );
    removeOnDestroy.querySelector('input').addEventListener('change', event => {
      state.awxRemoveOnDestroy = event.currentTarget.checked;
    });

    const refresh = button('Odśwież zasoby AWX', async () => {
      capture();
      await discover(state, state.awxCredentialId);
      rerender();
    }, 'ghost');

    panel.append(
      state.awxDiscoveryError
        ? node('div', { class: 'blueprint-wizard-info danger' },
            node('strong', { text: 'Autodiscovery AWX nie powiodło się' }),
            node('span', { text: state.awxDiscoveryError }))
        : (state.awxDiscovery
            ? node('div', { class: 'blueprint-wizard-info' },
                node('strong', { text: 'AWX połączony' }),
                node('span', { text: 'API: ' + (discovery.api_base || '—')
                  + ' · organizacje: ' + organizations.length
                  + ' · projekty: ' + projects.length
                  + ' · inventory: ' + inventories.length
                  + ' · job templates: ' + jobTemplates.length }))
            : null),
      node('div', { class: 'blueprint-wizard-inline-actions wide' }, refresh),
      organization,
      project,
      inventory,
      inventoryName,
      groupEnv,
      groupApmid,
      jobTemplate,
      retry,
      timeout,
      removeOnDestroy,
      node('p', { class: 'muted wide', text: 'AWX nie trafia do cloud-init. CloudPortal używa zaszyfrowanego Credentiala po uzyskaniu adresu IP VM, rejestruje host idempotentnie i dopiero potem opcjonalnie uruchamia Job Template.' })
    );
    return panel;
  }

  function summaryRows(state, data) {
    if (!enabled(state)) return [['AWX', 'Wyłączony']];
    const discovery = state.awxDiscovery || {};
    const nameFor = (rows, id) => (rows || []).find(row => String(row.id) === String(id))?.name || (id ? '#' + id : '—');
    const credential = (data.credentials || []).find(row => String(row.id) === String(state.awxCredentialId));
    return [
      ['AWX', credential?.name || (state.awxCredentialId ? '#' + state.awxCredentialId : '—')],
      ['Organizacja AWX', nameFor(discovery.organizations, state.awxOrganizationId)],
      ['Projekt AWX', nameFor(discovery.projects, state.awxProjectId)],
      ['Inventory AWX', state.awxInventoryId
        ? nameFor(discovery.inventories, state.awxInventoryId)
        : 'Automatycznie: ' + (state.awxInventoryName || 'CloudPortal')],
      ['Job Template', state.awxJobTemplateId
        ? nameFor(discovery.job_templates, state.awxJobTemplateId)
        : 'Nie uruchamiaj'],
      ['Grupy', [
        state.awxGroupByEnvironment ? 'environment' : '',
        state.awxGroupByApmid ? 'APMID' : '',
      ].filter(Boolean).join(', ') || 'Brak'],
    ];
  }

  parts.awx = { enabled, toggleStep, discover, validate, render, summaryRows };
  registerExtension('blueprint-wizard-awx', () => {});
})();
