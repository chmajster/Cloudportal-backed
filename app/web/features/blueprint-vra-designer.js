'use strict';

(() => {
  const STEP_CATALOG = [
    { group: 'Infrastruktura', items: [
      ['terraform_plan', 'Terraform plan', 'Plan infrastruktury'],
      ['terraform_apply', 'Terraform apply', 'Utworzenie / zmiana zasobów'],
      ['terraform_destroy', 'Terraform destroy', 'Usunięcie zasobów'],
      ['create_snapshot', 'Snapshot', 'Punkt przywracania'],
    ] },
    { group: 'Gotowość maszyny', items: [
      ['wait_for_vm', 'Wait for VM', 'Oczekiwanie na VM'],
      ['wait_for_agent', 'Wait for agent', 'Oczekiwanie na guest agent'],
      ['wait_for_ip', 'Wait for IP', 'Oczekiwanie na adres IP'],
      ['wait_for_ssh', 'Wait for SSH', 'Oczekiwanie na SSH'],
      ['health_check', 'Health check', 'Kontrola gotowości'],
    ] },
    { group: 'Automatyzacja i sterowanie', items: [
      ['run_ansible_playbook', 'Ansible playbook', 'Konfiguracja po wdrożeniu'],
      ['register_awx', 'AWX onboarding', 'Rejestracja hosta w AWX po Cloud-init'],
      ['condition', 'Condition', 'Warunek wykonania'],
      ['approval', 'Approval', 'Brama akceptacji'],
      ['delay', 'Delay', 'Opóźnienie'],
      ['notification', 'Notification', 'Powiadomienie'],
    ] },
  ];
  const STEP_TYPES = STEP_CATALOG.flatMap(group => group.items.map(item => item[0]));
  const TYPE_META = new Map(STEP_CATALOG.flatMap(group => group.items.map(item => [item[0], {
    group: group.group, label: item[1], description: item[2],
  }])));
  const TERRAFORM_STEP_TYPES = new Set(['terraform_plan', 'terraform_apply', 'terraform_destroy']);

  function defaultStepTimeout(type) {
    if (TERRAFORM_STEP_TYPES.has(type)) return 3600;
    if (type === 'wait_for_ip') return 180;
    return 600;
  }

  const deepClone = value => JSON.parse(JSON.stringify(value));
  const qs = (root, selector) => root.querySelector(selector);

  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === undefined || value === null) continue;
      if (key === 'class') node.className = value;
      else if (key === 'text') node.textContent = value;
      else if (key === 'html') node.innerHTML = value;
      else if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2).toLowerCase(), value);
      else if (key === 'checked') node.checked = Boolean(value);
      else if (key === 'value') node.value = value;
      else node.setAttribute(key, value);
    }
    for (const child of children.flat()) {
      if (child === undefined || child === null) continue;
      node.append(child.nodeType ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  function svgEl(tag, attrs = {}) {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value !== undefined && value !== null) node.setAttribute(key, String(value));
    }
    return node;
  }

  function iconButton(label, title, handler, kind = '') {
    return el('button', { type: 'button', class: 'vra-tool ' + kind, title, onclick: handler }, label);
  }

  function textField(label, value, onChange, options = {}) {
    const input = el(options.multiline ? 'textarea' : 'input', {
      class: 'vra-field-control',
      type: options.type || 'text',
      value: value ?? '',
      placeholder: options.placeholder || '',
      min: options.min,
      max: options.max,
    });
    if (options.multiline) input.value = value ?? '';
    input.addEventListener(options.live ? 'input' : 'change', () => onChange(input.value, input));
    return el('label', { class: 'vra-field ' + (options.wide ? 'wide' : '') },
      el('span', { class: 'vra-field-label', text: label }),
      input,
      options.help ? el('small', { class: 'vra-field-help', text: options.help }) : null);
  }

  function selectField(label, value, choices, onChange) {
    const select = el('select', { class: 'vra-field-control' });
    for (const choice of choices) {
      select.append(el('option', { value: choice.value, text: choice.label, selected: String(choice.value) === String(value) }));
    }
    select.value = value ?? '';
    select.addEventListener('change', () => onChange(select.value));
    return el('label', { class: 'vra-field' },
      el('span', { class: 'vra-field-label', text: label }), select);
  }

  function checkboxField(label, checked, onChange) {
    const input = el('input', { type: 'checkbox', checked });
    input.addEventListener('change', () => onChange(input.checked));
    return el('label', { class: 'vra-check' }, input, el('span', { text: label }));
  }

  function parseJson(value, label, fallback) {
    const raw = String(value || '').trim();
    if (!raw) return fallback;
    try {
      const parsed = JSON.parse(raw);
      return parsed;
    } catch (_) {
      throw new Error(label + ' musi zawierać poprawny JSON.');
    }
  }

  function parsePositiveIds(value) {
    return String(value || '')
      .split(',')
      .map(item => item.trim())
      .filter(Boolean)
      .map(Number)
      .filter(id => Number.isInteger(id) && id > 0);
  }

  function slugify(value) {
    return String(value || '')
      .normalize('NFKD').replace(/[\u0300-\u036f]/g, '')
      .toLowerCase().replace(/[^a-z0-9_.-]+/g, '-')
      .replace(/^-+|-+$/g, '').replace(/-{2,}/g, '-').slice(0, 63);
  }

  function nativeBlueprint(item, data) {
    if (item) {
      const result = {};
      for (const key of [
        'slug', 'name', 'description', 'is_active', 'visibility',
        'allowed_role_ids', 'allowed_user_ids', 'manager_role_ids',
        'variables_schema', 'deployment', 'workflow',
        'requires_approval', 'recovery_policy',
      ]) result[key] = deepClone(item[key]);
      return result;
    }
    const enabledTemplates = data.templates.filter(value => value.enabled !== false);
    const preferredTemplate = enabledTemplates.find(template =>
      data.providers.some(provider => provider.type === template.provider));
    const provider = data.providers.find(value => value.type === preferredTemplate?.provider) || data.providers[0] || {};
    const template = enabledTemplates.find(value => value.provider === provider.type) || {};
    const defaults = {};
    for (const [name, spec] of Object.entries(template.variables_schema?.properties || {})) {
      if (spec.default !== undefined && spec.default !== null) defaults[name] = spec.default;
    }
    if ('name' in (template.variables_schema?.properties || {})) defaults.name = '{{ hostname }}';
    return {
      slug: 'new-blueprint',
      name: 'New Blueprint',
      description: '',
      is_active: true,
      visibility: { backend: true, cloudportal: false, api: true },
      allowed_role_ids: [],
      allowed_user_ids: [],
      manager_role_ids: [],
      variables_schema: {},
      deployment: {
        name: '{{ hostname }}',
        provider_id: Number(provider.id || 1),
        credentials_id: Number(provider.credentials_id || 1),
        template: template.id || 'proxmox-vm',
        variables: defaults,
        executor: 'terraform',
        hostname_values: {},
        select_apmid_on_execute: false,
        select_environment_on_execute: false,
      },
      workflow: [{
        id: 'apply', type: 'terraform_apply', depends_on: [], conditions: {},
        retry: 0, timeout: 3600, rollback: null,
      }],
      requires_approval: false,
      recovery_policy: 'preserve',
    };
  }

  function normalizeStep(step) {
    const type = String(step.type || 'condition');
    const parsedTimeout = Number(step.timeout ?? defaultStepTimeout(type));
    return {
      id: String(step.id || 'step').slice(0, 63),
      type,
      depends_on: Array.isArray(step.depends_on) ? [...new Set(step.depends_on.map(String))] : [],
      conditions: step.conditions && typeof step.conditions === 'object' && !Array.isArray(step.conditions) ? deepClone(step.conditions) : {},
      retry: Math.max(0, Math.min(10, Number(step.retry || 0))),
      timeout: Math.max(1, Math.min(86400, Number.isFinite(parsedTimeout) ? parsedTimeout : defaultStepTimeout(type))),
      rollback: step.rollback ? String(step.rollback) : null,
    };
  }

  function validateGraph(blueprint) {
    const errors = [];
    const warnings = [];
    const steps = blueprint.workflow || [];
    const ids = steps.map(step => step.id);
    const known = new Set(ids);
    if (!steps.length) errors.push('Workflow nie zawiera kroków.');
    if (new Set(ids).size !== ids.length) errors.push('ID kroków muszą być unikalne.');
    for (const step of steps) {
      if (!step.id) errors.push('Każdy krok musi mieć ID.');
      for (const dep of step.depends_on || []) {
        if (!known.has(dep)) errors.push(step.id + ': brak zależności ' + dep + '.');
        if (dep === step.id) errors.push(step.id + ': krok nie może zależeć od siebie.');
      }
      if (step.rollback && !known.has(step.rollback)) errors.push(step.id + ': nieznany rollback ' + step.rollback + '.');
    }

    const visiting = new Set();
    const visited = new Set();
    function visit(id) {
      if (visited.has(id)) return;
      if (visiting.has(id)) {
        errors.push('Workflow zawiera cykl obejmujący krok ' + id + '.');
        return;
      }
      visiting.add(id);
      const step = steps.find(value => value.id === id);
      for (const dep of step?.depends_on || []) visit(dep);
      visiting.delete(id);
      visited.add(id);
    }
    ids.forEach(visit);

    const provisioning = steps.filter(step => ['terraform_apply', 'create_vm', 'clone_vm'].includes(step.type));
    if (!provisioning.length) warnings.push('Brak kroku provisioningowego (Terraform apply / Create VM / Clone VM).');
    if (steps.some(step => step.type === 'approval') && !blueprint.requires_approval) {
      warnings.push('Workflow zawiera Approval, ale globalne requires_approval jest wyłączone.');
    }
    return { errors: [...new Set(errors)], warnings: [...new Set(warnings)] };
  }

  function validateBlueprintReferences(blueprint, data, context = {}) {
    const errors = [];
    const deployment = blueprint.deployment || {};
    const provider = data.providers.find(value => String(value.id) === String(deployment.provider_id));
    const template = data.templates.find(value => value.id === deployment.template);
    const originalTemplate = context.originalTemplate || null;
    if (!provider) errors.push('Wybrany provider nie istnieje lub nie jest dostępny.');
    if (provider && String(deployment.credentials_id) !== String(provider.credentials_id)) {
      errors.push('Credential Blueprintu musi być credentialem wybranego providera.');
    }
    if (!template) errors.push('Wybrany template nie istnieje w katalogu.');
    if (provider && template && template.provider !== provider.type) {
      errors.push('Template nie jest zgodny z typem wybranego providera.');
    }
    if (template?.enabled === false && deployment.template !== originalTemplate) {
      errors.push('Nie można wybrać wyłączonego template dla nowego lub zmienianego Blueprintu.');
    }
    const workflowTypes = new Set((blueprint.workflow || []).map(step => step.type));
    if (!context.blueprintId && !workflowTypes.has('terraform_apply')) {
      errors.push('Nowy Blueprint musi zawierać krok Terraform apply.');
    }
    const proxmoxOnly = new Set([
      'wait_for_vm', 'wait_for_agent', 'wait_for_ip', 'wait_for_ssh',
      'run_ansible_playbook', 'register_awx', 'create_snapshot', 'health_check',
    ]);
    if (provider && provider.type !== 'proxmox') {
      const invalid = [...workflowTypes].filter(type => proxmoxOnly.has(type));
      if (invalid.length) errors.push('Wybrany provider nie obsługuje kroków: ' + invalid.join(', ') + '.');
      if (deployment.ansible) errors.push('Post-provisioning Ansible wymaga providera Proxmox.');
    }
    return errors;
  }

  function autoPositions(workflow) {
    const byId = new Map(workflow.map(step => [step.id, step]));
    const depths = new Map();
    const resolving = new Set();
    function depth(id) {
      if (depths.has(id)) return depths.get(id);
      if (resolving.has(id)) return 0;
      resolving.add(id);
      const step = byId.get(id);
      const value = step && step.depends_on.length
        ? 1 + Math.max(...step.depends_on.filter(dep => byId.has(dep)).map(depth), 0)
        : 0;
      resolving.delete(id);
      depths.set(id, value);
      return value;
    }
    workflow.forEach(step => depth(step.id));
    const rows = new Map();
    const result = {};
    workflow.forEach(step => {
      const d = depths.get(step.id) || 0;
      const row = rows.get(d) || 0;
      rows.set(d, row + 1);
      result[step.id] = { x: 80 + d * 300, y: 80 + row * 145 };
    });
    return result;
  }

  async function loadData() {
    const unwrap = value => value?.items || value || [];
    const [providers, templates, credentials] = await Promise.all([
      api('/providers?limit=200').catch(() => ({ items: [] })),
      api('/templates').catch(() => ({ items: [] })),
      api('/credentials?limit=200').catch(() => ({ items: [] })),
    ]);
    return { providers: unwrap(providers), templates: unwrap(templates), credentials: unwrap(credentials) };
  }

  async function openDesigner(item = null) {
    if (!allowed('blueprints.read')) {
      toast('Brak uprawnienia blueprints.read.', 'error');
      return;
    }

    const data = await loadData();
    const state = {
      id: item?.id || null,
      version: item?.version || null,
      blueprint: nativeBlueprint(item, data),
      positions: {},
      selectedId: null,
      connectFrom: null,
      inspectorTab: 'step',
      advancedOpen: false,
      zoom: 1,
      grid: true,
      dirty: false,
      yaml: '',
      yamlDirty: true,
      history: [],
      future: [],
      validation: { errors: [], warnings: [] },
      serverValidation: null,
      dragging: null,
    };
    state.blueprint.workflow = state.blueprint.workflow.map(normalizeStep);
    state.positions = autoPositions(state.blueprint.workflow);
    state.selectedId = state.blueprint.workflow[0]?.id || null;

    const overlay = el('div', { class: 'vra-designer-shell' });
    const header = el('header', { class: 'vra-designer-header' });
    const main = el('div', { class: 'vra-designer-main' });
    const footer = el('footer', { class: 'vra-designer-status' });
    overlay.append(header, main, footer);
    document.body.append(overlay);

    function selectedStep() {
      return state.blueprint.workflow.find(step => step.id === state.selectedId) || null;
    }

    function snapshot() {
      state.history.push(JSON.stringify({ blueprint: state.blueprint, positions: state.positions, selectedId: state.selectedId }));
      if (state.history.length > 80) state.history.shift();
      state.future = [];
    }

    function mutate(fn, options = {}) {
      if (!options.noHistory) snapshot();
      fn();
      state.dirty = true;
      state.yamlDirty = true;
      state.serverValidation = null;
      render();
    }

    function restore(serialized, destination) {
      destination.push(JSON.stringify({ blueprint: state.blueprint, positions: state.positions, selectedId: state.selectedId }));
      const value = JSON.parse(serialized);
      state.blueprint = value.blueprint;
      state.positions = value.positions;
      state.selectedId = value.selectedId;
      state.dirty = true;
      state.yamlDirty = true;
      render();
    }

    function undo() {
      if (!state.history.length) return;
      restore(state.history.pop(), state.future);
    }

    function redo() {
      if (!state.future.length) return;
      restore(state.future.pop(), state.history);
    }

    function makeStep(type, x = 100, y = 100) {
      const base = type.replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '') || 'step';
      const existing = new Set(state.blueprint.workflow.map(step => step.id));
      let id = base;
      let index = 2;
      while (existing.has(id)) id = base + '_' + index++;
      const previous = state.selectedId && existing.has(state.selectedId) ? [state.selectedId] : [];
      const timeout = defaultStepTimeout(type);
      return {
        step: { id, type, depends_on: previous, conditions: {}, retry: 0, timeout, rollback: null },
        position: { x, y },
      };
    }

    function addStep(type, point) {
      mutate(() => {
        const made = makeStep(type, point?.x ?? 120, point?.y ?? 120);
        state.blueprint.workflow.push(made.step);
        state.positions[made.step.id] = made.position;
        state.selectedId = made.step.id;
        state.inspectorTab = 'step';
      });
    }

    function removeStep(id) {
      mutate(() => {
        state.blueprint.workflow = state.blueprint.workflow.filter(step => step.id !== id);
        for (const step of state.blueprint.workflow) {
          step.depends_on = (step.depends_on || []).filter(dep => dep !== id);
          if (step.rollback === id) step.rollback = null;
        }
        delete state.positions[id];
        state.selectedId = state.blueprint.workflow[0]?.id || null;
        if (state.connectFrom === id) state.connectFrom = null;
      });
    }

    function renameStep(step, newId) {
      const cleaned = slugify(newId);
      if (!cleaned || cleaned === step.id) return;
      if (state.blueprint.workflow.some(value => value.id === cleaned)) {
        toast('ID kroku już istnieje.', 'error');
        return;
      }
      mutate(() => {
        const old = step.id;
        step.id = cleaned;
        for (const other of state.blueprint.workflow) {
          other.depends_on = (other.depends_on || []).map(dep => dep === old ? cleaned : dep);
          if (other.rollback === old) other.rollback = cleaned;
        }
        state.positions[cleaned] = state.positions[old] || { x: 100, y: 100 };
        delete state.positions[old];
        state.selectedId = cleaned;
        if (state.connectFrom === old) state.connectFrom = cleaned;
      });
    }

    function connect(sourceId, targetId) {
      if (!sourceId || !targetId || sourceId === targetId) return;
      const target = state.blueprint.workflow.find(step => step.id === targetId);
      if (!target) return;
      mutate(() => {
        target.depends_on = [...new Set([...(target.depends_on || []), sourceId])];
        state.connectFrom = null;
      });
    }

    function removeConnection(sourceId, targetId) {
      const target = state.blueprint.workflow.find(step => step.id === targetId);
      if (!target) return;
      mutate(() => {
        target.depends_on = (target.depends_on || []).filter(dep => dep !== sourceId);
      });
    }

    function fitLayout() {
      mutate(() => {
        state.positions = autoPositions(state.blueprint.workflow);
      });
    }

    async function syncYamlFromModel() {
      try {
        const result = await api('/blueprint-designer/yaml/render', {
          method: 'POST', body: state.blueprint,
        });
        state.yaml = result.yaml;
        state.yamlDirty = false;
        return true;
      } catch (error) {
        toast(error.message || 'Nie można wygenerować YAML.', 'error');
        return false;
      }
    }

    async function applyYaml() {
      const textarea = qs(overlay, '[data-vra-yaml]');
      const content = textarea?.value ?? state.yaml;
      try {
        const result = await api('/blueprint-designer/yaml/parse', {
          method: 'POST', body: { yaml: content },
        });
        snapshot();
        state.blueprint = result.blueprint;
        state.blueprint.workflow = state.blueprint.workflow.map(normalizeStep);
        state.positions = autoPositions(state.blueprint.workflow);
        state.selectedId = state.blueprint.workflow[0]?.id || null;
        state.yaml = result.yaml;
        state.yamlDirty = false;
        state.dirty = true;
        state.serverValidation = { ok: true, message: 'YAML i schemat są poprawne.' };
        render();
      } catch (error) {
        state.serverValidation = { ok: false, message: error.message || 'Niepoprawny YAML.' };
        renderFooter();
        toast(state.serverValidation.message, 'error');
      }
    }

    async function validateServer() {
      const local = validateGraph(state.blueprint);
      const referenceErrors = validateBlueprintReferences(state.blueprint, data, {
        blueprintId: state.id,
        originalTemplate: item?.deployment?.template || null,
      });
      local.errors.push(...referenceErrors);
      state.validation = local;
      if (local.errors.length) {
        state.serverValidation = { ok: false, message: local.errors[0] };
        renderFooter();
        toast('Workflow zawiera błędy.', 'error');
        return false;
      }
      try {
        const result = await api('/blueprint-designer/yaml/render', {
          method: 'POST', body: state.blueprint,
        });
        state.yaml = result.yaml;
        state.yamlDirty = false;
        state.serverValidation = { ok: true, message: local.warnings.length ? 'Schemat poprawny, są ostrzeżenia.' : 'Blueprint poprawny.' };
        renderFooter();
        return true;
      } catch (error) {
        state.serverValidation = { ok: false, message: error.message || 'Walidacja API nie powiodła się.' };
        renderFooter();
        toast(state.serverValidation.message, 'error');
        return false;
      }
    }

    async function save() {
      if (!allowed(state.id ? 'blueprints.update' : 'blueprints.create')) {
        toast('Brak uprawnienia do zapisu Blueprintu.', 'error');
        return;
      }
      if (!(await validateServer())) return;
      try {
        const result = await api(state.id ? '/blueprints/' + state.id : '/blueprints', {
          method: state.id ? 'PUT' : 'POST',
          body: state.blueprint,
          headers: state.id ? undefined : { 'Idempotency-Key': crypto.randomUUID() },
        });
        state.id = result.id;
        state.version = result.version;
        state.blueprint = nativeBlueprint(result, data);
        state.blueprint.workflow = state.blueprint.workflow.map(normalizeStep);
        state.dirty = false;
        state.history = [];
        state.future = [];
        toast('Blueprint zapisany.');
        render();
      } catch (error) {
        toast(error.message || 'Nie można zapisać Blueprintu.', 'error');
      }
    }

    async function exportYaml() {
      if (state.yamlDirty && !(await syncYamlFromModel())) return;
      const blob = new Blob([state.yaml], { type: 'application/yaml;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = el('a', { href: url, download: (state.blueprint.slug || 'blueprint') + '.yaml' });
      document.body.append(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    }

    function importYamlFile(file) {
      if (!file) return;
      if (file.size > 262144) {
        toast('Plik YAML jest za duży.', 'error');
        return;
      }
      const reader = new FileReader();
      reader.onload = () => {
        state.yaml = String(reader.result || '');
        state.yamlDirty = false;
        state.inspectorTab = 'yaml';
        render();
      };
      reader.readAsText(file);
    }

    async function copyYaml() {
      if (state.yamlDirty && !(await syncYamlFromModel())) return;
      await navigator.clipboard.writeText(state.yaml);
      toast('YAML skopiowany.');
    }

    function close() {
      if (state.dirty && !window.confirm('Masz niezapisane zmiany. Zamknąć designer?')) return;
      overlay.remove();
      document.removeEventListener('keydown', keyHandler);
      if (typeof navigate === 'function') navigate('blueprints');
    }

    function keyHandler(event) {
      if (!overlay.isConnected) return;
      const editing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName);
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
        event.preventDefault(); save(); return;
      }
      if (!editing && (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z' && !event.shiftKey) {
        event.preventDefault(); undo(); return;
      }
      if (!editing && (event.ctrlKey || event.metaKey) && (event.key.toLowerCase() === 'y' || (event.key.toLowerCase() === 'z' && event.shiftKey))) {
        event.preventDefault(); redo(); return;
      }
      if (event.key === 'Escape') {
        if (state.connectFrom) { state.connectFrom = null; renderCanvas(); }
        else if (!editing) close();
      }
      if (!editing && (event.key === 'Delete' || event.key === 'Backspace') && state.selectedId) {
        event.preventDefault();
        removeStep(state.selectedId);
      }
    }
    document.addEventListener('keydown', keyHandler);

    function renderHeader() {
      header.replaceChildren();
      const title = el('div', { class: 'vra-brand' },
        el('div', { class: 'vra-brand-mark', text: 'CP' }),
        el('div', {},
          el('strong', { text: 'Blueprint Designer' }),
          el('span', { text: 'vRA-style canvas · GUI + YAML' })));
      const meta = el('div', { class: 'vra-header-meta' },
        el('strong', { text: state.blueprint.name || 'Bez nazwy' }),
        el('span', { class: 'mono', text: (state.blueprint.slug || '—') + (state.version ? ' · v' + state.version : ' · nowy') }),
        state.dirty ? el('span', { class: 'vra-dirty', text: 'Niezapisane' }) : el('span', { class: 'vra-saved', text: 'Zapisano' }));
      const tools = el('div', { class: 'vra-toolbar' },
        iconButton('Cofnij', 'Ctrl+Z', undo),
        iconButton('Ponów', 'Ctrl+Y', redo),
        iconButton('Auto layout', 'Rozmieść graf automatycznie', fitLayout),
        iconButton('Waliduj', 'Waliduj lokalnie i przez API', validateServer),
        iconButton('Eksport YAML', 'Pobierz YAML', exportYaml),
        iconButton('Zapisz', 'Ctrl+S', save, 'primary'),
        iconButton('×', 'Zamknij', close, 'close'));
      header.append(title, meta, tools);
    }

    function renderPalette() {
      const root = el('aside', { class: 'vra-palette' });
      root.append(el('div', { class: 'vra-pane-title' },
        el('strong', { text: 'Components' }),
        el('span', { text: 'Przeciągnij na canvas' })));
      const search = el('input', { class: 'vra-palette-search', type: 'search', placeholder: 'Szukaj komponentu…' });
      root.append(search);
      const list = el('div', { class: 'vra-palette-list' });
      root.append(list);

      function paint(filter = '') {
        list.replaceChildren();
        const needle = filter.trim().toLowerCase();
        for (const group of STEP_CATALOG) {
          const items = group.items.filter(item => !needle || item.join(' ').toLowerCase().includes(needle) || group.group.toLowerCase().includes(needle));
          if (!items.length) continue;
          const section = el('section', { class: 'vra-palette-group' }, el('h4', { text: group.group }));
          for (const [type, label, description] of items) {
            const card = el('button', {
              type: 'button', class: 'vra-component', draggable: 'true',
              onclick: () => addStep(type),
            },
              el('span', { class: 'vra-component-icon', text: label.slice(0, 2).toUpperCase() }),
              el('span', {}, el('strong', { text: label }), el('small', { text: description })));
            card.addEventListener('dragstart', event => {
              event.dataTransfer.setData('application/x-cloudportal-step', type);
              event.dataTransfer.effectAllowed = 'copy';
            });
            section.append(card);
          }
          list.append(section);
        }
      }
      search.addEventListener('input', () => paint(search.value));
      paint();
      return root;
    }

    function graphExtents() {
      let width = 1400, height = 900;
      for (const pos of Object.values(state.positions)) {
        width = Math.max(width, Number(pos.x || 0) + 360);
        height = Math.max(height, Number(pos.y || 0) + 260);
      }
      return { width, height };
    }

    function renderEdges(svg) {
      svg.replaceChildren();
      const byId = new Map(state.blueprint.workflow.map(step => [step.id, step]));
      for (const step of state.blueprint.workflow) {
        const target = state.positions[step.id];
        if (!target) continue;
        for (const dep of step.depends_on || []) {
          const source = state.positions[dep];
          if (!source || !byId.has(dep)) continue;
          const x1 = source.x + 220;
          const y1 = source.y + 44;
          const x2 = target.x;
          const y2 = target.y + 44;
          const bend = Math.max(55, Math.abs(x2 - x1) / 2);
          const path = svgEl('path', {
            d: `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`,
            class: 'vra-edge',
          });
          path.addEventListener('click', event => event.stopPropagation());
          path.addEventListener('dblclick', event => {
            event.stopPropagation();
            removeConnection(dep, step.id);
          });
          svg.append(path);
        }
      }
    }

    function renderCanvas() {
      const host = qs(overlay, '[data-vra-canvas-host]');
      if (!host) return;
      host.replaceChildren();
      const extents = graphExtents();
      const scroller = el('div', { class: 'vra-canvas-scroll' });
      const viewport = el('div', {
        class: 'vra-graph-viewport ' + (state.grid ? 'grid' : ''),
        style: `width:${extents.width}px;height:${extents.height}px;transform:scale(${state.zoom});transform-origin:0 0;`,
      });
      const svg = svgEl('svg', { class: 'vra-edge-layer', width: extents.width, height: extents.height });
      viewport.append(svg);

      for (const step of state.blueprint.workflow) {
        const pos = state.positions[step.id] || { x: 100, y: 100 };
        const meta = TYPE_META.get(step.type) || { label: step.type, group: 'Workflow' };
        const card = el('article', {
          class: 'vra-node ' + (step.id === state.selectedId ? 'selected ' : '') + (step.id === state.connectFrom ? 'connecting ' : ''),
          style: `left:${pos.x}px;top:${pos.y}px`,
          'data-step-id': step.id,
          onclick: event => {
            event.stopPropagation();
            if (state.connectFrom && state.connectFrom !== step.id) connect(state.connectFrom, step.id);
            else {
              state.selectedId = step.id;
              state.inspectorTab = 'step';
              renderCanvas();
              renderInspector();
            }
          },
        },
          el('div', { class: 'vra-node-head' },
            el('span', { class: 'vra-node-icon', text: meta.label.slice(0, 2).toUpperCase() }),
            el('div', {}, el('strong', { text: meta.label }), el('small', { text: meta.group })),
            el('button', {
              type: 'button', class: 'vra-node-more', title: 'Usuń',
              onclick: event => { event.stopPropagation(); removeStep(step.id); },
            }, '×')),
          el('div', { class: 'vra-node-id mono', text: step.id }),
          el('div', { class: 'vra-node-foot' },
            el('span', { text: (step.depends_on || []).length + ' wejść' }),
            el('button', {
              type: 'button', class: 'vra-connect-button',
              onclick: event => {
                event.stopPropagation();
                state.connectFrom = state.connectFrom === step.id ? null : step.id;
                renderCanvas();
                renderFooter();
              },
            }, state.connectFrom === step.id ? 'Anuluj' : 'Połącz →')));

        card.addEventListener('pointerdown', event => {
          if (event.button !== 0 || event.target.closest('button')) return;
          const start = { x: event.clientX, y: event.clientY, left: pos.x, top: pos.y };
          card.setPointerCapture(event.pointerId);
          state.dragging = step.id;
          const move = moveEvent => {
            const dx = (moveEvent.clientX - start.x) / state.zoom;
            const dy = (moveEvent.clientY - start.y) / state.zoom;
            state.positions[step.id] = {
              x: Math.max(10, Math.round(start.left + dx)),
              y: Math.max(10, Math.round(start.top + dy)),
            };
            card.style.left = state.positions[step.id].x + 'px';
            card.style.top = state.positions[step.id].y + 'px';
            renderEdges(svg);
          };
          const up = () => {
            card.removeEventListener('pointermove', move);
            state.dragging = null;
            state.dirty = true;
          };
          card.addEventListener('pointermove', move);
          card.addEventListener('pointerup', up, { once: true });
          card.addEventListener('pointercancel', up, { once: true });
        });
        viewport.append(card);
      }
      renderEdges(svg);
      viewport.addEventListener('click', () => {
        state.selectedId = null;
        renderCanvas();
        renderInspector();
      });
      scroller.addEventListener('dragover', event => {
        if (event.dataTransfer.types.includes('application/x-cloudportal-step')) {
          event.preventDefault();
          event.dataTransfer.dropEffect = 'copy';
        }
      });
      scroller.addEventListener('drop', event => {
        const type = event.dataTransfer.getData('application/x-cloudportal-step');
        if (!STEP_TYPES.includes(type)) return;
        event.preventDefault();
        const rect = scroller.getBoundingClientRect();
        const x = (event.clientX - rect.left + scroller.scrollLeft) / state.zoom;
        const y = (event.clientY - rect.top + scroller.scrollTop) / state.zoom;
        addStep(type, { x, y });
      });
      scroller.append(viewport);
      host.append(scroller);

      const zoom = el('div', { class: 'vra-zoom' },
        iconButton('−', 'Pomniejsz', () => { state.zoom = Math.max(.5, state.zoom - .1); renderCanvas(); }),
        el('span', { text: Math.round(state.zoom * 100) + '%' }),
        iconButton('+', 'Powiększ', () => { state.zoom = Math.min(1.8, state.zoom + .1); renderCanvas(); }),
        iconButton(state.grid ? 'Grid on' : 'Grid off', 'Siatka', () => { state.grid = !state.grid; renderCanvas(); }));
      host.append(zoom);
    }

    function renderStepInspector(body) {
      const step = selectedStep();
      if (!step) {
        body.append(el('div', { class: 'vra-empty' },
          el('strong', { text: 'Nie wybrano kroku' }),
          el('span', { text: 'Wybierz komponent na canvasie albo dodaj nowy z palety.' })));
        return;
      }
      const meta = TYPE_META.get(step.type);
      body.append(el('div', { class: 'vra-inspector-summary' },
        el('span', { class: 'vra-node-icon large', text: (meta?.label || step.type).slice(0, 2).toUpperCase() }),
        el('div', {}, el('strong', { text: meta?.label || step.type }), el('span', { text: meta?.description || '' }))));

      body.append(textField('ID kroku', step.id, value => renameStep(step, value)));
      const typeChoices = STEP_TYPES.map(type => ({ value: type, label: TYPE_META.get(type)?.label || type }));
      if (!STEP_TYPES.includes(step.type)) typeChoices.unshift({ value: step.type, label: 'Legacy / YAML: ' + step.type });
      body.append(selectField('Typ', step.type, typeChoices, value => mutate(() => {
        const previousType = step.type;
        const previousDefaultTimeout = defaultStepTimeout(previousType);
        step.type = value;
        if (Number(step.timeout) === previousDefaultTimeout) step.timeout = defaultStepTimeout(value);
      })));
      body.append(textField('Zależy od', (step.depends_on || []).join(', '), value => mutate(() => {
        step.depends_on = [...new Set(value.split(',').map(item => item.trim()).filter(Boolean))];
      }), { help: 'ID kroków oddzielone przecinkami.' }));

      const timeoutDefault = defaultStepTimeout(step.type);
      const terraformStep = TERRAFORM_STEP_TYPES.has(step.type);
      const timeoutLabel = terraformStep ? 'Timeout Terraform / OpenTofu [s]' : 'Timeout kroku [s]';
      const timeoutHelp = terraformStep
        ? 'Maksymalny czas dla tego kroku Terraform/OpenTofu. Domyślnie 3600 s.'
        : step.type === 'wait_for_ip'
          ? 'Adres IP jest sprawdzany co 10 s przez maksymalnie 180 s.'
          : 'Maksymalny czas wykonania tego kroku workflow. Domyślnie 600 s.';
      const advanced = el('details', {
        class: 'advanced-options wide',
        open: state.advancedOpen ? '' : null,
      },
        el('summary', { text: 'Opcje zaawansowane' }),
        el('div', { class: 'advanced-options-body' },
          textField('Retry', step.retry, value => mutate(() => { step.retry = Number(value || 0); }), { type: 'number', min: 0, max: 10 }),
          textField(timeoutLabel, step.timeout, value => mutate(() => {
            const parsed = Number(value);
            step.timeout = Number.isFinite(parsed) ? Math.max(1, Math.min(86400, parsed)) : timeoutDefault;
          }), { type: 'number', min: 1, max: 86400, help: timeoutHelp }),
          el('div', { class: 'vra-inspector-actions' },
            iconButton('Domyślny timeout', 'Przywróć domyślny timeout dla tego typu kroku', () => mutate(() => {
              step.timeout = defaultStepTimeout(step.type);
            }))),
          textField('Rollback step', step.rollback || '', value => mutate(() => { step.rollback = value.trim() || null; }), { placeholder: 'ID kroku' }),
          textField('Conditions (JSON)', JSON.stringify(step.conditions || {}, null, 2), value => {
            try {
              const parsed = parseJson(value, 'Conditions', {});
              mutate(() => { step.conditions = parsed; });
            } catch (error) { toast(error.message, 'error'); }
          }, { multiline: true, wide: true, help: 'Warunki runtime przekazywane bez zmian do workflow engine.' })
        ));
      advanced.addEventListener('toggle', () => { state.advancedOpen = advanced.open; });
      body.append(advanced);
      body.append(el('div', { class: 'vra-inspector-actions' },
        iconButton(state.connectFrom === step.id ? 'Anuluj łączenie' : 'Połącz z…', 'Dodaj zależność przez canvas', () => {
          state.connectFrom = state.connectFrom === step.id ? null : step.id;
          renderCanvas(); renderFooter();
        }),
        iconButton('Usuń krok', 'Usuń zaznaczony krok', () => removeStep(step.id), 'danger')));
    }

    function renderBlueprintInspector(body) {
      const bp = state.blueprint;
      const deployment = bp.deployment || (bp.deployment = {});
      const selectedProvider = data.providers.find(value => String(value.id) === String(deployment.provider_id)) || null;
      const providerChoices = data.providers.map(value => ({ value: value.id, label: value.name + ' [' + value.type + ']' }));
      const visibleTemplates = data.templates.filter(value =>
        value.provider === selectedProvider?.type
        && (value.enabled !== false || (state.id && value.id === deployment.template)));
      const templateChoices = visibleTemplates.map(value => ({
        value: value.id,
        label: value.name + ' [' + value.provider + ']' + (value.enabled === false ? ' — wyłączony' : ''),
      }));
      const providerCredential = data.credentials.find(value =>
        String(value.id) === String(selectedProvider?.credentials_id)) || null;
      const credentialChoices = selectedProvider?.credentials_id ? [{
        value: selectedProvider.credentials_id,
        label: providerCredential
          ? providerCredential.name + ' [' + providerCredential.type + ']'
          : 'Credential #' + selectedProvider.credentials_id,
      }] : [];

      body.append(textField('Nazwa', bp.name, value => mutate(() => {
        bp.name = value; if (!bp.slug || bp.slug === 'new-blueprint') bp.slug = slugify(value);
      })));
      body.append(textField('Slug', bp.slug, value => mutate(() => { bp.slug = slugify(value); })));
      body.append(textField('Opis', bp.description || '', value => mutate(() => { bp.description = value; }), { multiline: true }));
      body.append(el('div', { class: 'vra-check-grid' },
        checkboxField('Aktywny', bp.is_active, value => mutate(() => { bp.is_active = value; })),
        checkboxField('Approval policy', bp.requires_approval, value => mutate(() => { bp.requires_approval = value; })),
        checkboxField('Backend', bp.visibility?.backend, value => mutate(() => { bp.visibility.backend = value; })),
        checkboxField('CloudPortal', bp.visibility?.cloudportal, value => mutate(() => { bp.visibility.cloudportal = value; })),
        checkboxField('API', bp.visibility?.api, value => mutate(() => { bp.visibility.api = value; }))));
      body.append(selectField('Recovery', bp.recovery_policy, [
        { value: 'preserve', label: 'Preserve on failure' },
        { value: 'destroy_on_failure', label: 'Destroy on failure' },
      ], value => mutate(() => { bp.recovery_policy = value; })));

      body.append(el('h4', { class: 'vra-section-title', text: 'Deployment' }));
      if (providerChoices.length) body.append(selectField('Provider', deployment.provider_id, providerChoices, value => mutate(() => {
        const provider = data.providers.find(item => String(item.id) === String(value));
        deployment.provider_id = Number(value);
        if (provider?.credentials_id) deployment.credentials_id = Number(provider.credentials_id);
        const template = data.templates.find(item => item.provider === provider?.type && item.enabled !== false);
        deployment.template = template?.id || '';
      })));
      else body.append(textField('Provider ID', deployment.provider_id, value => mutate(() => { deployment.provider_id = Number(value); }), { type: 'number' }));

      if (credentialChoices.length) {
        if (String(deployment.credentials_id) !== String(selectedProvider.credentials_id)) {
          deployment.credentials_id = Number(selectedProvider.credentials_id);
        }
        body.append(selectField('Credential providera', deployment.credentials_id, credentialChoices, () => {}));
      } else {
        body.append(el('div', { class: 'vra-empty' },
          el('strong', { text: 'Brak credentiala providera' }),
          el('span', { text: 'Wybrany provider nie ma przypisanego credentiala wymaganego do wdrożenia.' })));
      }

      if (templateChoices.length) {
        body.append(selectField('Template', deployment.template, templateChoices, value => mutate(() => { deployment.template = value; })));
      } else {
        body.append(el('div', { class: 'vra-empty' },
          el('strong', { text: 'Brak zgodnego template' }),
          el('span', { text: 'Dla wybranego providera nie ma aktywnego template infrastruktury.' })));
      }

      body.append(textField('Deployment name', deployment.name || '', value => mutate(() => { deployment.name = value; }), { placeholder: '{{ hostname }}' }));
      body.append(selectField('Executor', deployment.executor || 'terraform', [
        { value: 'terraform', label: 'Terraform' }, { value: 'opentofu', label: 'OpenTofu' },
      ], value => mutate(() => { deployment.executor = value; })));
      body.append(textField('Variables (JSON)', JSON.stringify(deployment.variables || {}, null, 2), value => {
        try { const parsed = parseJson(value, 'Variables', {}); mutate(() => { deployment.variables = parsed; }); }
        catch (error) { toast(error.message, 'error'); }
      }, { multiline: true }));
      body.append(textField('Ansible (JSON / null)', deployment.ansible ? JSON.stringify(deployment.ansible, null, 2) : '', value => {
        try { const parsed = value.trim() ? parseJson(value, 'Ansible', {}) : null; mutate(() => { deployment.ansible = parsed; }); }
        catch (error) { toast(error.message, 'error'); }
      }, { multiline: true }));

      body.append(el('h4', { class: 'vra-section-title', text: 'Inputs i dostęp' }));
      body.append(textField('Variables schema (JSON)', JSON.stringify(bp.variables_schema || {}, null, 2), value => {
        try { const parsed = parseJson(value, 'Variables schema', {}); mutate(() => { bp.variables_schema = parsed; }); }
        catch (error) { toast(error.message, 'error'); }
      }, { multiline: true }));
      body.append(textField('Allowed role IDs', (bp.allowed_role_ids || []).join(', '), value => mutate(() => {
        bp.allowed_role_ids = parsePositiveIds(value);
      })));
      body.append(textField('Allowed user IDs', (bp.allowed_user_ids || []).join(', '), value => mutate(() => {
        bp.allowed_user_ids = parsePositiveIds(value);
      })));
      body.append(textField('Manager role IDs', (bp.manager_role_ids || []).join(', '), value => mutate(() => {
        bp.manager_role_ids = parsePositiveIds(value);
      })));
    }

    function renderYamlInspector(body) {
      const tools = el('div', { class: 'vra-yaml-toolbar' },
        iconButton('Generuj z GUI', 'Zbuduj YAML z aktualnego modelu', async () => {
          if (await syncYamlFromModel()) renderInspector();
        }),
        iconButton('Zastosuj YAML', 'Waliduj YAML i odtwórz canvas', applyYaml, 'primary'),
        iconButton('Kopiuj', 'Kopiuj YAML', copyYaml),
        iconButton('Pobierz', 'Pobierz plik .yaml', exportYaml));
      const file = el('input', { type: 'file', accept: '.yaml,.yml,application/yaml,text/yaml', class: 'vra-yaml-file' });
      file.addEventListener('change', () => importYamlFile(file.files?.[0]));
      tools.append(el('label', { class: 'vra-file-label' }, 'Import pliku', file));

      const textarea = el('textarea', {
        class: 'vra-yaml-editor mono',
        'data-vra-yaml': 'true',
        spellcheck: 'false',
      });
      textarea.value = state.yamlDirty
        ? '# YAML jest niezsynchronizowany z GUI. Kliknij „Generuj z GUI”.\n' + (state.yaml || '')
        : state.yaml;
      textarea.addEventListener('input', () => {
        state.yaml = textarea.value;
        state.yamlDirty = false;
        state.dirty = true;
      });
      body.append(tools, textarea,
        el('div', { class: 'vra-yaml-hint' },
          el('strong', { text: 'Format' }),
          el('span', { text: 'cloudportal.io/v1 / Blueprint. Parser akceptuje również natywny payload API.' })));
    }

    function renderInspector() {
      const root = qs(overlay, '[data-vra-inspector]');
      if (!root) return;
      root.replaceChildren();
      const tabs = el('div', { class: 'vra-inspector-tabs' });
      for (const [id, label] of [['step', 'Krok'], ['blueprint', 'Blueprint'], ['yaml', 'YAML']]) {
        tabs.append(el('button', {
          type: 'button', class: state.inspectorTab === id ? 'active' : '',
          text: label,
          onclick: () => { state.inspectorTab = id; if (id === 'yaml' && !state.yaml) syncYamlFromModel().then(renderInspector); else renderInspector(); },
        }));
      }
      const body = el('div', { class: 'vra-inspector-body' });
      if (state.inspectorTab === 'step') renderStepInspector(body);
      else if (state.inspectorTab === 'blueprint') renderBlueprintInspector(body);
      else renderYamlInspector(body);
      root.append(tabs, body);
    }

    function renderFooter() {
      state.validation = validateGraph(state.blueprint);
      footer.replaceChildren();
      const edges = state.blueprint.workflow.reduce((sum, step) => sum + (step.depends_on || []).length, 0);
      const status = state.serverValidation
        ? (state.serverValidation.ok ? 'API: poprawny' : 'API: błąd')
        : (state.validation.errors.length ? 'Błędy grafu' : state.validation.warnings.length ? 'Ostrzeżenia' : 'Graf poprawny');
      footer.append(
        el('span', { text: 'Kroki: ' + state.blueprint.workflow.length }),
        el('span', { text: 'Połączenia: ' + edges }),
        el('span', { text: 'Zoom: ' + Math.round(state.zoom * 100) + '%' }),
        state.connectFrom ? el('span', { class: 'warn', text: 'Łączenie: ' + state.connectFrom + ' → wybierz cel' }) : null,
        el('span', { class: state.serverValidation?.ok === false || state.validation.errors.length ? 'error' : state.validation.warnings.length ? 'warn' : 'ok', text: status }),
        state.serverValidation?.message ? el('span', { class: 'vra-status-message', text: state.serverValidation.message }) : null);
    }

    function renderMain() {
      main.replaceChildren();
      const palette = renderPalette();
      const canvasPane = el('section', { class: 'vra-canvas-pane' },
        el('div', { class: 'vra-canvas-title' },
          el('div', {}, el('strong', { text: 'Design' }), el('span', { text: 'DAG provisioningu' })),
          el('div', { class: 'vra-canvas-help', text: 'Kliknij „Połącz →”, potem krok docelowy. Podwójny klik na linii usuwa zależność.' })),
        el('div', { class: 'vra-canvas-host', 'data-vra-canvas-host': 'true' }));
      const inspector = el('aside', { class: 'vra-inspector', 'data-vra-inspector': 'true' });
      main.append(palette, canvasPane, inspector);
      renderCanvas();
      renderInspector();
    }

    function render() {
      renderHeader();
      renderMain();
      renderFooter();
    }

    render();
    if (item?.id) {
      api('/blueprints/' + item.id + '/yaml')
        .then(result => { state.yaml = result.yaml || ''; state.yamlDirty = false; if (state.inspectorTab === 'yaml') renderInspector(); })
        .catch(() => {});
    } else {
      syncYamlFromModel().then(() => { if (state.inspectorTab === 'yaml') renderInspector(); });
    }
  }

  registerRoutedForm({
    id: 'blueprint-designer-create',
    pattern: /^\/blueprints\/designer\/new$/,
    parent: 'blueprints',
    permission: 'blueprints.create',
    label: 'Blueprint Designer',
    surface: false,
  }, () => openDesigner());
  registerRoutedForm({
    id: 'blueprint-designer-edit',
    pattern: /^\/blueprints\/(?<id>\d+)(?:\/[^/]+)?\/designer$/,
    parent: 'blueprints',
    permission: 'blueprints.update',
    label: 'Blueprint Designer',
    surface: false,
  }, async match => openDesigner(await api('/blueprints/' + match.params.id)));

  window.BlueprintVRADesigner = {
    open: item => item
      ? navigate('/blueprints/' + encodeURIComponent(item.id) + '/' + encodeURIComponent(item.slug || item.name || 'blueprint') + '/designer')
      : navigate('/blueprints/designer/new'),
    render: openDesigner,
    validateReferences: validateBlueprintReferences,
  };
  registerExtension('blueprint-vra-designer', () => {});
})();
