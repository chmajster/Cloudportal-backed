'use strict';

(() => {
async function schedulesView() {
  const schedules = (await api('/schedules?limit=200')).items;
  const actions = allowed('schedules.create') && allowed('deployments.read') ? [button('Nowy harmonogram', () => navigate('/operations/schedules/new'), 'primary')] : [];
  dom.content.replaceChildren(heading('Trwałe operacje Terraform uruchamiane przez dispatcher z ponowną kontrolą uprawnień.', actions),
    table([
      { label: 'Nazwa', value: item => node('strong', { text: item.name }) },
      { label: 'Operacja', value: item => operationLabel(item.operation) },
      { label: 'Wdrożenie', class: 'mono', value: item => short(item.deployment_id, 18) },
      { label: 'Następne', value: item => formatDate(item.next_run_at) },
      { label: 'Interwał', value: item => item.interval_seconds ? formatDuration(item.interval_seconds) : 'Jednorazowo' },
      { label: 'Status', value: item => badge(statusLabel(item.is_active ? 'active' : 'disabled'), item.is_active ? 'ok' : 'info') },
      { label: 'Błąd', value: item => item.last_error || '—' },
    ], schedules, item => {
      const result = [];
      if (allowed('schedules.update')) {
        if (allowed('deployments.read')) result.push(button(item.is_active ? 'Edytuj' : 'Włącz i edytuj', () => navigate('/operations/schedules/edit/' + encodeURIComponent(item.id) + '/' + encodeURIComponent(item.name || 'schedule'))));
        if (item.is_active) result.push(button('Wyłącz', async () => { await api(`/schedules/${item.id}/disable`, { method: 'POST' }); navigate('schedules'); }));
      }
      if (allowed('schedules.delete')) result.push(button('Usuń', () => confirmAction('Usuń harmonogram', item.name, async () => {
        await api(`/schedules/${item.id}`, { method: 'DELETE' }); navigate('schedules');
      }), 'danger'));
      return result;
    }));
}

async function scheduleForm(item = null) {
  try {
    const deployments = (await api('/deployments?limit=200')).items.filter(row => row.status !== 'destroyed');
    const dateValue = toDateTimeLocal(item?.next_run_at || new Date(Date.now() + 3600000));
    const commonIntervals = new Set([3600, 21600, 43200, 86400, 604800]);
    const currentInterval = Number(item?.interval_seconds || 0);
    const presetValue = currentInterval && !commonIntervals.has(currentInterval) ? 'custom' : String(currentInterval || '');
    const recurrenceField = selectField('Powtarzanie', 'interval_preset', [
      { value: '', label: 'Jednorazowo' },
      { value: '3600', label: 'Co godzinę' },
      { value: '21600', label: 'Co 6 godzin' },
      { value: '43200', label: 'Co 12 godzin' },
      { value: '86400', label: 'Codziennie' },
      { value: '604800', label: 'Co tydzień' },
      { value: 'custom', label: 'Własny interwał' },
    ], presetValue);
    const customInterval = field('Własny interwał (sekundy)', 'interval_seconds', {
      type: 'number', min: 60, value: presetValue === 'custom' ? currentInterval : '',
      help: 'Minimum 60 sekund.',
    });
    const refreshRecurrence = () => { customInterval.hidden = recurrenceField.querySelector('select').value !== 'custom'; };
    recurrenceField.querySelector('select').addEventListener('change', refreshRecurrence);
    refreshRecurrence();

    const fields = node('div', { class: 'form-grid' },
      field('Nazwa', 'name', { required: true, value: item?.name || '' }),
      selectField('Wdrożenie', 'deployment_id', deployments.map(row => ({ value: row.id, label: `${row.name} · ${short(row.id, 10)}` })), item?.deployment_id || '', { required: true, placeholder: 'Wybierz wdrożenie' }),
      selectField('Operacja', 'operation', [{ value: 'terraform.plan', label: 'Plan' }, { value: 'terraform.apply', label: 'Zastosuj' }, { value: 'terraform.destroy', label: 'Usuń zasoby' }], item?.operation || 'terraform.plan'),
      field('Następne uruchomienie', 'next_run_at', { type: 'datetime-local', required: true, value: dateValue }),
      recurrenceField,
      customInterval);
    openModal({ title: item ? 'Edytuj harmonogram' : 'Nowy harmonogram', eyebrow: 'Harmonogram', body: fields, onSubmit: async data => {
      const preset = data.get('interval_preset');
      const intervalSeconds = preset === 'custom'
        ? Number(data.get('interval_seconds'))
        : (preset ? Number(preset) : null);
      if (preset === 'custom' && (!Number.isFinite(intervalSeconds) || intervalSeconds < 60)) {
        throw new Error('Własny interwał musi mieć co najmniej 60 sekund.');
      }
      await api(item ? `/schedules/${item.id}` : '/schedules', { method: item ? 'PUT' : 'POST', body: {
        name: data.get('name'), deployment_id: data.get('deployment_id'), operation: data.get('operation'),
        next_run_at: new Date(data.get('next_run_at')).toISOString(),
        interval_seconds: intervalSeconds,
      } });
      toast('Harmonogram zapisany.');
      navigate('schedules');
    }});
  } catch (error) { toast(error.message, 'error'); }
}

async function webhooksView() {
  const [hooks, deliveries] = await Promise.all([api('/webhooks?limit=200'), api('/webhook-deliveries?limit=200')]);
  const actions = allowed('webhooks.create') ? [button('Nowy webhook', () => navigate('/operations/webhooks/new'), 'primary')] : [];
  dom.content.replaceChildren(heading('Podpisane HMAC dostawy HTTPS. Host musi znajdować się w allowliście backendu.', actions),
    node('section', { class: 'panel' },
      node('div', { class: 'panel-header' }, node('h2', { text: 'Endpointy' })),
      table([
        { label: 'Nazwa', value: item => node('strong', { text: item.name }) },
        { label: 'URL', class: 'mono', value: item => short(item.url, 48) },
        { label: 'Zdarzenia', value: item => item.events.map(webhookEventLabel).join(', ') },
        { label: 'Status', value: item => badge(item.is_active ? 'active' : 'inactive', item.is_active ? 'ok' : 'danger') },
      ], hooks.items, item => {
        const result = [];
        if (allowed('webhooks.update')) {
          result.push(button('Edytuj', () => navigate('/operations/webhooks/edit/' + encodeURIComponent(item.id) + '/' + encodeURIComponent(item.name || 'webhook'))));
          result.push(button('Rotuj sekret', async () => {
            const value = await api(`/webhooks/${item.id}/rotate-secret`, { method: 'POST' });
            showSecret('Nowy webhook secret', value.secret);
          }));
        }
        if (allowed('webhooks.delete')) result.push(button('Usuń', () => confirmAction('Usuń webhook', item.name, async () => {
          await api(`/webhooks/${item.id}`, { method: 'DELETE' }); navigate('webhooks');
        }), 'danger'));
        return result;
      })
    ),
    node('section', { class: 'panel' },
      node('div', { class: 'panel-header' }, node('h2', { text: 'Dostawy' })),
      table([
        { label: 'Zdarzenie', value: item => webhookEventLabel(item.event) },
        { label: 'Zasób', class: 'mono', value: item => short(item.resource_id, 18) },
        { label: 'Status', value: item => badge(item.status, statusKind(item.status)) },
        { label: 'Próby', value: item => item.attempts },
        { label: 'Następna', value: item => formatDate(item.next_attempt_at) },
        { label: 'Błąd', value: item => item.last_error || '—' },
      ], deliveries.items)
    )
  );
}

function webhookForm(item = null) {
  const events = item?.events || ['job.successful', 'job.failed'];
  const fields = node('div', { class: 'form-grid' },
    field('Nazwa', 'name', { required: true, value: item?.name || '' }),
    field('HTTPS URL', 'url', { required: true, value: item?.url || '', wide: true }),
    checkboxField('Zadanie zakończone', 'event_job_successful', events.includes('job.successful')),
    checkboxField('Zadanie zakończone błędem', 'event_job_failed', events.includes('job.failed')),
    checkboxField('Zadanie anulowane', 'event_job_cancelled', events.includes('job.cancelled')),
    checkboxField('Odzyskiwanie dodane do kolejki', 'event_recovery_queued', events.includes('recovery.queued')),
    checkboxField('Odzyskiwanie zakończone', 'event_recovery_successful', events.includes('recovery.successful')),
    checkboxField('Odzyskiwanie zakończone błędem', 'event_recovery_failed', events.includes('recovery.failed')),
    checkboxField('Alert systemowy', 'event_system_alert', events.includes('system.alert')),
    checkboxField('Aktywny', 'is_active', item?.is_active ?? true));
  openModal({ title: item ? 'Edytuj webhook' : 'Nowy webhook', eyebrow: 'Signed HMAC', body: fields, onSubmit: async data => {
    const selected = [];
    for (const event of ['job.successful', 'job.failed', 'job.cancelled', 'recovery.queued', 'recovery.successful', 'recovery.failed', 'system.alert']) {
      if (data.has('event_' + event.replace('.', '_'))) selected.push(event);
    }
    if (!selected.length) throw new Error('Wybierz co najmniej jeden event.');
    const result = await api(item ? `/webhooks/${item.id}` : '/webhooks', {
      method: item ? 'PUT' : 'POST', idempotent: !item, body: {
        name: data.get('name'), url: data.get('url'), events: selected, is_active: data.has('is_active'),
      },
    });
    if (!item && result.secret) {
      navigate('webhooks');
      showSecret('Webhook secret', result.secret);
    } else {
      toast('Webhook zapisany.');
      navigate('webhooks');
    }
    return item ? true : false;
  }});
}

registerRoutedForm({
  id: 'schedules-create',
  pattern: /^\/operations\/schedules\/new$/,
  parent: 'schedules',
  permission: 'schedules.create',
  label: 'Harmonogramy',
}, () => scheduleForm());
registerRoutedForm({
  id: 'schedules-edit',
  pattern: /^\/operations\/schedules\/edit\/(?<id>\d+)(?:\/[^/]+)?$/,
  parent: 'schedules',
  permission: 'schedules.update',
  label: 'Harmonogramy',
}, async match => {
  const rows = (await api('/schedules?limit=200')).items;
  const item = rows.find(value => Number(value.id) === Number(match.params.id));
  if (!item) throw new Error('Nie znaleziono harmonogramu.');
  await scheduleForm(item);
});
registerRoutedForm({
  id: 'webhooks-create',
  pattern: /^\/operations\/webhooks\/new$/,
  parent: 'webhooks',
  permission: 'webhooks.create',
  label: 'Webhooki',
}, () => webhookForm());
registerRoutedForm({
  id: 'webhooks-edit',
  pattern: /^\/operations\/webhooks\/edit\/(?<id>\d+)(?:\/[^/]+)?$/,
  parent: 'webhooks',
  permission: 'webhooks.update',
  label: 'Webhooki',
}, async match => {
  const rows = (await api('/webhooks?limit=200')).items;
  const item = rows.find(value => Number(value.id) === Number(match.params.id));
  if (!item) throw new Error('Nie znaleziono webhooka.');
  webhookForm(item);
});
registerView({ id: 'schedules', label: 'Harmonogramy', icon: 'S', permission: 'schedules.read', order: 130 }, schedulesView);
registerView({ id: 'webhooks', label: 'Webhooki', icon: 'W', permission: 'webhooks.read', order: 140 }, webhooksView);

// Policy Engine administration (operations domain)
  let capabilities = null;
  let policyScopeOptions = null;

  const statusLabels = {
    draft: 'Draft',
    dry_run: 'Dry-run',
    enforced: 'Enforced',
    disabled: 'Disabled',
    archived: 'Archived',
  };

  function jsonValue(value, label) {
    try {
      const parsed = JSON.parse(String(value || '').trim() || '{}');
      return parsed;
    } catch (error) {
      throw new Error(label + ': niepoprawny JSON (' + error.message + ').');
    }
  }

  function jsonObject(value, label) {
    const parsed = jsonValue(value, label);
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
      throw new Error(label + ': wymagany obiekt JSON.');
    }
    return parsed;
  }

  function jsonArray(value, label) {
    const parsed = jsonValue(value, label);
    if (!Array.isArray(parsed)) throw new Error(label + ': wymagana tablica JSON.');
    return parsed;
  }

  function pretty(value) {
    return JSON.stringify(value ?? {}, null, 2);
  }

  async function loadCapabilities() {
    if (!capabilities) capabilities = await api('/policies/capabilities');
    return capabilities;
  }

  async function loadPolicyScopes() {
    if (!policyScopeOptions) policyScopeOptions = await api('/policies/scopes');
    return policyScopeOptions;
  }

  function scopeLabel(item, options = policyScopeOptions) {
    const scope = item?.scope || {};
    const tenantId = String(item?.tenant_id || scope.organization_ids?.[0] || '');
    const projectId = String(item?.project_id || scope.project_ids?.[0] || '');
    const projects = Array.isArray(options?.projects) ? options.projects : [];
    const tenants = Array.isArray(options?.tenants) ? options.tenants : [];
    const project = projects.find(row => String(row.id) === projectId);
    const tenant = tenants.find(row => String(row.id) === tenantId);
    const tenantName = tenant?.name || project?.tenant_name || tenantId || 'Organizacja';
    const projectName = project?.name || projectId || 'Projekt';

    const parts = [];
    if (item.scope_level === 'global') parts.push('Global');
    else if (item.scope_level === 'tenant') parts.push(tenantName);
    else parts.push(tenantName + ' → ' + projectName);

    if (Array.isArray(scope.apmids) && scope.apmids.length) {
      parts.push('APMID: ' + scope.apmids.join(', '));
    }
    if (Array.isArray(scope.environments) && scope.environments.length) {
      parts.push('ENV: ' + scope.environments.map(value => String(value).toUpperCase()).join(', '));
    }
    return parts.join(' · ');
  }

  function policyStatus(item) {
    const status = statusLabels[item.status] || item.status;
    const kind = item.status === 'enforced' ? 'ok'
      : item.status === 'dry_run' ? 'warning'
      : item.status === 'disabled' || item.status === 'archived' ? 'muted'
      : 'info';
    return badge(status, kind);
  }

  function policyEditor(item = null) {
    Promise.all([
      loadCapabilities(),
      loadPolicyScopes(),
      api('/project-context').catch(() => ({ selected: null })),
      api('/settings/vm-classification').catch(() => ({
        environments: { test: true, dev: true, nonprod: true, prod: true },
        apmids: [],
      })),
    ]).then(async ([caps, scopeOptions, projectContext, globalClassification]) => {
      const defaultScope = item?.scope || {
        actions: ['vm.create'],
        resource_types: ['vm'],
        apmids: ['LEO'],
        environments: ['dev'],
      };
      const defaultCondition = item?.condition || {};
      const defaultEffects = item?.effects || [
        { type: 'allow', mode: 'whitelist', message: 'Dozwolony deployment w wybranym APMID/ENV' },
      ];

      const projects = Array.isArray(scopeOptions?.projects) ? scopeOptions.projects : [];
      const tenantWide = Array.isArray(scopeOptions?.tenants) ? scopeOptions.tenants : [];
      const tenantMap = new Map();
      tenantWide.forEach(row => tenantMap.set(String(row.id), row));
      projects.forEach(row => {
        const id = String(row.tenant_id);
        if (!tenantMap.has(id)) {
          tenantMap.set(id, {
            id,
            name: row.tenant_name || id,
            slug: row.tenant_slug || '',
          });
        }
      });
      const allTenants = [...tenantMap.values()].sort((a, b) =>
        String(a.name || a.id).localeCompare(String(b.name || b.id), 'pl'));
      const tenantWideIds = new Set(tenantWide.map(row => String(row.id)));
      const contextProjectId = String(projectContext?.selected?.id || '');
      const contextProject = projects.find(row => String(row.id) === contextProjectId) || null;

      let selectedLevel = item?.scope_level || (projects.length ? 'project' : tenantWide.length ? 'tenant' : 'global');
      let selectedTenantId = String(
        item?.tenant_id
        || contextProject?.tenant_id
        || projects[0]?.tenant_id
        || tenantWide[0]?.id
        || ''
      );
      let selectedProjectId = String(
        item?.project_id
        || (contextProject && String(contextProject.tenant_id) === selectedTenantId ? contextProject.id : '')
        || projects.find(row => String(row.tenant_id) === selectedTenantId)?.id
        || projects[0]?.id
        || ''
      );
      let classification = globalClassification;
      if (selectedTenantId && selectedLevel !== 'global') {
        classification = await api(
          '/tenants/' + encodeURIComponent(selectedTenantId) + '/vm-classification'
        ).catch(() => globalClassification);
      }

      const levelChoices = [];
      if (scopeOptions?.global_allowed || selectedLevel === 'global') {
        levelChoices.push({ value: 'global', label: 'Global — wszystkie organizacje i projekty' });
      }
      if (tenantWide.length || selectedLevel === 'tenant') {
        levelChoices.push({ value: 'tenant', label: 'Organizacja — wszystkie projekty organizacji' });
      }
      if (projects.length || selectedLevel === 'project') {
        levelChoices.push({ value: 'project', label: 'Projekt — dokładnie jeden projekt' });
      }
      if (!levelChoices.some(row => row.value === selectedLevel)) {
        selectedLevel = levelChoices[0]?.value || 'project';
      }

      const levelField = selectField('Poziom przypisania', 'scope_level', levelChoices, selectedLevel, {
        required: true,
        wide: true,
      });
      levelField.append(node('span', { class: 'field-help',
        text: 'Określa gdzie polityka jest przechowywana i dziedziczona. Dodatkowe APMID/ENV zawężają jej działanie.' }));

      const tenantField = selectField('Organizacja', 'target_tenant_id', [], selectedTenantId, {
        required: true,
        wide: true,
      });
      const projectField = selectField('Projekt', 'target_project_id', [], selectedProjectId, {
        required: true,
        wide: true,
      });
      const tenantSelect = tenantField.querySelector('select');
      const projectSelect = projectField.querySelector('select');
      if (item?.tenant_id) {
        tenantSelect.disabled = true;
        tenantField.append(node('span', { class: 'field-help',
          text: 'Podczas edycji organizacja istniejącej polityki pozostaje bez zmian.' }));
      }
      if (item?.project_id) {
        projectSelect.disabled = true;
        projectField.append(node('span', { class: 'field-help',
          text: 'Podczas edycji projekt istniejącej polityki pozostaje bez zmian.' }));
      }

      const configuredApmids = Array.isArray(classification?.apmids) ? classification.apmids : [];
      const scopeApmids = (Array.isArray(defaultScope.apmids) ? defaultScope.apmids : [])
        .map(value => String(value).toUpperCase());
      const apmids = [...new Set([...configuredApmids, ...scopeApmids].map(value => String(value).toUpperCase()).filter(Boolean))]
        .sort((a, b) => a.localeCompare(b, 'pl'));
      const environmentOrder = ['dev', 'test', 'nonprod', 'prod'];
      const scopeEnvironments = Array.isArray(defaultScope.environments) ? defaultScope.environments : [];
      const environments = [...new Set([
        ...environmentOrder.filter(name => classification?.environments?.[name] !== false),
        ...scopeEnvironments.map(value => String(value).toLowerCase()),
      ])];

      const apmidField = multiCheckboxField(
        'APMID',
        'scope_apmids',
        apmids.map(value => ({ value, label: value })),
        scopeApmids,
        {
          wide: true,
          empty: 'Brak skonfigurowanych APMID. Dodaj APMID w Narzędziach albo użyj trybu zaawansowanego.',
          help: 'Brak zaznaczenia = polityka dotyczy każdego APMID w wybranym zakresie.',
        }
      );
      const environmentField = multiCheckboxField(
        'Środowiska / ENV',
        'scope_environments',
        environments.map(value => ({ value, label: value.toUpperCase() })),
        scopeEnvironments,
        {
          wide: true,
          empty: 'Brak dostępnych środowisk.',
          help: 'Możesz zaznaczyć DEV, TEST, NONPROD, PROD lub dowolną ich kombinację.',
        }
      );

      const actionsField = field('Akcje', 'scope_actions', {
        value: (Array.isArray(defaultScope.actions) ? defaultScope.actions : []).join(', '),
        wide: true,
        help: 'Lista po przecinku, np. vm.create, vm.power, vm.delete. Puste = wszystkie akcje.',
      });
      const resourceTypesField = field('Typy zasobów', 'scope_resource_types', {
        value: (Array.isArray(defaultScope.resource_types) ? defaultScope.resource_types : []).join(', '),
        wide: true,
        help: 'Lista po przecinku, np. vm, blueprint. Puste = wszystkie typy zasobów.',
      });
      const scopeKeysField = field('Pełny scope key — opcjonalnie', 'scope_keys', {
        tag: 'textarea',
        value: (Array.isArray(defaultScope.scope_keys) ? defaultScope.scope_keys : []).join('\n'),
        wide: true,
        help: 'Jeden wpis na linię. Przykład: Organizacja-LEO-131-IAASTEAM-PROD.',
      });

      const scopePreview = node('div', { class: 'wide' },
        node('span', { class: 'muted', text: 'Efektywny zakres: ' }),
        node('strong', { text: '—' })
      );
      const scopePreviewValue = scopePreview.querySelector('strong');

      const advancedScope = node('details', { class: 'policy-json-details wide' },
        node('summary', { text: 'Zaawansowane selektory scope — JSON' }),
        field('Scope JSON', 'scope_advanced', {
          tag: 'textarea',
          value: pretty(defaultScope),
          wide: true,
          help: 'Dla users/roles/groups/blueprints/providers/tags/conditions. Pola Organizacja/Projekt/APMID/ENV/Akcje/Typ zasobu z formularza mają pierwszeństwo.',
        })
      );

      const projectsForTenant = tenantId => projects.filter(row =>
        String(row.tenant_id) === String(tenantId));

      function setSelectOptions(select, rows, value, placeholder = null) {
        const options = [];
        if (placeholder) options.push(node('option', { value: '', text: placeholder }));
        rows.forEach(row => options.push(node('option', {
          value: String(row.id),
          text: row.slug ? String(row.name || row.id) + ' (' + row.slug + ')' : String(row.name || row.id),
          selected: String(row.id) === String(value),
        })));
        select.replaceChildren(...options);
      }

      function selectedValues(name) {
        return [...body.querySelectorAll('input[name="' + name + '"]:checked')]
          .map(input => String(input.value));
      }

      function refreshPreview() {
        const tenant = allTenants.find(row => String(row.id) === String(selectedTenantId));
        const project = projects.find(row => String(row.id) === String(selectedProjectId));
        const apmidValues = selectedValues('scope_apmids');
        const environmentValues = selectedValues('scope_environments').map(value => value.toUpperCase());
        const parts = [];
        if (selectedLevel === 'global') {
          parts.push('GLOBAL');
        } else {
          parts.push(tenant?.name || selectedTenantId || 'Organizacja');
          if (selectedLevel === 'project') parts.push(project?.name || selectedProjectId || 'Projekt');
        }
        if (apmidValues.length) parts.push('APMID: ' + apmidValues.join(', '));
        if (environmentValues.length) parts.push('ENV: ' + environmentValues.join(', '));
        scopePreviewValue.textContent = parts.join(' → ') || '—';
      }

      function refreshTargetFields() {
        const tenantRows = selectedLevel === 'tenant'
          ? allTenants.filter(row => tenantWideIds.has(String(row.id)))
          : allTenants.filter(row => projects.some(project => String(project.tenant_id) === String(row.id)));

        if (selectedLevel !== 'global') {
          if (!tenantRows.some(row => String(row.id) === String(selectedTenantId))) {
            selectedTenantId = String(tenantRows[0]?.id || '');
          }
          setSelectOptions(tenantSelect, tenantRows, selectedTenantId, tenantRows.length ? null : 'Brak organizacji');
        }

        const matchingProjects = projectsForTenant(selectedTenantId);
        if (!matchingProjects.some(row => String(row.id) === String(selectedProjectId))) {
          selectedProjectId = String(matchingProjects[0]?.id || '');
        }
        setSelectOptions(projectSelect, matchingProjects, selectedProjectId, matchingProjects.length ? null : 'Brak projektów');

        tenantField.hidden = selectedLevel === 'global';
        projectField.hidden = selectedLevel !== 'project';
        tenantSelect.required = selectedLevel !== 'global';
        projectSelect.required = selectedLevel === 'project';

        refreshPreview();
      }

      const body = node('div', { class: 'policy-form form-grid' },
        formSection('Podstawowe informacje', 'Nazwa, typ i sposób egzekwowania polityki.',
          field('Nazwa', 'name', { required: true, maxlength: 160, value: item?.name || '' }),
          selectField('Typ', 'policy_type',
            caps.policy_types.map(value => ({ value, label: value })),
            item?.policy_type || 'access'),
          selectField('Status', 'status', caps.statuses.map(value => ({
            value, label: statusLabels[value] || value,
          })), item?.status || 'draft'),
          selectField('Enforcement', 'enforcement', [
            { value: 'hard', label: 'HARD' },
            { value: 'soft', label: 'SOFT' },
            { value: 'advisory', label: 'ADVISORY' },
          ], item?.enforcement || 'hard'),
          field('Priorytet', 'priority', {
            type: 'number', min: 0, max: 10000, required: true,
            value: item?.priority ?? 5000,
          }),
          field('Opis', 'description', {
            tag: 'textarea', maxlength: 8000, value: item?.description || '', wide: true,
          })
        ),
        formSection('Zakres przypisania', 'Wybierz zakres hierarchicznie. Nie trzeba wpisywać identyfikatorów ani JSON.',
          levelField,
          tenantField,
          projectField,
          apmidField,
          environmentField,
          actionsField,
          resourceTypesField,
          scopeKeysField,
          scopePreview,
          advancedScope
        ),
        formSection('Warunki i efekty', 'Zaawansowana logika warunkowa i wynik działania polityki.',
          field('Warunek — JSON', 'condition', {
            tag: 'textarea', value: pretty(defaultCondition), wide: true,
            help: 'Drzewo all/any/not lub leaf: {"field":"resource.cpu","operator":"lte","value":8}.',
          }),
          field('Efekty — JSON', 'effects', {
            tag: 'textarea', value: pretty(defaultEffects), wide: true,
            help: 'Np. allow/deny/require_approval/limit_value/force_value/set_default/add_tag/select_storage.',
          })
        )
      );

      levelField.querySelector('select').addEventListener('change', event => {
        selectedLevel = String(event.currentTarget.value);
        refreshTargetFields();
      });
      tenantSelect.addEventListener('change', event => {
        selectedTenantId = String(event.currentTarget.value);
        selectedProjectId = String(projectsForTenant(selectedTenantId)[0]?.id || '');
        refreshTargetFields();
      });
      projectSelect.addEventListener('change', event => {
        selectedProjectId = String(event.currentTarget.value);
        refreshPreview();
      });
      apmidField.querySelectorAll('input[type="checkbox"]').forEach(input =>
        input.addEventListener('change', refreshPreview));
      environmentField.querySelectorAll('input[type="checkbox"]').forEach(input =>
        input.addEventListener('change', refreshPreview));

      refreshTargetFields();

      openModal({
        title: item ? 'Edytuj politykę' : 'Nowa polityka',
        eyebrow: 'Policy Engine',
        body,
        wide: true,
        submitLabel: item ? 'Zapisz nową wersję' : 'Utwórz politykę',
        onSubmit: async data => {
          const splitValues = value => String(value || '')
            .split(/[\n,;]/)
            .map(row => row.trim())
            .filter(Boolean);

          const scope = jsonObject(data.get('scope_advanced'), 'Scope');
          ['apmids', 'environments', 'scope_keys', 'actions', 'resource_types']
            .forEach(key => delete scope[key]);

          const level = String(data.get('scope_level') || selectedLevel);
          const levelChanged = Boolean(item && level !== item.scope_level);
          if (level !== 'global' || levelChanged) {
            [
              'organization_ids', 'organizations', 'organization_slugs',
              'project_ids', 'projects', 'project_slugs',
            ].forEach(key => delete scope[key]);
          }

          let targetTenantId = selectedTenantId;
          let targetProjectId = selectedProjectId;

          if (item?.tenant_id) targetTenantId = String(item.tenant_id);
          if (item?.project_id) targetProjectId = String(item.project_id);

          if (level === 'tenant' && !targetTenantId) {
            throw new Error('Wybierz organizację dla polityki.');
          }
          if (level === 'project' && (!targetTenantId || !targetProjectId)) {
            throw new Error('Wybierz organizację i projekt dla polityki.');
          }

          if (level === 'tenant' || level === 'project') {
            scope.organization_ids = [targetTenantId];
          }
          if (level === 'project') {
            scope.project_ids = [targetProjectId];
          }

          const apmidValues = data.getAll('scope_apmids').map(value => String(value));
          const environmentValues = data.getAll('scope_environments').map(value => String(value));
          const actionValues = splitValues(data.get('scope_actions'));
          const resourceTypeValues = splitValues(data.get('scope_resource_types'));
          const scopeKeyValues = splitValues(data.get('scope_keys'));

          if (apmidValues.length) scope.apmids = apmidValues;
          if (environmentValues.length) scope.environments = environmentValues;
          if (actionValues.length) scope.actions = actionValues;
          if (resourceTypeValues.length) scope.resource_types = resourceTypeValues;
          if (scopeKeyValues.length) scope.scope_keys = scopeKeyValues;

          const payload = {
            name: String(data.get('name') || '').trim(),
            description: String(data.get('description') || ''),
            policy_type: String(data.get('policy_type')),
            priority: Number(data.get('priority')),
            enforcement: String(data.get('enforcement')),
            status: String(data.get('status')),
            scope_level: level,
            scope,
            condition: jsonObject(data.get('condition'), 'Warunek'),
            effects: jsonArray(data.get('effects'), 'Efekty'),
          };
          if (item) payload.expected_version = item.version;

          let requestTenantId = targetTenantId;
          let requestProjectId = targetProjectId;

          if (item?.project_id) {
            // Existing project-scoped policy must be loaded and authorized in
            // its original project even when the new level becomes tenant/global.
            requestTenantId = String(item.tenant_id);
            requestProjectId = String(item.project_id);
          } else if (item?.tenant_id) {
            // Tenant-scoped policy is visible from every project in the same
            // tenant. Prefer the selected target project when promoting it to
            // project scope, otherwise use any manageable project in that tenant.
            requestTenantId = String(item.tenant_id);
            if (level !== 'project') {
              requestProjectId = String(projectsForTenant(requestTenantId)[0]?.id || '');
            }
          } else if (level === 'tenant' && targetTenantId) {
            requestProjectId = String(projectsForTenant(targetTenantId)[0]?.id || '');
          }

          const requestOptions = {
            method: item ? 'PUT' : 'POST',
            body: payload,
          };
          if (requestTenantId && requestProjectId && (item || level !== 'global')) {
            requestOptions.headers = {
              'X-Tenant-ID': requestTenantId,
              'X-Project-ID': requestProjectId,
            };
          }

          await api(item ? '/policies/' + encodeURIComponent(item.id) : '/policies', requestOptions);
          toast(item ? 'Polityka zapisana jako nowa wersja.' : 'Polityka utworzona.');
          await navigate('policies');
          return false;
        },
      });
    }).catch(error => toast(error.message, 'error'));
  }

  async function rollbackPolicy(item, version) {
    await api('/policies/' + encodeURIComponent(item.id) + '/rollback', {
      method: 'POST',
      body: { version: Number(version), expected_version: item.version },
    });
    toast('Przywrócono wersję polityki.');
    await navigate('policies');
  }

  async function versionsModal(item) {
    const rows = (await api('/policies/' + encodeURIComponent(item.id) + '/versions')).items || [];
    openModal({
      title: item.name + ' — wersje',
      eyebrow: 'Policy Engine / historia',
      wide: true,
      body: node('div', { class: 'stack' },
        table([
          { label: 'Wersja', value: row => '#' + row.version },
          { label: 'Utworzono', value: row => formatDate(row.created_at) },
          { label: 'Status', value: row => row.snapshot?.status || '—' },
          { label: 'Priorytet', value: row => row.snapshot?.priority ?? '—' },
        ], rows, row => row.version !== item.version && allowed('policies.manage')
          ? [button('Rollback', () => rollbackPolicy(item, row.version), 'ghost')]
          : []),
        node('details', { class: 'policy-json-details' },
          node('summary', { text: 'Pełne snapshoty JSON' }),
          node('pre', { class: 'policy-json', text: pretty(rows) }))
      ),
      submitLabel: null,
    });
  }

  function exceptionForm(item) {
    const body = node('div', { class: 'form-grid' },
      field('Nazwa wyjątku', 'name', { required: true, maxlength: 160 }),
      field('Ticket / Change', 'ticket', { maxlength: 160 }),
      field('Powód', 'reason', { tag: 'textarea', required: true, maxlength: 8000, wide: true }),
      selectField('Status', 'status', [
        { value: 'approved', label: 'Approved' },
        { value: 'pending', label: 'Pending' },
      ], 'approved'),
      field('Ważny od (ISO 8601)', 'valid_from', { placeholder: '2026-09-26T08:00:00Z' }),
      field('Ważny do (ISO 8601)', 'valid_until', { placeholder: '2026-09-27T08:00:00Z' }),
      field('Warunek wyjątku — JSON', 'condition', {
        tag: 'textarea', wide: true,
        value: pretty({ field: 'actor.id', operator: 'eq', value: state.identity?.user?.id || 0 }),
      })
    );
    openModal({
      title: 'Nowy wyjątek — ' + item.name,
      eyebrow: 'Policy Engine / exception',
      body,
      wide: true,
      submitLabel: 'Dodaj wyjątek',
      onSubmit: async data => {
        const payload = {
          name: String(data.get('name') || '').trim(),
          ticket: String(data.get('ticket') || '').trim(),
          reason: String(data.get('reason') || '').trim(),
          status: String(data.get('status')),
          condition: jsonObject(data.get('condition'), 'Warunek wyjątku'),
          valid_from: String(data.get('valid_from') || '').trim() || null,
          valid_until: String(data.get('valid_until') || '').trim() || null,
        };
        await api('/policies/' + encodeURIComponent(item.id) + '/exceptions', {
          method: 'POST', body: payload,
        });
        toast('Wyjątek zapisany.');
        await policyDetails(item.id);
        return false;
      },
    });
  }

  async function revokeException(item, exception) {
    await api('/policies/' + encodeURIComponent(item.id) + '/exceptions/' + encodeURIComponent(exception.id), {
      method: 'DELETE',
    });
    toast('Wyjątek wycofany.');
    await policyDetails(item.id);
  }

  async function policyDetails(id) {
    const item = await api('/policies/' + encodeURIComponent(id));
    const exceptions = (await api('/policies/' + encodeURIComponent(id) + '/exceptions')).items || [];
    const actions = [
      allowed('policies.manage') ? button('Edytuj', () => policyEditor(item), 'primary') : null,
      button('Wersje', () => versionsModal(item), 'ghost'),
      allowed('policies.exception.manage') ? button('Dodaj wyjątek', () => exceptionForm(item), 'ghost') : null,
    ].filter(Boolean);
    if (allowed('policies.manage') && item.status !== 'archived') {
      actions.push(button('Archiwizuj', async () => {
        await api('/policies/' + encodeURIComponent(item.id), { method: 'DELETE' });
        toast('Polityka zarchiwizowana.');
        await navigate('policies');
      }, 'danger'));
    }

    openModal({
      title: item.name,
      eyebrow: 'Policy Engine / szczegóły',
      wide: true,
      body: node('div', { class: 'stack' },
        node('div', { class: 'action-group' }, actions),
        node('div', { class: 'policy-meta-grid' },
          node('div', {}, node('span', { class: 'muted', text: 'Typ' }), node('strong', { text: item.policy_type })),
          node('div', {}, node('span', { class: 'muted', text: 'Scope' }), node('strong', { text: scopeLabel(item) })),
          node('div', {}, node('span', { class: 'muted', text: 'Status' }), policyStatus(item)),
          node('div', {}, node('span', { class: 'muted', text: 'Enforcement' }), node('strong', { text: item.enforcement.toUpperCase() })),
          node('div', {}, node('span', { class: 'muted', text: 'Priorytet' }), node('strong', { text: String(item.priority) })),
          node('div', {}, node('span', { class: 'muted', text: 'Wersja' }), node('strong', { text: '#' + item.version }))
        ),
        node('p', { text: item.description || 'Brak opisu.' }),
        node('h3', { text: 'Definicja' }),
        node('pre', { class: 'policy-json', text: pretty({
          scope: item.scope,
          condition: item.condition,
          effects: item.effects,
        }) }),
        node('h3', { text: 'Wyjątki' }),
        exceptions.length
          ? table([
              { label: 'Nazwa', value: row => row.name },
              { label: 'Status', value: row => row.status },
              { label: 'Ticket', value: row => row.ticket || '—' },
              { label: 'Ważny do', value: row => row.valid_until ? formatDate(row.valid_until) : 'bez limitu' },
            ], exceptions, row => allowed('policies.exception.manage') && row.status !== 'revoked'
              ? [button('Wycofaj', () => revokeException(item, row), 'ghost')]
              : [])
          : node('p', { class: 'muted', text: 'Brak wyjątków.' })
      ),
      submitLabel: null,
    });
  }

  function simulator() {
    const body = node('div', { class: 'form-grid' },
      field('Action', 'action', { required: true, value: 'vm.create' }),
      field('Resource type', 'resource_type', { required: true, value: 'vm' }),
      field('Resource ID', 'resource_id', { value: '' }),
      field('Context — JSON', 'context', {
        tag: 'textarea', wide: true,
        value: pretty({
          resource: {
            apmid: 'LEO',
            environment: 'dev',
            cpu: 4,
            memory_mb: 8192,
            tags: ['linux'],
          },
          blueprint: { id: 1, slug: 'ubuntu-server' },
        }),
      }),
      node('div', { class: 'wide policy-simulator-result', hidden: true })
    );
    const output = body.querySelector('.policy-simulator-result');
    openModal({
      title: 'Symulator Policy Engine',
      eyebrow: 'Explain / dry-run',
      body,
      wide: true,
      submitLabel: 'Oceń',
      onSubmit: async data => {
        const result = await api('/policies/evaluate', {
          method: 'POST',
          body: {
            action: String(data.get('action')),
            resource_type: String(data.get('resource_type')),
            resource_id: String(data.get('resource_id') || '').trim() || null,
            context: jsonObject(data.get('context'), 'Context'),
          },
        });
        output.hidden = false;
        output.replaceChildren(
          node('div', { class: 'policy-decision policy-decision-' + result.decision },
            node('strong', { text: 'DECISION: ' + String(result.decision).toUpperCase() })),
          node('pre', { class: 'policy-json', text: pretty(result) })
        );
        return false;
      },
    });
  }

  async function decisionLog() {
    const rows = (await api('/policies/decisions?limit=200&offset=0')).items || [];
    openModal({
      title: 'Decision Log',
      eyebrow: 'Policy Engine / audit',
      wide: true,
      body: node('div', { class: 'stack' },
        table([
          { label: 'Czas', value: row => formatDate(row.timestamp) },
          { label: 'Action', value: row => row.action },
          { label: 'Resource', value: row => row.resource_type + (row.resource_id ? ' / ' + row.resource_id : '') },
          { label: 'Decision', value: row => badge(row.decision, row.decision === 'allow' ? 'ok' : row.decision === 'deny' ? 'danger' : 'warning') },
          { label: 'Policies', value: row => (row.matched_policy_ids || []).length },
        ], rows, row => [button('Trace', () => openModal({
          title: 'Decision ' + row.id,
          eyebrow: row.action,
          wide: true,
          body: node('pre', { class: 'policy-json', text: pretty(row) }),
          submitLabel: null,
        }), 'ghost')])
      ),
      submitLabel: null,
    });
  }

  async function policiesView() {
    const [, scopeOptions, policyResult] = await Promise.all([
      loadCapabilities(),
      loadPolicyScopes(),
      api('/policies'),
    ]);
    const rows = policyResult.items || [];
    const actions = [];
    if (allowed('policies.manage')) actions.push(button('Nowa polityka', () => policyEditor(), 'primary'));
    if (allowed('policies.simulate')) actions.push(button('Symulator', simulator, 'ghost'));
    if (allowed('policies.audit')) actions.push(button('Decision Log', decisionLog, 'ghost'));

    dom.content.replaceChildren(
      heading('Policy Engine', actions),
      node('p', {
        class: 'muted',
        text: 'Centralne reguły ABAC/policy dla Blueprintów, APMID/ENV, Day-2, approval, limitów, placement i lifecycle. RBAC nadal określa bazowe uprawnienie do akcji.',
      }),
      table([
        { label: 'Nazwa', value: item => node('strong', { text: item.name }) },
        { label: 'Typ', value: item => item.policy_type },
        { label: 'Scope', value: item => scopeLabel(item, scopeOptions) },
        { label: 'Status', value: item => policyStatus(item) },
        { label: 'Tryb', value: item => item.enforcement.toUpperCase() },
        { label: 'Priorytet', value: item => item.priority },
        { label: 'Wersja', value: item => '#' + item.version },
        { label: 'Aktualizacja', value: item => formatDate(item.updated_at) },
      ], rows, item => [button('Szczegóły', () => policyDetails(item.id), 'ghost')])
    );
  }

  registerCommand('policies.create', () => policyEditor());
  registerView({
    id: 'policies',
    label: 'Policy Engine',
    icon: 'P',
    permission: 'policies.read',
    order: 25,
  }, policiesView);
})();
