'use strict';

(() => {
async function create(deployment = {}, credentials = [], workflow = [], onChange = () => {}) {
  const config = deployment?.awx || {};
  const runtimeEnvironmentSync = deployment?.select_environment_on_execute === true;
  const runtimeApmidSync = deployment?.select_apmid_on_execute === true;
  const awxStep = (workflow || []).find(step => step.type === 'register_awx') || null;
  const awxCredentials = credentials.filter(value => value.type === 'awx');
  const selection = {
    credential: String(config.credential_id || ''),
    organization: String(config.organization_id || ''),
    project: String(config.project_id || ''),
    inventory: String(config.inventory_id || ''),
    jobTemplate: String(config.job_template_id || ''),
  };
  let discovery = null;

  const toggle = checkboxField(
    'Używaj AWX / Automation Controller dla maszyn z tego Blueprintu',
    'awx_enabled',
    Boolean(deployment?.awx || awxStep)
  );
  const credentialField = selectField(
    'Połączenie AWX', 'awx_credential_id',
    [{ value: '', label: awxCredentials.length ? 'Wybierz AWX' : 'Brak zapisanych połączeń AWX' },
      ...awxCredentials.map(value => ({ value: value.id, label: value.name + (value.endpoint ? ' · ' + value.endpoint : '') }))],
    selection.credential,
    { wide: true }
  );
  const organizationField = selectField(
    'Organizacja AWX', 'awx_organization_id', [], selection.organization,
    { wide: true, placeholder: 'Automatycznie / bez ograniczenia do organizacji' }
  );
  const projectField = selectField(
    'Projekt AWX', 'awx_project_id', [], selection.project,
    { wide: true, placeholder: 'Bez przypisanego projektu / nie filtruj Job Template' }
  );
  const inventoryField = selectField(
    'Inventory AWX', 'awx_inventory_id', [], selection.inventory,
    { wide: true, placeholder: 'Automatycznie — użyj lub utwórz inventory' }
  );
  const inventoryNameField = field('Pattern nazwy inventory tworzonego automatycznie', 'awx_inventory_name', {
    value: config.inventory_name || '<Projekt>-<APMID>-<ENV>',
    wide: true,
    placeholder: '<Projekt>-<APMID>-<ENV>',
    help: 'Dostępne tokeny: <Projekt>, <APMID>, <ENV>. <Projekt> używa wybranego projektu AWX, a bez wyboru bieżącego projektu CloudPortal. APMID i ENV są rozwiązywane podczas tworzenia VM.',
  });
  const groupEnvironmentField = checkboxField(
    'Twórz/przypisuj grupę env-<environment>',
    'awx_group_environment',
    runtimeEnvironmentSync ? true : (config.group_by_environment ?? true)
  );
  const groupApmidField = checkboxField(
    'Twórz/przypisuj grupę apmid-<APMID>',
    'awx_group_apmid',
    runtimeApmidSync ? true : (config.group_by_apmid ?? true)
  );
  const groupEnvironmentControl = groupEnvironmentField.querySelector('input');
  const groupApmidControl = groupApmidField.querySelector('input');
  groupEnvironmentControl.disabled = runtimeEnvironmentSync;
  groupApmidControl.disabled = runtimeApmidSync;

  const jobTemplateField = selectField(
    'Job Template po onboardingu', 'awx_job_template_id', [], selection.jobTemplate,
    { wide: true, placeholder: 'Nie uruchamiaj automatycznie' }
  );
  const retryField = field('Liczba ponowień AWX', 'awx_retry', {
    type: 'number', min: 0, max: 10, value: awxStep?.retry ?? 3,
    help: 'Dotyczy kroku register_awx.',
  });
  const timeoutField = field('Timeout AWX (s)', 'awx_timeout', {
    type: 'number', min: 10, max: 3600, value: awxStep?.timeout ?? 300,
    help: 'Maksymalny czas pojedynczej próby onboardingu.',
  });
  const removeOnDestroyField = checkboxField(
    'Usuń host z AWX po usunięciu VM',
    'awx_remove_on_destroy',
    config.remove_on_destroy ?? true
  );
  const status = node('div', { class: 'blueprint-wizard-info wide', hidden: true });
  const controls = node('div', { class: 'form-grid wide' },
    credentialField,
    organizationField,
    projectField,
    inventoryField,
    inventoryNameField,
    groupEnvironmentField,
    groupApmidField,
    jobTemplateField,
    retryField,
    timeoutField,
    removeOnDestroyField
  );
  const runtimeSyncInfo = (runtimeEnvironmentSync || runtimeApmidSync)
    ? node('div', { class: 'blueprint-wizard-info wide' },
        node('strong', { text: 'Synchronizacja klasyfikacji z AWX' }),
        node('span', { text: [
          runtimeEnvironmentSync ? 'Runtime Environment będzie przekazany do AWX.' : '',
          runtimeApmidSync ? 'Runtime APMID będzie przekazany do AWX.' : '',
        ].filter(Boolean).join(' ') }))
    : null;
  const section = formSection(
    'AWX / Automation Controller',
    'Skonfiguruj onboarding hosta do AWX. Szybka i klasyczna edycja zapisują tę samą konfigurację co kreator krok po kroku.',
    toggle,
    status,
    runtimeSyncInfo,
    controls
  );

  const credentialSelect = credentialField.querySelector('select');
  const organizationSelect = organizationField.querySelector('select');
  const projectSelect = projectField.querySelector('select');
  const inventorySelect = inventoryField.querySelector('select');
  const jobTemplateSelect = jobTemplateField.querySelector('select');
  const toggleControl = toggle.querySelector('input');

  const setStatus = (message = '', danger = false) => {
    status.textContent = message;
    status.hidden = !message;
    status.classList.toggle('danger', danger);
  };
  const withSaved = (rows, selected, mapper, label) => {
    const result = rows.map(mapper);
    if (selected && !rows.some(row => String(row.id) === String(selected))) {
      result.unshift({ value: selected, label: 'Zapisane ' + label + ' #' + selected });
    }
    return result;
  };
  const refreshChoices = () => {
    const organizations = discovery?.organizations || [];
    const projects = discovery?.projects || [];
    const inventories = discovery?.inventories || [];
    const templates = discovery?.job_templates || [];

    if (discovery && selection.organization
        && !organizations.some(row => String(row.id) === selection.organization)) {
      selection.organization = '';
    }
    const filteredProjects = projects.filter(row =>
      !selection.organization || String(row.organization || '') === selection.organization);
    if (discovery && selection.project
        && !filteredProjects.some(row => String(row.id) === selection.project)) {
      selection.project = '';
      selection.jobTemplate = '';
    }
    const filteredInventories = inventories.filter(row =>
      !selection.organization || String(row.organization || '') === selection.organization);
    if (discovery && selection.inventory
        && !filteredInventories.some(row => String(row.id) === selection.inventory)) {
      selection.inventory = '';
    }
    const projectById = new Map(projects.map(row => [String(row.id), row]));
    const filteredTemplates = templates.filter(row => {
      if (selection.project) return String(row.project || '') === selection.project;
      if (!selection.organization || !row.project) return true;
      return String(projectById.get(String(row.project))?.organization || '') === selection.organization;
    });
    if (discovery && selection.jobTemplate
        && !filteredTemplates.some(row => String(row.id) === selection.jobTemplate)) {
      selection.jobTemplate = '';
    }

    window.BlueprintFormUtils.setSelectChoices(
      organizationSelect,
      withSaved(organizations, selection.organization, row => ({ value: row.id, label: row.name }), 'organizacja'),
      selection.organization,
      'Automatycznie / bez ograniczenia do organizacji'
    );
    window.BlueprintFormUtils.setSelectChoices(
      projectSelect,
      withSaved(filteredProjects, selection.project, row => ({
        value: row.id,
        label: row.name + (row.scm_type ? ' · SCM: ' + row.scm_type : ''),
      }), 'projekt'),
      selection.project,
      'Bez przypisanego projektu / nie filtruj Job Template'
    );
    window.BlueprintFormUtils.setSelectChoices(
      inventorySelect,
      withSaved(filteredInventories, selection.inventory, row => ({ value: row.id, label: row.name }), 'inventory'),
      selection.inventory,
      'Automatycznie — użyj lub utwórz inventory'
    );
    window.BlueprintFormUtils.setSelectChoices(
      jobTemplateSelect,
      withSaved(filteredTemplates, selection.jobTemplate, row => ({
        value: row.id,
        label: row.name + (row.playbook ? ' · playbook: ' + row.playbook : ''),
      }), 'Job Template'),
      selection.jobTemplate,
      'Nie uruchamiaj automatycznie'
    );
  };
  const updateVisibility = () => {
    controls.hidden = !toggleControl.checked;
    if (toggleControl.checked && awxStep && !deployment?.awx && !selection.credential) {
      setStatus('Krok register_awx istnieje, ale Blueprint nie ma konfiguracji AWX. Uzupełnij połączenie i ustawienia poniżej przed zapisem.', true);
    } else if (!toggleControl.checked) {
      setStatus('');
    }
  };
  const loadDiscovery = async (reset = false) => {
    selection.credential = String(credentialSelect.value || '');
    if (reset) {
      selection.organization = '';
      selection.project = '';
      selection.inventory = '';
      selection.jobTemplate = '';
    }
    discovery = null;
    refreshChoices();
    if (!selection.credential) {
      if (toggleControl.checked) {
        setStatus(awxCredentials.length
          ? 'Wybierz połączenie AWX, aby pobrać organizacje, projekty, inventory i Job Template.'
          : 'Brak Credentiala typu AWX. Dodaj połączenie AWX w sekcji Dane dostępowe.', true);
      }
      return;
    }
    setStatus('Pobieranie organizacji, projektów, inventory i Job Template z AWX…');
    try {
      discovery = await api('/credentials/' + Number(selection.credential) + '/awx/discovery');
      refreshChoices();
      setStatus(
        'AWX połączony · organizacje: ' + (discovery.organizations || []).length
        + ' · projekty: ' + (discovery.projects || []).length
        + ' · inventory: ' + (discovery.inventories || []).length
        + ' · job templates: ' + (discovery.job_templates || []).length
      );
    } catch (error) {
      refreshChoices();
      setStatus('Nie udało się pobrać zasobów AWX: ' + error.message, true);
    }
  };

  toggleControl.addEventListener('change', async () => {
    updateVisibility();
    if (toggleControl.checked && selection.credential && !discovery) await loadDiscovery(false);
    onChange();
  });
  credentialSelect.addEventListener('change', async () => {
    await loadDiscovery(true);
    onChange();
  });
  organizationSelect.addEventListener('change', () => {
    selection.organization = String(organizationSelect.value || '');
    refreshChoices();
    onChange();
  });
  projectSelect.addEventListener('change', () => {
    selection.project = String(projectSelect.value || '');
    refreshChoices();
    onChange();
  });
  inventorySelect.addEventListener('change', () => {
    selection.inventory = String(inventorySelect.value || '');
    onChange();
  });
  jobTemplateSelect.addEventListener('change', () => {
    selection.jobTemplate = String(jobTemplateSelect.value || '');
    onChange();
  });
  retryField.querySelector('input').addEventListener('input', onChange);
  timeoutField.querySelector('input').addEventListener('input', onChange);

  refreshChoices();
  updateVisibility();
  if (toggleControl.checked && selection.credential) await loadDiscovery(false);

  return {
    section,
    enabled: () => Boolean(toggleControl.checked),
    retry: () => Number(retryField.querySelector('input').value || 3),
    timeout: () => Number(timeoutField.querySelector('input').value || 300),
    value: () => {
      if (!toggleControl.checked) return null;
      const credentialId = Number(credentialSelect.value || 0);
      if (!credentialId) throw new Error('Wybierz połączenie AWX dla tego Blueprintu.');
      return {
        credential_id: credentialId,
        organization_id: organizationSelect.value ? Number(organizationSelect.value) : null,
        project_id: projectSelect.value ? Number(projectSelect.value) : null,
        inventory_id: inventorySelect.value ? Number(inventorySelect.value) : null,
        inventory_name: String(inventoryNameField.querySelector('input').value || '<Projekt>-<APMID>-<ENV>').trim() || '<Projekt>-<APMID>-<ENV>',
        group_by_environment: runtimeEnvironmentSync || groupEnvironmentControl.checked,
        group_by_apmid: runtimeApmidSync || groupApmidControl.checked,
        job_template_id: jobTemplateSelect.value ? Number(jobTemplateSelect.value) : null,
        remove_on_destroy: removeOnDestroyField.querySelector('input').checked,
      };
    },
  };
}

function quickWorkflow(item, awx, options, retry, timeout) {
  const existingWorkflow = Array.isArray(item?.workflow)
    ? item.workflow.map(step => ({
        ...step,
        depends_on: [...(step.depends_on || [])],
        conditions: { ...(step.conditions || {}) },
      }))
    : [];
  const existingAwxSteps = existingWorkflow.filter(step => step.type === 'register_awx');
  const existingHasCloudInit = existingWorkflow.some(step => step.type === 'cloud_init');

  if (awx && existingAwxSteps.length && existingHasCloudInit) {
    return existingWorkflow.map(step => step.type === 'register_awx'
      ? { ...step, retry, timeout }
      : step);
  }
  if (!awx && existingAwxSteps.length && existingHasCloudInit) {
    const removed = new Map(existingAwxSteps.map(step => [step.id, step]));
    return existingWorkflow
      .filter(step => step.type !== 'register_awx')
      .map(step => ({
        ...step,
        depends_on: [...new Set((step.depends_on || []).flatMap(id =>
          removed.has(id) ? (removed.get(id).depends_on || []) : [id]))],
      }));
  }
  return window.BlueprintFormUtils.blueprintWorkflow({
    ...options,
    cloudInit: Boolean(awx),
    awx: Boolean(awx),
    awxRetry: retry,
    awxTimeout: timeout,
  });
}

function validateWorkflow(awx, workflow) {
  const workflowTypes = new Set((workflow || []).map(step => step.type));
  if (awx && !workflowTypes.has('register_awx')) {
    throw new Error('Włączony AWX wymaga kroku „Rejestracja w AWX” w workflow.');
  }
  if (awx && !workflowTypes.has('cloud_init')) {
    throw new Error('Onboarding AWX wymaga kroku Cloud-init w workflow.');
  }
  if (!awx && workflowTypes.has('register_awx')) {
    throw new Error('Krok „Rejestracja w AWX” wymaga włączenia i konfiguracji AWX powyżej.');
  }
}

registerExtension('blueprint-awx-editor', () => {
  window.BlueprintAwxEditor = Object.freeze({ create, quickWorkflow, validateWorkflow });
});
})();
