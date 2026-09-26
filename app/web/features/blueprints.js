'use strict';
(() => {
function blueprintTemplateVariableField(name, spec, value) {
  const type = schemaType(spec);
  const label = FIELD_LABELS[name] || spec.title || name;
  const current = value ?? spec.default ?? '';
  const help = `Typ: ${type}. Możesz użyć wartości lub placeholdera, np. {{ cpu }}.`;
  const wrapper = field(label, `deployment_var_${name}`, {
    tag: type === 'array' ? 'textarea' : 'input',
    value: Array.isArray(current) ? current.join('\n') : String(current),
    wide: type === 'array' || ['ssh_public_key', 'subnet_id'].includes(name),
    help,
  });
  wrapper.dataset.blueprintTemplateVariable = name;
  return wrapper;
}
function readBlueprintTemplateVariables(root, template) {
  const result = {};
  const properties = template?.variables_schema?.properties || {};
  for (const [name, spec] of Object.entries(properties)) {
    const control = root.elements?.[`deployment_var_${name}`] || root.querySelector?.(`[name="deployment_var_${name}"]`);
    if (!control) continue;
    const raw = String(control.value ?? '').trim();
    if (!raw) continue;
    if (/{{\s*[^}]+\s*}}/.test(raw)) {
      result[name] = raw;
      continue;
    }
    const type = schemaType(spec);
    if (type === 'integer') result[name] = Number.parseInt(raw, 10);
    else if (type === 'number') result[name] = Number(raw);
    else if (type === 'boolean') result[name] = ['true', '1', 'tak', 'yes'].includes(raw.toLowerCase());
    else if (type === 'array') result[name] = splitValues(raw);
    else result[name] = raw;
  }
  return result;
}
function canManageBlueprintByRole(item) {
  const required = new Set((item?.manager_role_ids || []).map(Number));
  if (!required.size) return true;
  const owned = new Set((state.identity?.roles || []).map(role => Number(role.id)));
  return [...required].some(id => owned.has(id));
}

function blueprintScopeQuery(scope = null) {
  const params = new URLSearchParams();
  if (scope?.tenant_id) params.set('tenantId', String(scope.tenant_id));
  if (scope?.project_id) params.set('projectId', String(scope.project_id));
  const query = params.toString();
  return query ? '?' + query : '';
}

function blueprintExecutionPath(item, scope = null) {
  return '/blueprints/' + encodeURIComponent(item.id)
    + '/' + encodeURIComponent(item.slug || item.name || 'blueprint')
    + '/execute' + blueprintScopeQuery(scope);
}

async function blueprintsView() {
  const scopeResult = await api('/blueprints/creation-scopes?permission=blueprints.read&limit=200');
  const scopes = scopeResult.items || [];
  if (!scopes.length) {
    dom.content.replaceChildren(
      heading('Blueprinty są izolowane per projekt i wymagają uprawnienia blueprints.read.'),
      node('div', { class: 'empty-state' },
        node('strong', { text: 'Brak dostępu do Blueprintów' }),
        node('p', { class: 'muted', text: 'Nie masz blueprints.read w żadnym aktywnym projekcie.' }))
    );
    return;
  }

  const remembered = window.CloudportalBlueprintScope || null;
  const projectContext = await api('/project-context').catch(() => ({ selected: null }));
  const contextProjectId = String(projectContext?.selected?.id || '');
  const selected = scopes.find(scope =>
    remembered
    && String(scope.tenant_id) === String(remembered.tenant_id)
    && String(scope.project_id) === String(remembered.project_id)
  ) || scopes.find(scope => String(scope.project_id) === contextProjectId) || scopes[0];

  window.CloudportalBlueprintScope = {
    tenant_id: String(selected.tenant_id),
    project_id: String(selected.project_id),
  };
  const scopeHeaders = {
    'X-Tenant-ID': String(selected.tenant_id),
    'X-Project-ID': String(selected.project_id),
  };
  const scopeAllows = permission =>
    allowed(permission) || (selected.permissions || []).includes(permission);

  const [blueprintResult, roleResult, avatarResult] = await Promise.all([
    api('/blueprints?limit=200', { headers: scopeHeaders }),
    allowed('roles.read') ? api('/roles?limit=200') : Promise.resolve({ items: [] }),
    api('/blueprint-avatars').catch(() => ({ items: [] })),
  ]);
  const blueprints = blueprintResult.items;
  const roleNames = new Map(roleResult.items.map(role => [Number(role.id), role.name]));
  const avatarById = new Map((avatarResult.items || []).map(item => [String(item.id), item]));
  const canDesignBlueprint = scopeAllows('providers.read')
    && scopeAllows('credentials.read')
    && scopeAllows('terraform.read');
  const actions = [];

  if (scopeAllows('blueprints.create') && canDesignBlueprint) {
    actions.push(button('Nowy Blueprint — kreator', () => window.BlueprintWizard.open({
      tenantId: selected.tenant_id,
      projectId: selected.project_id,
    }), 'primary'));
    // Legacy designers still use global permission discovery and are therefore
    // exposed only when their complete global permission set is available.
    if (window.ApplianceBlueprintUI && allowed('blueprints.create') && allowed('terraform.execute')) {
      actions.push(button('Importuj appliance OVA', () => window.ApplianceBlueprintUI.open().catch(error => toast(error.message, 'error'))));
    }
    if (window.BlueprintVRADesigner && allowed('blueprints.create')) {
      actions.push(button('Designer vRA / YAML', () => window.BlueprintVRADesigner.open()));
    }
  }

  const scopeSelect = selectField(
    'Projekt Blueprintów',
    'blueprint_scope_project',
    scopes.map(scope => ({
      value: String(scope.tenant_id) + '|' + String(scope.project_id),
      label: (scope.tenant_name || scope.tenant_id) + ' · ' + (scope.project_name || scope.project_id),
    })),
    String(selected.tenant_id) + '|' + String(selected.project_id),
    {
      wide: true,
      help: 'Lista i wszystkie operacje poniżej są wykonywane wyłącznie w wybranym tenant/projekcie.',
    }
  );
  scopeSelect.querySelector('select').addEventListener('change', event => {
    const [tenantId, projectId] = String(event.currentTarget.value).split('|');
    window.CloudportalBlueprintScope = { tenant_id: tenantId, project_id: projectId };
    blueprintsView().catch(error => toast(error.message, 'error'));
  });

  dom.content.replaceChildren(
    heading('Wersjonowane definicje self-service. DAG, formularz zmiennych i provisioning są wykonywane przez wspólną warstwę API.', actions),
    node('section', { class: 'panel' }, scopeSelect),
    table([
      { label: 'Blueprint', value: item => {
        const avatar = item.avatar_id ? avatarById.get(String(item.avatar_id)) : null;
        return node('div', { class: 'blueprint-list-name' },
          node('span', { class: 'blueprint-list-avatar', 'aria-hidden': 'true' },
            avatar?.data_uri
              ? node('img', { src: avatar.data_uri, alt: '', loading: 'lazy', decoding: 'async' })
              : appIcon('box')),
          node('div', {},
            node('strong', { text: item.name }),
            node('div', { class: 'mono muted', text: `${item.slug} · v${item.version}` })));
      } },
      { label: 'Status', value: item => badge(statusLabel(item.is_active ? 'active' : 'inactive'), item.is_active ? 'ok' : 'danger') },
      { label: 'Widoczność', value: item => Object.entries(item.visibility).filter(([, value]) => value).map(([key]) => ({ backend: 'Backend', cloudportal: 'CloudPortal', api: 'API' }[key] || key)).join(', ') || '—' },
      { label: 'Kroki', value: item => item.workflow.length },
      { label: 'Zarządzanie', value: item => (item.manager_role_ids || []).length
        ? (item.manager_role_ids || []).map(id => roleNames.get(Number(id)) || ('Rola #' + id)).join(', ')
        : badge('Bez roli dedykowanej', 'warning') },
      { label: 'Zasady', value: item => node('div', { class: 'row-actions' }, window.BlueprintApprovalPolicyUI.badgeFor(item), item.recovery_policy === 'destroy_on_failure' ? badge('Usuń po błędzie', 'danger') : badge('Zachowaj po błędzie', 'info')) },
      { label: 'Aktualizacja', value: item => formatDate(item.updated_at) },
    ], blueprints, item => {
      const result = [];
      const executionControl = window.BlueprintProvisioningGuards.executionControl(
        item,
        () => navigate(blueprintExecutionPath(item, selected)),
        scopeAllows
      );
      if (executionControl) result.push(executionControl);
      const canManage = canManageBlueprintByRole(item);
      if (scopeAllows('blueprints.update') && canManage && canDesignBlueprint) {
        if (window.BlueprintVRADesigner && allowed('blueprints.update')) {
          result.push(button('Designer vRA / YAML', () => window.BlueprintVRADesigner.open(item)));
        }
        result.push(button('Edytuj', () => window.BlueprintWizard.open({
          item,
          tenantId: selected.tenant_id,
          projectId: selected.project_id,
        })));
      }
      if (scopeAllows('blueprints.delete') && canManage) {
        result.push(button('Usuń', () => confirmAction(
          'Usuń Blueprint',
          `Definicja ${item.name} zostanie usunięta. Istniejące wdrożenia zachowają snapshot.`,
          async () => {
            await api(`/blueprints/${item.id}`, { method: 'DELETE', headers: scopeHeaders });
            toast('Blueprint usunięty.');
            await blueprintsView();
          }
        ), 'danger'));
      }
      return result;
    })
  );
}

async function executeBlueprint(item, scope = null) {
  const scopeHeaders = scope ? {
    'X-Tenant-ID': String(scope.tenant_id),
    'X-Project-ID': String(scope.project_id),
  } : {};
  const scopeAllows = permission =>
    allowed(permission) || (scope?.permissions || []).includes(permission);
  try {
    const fields = node('div', { class: 'form-grid' });
    const apmidContext = await window.BlueprintRuntimeApmid.prepare(item, fields, scopeHeaders);

    for (const [name, definition] of Object.entries(item.variables_schema || {})) {
      if (definition.type === 'select') {
        fields.append(selectField(definition.label || name, name, (definition.options || []).map(value => ({ value, label: value })), definition.default, { required: definition.required }));
      } else if (definition.type === 'boolean') {
        fields.append(checkboxField(definition.label || name, name, Boolean(definition.default)));
      } else {
        fields.append(field(definition.label || name, name, {
          type: definition.type === 'integer' ? 'number' : 'text',
          value: definition.default ?? '',
          min: definition.min,
          max: definition.max,
          required: definition.required,
        }));
      }
    }

    let scheme = null;
    if (item.deployment?.hostname_scheme_id && scopeAllows('hostnames.read')) {
      const result = await api('/hostname-schemes?limit=200', { headers: scopeHeaders });
      scheme = result.items.find(value => Number(value.id) === Number(item.deployment.hostname_scheme_id)) || null;
    }
    if (scheme) {
      const defaults = item.deployment?.hostname_values || {};
      const missingTokens = window.BlueprintRuntimeApmid.hostnameTokens(scheme.pattern)
        .filter(token => !['location', 'role'].includes(token))
        .filter(token => !defaults[token]);
      if (missingTokens.length) {
        const missingPattern = missingTokens.map(token => `{${token}}`).join('-');
        fields.append(formSection(
          'Nazwa hosta',
          `Wzorzec: ${scheme.pattern}. Pozostałe składniki są zapisane w Blueprintcie.`,
          window.BlueprintRuntimeApmid.hostnameValueFields(missingPattern),
        ));
      } else {
        fields.append(node('div', { class: 'field-help wide', text: `Nazwa hosta zostanie wygenerowana automatycznie według wzorca ${scheme.pattern}.` }));
      }
    } else if (item.deployment?.hostname_scheme_id) {
      fields.append(node('div', { class: 'field-help wide', text: 'Blueprint ma zapisany schemat nazwy hosta. Brak uprawnienia do odczytu schematu — zostaną użyte zapisane wartości domyślne.' }));
    }

    openModal({
      title: `Utwórz VM · ${item.name}`,
      eyebrow: `Produkt z Blueprintu v${item.version}`,
      body: fields,
      submitLabel: 'Utwórz VM',
      wide: true,
      onSubmit: async (_data, form) => {
        const variables = {};
        for (const [name, definition] of Object.entries(item.variables_schema || {})) {
          const control = form.elements[name];
          if (definition.type === 'boolean') variables[name] = control.checked;
          else if (control.value !== '') variables[name] = definition.type === 'integer' ? Number(control.value) : control.value;
        }
        const payload = {
          variables,
          hostname_values: window.BlueprintRuntimeApmid.readHostnameValues(form),
        };
        const runtimeClassification = window.BlueprintRuntimeApmid.read(form, apmidContext);
        if (apmidContext?.apmidSelectable && !runtimeClassification.apmid) {
          throw new Error('Wybierz APMID przed utworzeniem VM.');
        }
        if (apmidContext?.environmentSelectable && !runtimeClassification.environment) {
          throw new Error('Wybierz Environment przed utworzeniem VM.');
        }
        if (runtimeClassification.apmid) payload.apmid = runtimeClassification.apmid;
        if (runtimeClassification.environment) payload.environment = runtimeClassification.environment;

        const result = await api(`/blueprints/${item.id}/execute`, {
          method: 'POST',
          idempotent: true,
          body: payload,
          headers: scopeHeaders,
        });
        toast(`Utworzono „${result.name}”. VM jest już widoczna w „Moje zasoby”. Status i etap provisioningu będą aktualizowane automatycznie; jeśli Proxmox jest offline, zadanie wznowi się po odzyskaniu połączenia.`);
        navigate('my-resources');
      },
    });
  } catch (error) {
    toast(error.message, 'error');
  }
}

window.BlueprintsFeature = Object.freeze({
  canManage: canManageBlueprintByRole,
  execute: executeBlueprint,
  executionPath: blueprintExecutionPath,
});

registerCommand('blueprints.proxmoxTemplateWizard', item => item ? window.BlueprintWizard.open({ item }) : window.BlueprintWizard.open());
registerCommand('blueprints.proxmoxWithHostnameScheme', schemeId => window.BlueprintWizard.open({ hostnameSchemeId: schemeId }));
registerCommand('blueprints.execute', item => navigate(blueprintExecutionPath(item, window.CloudportalBlueprintScope || null)));
registerCommand('blueprints.create', () => window.BlueprintWizard.open({
  tenantId: window.CloudportalBlueprintScope?.tenant_id || '',
  projectId: window.CloudportalBlueprintScope?.project_id || '',
}));
registerView({ id: 'blueprints', label: 'Blueprinty', icon: 'B', permission: null, order: 70 }, blueprintsView);
})();
