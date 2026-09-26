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
  const scopeInfo = node('div', { class: 'blueprint-wizard-info wide' },
    node('strong', { text: 'Scope AWX jest dziedziczony z CloudPortal' }),
    node('span', { text: 'Tenant CloudPortal = Organization AWX, Project CloudPortal = Project AWX. Tych wartości nie wybiera się ręcznie w Blueprintcie.' })
  );
  const inventoryField = selectField(
    'Inventory AWX', 'awx_inventory_id', [], selection.inventory,
    { wide: true, placeholder: 'Automatycznie — użyj lub utwórz inventory' }
  );
  const inventoryNameField = field('Pattern nazwy inventory tworzonego automatycznie', 'awx_inventory_name', {
    value: config.inventory_name || '<Projekt>-<APMID>-<ENV>',
    wide: true,
    placeholder: '<Projekt>-<APMID>-<ENV>',
    help: 'Dostępne tokeny: <Projekt>, <APMID>, <ENV>. <Projekt> zawsze oznacza Project AWX mapowany z bieżącego Projectu CloudPortal. APMID i ENV są rozwiązywane podczas tworzenia VM.',
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
    scopeInfo,
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
    const inventories = discovery?.inventories || [];
    const templates = discovery?.job_templates || [];

    if (discovery && selection.inventory
        && !inventories.some(row => String(row.id) === selection.inventory)) {
      selection.inventory = '';
    }
    if (discovery && selection.jobTemplate
        && !templates.some(row => String(row.id) === selection.jobTemplate)) {
      selection.jobTemplate = '';
    }

    window.BlueprintFormUtils.setSelectChoices(
      inventorySelect,
      withSaved(inventories, selection.inventory, row => ({
        value: row.id,
        label: row.name,
      }), 'inventory'),
      selection.inventory,
      'Automatycznie — użyj lub utwórz inventory w Organization mapowanej z Tenanta'
    );
    window.BlueprintFormUtils.setSelectChoices(
      jobTemplateSelect,
      withSaved(templates, selection.jobTemplate, row => ({
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
      selection.inventory = '';
      selection.jobTemplate = '';
    }
    discovery = null;
    refreshChoices();
    if (!selection.credential) {
      if (toggleControl.checked) {
        setStatus(awxCredentials.length
          ? 'Wybierz połączenie AWX, aby pobrać inventory i Job Template. Organization/Project wynikają ze scope CloudPortal.'
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
