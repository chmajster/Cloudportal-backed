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
  if (typeof item?.can_manage === 'boolean') return item.can_manage;
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

function blueprintDetailsPath(item, scope = null) {
  return '/blueprints/' + encodeURIComponent(item.id)
    + '/' + encodeURIComponent(item.slug || item.name || 'blueprint')
    + blueprintScopeQuery(scope);
}

function blueprintDetailsLink(item, scope = null) {
  const path = blueprintDetailsPath(item, scope);
  return node('a', {
    class: 'button blueprint-details-link',
    href: '#' + path,
    text: 'Szczegóły',
    title: 'Otwórz szczegóły Blueprintu',
    'aria-label': 'Szczegóły Blueprintu ' + item.name,
  });
}

async function toggleBlueprintEnabled(item, scopeHeaders) {
  const enabled = !item.is_active;
  try {
    await api(`/blueprints/${item.id}/enabled`, {
      method: 'PUT',
      body: { enabled },
      headers: { ...scopeHeaders, 'If-Match': String(item.version) },
    });
    toast(`${enabled ? 'Włączono' : 'Wyłączono'} Blueprint „${item.name}”.`);
    await blueprintsView();
  } catch (error) {
    toast(error.message, 'error');
  }
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
  const selected = scopes.find(scope => String(scope.project_id) === contextProjectId)
    || scopes.find(scope =>
      remembered
      && String(scope.tenant_id) === String(remembered.tenant_id)
      && String(scope.project_id) === String(remembered.project_id)
    )
    || scopes[0];

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
      help: 'Zmiana tego pola aktualizuje również globalny kontekst pracy w górnym pasku. Operacje są wykonywane wyłącznie w wybranym tenant/projekcie.',
    }
  );
  scopeSelect.querySelector('select').addEventListener('change', async event => {
    const [tenantId, projectId] = String(event.currentTarget.value).split('|');
    const chosen = scopes.find(scope =>
      String(scope.tenant_id) === String(tenantId)
      && String(scope.project_id) === String(projectId));
    window.CloudportalBlueprintScope = { tenant_id: tenantId, project_id: projectId };
    try {
      if (chosen && globalThis.CPProjectContext?.choose) {
        await globalThis.CPProjectContext.choose({
          id: chosen.project_id,
          tenant_id: chosen.tenant_id,
          name: chosen.project_name,
          slug: chosen.project_slug,
        }, () => viewIs('blueprints'), { notify: false });
      }
      await blueprintsView();
    } catch (error) {
      toast(error.message, 'error');
    }
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
        : badge('Bez roli zarządzającej', 'warning') },
      { label: 'Zasady', value: item => node('div', { class: 'row-actions' }, window.BlueprintApprovalPolicyUI.badgeFor(item), item.recovery_policy === 'destroy_on_failure' ? badge('Usuń po błędzie', 'danger') : badge('Zachowaj po błędzie', 'info')) },
      { label: 'Aktualizacja', value: item => formatDate(item.updated_at) },
    ], blueprints, item => {
      const result = [blueprintDetailsLink(item, selected)];
      const executionControl = window.BlueprintProvisioningGuards.executionControl(
        item,
        () => navigate(blueprintExecutionPath(item, selected)),
        scopeAllows
      );
      if (executionControl) result.push(executionControl);
      const canManage = canManageBlueprintByRole(item);
      if (scopeAllows('blueprints.update') && canManage) {
        result.push(button(
          item.is_active ? 'Wyłącz' : 'Włącz',
          () => toggleBlueprintEnabled(item, scopeHeaders),
          item.is_active ? 'danger' : 'primary'
        ));
      }
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
          `Definicja ${item.name} zostanie usunięta. Jeżeli z tego Blueprintu trwa provisioning, usunięcie trafi do kolejki i wykona się automatycznie po zakończeniu aktywnego zadania. Istniejące wdrożenia zachowają snapshot.`,
          async () => {
            const result = await api(`/blueprints/${item.id}`, { method: 'DELETE', headers: scopeHeaders });
            toast(result.queued
              ? 'Usunięcie Blueprintu dodane do kolejki. Wykona się po zakończeniu aktywnego provisioning.'
              : 'Blueprint usunięty.');
            navigate('blueprints');
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

    if (scopeAllows('availability.read') && scopeAllows('availability.assign')) {
      const availability = await api('/availability-plans?active_only=true&limit=200', { headers: scopeHeaders });
      const plans = availability.items || [];
      if (plans.length) {
        fields.append(formSection(
          'Dostępność VM',
          'Opcjonalnie wybierz Availability Plan. Po utworzeniu VM CloudPortal zastosuje rzeczywistą konfigurację Proxmox HA.',
          selectField('Availability Plan', 'availability_plan_id', [
            { value: '', label: 'Bez Availability Planu' },
            ...plans.map(plan => ({
              value: plan.id,
              label: plan.name + ' · ' + (plan.state || 'started')
                + (plan.group ? ' · grupa ' + plan.group : ''),
            })),
          ], ''),
        ));
      }
    }

    const placementMode = String(item.deployment?.placement_mode || 'FIXED').toUpperCase();
    if (['POOL', 'POLICY'].includes(placementMode)) {
      const poolLabel = placementMode === 'POOL'
        ? 'Pula: ' + (item.deployment?.resource_pool_id || '—')
        : 'Pula: wybierana przez Placement Rules';
      fields.append(formSection(
        'Placement',
        'Backend pobierze live capacity, zastosuje hard constraints, scoring i rezerwację zasobów.',
        node('div', { class: 'blueprint-execution-placement-summary wide' },
          badge(placementMode, 'info'),
          node('span', { text: poolLabel }),
          item.deployment?.logical_network
            ? node('span', { text: 'Network: ' + item.deployment.logical_network }) : null,
          item.deployment?.storage_class
            ? node('span', { text: 'Storage: ' + item.deployment.storage_class }) : null)
      ));

      if (scopeAllows('placement.override')) {
        const overrideToggle = checkboxField('Override placement', 'placement_override_enabled', false);
        const overridePanel = node('div', { class: 'form-grid wide', hidden: true });
        const providers = (await api('/providers?limit=200', { headers: scopeHeaders })).items || [];
        const providerField = searchableSelectField(
          'Platforma', 'placement_override_provider_id',
          providers.map(row => ({ value: row.id, label: row.name + ' · ' + row.type })),
          providers[0]?.id || '', { required: false, wide: true, placeholder: 'Wpisz nazwę platformy…' }
        );
        const nodeField = searchableSelectField(
          'Node', 'placement_override_node', [], '',
          { wide: true, placeholder: 'Wpisz nazwę node…' }
        );
        const storageField = searchableSelectField(
          'Storage', 'placement_override_storage', [], '',
          { wide: true, placeholder: 'Wpisz nazwę storage…' }
        );
        const networkField = searchableSelectField(
          'Network', 'placement_override_network', [], '',
          { wide: true, placeholder: 'Wpisz nazwę network…' }
        );
        const discovery = node('p', { class: 'field-help wide', text: 'Target będzie ponownie zweryfikowany przez backend i nadal musi spełniać hard constraints puli.' });
        overridePanel.append(providerField, nodeField, storageField, networkField, discovery);

        let generation = 0;
        const loadNodeResources = async (providerId, selectedNode = '') => {
          const current = ++generation;
          if (!providerId) return;
          try {
            const nodes = (await api('/providers/' + encodeURIComponent(providerId) + '/nodes', { headers: scopeHeaders })).items || [];
            if (current !== generation) return;
            nodeField.searchableSelect.setChoices(
              nodes.map(row => ({ value: row.node || row.id, label: row.node || row.id })),
              selectedNode || nodes[0]?.node || nodes[0]?.id || ''
            );
            const loadTargetResources = async nodeValue => {
              const token = ++generation;
              if (!nodeValue) {
                storageField.searchableSelect.setChoices([], '');
                networkField.searchableSelect.setChoices([], '');
                return;
              }
              const [storages, networks] = await Promise.all([
                api('/providers/' + encodeURIComponent(providerId) + '/storages?node=' + encodeURIComponent(nodeValue), { headers: scopeHeaders }),
                api('/providers/' + encodeURIComponent(providerId) + '/networks?node=' + encodeURIComponent(nodeValue), { headers: scopeHeaders }),
              ]);
              if (token !== generation) return;
              storageField.searchableSelect.setChoices(
                (storages.items || []).map(row => ({
                  value: row.storage || row.id,
                  label: (row.storage || row.id) + (row.content ? ' · ' + row.content : ''),
                })),
                ''
              );
              networkField.searchableSelect.setChoices(
                (networks.items || []).filter(row => row.iface || row.network || row.id).map(row => ({
                  value: row.iface || row.network || row.id,
                  label: (row.iface || row.network || row.id) + (row.type ? ' · ' + row.type : ''),
                })),
                ''
              );
            };
            nodeField.searchableSelect.onChange(value => loadTargetResources(value));
            await loadTargetResources(nodeField.searchableSelect.value());
          } catch (error) {
            discovery.textContent = 'Discovery override: ' + error.message;
          }
        };
        providerField.searchableSelect.onChange(value => loadNodeResources(value));
        overrideToggle.querySelector('input').addEventListener('change', async event => {
          overridePanel.hidden = !event.currentTarget.checked;
          if (event.currentTarget.checked) {
            await loadNodeResources(providerField.searchableSelect.value());
          }
        });
        fields.append(formSection(
          'Override placement',
          'Dostępny wyłącznie z permission placement.override. Nie omija hard constraints ani RBAC.',
          overrideToggle,
          overridePanel
        ));
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
        const availabilityPlanId = String(form.elements.availability_plan_id?.value || '').trim();
        if (availabilityPlanId) payload.availability_plan_id = availabilityPlanId;
        if (form.elements.placement_override_enabled?.checked) {
          const providerId = Number(form.elements.placement_override_provider_id?.value || 0);
          if (!providerId) throw new Error('Wybierz platformę dla override placement.');
          payload.placement_override = {
            provider_id: providerId,
            node: String(form.elements.placement_override_node?.value || '').trim() || null,
            storage: String(form.elements.placement_override_storage?.value || '').trim() || null,
            network: String(form.elements.placement_override_network?.value || '').trim() || null,
          };
        }

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
  detailsPath: blueprintDetailsPath,
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
