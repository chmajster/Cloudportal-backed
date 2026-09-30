'use strict';
(() => {
  const STEPS = [
    'Podstawowe informacje',
    'Kogo dotyczy',
    'Gdzie obowiązuje',
    'Warunki',
    'Dozwolone / zabronione akcje',
    'Limity i dozwolone wartości',
    'Approval',
    'Priorytet i konflikty',
    'Podsumowanie',
    'Test polityki',
    'Zapis',
  ];
  const STATUS_LABELS = {
    draft: 'Draft',
    dry_run: 'Dry-run',
    enforced: 'Aktywna',
    disabled: 'Wyłączona',
    archived: 'Archiwalna',
  };
  const EFFECT_LABELS = { allow: 'Pozwól', deny: 'Zabroń' };
  const OPERATOR_FALLBACK = {
    eq: 'jest równe', neq: 'nie jest równe', in: 'jest jednym z',
    not_in: 'nie jest jednym z', contains: 'zawiera', not_contains: 'nie zawiera',
    starts_with: 'zaczyna się od', ends_with: 'kończy się na',
    gt: 'większe niż', gte: 'większe lub równe', lt: 'mniejsze niż',
    lte: 'mniejsze lub równe', exists: 'istnieje', not_exists: 'nie istnieje',
  };
  let cache = null;
  let wizard = null;
  let listState = { search: '', status: '', type: '', section: 'policies' };
  let lastPolicies = [];
  const can = (...permissions) => permissions.some(permission => allowed(permission));
  const copy = value => JSON.parse(JSON.stringify(value ?? null));
  const optionalApi = (path, fallback, options) => api(path, options).catch(() => fallback);
  const asArray = value => Array.isArray(value) ? value : value == null || value === '' ? [] : [value];
  const unique = values => [...new Set(values.filter(value => value !== null && value !== undefined && value !== ''))];
  function policyHeaders(target, item = null) {
    let tenantId = String(target.tenantId || '');
    let projectId = String(target.projectId || '');
    if (item?.project_id) {
      tenantId = String(item.tenant_id || '');
      projectId = String(item.project_id || '');
    } else if (item?.tenant_id) {
      tenantId = String(item.tenant_id || '');
      if (!projectId) {
        projectId = String(cache?.scopes?.projects?.find(row => String(row.tenant_id) === tenantId)?.id || '');
      }
    } else if (target.scopeLevel === 'tenant' && tenantId && !projectId) {
      projectId = String(cache?.scopes?.projects?.find(row => String(row.tenant_id) === tenantId)?.id || '');
    }
    return tenantId && projectId ? { 'X-Tenant-ID': tenantId, 'X-Project-ID': projectId } : {};
  }
  async function loadData(force = false) {
    if (cache && !force) return cache;
    const [capabilities, scopes, templates, users, roles, providers, blueprints, terraformTemplates, resources] = await Promise.all([
      api('/policies/capabilities'),
      api('/policies/scopes'),
      api('/policies/templates'),
      can('users.read') ? optionalApi('/users?limit=200', { items: [] }) : Promise.resolve({ items: [] }),
      can('roles.read') ? optionalApi('/roles?limit=200', { items: [] }) : Promise.resolve({ items: [] }),
      can('providers.read') ? optionalApi('/providers?limit=200', { items: [] }) : Promise.resolve({ items: [] }),
      can('blueprints.read') ? optionalApi('/blueprints?limit=200', { items: [] }) : Promise.resolve({ items: [] }),
      optionalApi('/templates', { items: [] }),
      can('inventory.read') ? optionalApi('/inventory/vms?limit=200', { items: [] }) : Promise.resolve({ items: [] }),
    ]);
    cache = {
      capabilities,
      scopes,
      templates: templates.items || [],
      users: users.items || users || [],
      roles: roles.items || roles || [],
      providers: providers.items || providers || [],
      blueprints: blueprints.items || blueprints || [],
      terraformTemplates: terraformTemplates.items || terraformTemplates || [],
      resources: resources.items || resources || [],
      placement: { loaded: false, loading: false, networks: [], storages: [], nodes: [], templates: [] },
    };
    return cache;
  }
  function firstScope() {
    const project = cache?.scopes?.projects?.[0] || null;
    const tenant = cache?.scopes?.tenants?.[0] || null;
    return {
      scopeLevel: project ? 'project' : tenant ? 'tenant' : 'global',
      tenantId: String(project?.tenant_id || tenant?.id || ''),
      projectId: String(project?.id || ''),
    };
  }
  function blankLeaf() {
    return { field: 'resource.environment', operator: 'eq', value: 'dev' };
  }
  function emptyWizard() {
    const base = firstScope();
    return {
      mode: 'create', itemId: null, original: null, step: 0,
      name: '', description: '', status: 'draft', policyType: 'access',
      enforcement: 'hard', priority: 500,
      scopeLevel: base.scopeLevel, tenantId: base.tenantId, projectId: base.projectId,
      subjectMode: 'all', userIds: [], roleIds: [],
      apmids: [], environments: [], entities: [], providerIds: [], blueprintIds: [],
      resourceTypes: ['vm'], actions: [],
      condition: {},
      effect: 'allow',
      limits: {
        cpuMin: '', cpuMax: '', ramMin: '', ramMax: '', diskMin: '', diskMax: '',
        networks: [], storages: [], providers: [], nodes: [], templates: [],
        maxDisks: '', maxNics: '',
      },
      approval: {
        enabled: false, field: 'resource.memory_mb', operator: 'gt', value: 8192,
        approverType: 'project_admin',
      },
      preservedEffects: [],
      conflicts: null, impact: null, testResult: null,
      test: {
        userId: '', apmid: '', environment: 'dev', action: 'vm.create',
        cpu: 2, ramGb: 4, diskGb: 40, network: '', storage: '',
      },
    };
  }
  function limitEffectMap(effects) {
    const result = new Map();
    effects.filter(effect => effect.type === 'limit_value').forEach(effect => result.set(effect.field, effect));
    return result;
  }
  function wizardFromPolicy(item) {
    const base = blankWizard();
    const scope = item.scope || {};
    const effects = item.effects || [];
    const limits = limitEffectMap(effects);
    const cpu = limits.get('resource.cpu') || {};
    const ram = limits.get('resource.memory_mb') || {};
    const disk = limits.get('resource.disk_gb') || {};
    const networks = limits.get('resource.network') || {};
    const storages = limits.get('resource.storage') || {};
    const providers = limits.get('resource.provider_id') || {};
    const nodes = limits.get('resource.node') || {};
    const templates = limits.get('resource.template') || {};
    const maxDisks = limits.get('resource.additional_disks') || {};
    const maxNics = limits.get('resource.nic_count') || {};
    const approval = effects.find(effect => effect.type === 'require_approval') || null;
    const represented = new Set([
      'allow', 'deny', 'limit_value', 'require_approval',
    ]);
    const effect = effects.some(value => value.type === 'deny') ? 'deny' : 'allow';
    const subjectMode = scope.user_ids?.length ? 'users' : scope.role_ids?.length ? 'roles' : 'all';
    return {
      ...base,
      mode: item.id ? 'edit' : 'create',
      itemId: item.id || null,
      original: item.id ? copy(item) : null,
      name: item.name || '',
      description: item.description || '',
      status: item.status === 'archived' ? 'draft' : item.status || 'draft',
      policyType: item.policy_type || 'access',
      enforcement: item.enforcement || 'hard',
      priority: Number(item.priority ?? 500),
      scopeLevel: item.scope_level || base.scopeLevel,
      tenantId: String(item.tenant_id || scope.organization_ids?.[0] || base.tenantId),
      projectId: String(item.project_id || scope.project_ids?.[0] || base.projectId),
      subjectMode,
      userIds: (scope.user_ids || []).map(String),
      roleIds: (scope.role_ids || []).map(String),
      apmids: (scope.apmids || []).map(String),
      environments: (scope.environments || []).map(String),
      entities: (scope.entities || []).map(String),
      providerIds: (scope.provider_ids || []).map(String),
      blueprintIds: (scope.blueprint_ids || []).map(String),
      resourceTypes: (scope.resource_types || []).map(String),
      actions: (scope.actions || []).map(String),
      condition: copy(item.condition || {}),
      effect,
      limits: {
        cpuMin: cpu.min ?? '', cpuMax: cpu.max ?? '',
        ramMin: ram.min == null ? '' : Number(ram.min) / 1024,
        ramMax: ram.max == null ? '' : Number(ram.max) / 1024,
        diskMin: disk.min ?? '', diskMax: disk.max ?? '',
        networks: asArray(networks.allowed).map(String),
        storages: asArray(storages.allowed).map(String),
        providers: asArray(providers.allowed).map(String),
        nodes: asArray(nodes.allowed).map(String),
        templates: asArray(templates.allowed).map(String),
        maxDisks: maxDisks.max ?? '', maxNics: maxNics.max ?? '',
      },
      approval: {
        enabled: Boolean(approval),
        field: approval?.when?.field || 'resource.memory_mb',
        operator: approval?.when?.operator || 'gt',
        value: approval?.when?.value ?? 8192,
        approverType: approval?.approver?.type || 'project_admin',
      },
      preservedEffects: effects.filter(effect => !represented.has(effect.type)).map(copy),
      conflicts: null, impact: null, testResult: null,
    };
  }
  function tenantProjects(tenantId) {
    return (cache?.scopes?.projects || []).filter(row => String(row.tenant_id) === String(tenantId));
  }
  function classification(target = wizard) {
    if (target.scopeLevel === 'global') {
      const all = Object.values(cache?.scopes?.classifications || {});
      return {
        apmids: unique(all.flatMap(row => row?.apmids || [])),
        environments: Object.fromEntries(['dev', 'test', 'nonprod', 'prod'].map(name => [
          name, all.some(row => row?.environments?.[name] !== false),
        ])),
      };
    }
    return cache?.scopes?.classifications?.[String(target.tenantId)] || {
      apmids: [], environments: { dev: true, test: true, nonprod: true, prod: true },
    };
  }
  function entityOptions(target = wizard) {
    const cls = classification(target);
    const roles = cache?.capabilities?.entity_roles || [];
    const environments = Object.entries(cls.environments || {})
      .filter(([, enabled]) => enabled !== false)
      .map(([value]) => String(value).trim().toLowerCase())
      .filter(Boolean);
    const apmids = unique((cls.apmids || [])
      .map(value => String(value).trim().toUpperCase())
      .filter(Boolean));
    const options = [];
    const seen = new Set();
    for (const apmid of apmids) {
      for (const environment of environments) {
        for (const role of roles) {
          const roleId = String(role.id || '').trim().toLowerCase();
          if (!roleId) continue;
          const value = 'entity.' + apmid + '.' + environment + '.' + roleId;
          if (seen.has(value)) continue;
          seen.add(value);
          options.push({
            value,
            label: value + (role.label ? ' · ' + role.label : ''),
          });
        }
      }
    }
    for (const value of target.entities || []) {
      const normalized = String(value);
      if (!seen.has(normalized)) {
        seen.add(normalized);
        options.push({ value: normalized, label: normalized + ' · zapisane' });
      }
    }
    return options;
  }
  function option(value, label, selected = false) {
    return node('option', { value: String(value), text: label, selected });
  }
  function selectControl(label, options, value, onChange, config = {}) {
    const select = node('select', {
      disabled: config.disabled,
      onChange: event => onChange(event.currentTarget.value),
    }, ...(config.placeholder ? [option('', config.placeholder, value === '')] : []),
    ...options.map(row => option(row.value, row.label, String(row.value) === String(value))));
    return node('label', { class: 'policy-field' },
      node('span', { text: label }), select,
      config.help ? node('small', { text: config.help }) : null);
  }
  function textControl(label, value, onInput, config = {}) {
    const tag = config.multiline ? 'textarea' : 'input';
    const input = node(tag, {
      type: config.multiline ? null : (config.type || 'text'),
      value: value ?? '',
      min: config.min, max: config.max, step: config.step,
      placeholder: config.placeholder,
      onInput: event => onInput(event.currentTarget.value),
    });
    return node('label', { class: 'policy-field' },
      node('span', { text: label }), input,
      config.help ? node('small', { text: config.help }) : null);
  }
  function checkboxControl(label, checked, onChange, help = '') {
    return node('label', { class: 'policy-switch' },
      node('input', { type: 'checkbox', checked, onChange: event => onChange(event.currentTarget.checked) }),
      node('span', {}, node('strong', { text: label }), help ? node('small', { text: help }) : null));
  }
  function checklist(title, options, selected, onChange, config = {}) {
    const selectedSet = new Set((selected || []).map(String));
    const body = node('div', { class: 'policy-check-grid' },
      ...options.map(row => node('label', { class: 'policy-check' },
        node('input', {
          type: 'checkbox',
          value: String(row.value),
          checked: selectedSet.has(String(row.value)),
          onChange: event => {
            const next = new Set(selectedSet);
            event.currentTarget.checked ? next.add(String(row.value)) : next.delete(String(row.value));
            onChange([...next]);
          },
        }),
        node('span', { text: row.label })
      ))
    );
    return node('section', { class: 'policy-picker' },
      node('div', { class: 'policy-picker-title' },
        node('strong', { text: title }),
        config.selectAll && options.length ? button(
          selectedSet.size === options.length ? 'Odznacz wszystkie' : 'Zaznacz wszystkie',
          () => onChange(selectedSet.size === options.length ? [] : options.map(row => String(row.value))),
          'ghost'
        ) : null
      ),
      config.help ? node('p', { class: 'muted', text: config.help }) : null,
      options.length ? body : node('p', { class: 'muted', text: config.empty || 'Brak dostępnych wartości.' })
    );
  }
  function section(title, description, ...children) {
    return node('section', { class: 'policy-section' },
      node('div', { class: 'policy-section-head' },
        node('h3', { text: title }),
        description ? node('p', { class: 'muted', text: description }) : null),
      node('div', { class: 'policy-section-body' }, ...children));
  }
  function priorityPreset(value, label) {
    return button(value + ' — ' + label, () => {
      wizard.priority = Number(value);
      renderWizard();
    }, wizard.priority === Number(value) ? 'primary' : 'ghost');
  }
  function conditionField(path) {
    return cache.capabilities.condition_fields.find(field => field.path === path)
      || { path, label: path, type: 'text', operators: ['eq', 'neq'] };
  }
  function conditionOptions(field) {
    const cls = classification();
    if (field.type === 'environment') {
      return Object.entries(cls.environments || {}).filter(([, enabled]) => enabled !== false)
        .map(([value]) => ({ value, label: value.toUpperCase() }));
    }
    if (field.type === 'apmid') return (cls.apmids || []).map(value => ({ value, label: value }));
    if (field.type === 'user') return cache.users.map(row => ({ value: row.id, label: row.username || row.email || String(row.id) }));
    if (field.type === 'role_list') return cache.roles.map(row => ({ value: row.id, label: row.name || String(row.id) }));
    if (field.type === 'organization') return (cache.scopes.tenants || []).map(row => ({ value: row.id, label: row.name }));
    if (field.type === 'project') return (cache.scopes.projects || []).map(row => ({ value: row.id, label: row.name }));
    if (field.type === 'provider') return cache.providers.map(row => ({ value: row.id, label: row.name || row.endpoint || String(row.id) }));
    if (field.type === 'blueprint') return cache.blueprints.map(row => ({ value: row.id, label: row.name || row.slug || String(row.id) }));
    if (field.type === 'resource_type') return cache.capabilities.resource_types.map(value => ({ value, label: value }));
    if (field.type === 'action') return cache.capabilities.action_catalog.flatMap(group => group.actions);
    if (field.type === 'weekday') return ['monday','tuesday','wednesday','thursday','friday','saturday','sunday']
      .map(value => ({ value, label: value }));
    if (field.type === 'method') return ['GET','POST','PUT','PATCH','DELETE'].map(value => ({ value, label: value }));
    if (field.type === 'boolean') return [{ value: 'true', label: 'Tak' }, { value: 'false', label: 'Nie' }];
    if (field.type === 'network') return cache.placement.networks.map(value => ({ value, label: value }));
    if (field.type === 'storage') return cache.placement.storages.map(value => ({ value, label: value }));
    if (field.type === 'template') return [
      ...cache.placement.templates.map(value => ({ value, label: value })),
      ...cache.terraformTemplates.map(row => ({ value: row.id, label: row.name || row.id })),
    ];
    return [];
  }
  function conditionValueControl(leaf) {
    const meta = conditionField(leaf.field);
    const choices = conditionOptions(meta);
    const noValue = ['exists', 'not_exists'].includes(leaf.operator);
    if (noValue) return node('span', { class: 'muted policy-condition-no-value', text: 'bez wartości' });
    const multi = ['in', 'not_in', 'contains_any', 'contains_all'].includes(leaf.operator);
    if (choices.length) {
      const select = node('select', {
        multiple: multi || null,
        onChange: event => {
          if (multi) leaf.value = [...event.currentTarget.selectedOptions].map(item => item.value);
          else if (meta.type === 'boolean') leaf.value = event.currentTarget.value === 'true';
          else if (meta.type === 'number') leaf.value = Number(event.currentTarget.value);
          else leaf.value = event.currentTarget.value;
          persistWizard();
        },
      }, ...choices.map(row => option(
        row.value, row.label,
        multi ? asArray(leaf.value).map(String).includes(String(row.value)) : String(leaf.value) === String(row.value)
      )));
      return select;
    }
    return node('input', {
      type: meta.type === 'number' ? 'number' : meta.type === 'date' ? 'date' : 'text',
      value: Array.isArray(leaf.value) ? leaf.value.join(', ') : leaf.value ?? '',
      onInput: event => {
        const raw = event.currentTarget.value;
        leaf.value = multi
          ? raw.split(',').map(value => value.trim()).filter(Boolean)
          : meta.type === 'number' ? Number(raw) : raw;
        persistWizard();
      },
    });
  }
  function renderConditionNode(value, onRemove, depth = 0) {
    if (!value || typeof value !== 'object') return null;
    const logical = ['all', 'any', 'not'].find(key => Object.prototype.hasOwnProperty.call(value, key));
    if (logical) {
      const children = logical === 'not' ? [value.not] : value[logical];
      const group = node('div', { class: 'policy-condition-group', 'data-depth': depth },
        node('div', { class: 'policy-condition-group-head' },
          selectControl('Logika', [
            { value: 'all', label: 'WSZYSTKIE — AND' },
            { value: 'any', label: 'DOWOLNY — OR' },
            { value: 'not', label: 'NIE — NOT' },
          ], logical, next => {
            if (next === logical) return;
            if (next === 'not') wizard.condition = replaceConditionReference(wizard.condition, value, { not: children[0] || blankLeaf() });
            else wizard.condition = replaceConditionReference(wizard.condition, value, { [next]: children.filter(Boolean).length ? children.filter(Boolean) : [blankLeaf()] });
            renderWizard();
          }),
          onRemove ? button('Usuń grupę', onRemove, 'ghost') : null
        ),
        node('div', { class: 'policy-condition-children' },
          ...children.filter(Boolean).map(child => renderConditionNode(child, () => {
            if (logical === 'not') {
              wizard.condition = replaceConditionReference(wizard.condition, value, {});
            } else {
              value[logical] = value[logical].filter(item => item !== child);
              if (!value[logical].length) wizard.condition = replaceConditionReference(wizard.condition, value, {});
            }
            renderWizard();
          }, depth + 1)).filter(Boolean)
        ),
        logical !== 'not' ? node('div', { class: 'policy-condition-actions' },
          button('+ Warunek', () => { value[logical].push(blankLeaf()); renderWizard(); }, 'ghost'),
          button('+ Grupa AND', () => { value[logical].push({ all: [blankLeaf()] }); renderWizard(); }, 'ghost'),
          button('+ Grupa OR', () => { value[logical].push({ any: [blankLeaf()] }); renderWizard(); }, 'ghost')
        ) : null
      );
      return group;
    }
    if (!value.field) return null;
    const meta = conditionField(value.field);
    const operators = meta.operators || ['eq', 'neq'];
    return node('div', { class: 'policy-condition-row' },
      selectControl('Pole', cache.capabilities.condition_fields.map(field => ({
        value: field.path, label: field.group + ' / ' + field.label,
      })), value.field, next => {
        const nextMeta = conditionField(next);
        value.field = next;
        value.operator = nextMeta.operators?.[0] || 'eq';
        const options = conditionOptions(nextMeta);
        value.value = options[0]?.value ?? (nextMeta.type === 'number' ? 0 : '');
        renderWizard();
      }),
      selectControl('Operator', operators.map(operator => ({
        value: operator,
        label: cache.capabilities.operator_labels?.[operator] || OPERATOR_FALLBACK[operator] || operator,
      })), value.operator, next => { value.operator = next; renderWizard(); }),
      node('label', { class: 'policy-field' }, node('span', { text: 'Wartość' }), conditionValueControl(value)),
      button('Usuń', onRemove, 'ghost')
    );
  }
  function replaceConditionReference(root, needle, replacement) {
    if (root === needle) return replacement;
    if (!root || typeof root !== 'object') return root;
    for (const key of ['all', 'any']) {
      if (Array.isArray(root[key])) {
        root[key] = root[key].map(child => child === needle ? replacement : replaceConditionReference(child, needle, replacement));
      }
    }
    if (root.not) root.not = root.not === needle ? replacement : replaceConditionReference(root.not, needle, replacement);
    return root;
  }
  function renderConditionBuilder() {
    if (!wizard.condition || Object.keys(wizard.condition).length === 0) {
      return node('div', { class: 'policy-empty-builder' },
        node('p', { text: 'Brak dodatkowych warunków. Polityka będzie działać dla całego wybranego zakresu.' }),
        button('+ Dodaj warunek', () => { wizard.condition = { all: [blankLeaf()] }; renderWizard(); }, 'primary'),
        button('+ Dodaj grupę OR', () => { wizard.condition = { any: [blankLeaf(), blankLeaf()] }; renderWizard(); }, 'ghost')
      );
    }
    return renderConditionNode(wizard.condition, () => { wizard.condition = {}; renderWizard(); });
  }
  async function ensurePlacementCatalog() {
    if (cache.placement.loaded || cache.placement.loading) return;
    cache.placement.loading = true;
    const providers = cache.providers.filter(row =>
      !wizard.providerIds.length || wizard.providerIds.includes(String(row.id))
    ).slice(0, 5);
    const headers = policyHeaders(wizard, wizard.original);
    const networks = new Set(), storages = new Set(), nodes = new Set(), templates = new Set();
    for (const provider of providers) {
      if (provider.type !== 'proxmox') continue;
      const nodeResult = await optionalApi('/providers/' + encodeURIComponent(provider.id) + '/nodes', { items: [] }, { headers });
      const providerNodes = (nodeResult.items || []).slice(0, 8);
      providerNodes.forEach(row => nodes.add(String(row.node || row.name || row.id || '')));
      const templateResult = await optionalApi('/providers/' + encodeURIComponent(provider.id) + '/templates', { items: [] }, { headers });
      (templateResult.items || []).forEach(row => templates.add(String(row.name || row.vmid || row.id || '')));
      for (const row of providerNodes) {
        const nodeName = String(row.node || row.name || row.id || '');
        if (!nodeName) continue;
        const [storageResult, networkResult] = await Promise.all([
          optionalApi('/providers/' + encodeURIComponent(provider.id) + '/storages?node=' + encodeURIComponent(nodeName), { items: [] }, { headers }),
          optionalApi('/providers/' + encodeURIComponent(provider.id) + '/networks?node=' + encodeURIComponent(nodeName), { items: [] }, { headers }),
        ]);
        (storageResult.items || []).forEach(item => storages.add(String(item.storage || item.id || '')));
        (networkResult.items || []).forEach(item => networks.add(String(item.iface || item.id || '')));
      }
    }
    cache.placement.networks = [...networks].filter(Boolean).sort();
    cache.placement.storages = [...storages].filter(Boolean).sort();
    cache.placement.nodes = [...nodes].filter(Boolean).sort();
    cache.placement.templates = [...templates].filter(Boolean).sort();
    cache.placement.loading = false;
    cache.placement.loaded = true;
  }
  function inputNumber(label, key, suffix = '') {
    return textControl(label + (suffix ? ' (' + suffix + ')' : ''), wizard.limits[key], value => {
      wizard.limits[key] = value === '' ? '' : Number(value);
      persistWizard();
    }, { type: 'number', min: 0 });
  }
  function renderBasics() {
    return section('Podstawowe informacje', 'Nadaj nazwę i określ zachowanie polityki.',
      node('div', { class: 'policy-form-grid' },
        textControl('Nazwa', wizard.name, value => { wizard.name = value; persistWizard(); }),
        selectControl('Typ polityki', cache.capabilities.policy_types.map(value => ({ value, label: value })), wizard.policyType, value => { wizard.policyType = value; persistWizard(); }),
        selectControl('Status', ['draft','dry_run','enforced','disabled'].map(value => ({ value, label: STATUS_LABELS[value] || value })), wizard.status, value => { wizard.status = value; persistWizard(); }),
        selectControl('Enforcement', [
          { value: 'hard', label: 'HARD — egzekwuj' },
          { value: 'soft', label: 'SOFT' },
          { value: 'advisory', label: 'ADVISORY — tylko ostrzegaj' },
        ], wizard.enforcement, value => { wizard.enforcement = value; persistWizard(); }),
        textControl('Opis', wizard.description, value => { wizard.description = value; persistWizard(); }, { multiline: true })
      )
    );
  }
  function renderSubjects() {
    return section('Kogo dotyczy', 'Wybierz odbiorców polityki bez wpisywania identyfikatorów.',
      selectControl('Ta polityka dotyczy', [
        { value: 'all', label: 'Wszystkich użytkowników w zakresie' },
        { value: 'users', label: 'Wybranych użytkowników' },
        { value: 'roles', label: 'Wybranych ról' },
      ], wizard.subjectMode, value => { wizard.subjectMode = value; renderWizard(); }),
      wizard.subjectMode === 'users' ? checklist('Użytkownicy', cache.users.map(row => ({
        value: row.id, label: row.username || row.email || String(row.id),
      })), wizard.userIds, values => { wizard.userIds = values; renderWizard(); }, { selectAll: true }) : null,
      wizard.subjectMode === 'roles' ? checklist('Role', cache.roles.map(row => ({
        value: row.id, label: row.name || String(row.id),
      })), wizard.roleIds, values => { wizard.roleIds = values; renderWizard(); }, { selectAll: true }) : null
    );
  }
  function renderScope() {
    const tenantRows = cache.scopes.tenants || [];
    const projectRows = tenantProjects(wizard.tenantId);
    const cls = classification();
    const envOptions = Object.entries(cls.environments || {}).filter(([, enabled]) => enabled !== false)
      .map(([value]) => ({ value, label: value.toUpperCase() }));
    return section('Gdzie obowiązuje', 'Scope jest wybierany z realnych organizacji, projektów i klasyfikacji CloudPortal.',
      node('div', { class: 'policy-form-grid' },
        selectControl('Poziom zakresu', [
          ...(cache.scopes.global_allowed ? [{ value: 'global', label: 'Global — wszystkie organizacje' }] : []),
          { value: 'tenant', label: 'Organizacja' },
          { value: 'project', label: 'Projekt' },
        ], wizard.scopeLevel, value => {
          wizard.scopeLevel = value;
          if (value !== 'global' && !wizard.tenantId) wizard.tenantId = String(tenantRows[0]?.id || '');
          if (value === 'project' && !wizard.projectId) wizard.projectId = String(tenantProjects(wizard.tenantId)[0]?.id || '');
          cache.placement.loaded = false;
          renderWizard();
        }),
        wizard.scopeLevel !== 'global' ? selectControl('Organizacja', tenantRows.map(row => ({
          value: row.id, label: row.name || row.slug || String(row.id),
        })), wizard.tenantId, value => {
          wizard.tenantId = value;
          wizard.projectId = String(tenantProjects(value)[0]?.id || '');
          wizard.apmids = []; wizard.environments = []; wizard.entities = [];
          cache.placement.loaded = false;
          renderWizard();
        }) : null,
        wizard.scopeLevel === 'project' ? selectControl('Projekt', projectRows.map(row => ({
          value: row.id, label: row.name || row.slug || String(row.id),
        })), wizard.projectId, value => { wizard.projectId = value; cache.placement.loaded = false; renderWizard(); }) : null
      ),
      checklist('APMID', (cls.apmids || []).map(value => ({ value, label: value })), wizard.apmids,
        values => { wizard.apmids = values; renderWizard(); }, { selectAll: true, help: 'Brak zaznaczenia oznacza wszystkie APMID w tym zakresie.' }),
      checklist('Environment', envOptions, wizard.environments,
        values => { wizard.environments = values; renderWizard(); }, { selectAll: true, help: 'Brak zaznaczenia oznacza wszystkie Environment.' }),
      checklist('Entity', entityOptions(), wizard.entities,
        values => { wizard.entities = values; renderWizard(); }, {
          selectAll: true,
          help: 'Format entity.<APMID>.<env>.<role>. Entity jest wymiarem Policy Engine i nie zastępuje RBAC.',
          empty: 'Brak Entity. Dodaj APMID lub włącz Environment w klasyfikacji.',
        }),
      checklist('Platformy', cache.providers.map(row => ({ value: row.id, label: row.name || row.endpoint || String(row.id) })),
        wizard.providerIds, values => { wizard.providerIds = values; cache.placement.loaded = false; renderWizard(); }, { selectAll: true }),
      checklist('Blueprinty', cache.blueprints.map(row => ({ value: row.id, label: row.name || row.slug || String(row.id) })),
        wizard.blueprintIds, values => { wizard.blueprintIds = values; renderWizard(); }, { selectAll: true }),
      checklist('Typy zasobów', cache.capabilities.resource_types.map(value => ({ value, label: value })),
        wizard.resourceTypes, values => { wizard.resourceTypes = values; renderWizard(); }, { selectAll: true })
    );
  }
  function renderConditions() {
    return section('No-code Condition Builder', 'Buduj zagnieżdżone AND / OR / NOT. Kod, regex i JSON nie są wymagane.',
      renderConditionBuilder());
  }
  function renderActions() {
    return section('Dozwolone / zabronione akcje', 'Efekt i akcje są wybierane checkboxami.',
      node('div', { class: 'policy-effect-choice' },
        selectControl('Efekt', [
          { value: 'allow', label: 'Pozwól' },
          { value: 'deny', label: 'Zabroń' },
        ], wizard.effect, value => { wizard.effect = value; renderWizard(); })
      ),
      ...cache.capabilities.action_catalog.map(group => checklist(
        group.category, group.actions, wizard.actions,
        values => {
          const groupValues = new Set(group.actions.map(row => String(row.value)));
          wizard.actions = wizard.actions.filter(value => !groupValues.has(String(value))).concat(values);
          wizard.actions = unique(wizard.actions);
          renderWizard();
        }, { selectAll: true }
      ))
    );
  }
  function renderLimits() {
    if (!cache.placement.loaded && !cache.placement.loading) {
      ensurePlacementCatalog().then(() => { if (wizard?.step === 5) renderWizard(); });
    }
    return section('Limity i dozwolone wartości', 'Ograniczenia są egzekwowane przez backend Policy Engine.',
      node('div', { class: 'policy-limit-grid' },
        inputNumber('Min CPU', 'cpuMin'),
        inputNumber('Max CPU', 'cpuMax'),
        inputNumber('Min RAM', 'ramMin', 'GB'),
        inputNumber('Max RAM', 'ramMax', 'GB'),
        inputNumber('Min dysk', 'diskMin', 'GB'),
        inputNumber('Max dysk', 'diskMax', 'GB'),
        inputNumber('Maks. dodatkowych dysków', 'maxDisks'),
        inputNumber('Maks. NIC', 'maxNics')
      ),
      cache.placement.loading ? node('p', { class: 'muted', text: 'Odczytywanie sieci, storage i node z dostępnych platform…' }) : null,
      checklist('Dozwolone sieci', cache.placement.networks.map(value => ({ value, label: value })), wizard.limits.networks,
        values => { wizard.limits.networks = values; renderWizard(); }, { selectAll: true }),
      checklist('Dozwolone storage', cache.placement.storages.map(value => ({ value, label: value })), wizard.limits.storages,
        values => { wizard.limits.storages = values; renderWizard(); }, { selectAll: true }),
      checklist('Dozwolone platformy', cache.providers.map(row => ({ value: row.id, label: row.name || String(row.id) })), wizard.limits.providers,
        values => { wizard.limits.providers = values; renderWizard(); }, { selectAll: true }),
      checklist('Dozwolone node', cache.placement.nodes.map(value => ({ value, label: value })), wizard.limits.nodes,
        values => { wizard.limits.nodes = values; renderWizard(); }, { selectAll: true }),
      checklist('Dozwolone template', unique([
        ...cache.placement.templates,
        ...cache.terraformTemplates.map(row => String(row.id)),
      ]).map(value => ({ value, label: value })), wizard.limits.templates,
        values => { wizard.limits.templates = values; renderWizard(); }, { selectAll: true })
    );
  }
  function renderApproval() {
    const meta = conditionField(wizard.approval.field);
    return section('Approval', 'Approval może być wymagany tylko po spełnieniu wskazanego warunku.',
      checkboxControl('Wymagaj zatwierdzenia', wizard.approval.enabled, value => {
        wizard.approval.enabled = value; renderWizard();
      }, 'Zamiast natychmiastowej operacji powstanie approval request.'),
      wizard.approval.enabled ? node('div', { class: 'policy-form-grid' },
        selectControl('Pole', cache.capabilities.condition_fields.map(field => ({
          value: field.path, label: field.group + ' / ' + field.label,
        })), wizard.approval.field, value => {
          wizard.approval.field = value;
          wizard.approval.operator = conditionField(value).operators?.[0] || 'eq';
          wizard.approval.value = conditionOptions(conditionField(value))[0]?.value ?? '';
          renderWizard();
        }),
        selectControl('Operator', (meta.operators || ['eq']).map(value => ({
          value, label: cache.capabilities.operator_labels?.[value] || OPERATOR_FALLBACK[value] || value,
        })), wizard.approval.operator, value => { wizard.approval.operator = value; renderWizard(); }),
        textControl('Wartość', wizard.approval.value, value => {
          const field = conditionField(wizard.approval.field);
          wizard.approval.value = field.type === 'number' ? Number(value) : value;
          persistWizard();
        }, { type: meta.type === 'number' ? 'number' : 'text' }),
        selectControl('Kto zatwierdza', [
          { value: 'project_admin', label: 'Administrator projektu' },
          { value: 'tenant_admin', label: 'Administrator organizacji' },
          { value: 'apmid_owner', label: 'Właściciel APMID' },
          { value: 'any_approver', label: 'Dowolny approver' },
        ], wizard.approval.approverType, value => { wizard.approval.approverType = value; persistWizard(); })
      ) : null
    );
  }
  async function checkConflicts() {
    const payload = buildPayload();
    wizard.conflicts = await api('/policies/conflicts', {
      method: 'POST',
      headers: policyHeaders(wizard, wizard.original),
      body: { policy: payload, exclude_policy_id: wizard.itemId },
    });
    renderWizard();
  }
  function renderConflicts() {
    const conflicts = wizard.conflicts?.items || [];
    return section('Priorytet i konflikty', 'Wyższy numer ma wyższy priorytet. Conflict detector wyjaśnia wynik.',
      node('div', { class: 'policy-form-grid' },
        textControl('Priorytet', wizard.priority, value => { wizard.priority = Number(value); persistWizard(); }, { type: 'number', min: 1, max: 1000 }),
        node('div', { class: 'policy-priority-presets' },
          ...(cache.capabilities.priority_presets || []).map(row => priorityPreset(row.value, row.label)))
      ),
      node('div', { class: 'policy-conflict-toolbar' },
        button('Sprawdź konflikty', () => checkConflicts().catch(error => toast(error.message, 'error')), 'primary')
      ),
      wizard.conflicts ? (
        conflicts.length ? node('div', { class: 'policy-conflict-list' },
          ...conflicts.map(item => node('article', { class: 'policy-conflict-card' },
            node('strong', { text: item.name }),
            node('span', { class: 'muted', text: 'Priorytet: ' + item.priority + ' · ' + item.status }),
            ...item.conflicts.map(conflict => node('p', {
              text: (conflict.severity === 'conflict' ? 'Konflikt: ' : 'Możliwe nakładanie: ') + conflict.reason,
            }))
          ))
        ) : node('div', { class: 'policy-ok-box', text: 'Nie wykryto konfliktów z widocznymi aktywnymi politykami.' })
      ) : node('p', { class: 'muted', text: 'Uruchom detektor przed aktywacją polityki.' })
    );
  }
  function humanCondition(value) {
    if (!value || !Object.keys(value).length) return 'brak dodatkowych warunków';
    if (value.all) return '(' + value.all.map(humanCondition).join(' ORAZ ') + ')';
    if (value.any) return '(' + value.any.map(humanCondition).join(' ALBO ') + ')';
    if (value.not) return 'NIE ' + humanCondition(value.not);
    const field = conditionField(value.field);
    return field.label + ' ' + (cache.capabilities.operator_labels?.[value.operator] || value.operator) + ' ' +
      (Array.isArray(value.value) ? value.value.join(', ') : String(value.value ?? ''));
  }
  function localSummary() {
    const chunks = [];
    chunks.push((wizard.effect === 'deny' ? 'Zabrania' : 'Pozwala') + ' wykonywać: ' +
      (wizard.actions.length ? wizard.actions.join(', ') : 'wybrane operacje') + '.');
    if (wizard.environments.length) chunks.push('Environment: ' + wizard.environments.map(value => value.toUpperCase()).join(', ') + '.');
    if (wizard.apmids.length) chunks.push('APMID: ' + wizard.apmids.join(', ') + '.');
    if (wizard.entities.length) chunks.push('Entity: ' + wizard.entities.join(', ') + '.');
    const limits = [];
    if (wizard.limits.cpuMax !== '') limits.push('CPU ≤ ' + wizard.limits.cpuMax);
    if (wizard.limits.ramMax !== '') limits.push('RAM ≤ ' + wizard.limits.ramMax + ' GB');
    if (wizard.limits.diskMax !== '') limits.push('Dysk ≤ ' + wizard.limits.diskMax + ' GB');
    if (limits.length) chunks.push('Limity: ' + limits.join(', ') + '.');
    if (wizard.limits.networks.length) chunks.push('Sieci: ' + wizard.limits.networks.join(', ') + '.');
    if (wizard.limits.storages.length) chunks.push('Storage: ' + wizard.limits.storages.join(', ') + '.');
    if (wizard.approval.enabled) chunks.push('Po spełnieniu warunku approval operacja wymaga zatwierdzenia.');
    return chunks.join(' ');
  }
  function renderSummary() {
    return section('Podsumowanie', 'Deterministyczny opis polityki przed zapisem.',
      node('div', { class: 'policy-natural-language' },
        node('h3', { text: wizard.name || 'Nowa polityka' }),
        node('p', { text: localSummary() }),
        node('dl', { class: 'policy-summary-grid' },
          node('div', {}, node('dt', { text: 'Kogo dotyczy' }), node('dd', { text: wizard.subjectMode === 'all' ? 'Wszyscy' : wizard.subjectMode === 'users' ? wizard.userIds.length + ' użytkowników' : wizard.roleIds.length + ' ról' })),
          node('div', {}, node('dt', { text: 'Scope' }), node('dd', { text: wizard.scopeLevel + ' / ' + (wizard.tenantId || 'global') + (wizard.projectId ? ' / ' + wizard.projectId : '') })),
          node('div', {}, node('dt', { text: 'Warunki' }), node('dd', { text: humanCondition(wizard.condition) })),
          node('div', {}, node('dt', { text: 'Priorytet' }), node('dd', { text: String(wizard.priority) }))
        )
      )
    );
  }
  function testContext() {
    const selectedProject = (cache.scopes.projects || []).find(row => String(row.id) === String(wizard.projectId));
    const selectedTenant = (cache.scopes.tenants || []).find(row => String(row.id) === String(wizard.tenantId));
    return {
      action: wizard.test.action || 'vm.create',
      resource_type: wizard.resourceTypes[0] || 'vm',
      context: {
        actor: { id: wizard.test.userId ? Number(wizard.test.userId) : state.identity?.user?.id },
        scope: {
          organization: selectedTenant?.name,
          project: selectedProject?.name,
        },
        resource: {
          apmid: wizard.test.apmid || null,
          environment: wizard.test.environment || null,
          cpu: Number(wizard.test.cpu || 0),
          memory_mb: Number(wizard.test.ramGb || 0) * 1024,
          disk_gb: Number(wizard.test.diskGb || 0),
          network: wizard.test.network || null,
          storage: wizard.test.storage || null,
        },
      },
    };
  }
  async function runTest() {
    wizard.testResult = await api('/policies/test', {
      method: 'POST',
      headers: policyHeaders(wizard, wizard.original),
      body: {
        policy: buildPayload(),
        evaluation: testContext(),
        exclude_policy_id: wizard.itemId,
        include_existing: true,
      },
    });
    renderWizard();
  }
  function renderTest() {
    const cls = classification();
    const envs = Object.entries(cls.environments || {}).filter(([, enabled]) => enabled !== false)
      .map(([value]) => ({ value, label: value.toUpperCase() }));
    const result = wizard.testResult;
    return section('Test polityki', 'Sprawdź konkretny przypadek bez wykonywania operacji.',
      node('div', { class: 'policy-form-grid' },
        selectControl('Użytkownik', [{ value: '', label: 'Bieżący użytkownik' }, ...cache.users.map(row => ({
          value: row.id, label: row.username || row.email || String(row.id),
        }))], wizard.test.userId, value => { wizard.test.userId = value; persistWizard(); }),
        selectControl('Environment', [{ value: '', label: 'Brak' }, ...envs], wizard.test.environment, value => { wizard.test.environment = value; persistWizard(); }),
        selectControl('APMID', [{ value: '', label: 'Brak' }, ...(cls.apmids || []).map(value => ({ value, label: value }))], wizard.test.apmid, value => { wizard.test.apmid = value; persistWizard(); }),
        selectControl('Akcja', cache.capabilities.action_catalog.flatMap(group => group.actions), wizard.test.action, value => { wizard.test.action = value; persistWizard(); }),
        textControl('CPU', wizard.test.cpu, value => { wizard.test.cpu = Number(value); persistWizard(); }, { type: 'number', min: 0 }),
        textControl('RAM (GB)', wizard.test.ramGb, value => { wizard.test.ramGb = Number(value); persistWizard(); }, { type: 'number', min: 0 }),
        textControl('Dysk (GB)', wizard.test.diskGb, value => { wizard.test.diskGb = Number(value); persistWizard(); }, { type: 'number', min: 0 }),
        selectControl('Sieć', [{ value: '', label: 'Brak' }, ...cache.placement.networks.map(value => ({ value, label: value }))], wizard.test.network, value => { wizard.test.network = value; persistWizard(); }),
        selectControl('Storage', [{ value: '', label: 'Brak' }, ...cache.placement.storages.map(value => ({ value, label: value }))], wizard.test.storage, value => { wizard.test.storage = value; persistWizard(); })
      ),
      button('Sprawdź', () => runTest().catch(error => toast(error.message, 'error')), 'primary'),
      result ? node('div', { class: 'policy-test-result policy-test-' + result.decision },
        node('strong', { text: result.decision === 'allow' ? 'DOZWOLONE' : result.decision === 'deny' ? 'ZABLOKOWANE' : 'WYMAGA ZATWIERDZENIA' }),
        node('p', { text: result.summary || localSummary() }),
        result.violations?.length ? node('ul', {}, ...result.violations.map(item => node('li', { text: item.message || item.type }))) : null,
        result.approvals?.length ? node('p', { text: 'Approval: ' + result.approvals.length }) : null,
        node('p', { class: 'muted', text: 'Dopasowane polityki: ' + (result.matched_policy_ids || []).join(', ') })
      ) : null
    );
  }
  async function simulateImpact() {
    wizard.impact = await api('/policies/simulate-impact', {
      method: 'POST',
      headers: policyHeaders(wizard, wizard.original),
      body: { policy: buildPayload(), exclude_policy_id: wizard.itemId, limit: 500 },
    });
    renderWizard();
  }
  function renderSave() {
    const impact = wizard.impact;
    return section('Zapis', 'Przed zapisem możesz sprawdzić wpływ na istniejące zasoby. Symulacja niczego nie modyfikuje.',
      node('div', { class: 'policy-save-summary' },
        node('strong', { text: wizard.name || 'Nowa polityka' }),
        node('p', { text: localSummary() })),
      button('Symuluj wpływ', () => simulateImpact().catch(error => toast(error.message, 'error')), 'ghost'),
      impact ? node('div', { class: 'policy-impact-grid' },
        info('Przeskanowano', impact.scanned_resources),
        info('Objęte polityką', impact.affected_resources),
        info('Non-compliant', impact.non_compliant_resources),
        info('Wymaga approval', impact.approval_resources)
      ) : null,
      impact?.samples?.length ? table([
        { label: 'Zasób', value: row => row.name },
        { label: 'Typ', value: row => row.resource_type },
        { label: 'Wynik', value: row => badge(row.status, row.status === 'deny' ? 'danger' : row.status === 'approval_required' ? 'warning' : 'ok') },
      ], impact.samples) : null
    );
  }
  function numeric(value) {
    return value === '' || value === null || value === undefined ? null : Number(value);
  }
  function buildPayload() {
    const scope = {};
    if (wizard.subjectMode === 'users' && wizard.userIds.length) scope.user_ids = wizard.userIds.map(value => Number(value));
    if (wizard.subjectMode === 'roles' && wizard.roleIds.length) scope.role_ids = wizard.roleIds.map(value => Number(value));
    if (wizard.scopeLevel !== 'global' && wizard.tenantId) scope.organization_ids = [wizard.tenantId];
    if (wizard.scopeLevel === 'project' && wizard.projectId) scope.project_ids = [wizard.projectId];
    if (wizard.apmids.length) scope.apmids = [...wizard.apmids];
    if (wizard.environments.length) scope.environments = [...wizard.environments];
    if (wizard.entities.length) scope.entities = [...wizard.entities];
    if (wizard.providerIds.length) scope.provider_ids = wizard.providerIds.map(value => /^\d+$/.test(value) ? Number(value) : value);
    if (wizard.blueprintIds.length) scope.blueprint_ids = wizard.blueprintIds.map(value => /^\d+$/.test(value) ? Number(value) : value);
    if (wizard.resourceTypes.length) scope.resource_types = [...wizard.resourceTypes];
    if (wizard.actions.length) scope.actions = [...wizard.actions];
    const effects = [{
      type: wizard.effect,
      ...(wizard.effect === 'allow' && wizard.policyType === 'access' ? { mode: 'whitelist' } : {}),
    }];
    const addLimit = (field, min, max, allowedValues) => {
      const effect = { type: 'limit_value', field };
      if (min !== null) effect.min = min;
      if (max !== null) effect.max = max;
      if (allowedValues?.length) effect.allowed = allowedValues;
      if ('min' in effect || 'max' in effect || 'allowed' in effect) effects.push(effect);
    };
    addLimit('resource.cpu', numeric(wizard.limits.cpuMin), numeric(wizard.limits.cpuMax));
    addLimit('resource.memory_mb',
      numeric(wizard.limits.ramMin) === null ? null : numeric(wizard.limits.ramMin) * 1024,
      numeric(wizard.limits.ramMax) === null ? null : numeric(wizard.limits.ramMax) * 1024);
    addLimit('resource.disk_gb', numeric(wizard.limits.diskMin), numeric(wizard.limits.diskMax));
    addLimit('resource.additional_disks', null, numeric(wizard.limits.maxDisks));
    addLimit('resource.nic_count', null, numeric(wizard.limits.maxNics));
    addLimit('resource.network', null, null, wizard.limits.networks);
    addLimit('resource.storage', null, null, wizard.limits.storages);
    addLimit('resource.provider_id', null, null, wizard.limits.providers.map(value => /^\d+$/.test(value) ? Number(value) : value));
    addLimit('resource.node', null, null, wizard.limits.nodes);
    addLimit('resource.template', null, null, wizard.limits.templates);
    if (wizard.approval.enabled) {
      effects.push({
        type: 'require_approval',
        approver: { type: wizard.approval.approverType },
        when: {
          field: wizard.approval.field,
          operator: wizard.approval.operator,
          value: wizard.approval.value,
        },
      });
    }
    effects.push(...wizard.preservedEffects.map(copy));
    return {
      name: wizard.name.trim(),
      description: wizard.description,
      policy_type: wizard.policyType,
      priority: Number(wizard.priority),
      enforcement: wizard.enforcement,
      status: wizard.status,
      scope_level: wizard.scopeLevel,
      scope,
      condition: copy(wizard.condition || {}),
      effects,
    };
  }
  function validateCurrentStep() {
    if (wizard.step === 0 && !wizard.name.trim()) return 'Podaj nazwę polityki.';
    if (wizard.step === 1 && wizard.subjectMode === 'users' && !wizard.userIds.length) return 'Wybierz co najmniej jednego użytkownika.';
    if (wizard.step === 1 && wizard.subjectMode === 'roles' && !wizard.roleIds.length) return 'Wybierz co najmniej jedną rolę.';
    if (wizard.step === 2 && wizard.scopeLevel !== 'global' && !wizard.tenantId) return 'Wybierz organizację.';
    if (wizard.step === 2 && wizard.scopeLevel === 'project' && !wizard.projectId) return 'Wybierz projekt.';
    if (wizard.step === 2 && !wizard.resourceTypes.length) return 'Wybierz co najmniej jeden typ zasobu.';
    if (wizard.step === 4 && !wizard.actions.length) return 'Wybierz co najmniej jedną akcję.';
    if (wizard.step === 5) {
      for (const [minKey, maxKey, label] of [['cpuMin','cpuMax','CPU'],['ramMin','ramMax','RAM'],['diskMin','diskMax','dysk']]) {
        const min = numeric(wizard.limits[minKey]), max = numeric(wizard.limits[maxKey]);
        if (min !== null && max !== null && min > max) return 'Minimum ' + label + ' nie może być większe niż maksimum.';
      }
    }
    return '';
  }
  function persistWizard() {
    if (!wizard) return;
    const key = wizard.mode === 'edit' ? 'policy-wizard-edit-' + wizard.itemId : 'policy-wizard-create';
    try { sessionStorage.setItem(key, JSON.stringify(wizard)); } catch {}
  }
  function clearWizardStorage() {
    try {
      sessionStorage.removeItem('policy-wizard-create');
      if (wizard?.itemId) sessionStorage.removeItem('policy-wizard-edit-' + wizard.itemId);
    } catch {}
  }
  function wizardPath(step = wizard.step) {
    const number = Number(step) + 1;
    return wizard.mode === 'edit'
      ? '/access/policies/edit/' + encodeURIComponent(wizard.itemId) + '/step/' + number
      : '/access/policies/new/step/' + number;
  }
  function stepBody() {
    return [
      renderBasics, renderSubjects, renderScope, renderConditions, renderActions,
      renderLimits, renderApproval, renderConflicts, renderSummary, renderTest, renderSave,
    ][wizard.step]();
  }
  function renderWizard() {
    persistWizard();
    const progress = node('div', { class: 'policy-wizard-progress' },
      ...STEPS.map((label, index) => node('button', {
        type: 'button',
        class: 'policy-wizard-step ' + (index === wizard.step ? 'active' : index < wizard.step ? 'complete' : ''),
        onClick: () => {
          const error = index > wizard.step ? validateCurrentStep() : '';
          if (error) return toast(error, 'error');
          wizard.step = index;
          navigate(wizardPath(index));
        },
      }, node('span', { text: String(index + 1) }), node('small', { text: label })))
    );
    const footer = node('div', { class: 'policy-wizard-footer' },
      wizard.step > 0 ? button('Wstecz', () => { wizard.step--; navigate(wizardPath()); }, 'ghost') : button('Anuluj', () => { clearWizardStorage(); wizard = null; navigate('policies'); }, 'ghost'),
      node('span', { class: 'muted', text: 'Krok ' + (wizard.step + 1) + ' z ' + STEPS.length }),
      wizard.step < STEPS.length - 1
        ? button('Dalej', () => {
            const error = validateCurrentStep();
            if (error) return toast(error, 'error');
            wizard.step++;
            navigate(wizardPath());
          }, 'primary')
        : button(wizard.mode === 'edit' ? 'Zapisz nową wersję' : 'Utwórz politykę', () => saveWizard().catch(error => toast(error.message, 'error')), 'primary')
    );
    dom.content.replaceChildren(
      heading(wizard.mode === 'edit' ? 'Edytuj politykę' : 'Nowa polityka'),
      node('p', { class: 'muted', text: 'Policy Engine / ' + STEPS[wizard.step] }),
      progress,
      node('div', { class: 'policy-wizard-body' }, stepBody()),
      footer
    );
  }
  async function saveWizard() {
    for (let step = 0; step < STEPS.length; step++) {
      const previous = wizard.step;
      wizard.step = step;
      const error = validateCurrentStep();
      wizard.step = previous;
      if (error) throw new Error('Krok ' + (step + 1) + ': ' + error);
    }
    const payload = buildPayload();
    if (wizard.mode === 'edit') payload.expected_version = wizard.original.version;
    await api(wizard.mode === 'edit' ? '/policies/' + encodeURIComponent(wizard.itemId) : '/policies', {
      method: wizard.mode === 'edit' ? 'PUT' : 'POST',
      headers: policyHeaders(wizard, wizard.original),
      body: payload,
    });
    clearWizardStorage();
    wizard = null;
    cache = null;
    toast('Polityka została zapisana.');
    await navigate('policies');
  }
  function restoreSession(key) {
    try {
      const raw = sessionStorage.getItem(key);
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  }
  async function openCreateWizard(step) {
    await loadData();
    if (!wizard || wizard.mode !== 'create') wizard = restoreSession('policy-wizard-create') || emptyWizard();
    wizard.step = Math.max(0, Math.min(STEPS.length - 1, Number(step || 1) - 1));
    renderWizard();
  }
  async function openEditWizard(id, step) {
    await loadData();
    if (!wizard || wizard.mode !== 'edit' || String(wizard.itemId) !== String(id)) {
      const restored = restoreSession('policy-wizard-edit-' + id);
      if (restored) wizard = restored;
      else wizard = wizardFromPolicy(await api('/policies/' + encodeURIComponent(id)));
    }
    wizard.step = Math.max(0, Math.min(STEPS.length - 1, Number(step || 1) - 1));
    renderWizard();
  }
  async function useTemplate(template) {
    await loadData();
    wizard = wizardFromPolicy(template.policy);
    wizard.mode = 'create';
    wizard.itemId = null;
    wizard.original = null;
    wizard.name = template.policy.name;
    wizard.step = 0;
    persistWizard();
    await navigate('/access/policies/new/step/1');
  }
  function countConditions(value) {
    if (!value || typeof value !== 'object' || !Object.keys(value).length) return 0;
    if (value.all) return value.all.reduce((sum, child) => sum + countConditions(child), 0);
    if (value.any) return value.any.reduce((sum, child) => sum + countConditions(child), 0);
    if (value.not) return countConditions(value.not);
    return value.field ? 1 : 0;
  }
  function scopeLabel(item) {
    if (item.scope_level === 'global') return 'Global';
    const tenant = (cache.scopes.tenants || []).find(row => String(row.id) === String(item.tenant_id));
    const project = (cache.scopes.projects || []).find(row => String(row.id) === String(item.project_id));
    const parts = [tenant?.name || item.tenant_id || 'Organizacja'];
    if (item.scope_level === 'project') parts.push(project?.name || item.project_id || 'Projekt');
    if (item.scope?.apmids?.length) parts.push('APMID ' + item.scope.apmids.join(', '));
    if (item.scope?.environments?.length) parts.push('ENV ' + item.scope.environments.map(value => String(value).toUpperCase()).join(', '));
    return parts.join(' · ');
  }
  function policyStatusBadge(item) {
    const kind = item.status === 'enforced' ? 'ok' : item.status === 'disabled' ? 'muted' : item.status === 'dry_run' ? 'warning' : 'info';
    return badge(STATUS_LABELS[item.status] || item.status, kind);
  }
  function filterPolicies(rows) {
    const search = listState.search.toLowerCase();
    return rows.filter(item => {
      if (listState.status && item.status !== listState.status) return false;
      if (listState.type && item.policy_type !== listState.type) return false;
      if (search && ![item.name, item.description, scopeLabel(item), item.policy_type].join(' ').toLowerCase().includes(search)) return false;
      return true;
    });
  }
  async function togglePolicy(item) {
    const enable = item.status !== 'enforced';
    await api('/policies/' + encodeURIComponent(item.id) + '/' + (enable ? 'enable' : 'disable'), {
      method: 'POST',
      headers: policyHeaders(wizardFromPolicy(item), item),
      body: { expected_version: item.version },
    });
    cache = null;
    await policiesView();
  }
  async function duplicatePolicy(item) {
    await loadData();
    wizard = wizardFromPolicy(item);
    wizard.mode = 'create'; wizard.itemId = null; wizard.original = null; wizard.status = 'draft';
    wizard.name = 'Kopia — ' + item.name;
    wizard.step = 0;
    persistWizard();
    await navigate('/access/policies/new/step/1');
  }
  function exportPolicy(item) {
    const payload = copy(item);
    ['id','version','created_by','updated_by','created_at','updated_at','summary','tenant_id','project_id'].forEach(key => delete payload[key]);
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const anchor = node('a', { href: url, download: (item.name || 'policy').replace(/[^a-z0-9_-]+/gi, '-') + '.json' });
    document.body.append(anchor); anchor.click(); anchor.remove();
    URL.revokeObjectURL(url);
  }
  async function importPolicy(file) {
    if (!file) return;
    const parsed = JSON.parse(await file.text());
    await loadData();
    const base = { ...emptyWizard(), ...wizardFromPolicy(parsed) };
    base.mode = 'create'; base.itemId = null; base.original = null; base.status = 'draft'; base.step = 0;
    // /conflicts validates the imported PolicyInput using the same backend
    // schema as create/update before it is admitted into the wizard.
    const temp = wizard; wizard = base;
    await api('/policies/conflicts', {
      method: 'POST', headers: policyHeaders(base), body: { policy: buildPayload() },
    });
    wizard = base;
    persistWizard();
    await navigate('/access/policies/new/step/1');
    if (temp && temp.mode === 'edit') clearWizardStorage();
  }
  function exceptionCondition(type, value) {
    if (type === 'user') return { field: 'actor.id', operator: 'eq', value: Number(value) };
    if (type === 'resource') return { field: 'resource.id', operator: 'eq', value: String(value) };
    if (type === 'project') return { field: 'scope.project_id', operator: 'eq', value: String(value) };
    return { field: 'scope.tenant_id', operator: 'eq', value: String(value) };
  }
  async function exceptionForm(item) {
    await loadData();
    let targetType = 'user';
    let targetValue = String(cache.users[0]?.id || state.identity?.user?.id || '');
    const valuesHost = node('div', {});
    const body = node('div', { class: 'form-grid' });
    function renderTarget() {
      let options = [];
      if (targetType === 'user') options = cache.users.map(row => ({ value: row.id, label: row.username || String(row.id) }));
      if (targetType === 'resource') options = cache.resources.map(row => ({ value: row.id || row.resource_id, label: row.name || row.hostname || String(row.id) }));
      if (targetType === 'project') options = cache.scopes.projects.map(row => ({ value: row.id, label: row.name }));
      if (targetType === 'organization') options = cache.scopes.tenants.map(row => ({ value: row.id, label: row.name }));
      if (!options.some(row => String(row.value) === String(targetValue))) targetValue = String(options[0]?.value || '');
      valuesHost.replaceChildren(selectControl('Wartość', options, targetValue, value => { targetValue = value; }));
    }
    const typeField = selectControl('Wyjątek dla', [
      { value: 'user', label: 'Użytkownik' },
      { value: 'resource', label: 'Zasób' },
      { value: 'project', label: 'Projekt' },
      { value: 'organization', label: 'Organizacja' },
    ], targetType, value => { targetType = value; renderTarget(); });
    renderTarget();
    body.append(
      typeField, valuesHost,
      field('Powód', 'reason', { tag: 'textarea', required: true, wide: true }),
      field('Ticket / Change', 'ticket', {}),
      field('Ważny do', 'valid_until', { type: 'datetime-local' }),
      selectField('Status', 'status', [{ value: 'approved', label: 'Approved' }, { value: 'pending', label: 'Pending' }], 'approved')
    );
    openModal({
      title: 'Dodaj wyjątek — ' + item.name,
      eyebrow: 'Policy Engine / wyjątek',
      body, wide: true, submitLabel: 'Dodaj wyjątek',
      onSubmit: async data => {
        if (!targetValue) throw new Error('Wybierz obiekt wyjątku.');
        await api('/policies/' + encodeURIComponent(item.id) + '/exceptions', {
          method: 'POST',
          body: {
            name: targetType + ': ' + targetValue,
            reason: String(data.get('reason') || '').trim(),
            ticket: String(data.get('ticket') || '').trim(),
            status: String(data.get('status')),
            valid_until: String(data.get('valid_until') || '').trim() || null,
            condition: exceptionCondition(targetType, targetValue),
          },
        });
        toast('Wyjątek zapisany.');
        await policyDetails(item.id);
        return false;
      },
    });
  }
  async function policyDetails(id) {
    await loadData();
    const [item, exceptions, versions] = await Promise.all([
      api('/policies/' + encodeURIComponent(id)),
      api('/policies/' + encodeURIComponent(id) + '/exceptions').catch(() => ({ items: [] })),
      api('/policies/' + encodeURIComponent(id) + '/versions').catch(() => ({ items: [] })),
    ]);
    const actions = [button('Wróć', () => navigate('policies'), 'ghost')];
    if (can('policies.update','policies.manage')) actions.push(button('Edytuj', () => navigate('/access/policies/edit/' + item.id + '/step/1'), 'primary'));
    if (can('policies.create','policies.manage')) actions.push(button('Duplikuj', () => duplicatePolicy(item), 'ghost'));
    if (can('policies.enable','policies.manage')) actions.push(button(item.status === 'enforced' ? 'Wyłącz' : 'Włącz', () => togglePolicy(item), 'ghost'));
    if (can('policies.exceptions','policies.exception.manage')) actions.push(button('Dodaj wyjątek', () => exceptionForm(item), 'ghost'));
    actions.push(button('Eksportuj', () => exportPolicy(item), 'ghost'));
    dom.content.replaceChildren(
      heading(item.name, actions),
      node('div', { class: 'policy-detail-hero' },
        policyStatusBadge(item),
        node('p', { text: item.summary || item.description || 'Brak opisu.' }),
        node('div', { class: 'policy-meta-grid' },
          info('Typ', item.policy_type),
          info('Scope', scopeLabel(item)),
          info('Priorytet', item.priority),
          info('Wersja', '#' + item.version),
          info('Warunki', countConditions(item.condition)),
          info('Efekty', (item.effects || []).length)
        )
      ),
      section('Czytelny opis', 'Podstawowy widok nie wymaga analizy JSON.',
        node('p', { text: 'Warunki: ' + humanCondition(item.condition || {}) }),
        node('p', { text: 'Akcje: ' + ((item.scope?.actions || []).join(', ') || 'wszystkie w zakresie') }),
        node('p', { text: 'Efekty: ' + (item.effects || []).map(effect => effect.type).join(', ') })
      ),
      section('Historia wersji', 'Każda edycja tworzy nową wersję.',
        table([
          { label: 'Wersja', value: row => '#' + row.version },
          { label: 'Autor', value: row => row.created_by },
          { label: 'Data', value: row => formatDate(row.created_at) },
          { label: 'Status', value: row => STATUS_LABELS[row.snapshot?.status] || row.snapshot?.status || '—' },
        ], versions.items || [], row => row.version !== item.version && can('policies.update','policies.manage') ? [
          button('Przywróć', async () => {
            await api('/policies/' + encodeURIComponent(item.id) + '/rollback', {
              method: 'POST', body: { version: row.version, expected_version: item.version },
            });
            await policyDetails(item.id);
          }, 'ghost'),
        ] : [])
      ),
      section('Wyjątki', 'Wyjątki są audytowane i mogą mieć datę wygaśnięcia.',
        (exceptions.items || []).length ? table([
          { label: 'Nazwa', value: row => row.name },
          { label: 'Status', value: row => row.status },
          { label: 'Powód', value: row => row.reason },
          { label: 'Ważny do', value: row => row.valid_until ? formatDate(row.valid_until) : 'bez limitu' },
        ], exceptions.items || [], row => row.status !== 'revoked' && can('policies.exceptions','policies.exception.manage') ? [
          button('Wycofaj', async () => {
            await api('/policies/' + item.id + '/exceptions/' + row.id, { method: 'DELETE' });
            await policyDetails(item.id);
          }, 'ghost'),
        ] : []) : node('p', { class: 'muted', text: 'Brak wyjątków.' })
      )
    );
  }
  async function complianceView() {
    const result = await api('/policies/compliance?limit=500');
    dom.content.replaceChildren(
      heading('Policy Engine — Compliance', [button('Polityki', () => { listState.section = 'policies'; policiesView(); }, 'ghost')]),
      node('div', { class: 'policy-impact-grid' },
        info('Compliant', result.counts?.compliant || 0),
        info('Non-compliant', result.counts?.non_compliant || 0),
        info('Warning', result.counts?.warning || 0),
        info('Unknown', result.counts?.unknown || 0)
      ),
      table([
        { label: 'Resource', value: row => row.resource },
        { label: 'Project', value: row => row.project_id },
        { label: 'APMID', value: row => row.apmid || '—' },
        { label: 'Environment', value: row => row.environment || '—' },
        { label: 'Status', value: row => badge(row.status, row.status === 'compliant' ? 'ok' : row.status === 'non_compliant' ? 'danger' : 'warning') },
        { label: 'Policies', value: row => (row.matched_policy_ids || []).length },
      ], result.items || [], row => row.violations?.length ? [
        button('Dlaczego?', () => openModal({
          title: row.resource + ' — compliance',
          eyebrow: 'Policy Engine',
          body: node('ul', {}, ...row.violations.map(item => node('li', { text: item.message || item.type }))),
          submitLabel: null,
        }), 'ghost'),
      ] : [])
    );
  }
  async function templatesView() {
    await loadData();
    dom.content.replaceChildren(
      heading('Szablony polityk', [button('Polityki', () => { listState.section = 'policies'; policiesView(); }, 'ghost')]),
      node('div', { class: 'policy-template-grid' },
        ...cache.templates.map(item => node('article', { class: 'policy-template-card' },
          node('h3', { text: item.name }),
          node('p', { text: item.description }),
          button('Użyj szablonu', () => useTemplate(item), 'primary')
        ))
      )
    );
  }
  async function policiesView() {
    await loadData();
    if (listState.section === 'compliance' && can('policies.compliance')) return complianceView();
    if (listState.section === 'templates') return templatesView();
    const result = await api('/policies');
    lastPolicies = result.items || [];
    const rows = filterPolicies(lastPolicies);
    const actions = [];
    if (can('policies.create','policies.manage')) actions.push(button('+ Nowa polityka', () => {
      wizard = null; navigate('/access/policies/new/step/1');
    }, 'primary'));
    actions.push(button('Szablony', () => { listState.section = 'templates'; templatesView(); }, 'ghost'));
    if (can('policies.compliance')) actions.push(button('Compliance', () => { listState.section = 'compliance'; complianceView(); }, 'ghost'));
    if (can('policies.audit')) actions.push(button('Decision Log', decisionLog, 'ghost'));
    const fileInput = node('input', {
      type: 'file', accept: 'application/json,.json', hidden: true,
      onChange: event => importPolicy(event.currentTarget.files?.[0]).catch(error => toast(error.message, 'error')),
    });
    if (can('policies.create','policies.manage')) actions.push(button('Importuj', () => fileInput.click(), 'ghost'));
    const search = node('input', {
      type: 'search', value: listState.search, placeholder: 'Szukaj polityki…',
      onInput: event => { listState.search = event.currentTarget.value; renderPolicyTable(); },
    });
    const status = node('select', { onChange: event => { listState.status = event.currentTarget.value; renderPolicyTable(); } },
      option('', 'Wszystkie statusy', listState.status === ''),
      ...['enforced','disabled','draft','dry_run'].map(value => option(value, STATUS_LABELS[value], listState.status === value)));
    const type = node('select', { onChange: event => { listState.type = event.currentTarget.value; renderPolicyTable(); } },
      option('', 'Wszystkie typy', listState.type === ''),
      ...cache.capabilities.policy_types.map(value => option(value, value, listState.type === value)));
    const tableHost = node('div', { class: 'policy-table-host' });
    function renderPolicyTable() {
      const filtered = filterPolicies(lastPolicies);
      tableHost.replaceChildren(table([
        { label: 'Nazwa', value: item => node('strong', { text: item.name }) },
        { label: 'Status', value: policyStatusBadge },
        { label: 'Typ', value: item => item.policy_type },
        { label: 'Scope', value: scopeLabel },
        { label: 'Warunki', value: item => countConditions(item.condition) },
        { label: 'Efekty', value: item => (item.effects || []).length },
        { label: 'Priorytet', value: item => item.priority },
        { label: 'Wersja', value: item => '#' + item.version },
        { label: 'Modyfikacja', value: item => formatDate(item.updated_at) },
        { label: 'Autor', value: item => cache.users.find(row => Number(row.id) === Number(item.updated_by))?.username || item.updated_by },
      ], filtered, item => [
        button('Szczegóły', () => policyDetails(item.id), 'ghost'),
        can('policies.update','policies.manage') ? button('Edytuj', () => navigate('/access/policies/edit/' + item.id + '/step/1'), 'ghost') : null,
      ].filter(Boolean)));
    }
    dom.content.replaceChildren(
      heading('Policy Engine', actions),
      fileInput,
      node('p', { class: 'muted', text: 'ABAC + RBAC + constraints. Konfiguracja podstawowa nie wymaga JSON, YAML, Rego ani składni warunków.' }),
      node('div', { class: 'policy-list-filters' }, search, status, type),
      tableHost
    );
    renderPolicyTable();
  }
  async function decisionLog() {
    const rows = (await api('/policies/decisions?limit=200&offset=0')).items || [];
    openModal({
      title: 'Decision Log',
      eyebrow: 'Policy Engine / audit',
      wide: true,
      body: table([
        { label: 'Czas', value: row => formatDate(row.timestamp) },
        { label: 'Akcja', value: row => row.action },
        { label: 'Resource', value: row => row.resource_type + (row.resource_id ? ' / ' + row.resource_id : '') },
        { label: 'Decyzja', value: row => badge(row.decision, row.decision === 'allow' ? 'ok' : row.decision === 'deny' ? 'danger' : 'warning') },
        { label: 'Policies', value: row => (row.matched_policy_ids || []).length },
      ], rows, row => [button('Dlaczego?', () => openModal({
        title: 'Decision ' + row.id,
        eyebrow: row.action,
        wide: true,
        body: node('div', { class: 'stack' },
          node('p', { text: 'Decyzja: ' + String(row.decision).toUpperCase() }),
          node('p', { text: 'Dopasowane polityki: ' + (row.matched_policy_ids || []).join(', ') }),
          ...(row.trace || []).map(trace => node('p', {
            class: 'muted',
            text: String(trace.policy_id || '') + ': ' + (trace.matched ? 'dopasowana' : 'pominięta') + ' — ' + (trace.reason || ''),
          }))
        ),
        submitLabel: null,
      }), 'ghost')]),
      submitLabel: null,
    });
  }
  registerCommand('policies.create', () => navigate('/access/policies/new/step/1'));
  registerRoutedForm({
    id: 'policy-create',
    pattern: /^\/access\/policies\/new\/step\/(?<step>\d+)$/,
    parent: 'policies',
    permission: 'policies.read',
    label: 'Policy Engine',
  }, match => openCreateWizard(match.params.step));
  registerRoutedForm({
    id: 'policy-edit',
    pattern: /^\/access\/policies\/edit\/(?<id>[^/]+)\/step\/(?<step>\d+)$/,
    parent: 'policies',
    permission: 'policies.read',
    label: 'Policy Engine',
  }, match => openEditWizard(match.params.id, match.params.step));
  registerView({
    id: 'policies',
    label: 'Policy Engine',
    icon: 'P',
    permission: 'policies.read',
    order: 25,
  }, policiesView);
})();
