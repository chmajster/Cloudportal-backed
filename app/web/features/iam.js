'use strict';

(() => {
let activeTab = 'assignments';
let selectedUserId = null;

const TABS = [
  ['assignments', 'Przypisania'],
  ['groups', 'Grupy'],
  ['simulator', 'RBAC Simulator'],
  ['review', 'Access Review'],
];

const SCOPE_TYPES = [
  ['GLOBAL', 'System'],
  ['ORGANIZATION', 'Organization'],
  ['PROJECT', 'Project'],
  ['APMID', 'APMID'],
  ['ENVIRONMENT', 'Environment'],
  ['RESOURCE_POOL', 'Resource Pool'],
  ['BLUEPRINT', 'Blueprint'],
  ['DEPLOYMENT', 'Deployment'],
  ['RESOURCE', 'Resource'],
  ['MACHINE', 'Machine'],
];

async function iamApi(path, options = {}, canRefresh = true) {
  return api(path, { ...options, scope: false }, canRefresh);
}

async function iamAll(path) {
  const items = [];
  let offset = 0;
  let total = Number.POSITIVE_INFINITY;
  while (offset < total) {
    const separator = path.includes('?') ? '&' : '?';
    const page = await iamApi(path + separator + 'limit=200&offset=' + offset);
    const chunk = page.items || [];
    items.push(...chunk);
    total = Number.isFinite(Number(page.total)) ? Number(page.total) : items.length;
    if (!chunk.length) break;
    offset += chunk.length;
  }
  return { items, total: Number.isFinite(total) ? total : items.length };
}

async function optionalApi(path, fallback) {
  try { return await iamApi(path); }
  catch { return fallback; }
}

function assignmentScope(item) {
  const parts = [item.scope_type];
  if (item.tenant_id) parts.push('org:' + item.tenant_id);
  if (item.project_id) parts.push('project:' + item.project_id);
  if (item.apmid) parts.push('APMID:' + item.apmid);
  if (item.environment) parts.push('ENV:' + item.environment);
  if (item.scope_id && ![item.tenant_id, item.project_id].includes(item.scope_id)) parts.push(item.scope_id);
  return parts.join(' / ');
}

function effectBadge(item) {
  if (item.effect === 'DENY') return badge('DENY', 'danger');
  if (item.approval_required) return badge('ALLOW + APPROVAL', 'warning');
  return badge('ALLOW', 'ok');
}

function statusBadge(value) {
  const kind = value === 'ACTIVE' ? 'ok' : value === 'EXPIRED' ? 'danger' : 'warning';
  return badge(value, kind);
}

function tabBar() {
  return node('div', { class: 'rbac-tabs', role: 'tablist', 'aria-label': 'Enterprise IAM' },
    ...TABS.map(([id, label]) => node('button', {
      type: 'button',
      class: 'rbac-tab ' + (activeTab === id ? 'active' : ''),
      role: 'tab',
      'aria-selected': String(activeTab === id),
      onClick: () => {
        activeTab = id;
        enterpriseIamView().catch(error => toast(error.message, 'error'));
      },
    }, label)));
}

function subjectLabel(item, users, groups) {
  if (item.subject_type === 'USER' || item.subject_type === 'SERVICE_ACCOUNT') {
    const user = users.find(value => String(value.id) === String(item.subject_id));
    return user ? user.username : item.subject_type + ':' + item.subject_id;
  }
  if (item.subject_type === 'GROUP') {
    const group = groups.find(value => String(value.id) === String(item.subject_id));
    return group ? 'Grupa: ' + group.name : 'GROUP:' + item.subject_id;
  }
  return item.subject_type + ':' + item.subject_id;
}

function iamRequiredPermission(error) {
  const detail = error?.data?.detail ?? error?.data ?? {};
  const required = detail?.required_permission;
  if (Array.isArray(required)) return required.join(' lub ');
  return required ? String(required) : '';
}

function iamLoadErrorMessage(label, path, error) {
  const lines = [
    'Nie udało się pobrać ' + label + '.',
    'HTTP ' + (error?.status || 'błąd sieci'),
    'GET /api/v1' + path,
  ];
  const permission = iamRequiredPermission(error);
  if (permission) lines.push('Brak permission: ' + permission);
  else if (error?.message) lines.push(error.message);
  return lines.join('\n');
}

function iamDiagnostic(label, path, error) {
  return node('pre', {
    class: 'form-error iam-load-error wide',
    text: iamLoadErrorMessage(label, path, error),
  });
}

function subjectChoiceLabel(item, kind) {
  if (kind === 'GROUP') return item.name || String(item.id);
  if (kind === 'API_TOKEN') {
    return (item.name || 'Token') + (item.token_prefix ? ' · ' + item.token_prefix : '');
  }
  const display = item.display_name
    || [item.first_name, item.last_name].filter(Boolean).join(' ')
    || item.username
    || String(item.id);
  return display + (item.email ? ' · ' + item.email : '');
}

function setIamFieldVisible(wrapper, visible) {
  wrapper.hidden = !visible;
  wrapper.querySelectorAll('input,select,textarea,button').forEach(control => {
    control.disabled = !visible;
  });
}

async function accessAssignmentForm(forcedUserId = null) {
  const formHost = node('div', { class: 'form-grid' },
    node('div', { class: 'field-help wide', text: 'Ładowanie użytkowników...' }),
    node('div', { class: 'field-help wide', text: 'Ładowanie organizacji...' }));

  let ready = false;
  let updateSubmitState = () => {};
  let loadSubjects = async () => {};

  openModal({
    title: forcedUserId ? 'Przypisz dostęp użytkownikowi' : 'Nowy RoleAssignment',
    eyebrow: 'Enterprise IAM',
    body: formHost,
    wide: true,
    submitLabel: 'Przypisz dostęp',
    onSubmit: async (_data, form) => {
      if (!ready) throw new Error('Dane formularza IAM nie zostały jeszcze załadowane.');

      const value = name => String(form.elements[name]?.value || '').trim();
      const kind = value('scope_type');
      const requiredByScope = {
        ORGANIZATION: ['tenant_id'],
        PROJECT: ['tenant_id', 'project_id'],
        APMID: ['tenant_id', 'project_id', 'apmid'],
        ENVIRONMENT: ['tenant_id', 'project_id', 'apmid', 'environment'],
        RESOURCE_POOL: ['tenant_id', 'project_id', 'scope_id'],
        BLUEPRINT: ['tenant_id', 'project_id', 'scope_id'],
        DEPLOYMENT: ['tenant_id', 'project_id', 'scope_id'],
        RESOURCE: ['tenant_id', 'project_id', 'scope_id'],
        MACHINE: ['tenant_id', 'project_id', 'scope_id'],
      };
      for (const name of requiredByScope[kind] || []) {
        if (!value(name)) throw new Error('Uzupełnij wymagane pole dla scope: ' + name + '.');
      }
      if (!value('subject_id')) throw new Error('Wybierz Subject.');
      if (!value('role_id')) throw new Error('Wybierz rolę.');

      let conditionTree = {};
      const rawConditions = value('conditions');
      if (rawConditions) {
        try { conditionTree = JSON.parse(rawConditions); }
        catch { throw new Error('Conditions JSON nie jest poprawnym JSON-em.'); }
        if (!conditionTree || Array.isArray(conditionTree) || typeof conditionTree !== 'object') {
          throw new Error('Conditions JSON musi być obiektem JSON.');
        }
      }

      const payload = {
        subject_type: forcedUserId ? 'USER' : value('subject_type'),
        subject_id: String(forcedUserId || value('subject_id')),
        role_id: Number(value('role_id')),
        effect: value('effect'),
        scope_type: kind,
        conditions: conditionTree,
        inherit: Boolean(form.elements.inherit?.checked),
        approval_required: Boolean(form.elements.approval_required?.checked),
        enabled: true,
      };
      if (kind !== 'GLOBAL' && value('tenant_id')) payload.tenant_id = value('tenant_id');
      if (!['GLOBAL', 'ORGANIZATION'].includes(kind) && value('project_id')) payload.project_id = value('project_id');
      if (kind === 'ORGANIZATION') payload.scope_id = value('tenant_id');
      if (kind === 'PROJECT') payload.scope_id = value('project_id');
      if (['APMID', 'ENVIRONMENT'].includes(kind)) payload.apmid = value('apmid');
      if (kind === 'ENVIRONMENT') payload.environment = value('environment');
      if (['RESOURCE_POOL', 'BLUEPRINT', 'DEPLOYMENT', 'RESOURCE', 'MACHINE'].includes(kind)) {
        payload.scope_id = value('scope_id');
      }
      if (value('valid_from')) payload.valid_from = new Date(value('valid_from')).toISOString();
      if (value('valid_until')) payload.valid_until = new Date(value('valid_until')).toISOString();

      await iamApi('/rbac/assignments', { method: 'POST', body: payload });
      toast('Dostęp przypisany.');
      activeTab = 'assignments';
      await enterpriseIamView();
    },
  });

  const form = document.querySelector('#modal-form');
  const submit = document.querySelector('#modal-actions button[type="submit"]')
    || dom.modalActions.querySelector('button[type="submit"]');
  if (submit) submit.disabled = true;

  const initialSubjectPath = '/iam/subjects?type=USER';
  const rolesPath = '/iam/roles';
  const organizationsPath = '/iam/organizations';

  let initialSubjects;
  let rolesResult;
  let organizationsResult;
  try {
    [initialSubjects, rolesResult, organizationsResult] = await Promise.all([
      iamAll(initialSubjectPath),
      iamAll(rolesPath),
      iamAll(organizationsPath),
    ]);
  } catch (error) {
    let label = 'danych IAM';
    let path = '/iam/subjects?type=USER';
    if (error?.data?.detail?.required_permission === 'roles.read'
        || error?.data?.detail?.required_permission === 'iam.roles.read') {
      label = 'ról'; path = rolesPath;
    } else if (String(error?.message || '').toLowerCase().includes('organization')) {
      label = 'organizacji'; path = organizationsPath;
    }
    formHost.replaceChildren(iamDiagnostic(label, path, error));
    toast(iamLoadErrorMessage(label, path, error), 'error');
    return;
  }

  const roles = rolesResult.items || [];
  const organizations = organizationsResult.items || [];
  const subjectType = selectField('Subject type', 'subject_type', [
    { value: 'USER', label: 'User' },
    { value: 'GROUP', label: 'Group' },
    { value: 'SERVICE_ACCOUNT', label: 'Service Account' },
    { value: 'API_TOKEN', label: 'API Token' },
  ], 'USER', { required: true });

  const subjectHost = node('div', { class: 'wide' });
  const role = searchableSelectField('Rola', 'role_id', roles.map(item => ({
    value: item.id,
    label: item.name + (item.system_role ? ' · system' : ''),
  })), '', {
    required: true,
    wide: true,
    selectFirst: false,
    placeholder: roles.length ? 'Wpisz nazwę roli…' : 'Brak ról',
  });
  if (!roles.length) role.append(node('span', { class: 'field-help', text: 'Brak ról' }));

  const effect = selectField('Effect', 'effect', [
    { value: 'ALLOW', label: 'ALLOW' },
    { value: 'DENY', label: 'DENY' },
  ], 'ALLOW', { required: true });
  const scopeType = selectField('Scope', 'scope_type',
    SCOPE_TYPES.map(([scopeValue, label]) => ({ value: scopeValue, label })),
    'GLOBAL', { required: true });

  const organization = searchableSelectField('Organization', 'tenant_id', organizations.map(item => ({
    value: item.id,
    label: item.name + (item.slug ? ' · ' + item.slug : ''),
  })), '', {
    wide: true,
    selectFirst: false,
    placeholder: organizations.length ? 'Wpisz nazwę organizacji…' : 'Brak organizacji',
  });
  const organizationStatus = node('span', {
    class: 'field-help',
    text: organizations.length ? '' : 'Brak organizacji',
  });
  organization.append(organizationStatus);

  const project = searchableSelectField('Project', 'project_id', [], '', {
    wide: true,
    selectFirst: false,
    placeholder: 'Najpierw wybierz Organization',
  });
  const projectStatus = node('span', { class: 'field-help', text: '' });
  project.append(projectStatus);

  const apmid = searchableSelectField('APMID', 'apmid', [], '', {
    wide: true,
    selectFirst: false,
    placeholder: 'Najpierw wybierz Project',
  });
  const apmidStatus = node('span', { class: 'field-help', text: '' });
  apmid.append(apmidStatus);

  const environment = searchableSelectField('ENV', 'environment', [], '', {
    wide: true,
    selectFirst: false,
    placeholder: 'Najpierw wybierz APMID',
  });
  const environmentStatus = node('span', { class: 'field-help', text: '' });
  environment.append(environmentStatus);

  const scopeId = field('Resource / scope ID', 'scope_id', { maxlength: 160, wide: true });
  const conditions = field('Conditions JSON', 'conditions', {
    tag: 'textarea',
    value: '{}',
    wide: true,
    help: 'Bez eval(). Obsługiwany jest bezpieczny condition tree Policy Engine.',
  });
  const validFrom = field('Valid from', 'valid_from', { type: 'datetime-local' });
  const validUntil = field('Valid until', 'valid_until', { type: 'datetime-local' });
  const inherit = checkboxField('Dziedzicz do scope potomnych', 'inherit', true);
  const approval = checkboxField('Wymagaj approval przed operacją', 'approval_required', false);

  formHost.replaceChildren(
    subjectType, subjectHost, role, effect, scopeType, organization, project, apmid,
    environment, scopeId, validFrom, validUntil, inherit, approval, conditions);

  const subjectTypeSelect = subjectType.querySelector('select');
  const scopeSelect = scopeType.querySelector('select');
  if (forcedUserId) subjectTypeSelect.disabled = true;

  let subjectGeneration = 0;
  loadSubjects = async (kind, preferred = '') => {
    const generation = ++subjectGeneration;
    const labelMap = {
      USER: 'użytkowników',
      GROUP: 'grup',
      SERVICE_ACCOUNT: 'kont serwisowych',
      API_TOKEN: 'tokenów API',
    };
    const label = labelMap[kind] || 'Subject';
    const path = '/iam/subjects?type=' + encodeURIComponent(kind);
    subjectHost.replaceChildren(node('div', { class: 'field-help', text: 'Ładowanie ' + label + '...' }));
    updateSubmitState();
    try {
      const result = kind === 'USER' && generation === 1
        ? initialSubjects
        : await iamAll(path);
      if (generation !== subjectGeneration) return;
      const choices = (result.items || []).map(item => ({
        value: item.id,
        label: subjectChoiceLabel(item, kind),
      }));
      const control = searchableSelectField('Subject', 'subject_id', choices, preferred, {
        required: true,
        wide: true,
        selectFirst: false,
        placeholder: choices.length ? 'Wpisz nazwę lub e-mail…' : 'Brak ' + label,
      });
      if (!choices.length) control.append(node('span', {
        class: 'field-help',
        text: kind === 'GROUP' ? 'Brak grup' : 'Brak użytkowników',
      }));
      control.searchableSelect.onChange(updateSubmitState);
      subjectHost.replaceChildren(control);
      updateSubmitState();
    } catch (error) {
      if (generation !== subjectGeneration) return;
      subjectHost.replaceChildren(iamDiagnostic(label, path, error));
      toast(iamLoadErrorMessage(label, path, error), 'error');
      updateSubmitState();
    }
  };

  let projectGeneration = 0;
  async function loadProjects(organizationId) {
    const generation = ++projectGeneration;
    project.searchableSelect.setChoices([], '');
    apmid.searchableSelect.setChoices([], '');
    environment.searchableSelect.setChoices([], '');
    if (!organizationId) {
      projectStatus.textContent = 'Najpierw wybierz Organization';
      updateSubmitState();
      return;
    }
    const path = '/iam/projects?organization_id=' + encodeURIComponent(organizationId);
    projectStatus.textContent = 'Ładowanie projektów...';
    updateSubmitState();
    try {
      const result = await iamAll(path);
      if (generation !== projectGeneration) return;
      const choices = (result.items || []).map(item => ({
        value: item.id,
        label: item.name + (item.slug ? ' · ' + item.slug : ''),
      }));
      project.searchableSelect.setChoices(choices, '');
      projectStatus.textContent = choices.length ? '' : 'Brak projektów';
      updateSubmitState();
    } catch (error) {
      if (generation !== projectGeneration) return;
      projectStatus.textContent = iamLoadErrorMessage('projektów', path, error);
      toast(projectStatus.textContent, 'error');
      updateSubmitState();
    }
  }

  let classificationGeneration = 0;
  async function loadClassification() {
    const organizationId = organization.searchableSelect.value();
    const projectId = project.searchableSelect.value();
    const generation = ++classificationGeneration;
    apmid.searchableSelect.setChoices([], '');
    environment.searchableSelect.setChoices([], '');
    if (!organizationId || !projectId) {
      apmidStatus.textContent = 'Najpierw wybierz Project';
      environmentStatus.textContent = '';
      updateSubmitState();
      return;
    }
    const path = '/iam/apmids?organization_id=' + encodeURIComponent(organizationId)
      + '&project_id=' + encodeURIComponent(projectId);
    apmidStatus.textContent = 'Ładowanie APMID...';
    updateSubmitState();
    try {
      const result = await iamApi(path);
      if (generation !== classificationGeneration) return;
      const choices = (result.items || []).map(item => ({ value: item.id, label: item.name }));
      apmid.searchableSelect.setChoices(choices, '');
      apmidStatus.textContent = choices.length ? '' : 'Brak APMID';
      updateSubmitState();
    } catch (error) {
      if (generation !== classificationGeneration) return;
      apmidStatus.textContent = iamLoadErrorMessage('APMID', path, error);
      toast(apmidStatus.textContent, 'error');
      updateSubmitState();
    }
  }

  let environmentGeneration = 0;
  async function loadEnvironments() {
    const organizationId = organization.searchableSelect.value();
    const projectId = project.searchableSelect.value();
    const apmidValue = apmid.searchableSelect.value();
    const generation = ++environmentGeneration;
    environment.searchableSelect.setChoices([], '');
    if (!organizationId || !projectId || !apmidValue) {
      environmentStatus.textContent = 'Najpierw wybierz APMID';
      updateSubmitState();
      return;
    }
    const path = '/iam/environments?organization_id=' + encodeURIComponent(organizationId)
      + '&project_id=' + encodeURIComponent(projectId)
      + '&apmid=' + encodeURIComponent(apmidValue);
    environmentStatus.textContent = 'Ładowanie ENV...';
    updateSubmitState();
    try {
      const result = await iamApi(path);
      if (generation !== environmentGeneration) return;
      const choices = (result.items || []).map(item => ({ value: item.id, label: item.name }));
      environment.searchableSelect.setChoices(choices, '');
      environmentStatus.textContent = choices.length ? '' : 'Brak środowisk ENV';
      updateSubmitState();
    } catch (error) {
      if (generation !== environmentGeneration) return;
      environmentStatus.textContent = iamLoadErrorMessage('ENV', path, error);
      toast(environmentStatus.textContent, 'error');
      updateSubmitState();
    }
  }

  function syncScope() {
    const kind = scopeSelect.value;
    const needsOrganization = kind !== 'GLOBAL';
    const needsProject = !['GLOBAL', 'ORGANIZATION'].includes(kind);
    setIamFieldVisible(organization, needsOrganization);
    setIamFieldVisible(project, needsProject);
    setIamFieldVisible(apmid, ['APMID', 'ENVIRONMENT'].includes(kind));
    setIamFieldVisible(environment, kind === 'ENVIRONMENT');
    setIamFieldVisible(scopeId, ['RESOURCE_POOL', 'BLUEPRINT', 'DEPLOYMENT', 'RESOURCE', 'MACHINE'].includes(kind));
    if (needsProject && organization.searchableSelect.value() && !project.searchableSelect.value()) {
      loadProjects(organization.searchableSelect.value()).catch(error => toast(error.message, 'error'));
    }
    updateSubmitState();
  }

  updateSubmitState = () => {
    if (!submit || !form) return;
    const value = name => String(form.elements[name]?.value || '').trim();
    const kind = value('scope_type');
    let validConditions = true;
    const raw = value('conditions');
    if (raw) {
      try {
        const parsed = JSON.parse(raw);
        validConditions = Boolean(parsed) && !Array.isArray(parsed) && typeof parsed === 'object';
      } catch {
        validConditions = false;
      }
    }
    conditions.querySelector('textarea')?.setCustomValidity(
      validConditions ? '' : 'Conditions JSON musi być poprawnym obiektem JSON.'
    );
    const requirements = {
      GLOBAL: [],
      ORGANIZATION: ['tenant_id'],
      PROJECT: ['tenant_id', 'project_id'],
      APMID: ['tenant_id', 'project_id', 'apmid'],
      ENVIRONMENT: ['tenant_id', 'project_id', 'apmid', 'environment'],
      RESOURCE_POOL: ['tenant_id', 'project_id', 'scope_id'],
      BLUEPRINT: ['tenant_id', 'project_id', 'scope_id'],
      DEPLOYMENT: ['tenant_id', 'project_id', 'scope_id'],
      RESOURCE: ['tenant_id', 'project_id', 'scope_id'],
      MACHINE: ['tenant_id', 'project_id', 'scope_id'],
    };
    const hasRequiredScope = (requirements[kind] || []).every(name => Boolean(value(name)));
    submit.disabled = !(
      ready
      && Boolean(forcedUserId || value('subject_id'))
      && Boolean(value('role_id'))
      && Boolean(value('effect'))
      && Boolean(kind)
      && hasRequiredScope
      && validConditions
    );
  };

  subjectTypeSelect.addEventListener('change', () => {
    loadSubjects(subjectTypeSelect.value).catch(error => toast(error.message, 'error'));
  });
  scopeSelect.addEventListener('change', syncScope);
  organization.searchableSelect.onChange(organizationId => {
    loadProjects(organizationId).catch(error => toast(error.message, 'error'));
    updateSubmitState();
  });
  project.searchableSelect.onChange(() => {
    loadClassification().catch(error => toast(error.message, 'error'));
    updateSubmitState();
  });
  apmid.searchableSelect.onChange(() => {
    loadEnvironments().catch(error => toast(error.message, 'error'));
    updateSubmitState();
  });
  role.searchableSelect.onChange(updateSubmitState);
  form.addEventListener('input', updateSubmitState);
  form.addEventListener('change', updateSubmitState);

  ready = true;
  await loadSubjects('USER', forcedUserId ? String(forcedUserId) : '');
  syncScope();
  updateSubmitState();
}

function iamDateTimeLocal(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 16);
}

function editAssignmentForm(item) {
  const effect = selectField('Effect', 'effect', [
    { value: 'ALLOW', label: 'ALLOW' },
    { value: 'DENY', label: 'DENY' },
  ], item.effect || 'ALLOW', { required: true });
  const validFrom = field('Valid from', 'valid_from', {
    type: 'datetime-local',
    value: iamDateTimeLocal(item.valid_from),
  });
  const validUntil = field('Valid until', 'valid_until', {
    type: 'datetime-local',
    value: iamDateTimeLocal(item.valid_until),
  });
  const inherit = checkboxField('Dziedzicz do scope potomnych', 'inherit', Boolean(item.inherit));
  const approval = checkboxField(
    'Wymagaj approval przed operacją',
    'approval_required',
    Boolean(item.approval_required),
  );
  const enabled = checkboxField('Binding aktywny', 'enabled', item.enabled !== false);
  const conditions = field('Conditions JSON', 'conditions', {
    tag: 'textarea',
    value: JSON.stringify(item.conditions || {}, null, 2),
    wide: true,
    help: 'Zmiana jest zapisywana natychmiast w RoleAssignment i Policy Engine korzysta z niej przy następnym evaluation.',
  });
  const body = node('div', { class: 'form-grid' },
    node('div', { class: 'wide panel' },
      node('strong', { text: item.role_name || 'RoleAssignment' }),
      node('div', { class: 'muted mono', text: assignmentScope(item) })),
    effect, validFrom, validUntil, inherit, approval, enabled, conditions);

  openModal({
    title: 'Edytuj przypisanie',
    eyebrow: 'Enterprise IAM · ' + item.id,
    body,
    wide: true,
    submitLabel: 'Zapisz zmiany',
    onSubmit: async (_data, form) => {
      const raw = String(form.elements.conditions?.value || '').trim();
      let conditionTree = {};
      if (raw) {
        try { conditionTree = JSON.parse(raw); }
        catch { throw new Error('Conditions JSON nie jest poprawnym JSON-em.'); }
        if (!conditionTree || Array.isArray(conditionTree) || typeof conditionTree !== 'object') {
          throw new Error('Conditions JSON musi być obiektem JSON.');
        }
      }
      const from = String(form.elements.valid_from?.value || '').trim();
      const until = String(form.elements.valid_until?.value || '').trim();
      if (from && until && new Date(until) <= new Date(from)) {
        throw new Error('Valid until musi być późniejsze niż Valid from.');
      }
      const payload = {
        effect: form.elements.effect.value,
        conditions: conditionTree,
        inherit: Boolean(form.elements.inherit?.checked),
        approval_required: Boolean(form.elements.approval_required?.checked),
        enabled: Boolean(form.elements.enabled?.checked),
        valid_from: from ? new Date(from).toISOString() : null,
        valid_until: until ? new Date(until).toISOString() : null,
      };
      await iamApi('/rbac/assignments/' + encodeURIComponent(item.id), {
        method: 'PATCH',
        body: payload,
      });
      toast('Przypisanie zaktualizowane.');
      await enterpriseIamView();
    },
  });
}

async function assignmentsPanel() {
  const query = selectedUserId
    ? '?limit=200&subject_type=USER&subject_id=' + encodeURIComponent(selectedUserId)
    : '?limit=200';
  const [assignmentResult, userResult, groupResult] = await Promise.all([
    iamApi('/rbac/assignments' + query),
    iamAll('/iam/subjects?type=USER'),
    iamAll('/iam/subjects?type=GROUP'),
  ]);
  const assignments = assignmentResult.items || [];
  const users = userResult.items || [];
  const groups = groupResult.items || [];
  const selected = users.find(item => Number(item.id) === Number(selectedUserId));

  const actions = [];
  const canCreateBinding = allowed('iam.assign') || allowed('iam.binding.create')
    || allowed('rbac.assignments.manage') || allowed('roles.assign');
  const canUpdateBinding = allowed('iam.binding.update')
    || allowed('rbac.assignments.manage') || allowed('roles.assign');
  const canDeleteBinding = allowed('iam.binding.delete')
    || allowed('rbac.assignments.manage') || allowed('roles.assign');
  if (canCreateBinding) {
    actions.push(button(selected ? 'Przypisz dostęp: ' + selected.username : 'Nowe przypisanie',
      () => accessAssignmentForm(selectedUserId).catch(error => toast(error.message, 'error')), 'primary'));
  }
  if (selectedUserId) actions.push(button('Pokaż wszystkie', () => {
    selectedUserId = null;
    enterpriseIamView().catch(error => toast(error.message, 'error'));
  }));

  return node('div', { class: 'rbac-tab-body rbac-stack' },
    node('section', { class: 'rbac-section' },
      node('div', { class: 'rbac-section-heading' },
        node('div', {},
          node('span', { class: 'rbac-kicker', text: selected ? 'Użytkownik: ' + selected.username : 'RoleAssignment' }),
          node('h2', { text: 'Efektywne źródła dostępu' }),
          node('p', { class: 'muted', text: 'ALLOW sumuje role, explicit DENY ma pierwszeństwo. Expired assignment pozostaje widoczny dla audytu.' })),
        node('div', { class: 'action-group' }, actions)),
      assignments.length ? table([
        { label: 'Subject', value: item => subjectLabel(item, users, groups) },
        { label: 'Scope', value: item => node('code', { class: 'mono', text: assignmentScope(item) }) },
        { label: 'Rola', value: item => node('strong', { text: item.role_name }) },
        { label: 'Effect', value: effectBadge },
        { label: 'Dziedziczenie', value: item => item.inherit ? 'Tak' : 'Nie' },
        { label: 'Ważność', value: item => item.valid_until ? formatDate(item.valid_until) : 'bezterminowo' },
        { label: 'Status', value: item => statusBadge(item.status) },
        { label: 'Źródło', value: item => item.source },
      ], assignments, item => [
        ...(canUpdateBinding ? [
          button('Edytuj', () => editAssignmentForm(item)),
        ] : []),
        ...(canDeleteBinding ? [
          button('Usuń', () => confirmAction(
            'Usuń przypisanie',
            'Usunięcie natychmiast zmieni efektywny dostęp. Wpis pozostanie w Audit Log.',
            async () => {
              await iamApi('/rbac/assignments/' + encodeURIComponent(item.id), { method: 'DELETE' });
              toast('Przypisanie usunięte.');
              await enterpriseIamView();
            }
          ), 'danger'),
        ] : []),
      ]) : node('div', { class: 'panel empty', text: 'Brak przypisań dla wybranego filtra.' })));
}

async function groupsPanel() {
  const result = await iamApi('/rbac/groups?limit=200');
  const groups = result.items || [];
  const actions = [];
  if (allowed('groups.create')) actions.push(button('Nowa grupa', () => {
    const body = node('div', { class: 'form-grid' },
      field('Nazwa', 'name', { required: true, maxlength: 160 }),
      field('Opis', 'description', { tag: 'textarea', maxlength: 4000, wide: true }),
      selectField('Źródło', 'external_source', [
        { value: '', label: 'Lokalna' },
        { value: 'LDAP', label: 'LDAP' },
        { value: 'OIDC', label: 'OIDC' },
        { value: 'SSO', label: 'SSO' },
      ], ''),
      field('IdP Group / external ID', 'external_id', { maxlength: 1024, wide: true }));
    openModal({
      title: 'Nowa grupa IAM',
      eyebrow: 'Groups / IdP mapping',
      body,
      submitLabel: 'Utwórz grupę',
      onSubmit: async (_data, form) => {
        const payload = {
          name: form.elements.name.value,
          description: form.elements.description.value,
          enabled: true,
        };
        if (form.elements.external_source.value) payload.external_source = form.elements.external_source.value;
        if (form.elements.external_id.value) payload.external_id = form.elements.external_id.value;
        await iamApi('/rbac/groups', { method: 'POST', body: payload });
        toast('Grupa utworzona.');
        await enterpriseIamView();
      },
    });
  }, 'primary'));

  return node('div', { class: 'rbac-tab-body rbac-stack' },
    node('section', { class: 'rbac-section' },
      node('div', { class: 'rbac-section-heading' },
        node('div', {},
          node('span', { class: 'rbac-kicker', text: 'USER / GROUP' }),
          node('h2', { text: 'Grupy i mapping IdP' }),
          node('p', { class: 'muted', text: 'Role mogą być przypisane do grupy. external_source/external_id przygotowuje mapping LDAP, OIDC i SSO bez nadawania praw po samym loginie.' })),
        node('div', { class: 'action-group' }, actions)),
      groups.length ? table([
        { label: 'Grupa', value: item => node('div', {}, node('strong', { text: item.name }), node('div', { class: 'muted', text: item.description || '' })) },
        { label: 'Źródło', value: item => item.external_source ? badge(item.external_source, 'info') : 'LOCAL' },
        { label: 'External ID', value: item => item.external_id ? node('code', { class: 'mono', text: item.external_id }) : '—' },
        { label: 'Członkowie', value: item => String(item.member_count) },
        { label: 'Status', value: item => badge(item.enabled ? 'Aktywna' : 'Wyłączona', item.enabled ? 'ok' : 'danger') },
      ], groups, item => allowed('groups.delete') ? [
        button('Usuń', () => confirmAction('Usuń grupę', 'Przypisania grupy zostaną usunięte razem z grupą.', async () => {
          await iamApi('/rbac/groups/' + encodeURIComponent(item.id), { method: 'DELETE' });
          toast('Grupa usunięta.');
          await enterpriseIamView();
        }), 'danger'),
      ] : []) : node('div', { class: 'panel empty', text: 'Brak grup IAM.' })));
}

async function simulatorPanel() {
  const [usersResult, permissionResult, tenantResult, projectResult] = await Promise.all([
    optionalApi('/users?limit=200', { items: [] }),
    iamApi('/rbac/permissions'),
    optionalApi('/tenants?limit=200', { items: [] }),
    optionalApi('/projects?limit=200', { items: [] }),
  ]);
  const users = usersResult.items || [];
  const permissions = (permissionResult.items || []).map(item => item.name);
  const tenants = tenantResult.items || [];
  const projects = projectResult.items || [];

  const result = node('section', { class: 'panel' },
    node('div', { class: 'rbac-empty-selection', text: 'Wybierz subject, action i scope. Wynik pokaże pełny evaluation trace.' }));

  const form = node('form', {
    class: 'panel',
    onSubmit: async event => {
      event.preventDefault();
      const payload = {
        user_id: Number(form.elements.user_id.value),
        action: form.elements.action.value,
        scope_type: form.elements.scope_type.value,
        resource: {},
        context: {},
      };
      if (form.elements.tenant_id.value) payload.tenant_id = form.elements.tenant_id.value;
      if (form.elements.project_id.value) payload.project_id = form.elements.project_id.value;
      if (form.elements.apmid.value) payload.apmid = form.elements.apmid.value;
      if (form.elements.environment.value) payload.environment = form.elements.environment.value;
      if (form.elements.scope_id.value) payload.scope_id = form.elements.scope_id.value;
      const decision = await iamApi('/authorization/simulate', { method: 'POST', body: payload });
      result.replaceChildren(
        node('div', { class: 'rbac-decision-head' },
          node('div', {},
            node('span', { class: 'rbac-kicker', text: 'Authorization decision' }),
            node('h2', { text: decision.decision }),
            node('p', { class: 'muted', text: decision.reason })),
          badge(decision.decision, decision.decision === 'ALLOW' ? 'ok' : decision.decision === 'DENY' ? 'danger' : 'warning')),
        node('div', { class: 'rbac-reason-list' },
          node('strong', { text: 'Role / assignments' }),
          node('pre', { class: 'mono', text: JSON.stringify({
            roles: decision.roles,
            assignments: decision.assignments,
            inherited: decision.inherited,
            denied: decision.denied,
            policy: decision.policy,
          }, null, 2) })),
        node('div', { class: 'rbac-reason-list' },
          node('strong', { text: 'Evaluation trace' }),
          node('pre', { class: 'mono', text: JSON.stringify(decision.trace, null, 2) })));
    },
  },
  node('div', { class: 'form-grid' },
    selectField('User', 'user_id', users.map(item => ({ value: item.id, label: item.username + ' · ' + item.email })), '', { required: true, placeholder: 'Wybierz użytkownika' }),
    selectField('Action', 'action', permissions.map(value => ({ value, label: value })), '', { required: true, placeholder: 'Wybierz permission' }),
    selectField('Scope', 'scope_type', SCOPE_TYPES.map(([value, label]) => ({ value, label })), 'GLOBAL', { required: true }),
    selectField('Organization', 'tenant_id', tenants.map(item => ({ value: item.id, label: item.name })), '', { placeholder: '—' }),
    selectField('Project', 'project_id', projects.map(item => ({ value: item.id, label: item.name })), '', { placeholder: '—' }),
    field('APMID', 'apmid', { maxlength: 63 }),
    field('ENV', 'environment', { maxlength: 32 }),
    field('Scope / resource ID', 'scope_id', { maxlength: 160, wide: true })),
  node('div', { class: 'action-group' }, node('button', { type: 'submit', class: 'button primary' }, 'Symuluj dostęp')));

  return node('div', { class: 'rbac-tab-body rbac-stack' }, form, result);
}

async function reviewPanel() {
  const result = await iamApi('/rbac/access-review');
  const items = result.items || [];
  return node('div', { class: 'rbac-tab-body rbac-stack' },
    node('section', { class: 'rbac-section' },
      node('div', { class: 'rbac-section-heading' },
        node('div', {},
          node('span', { class: 'rbac-kicker', text: 'Governance' }),
          node('h2', { text: 'Access Review' }),
          node('p', { class: 'muted', text: 'Zestawienie użytkowników, ról, źródeł i expirations. Expired assignments pozostają dostępne dla audytu.' }))),
      table([
        { label: 'User', value: item => node('div', {}, node('strong', { text: item.username }), node('div', { class: 'muted', text: item.email })) },
        { label: 'Organization', value: item => item.organization_id || '—' },
        { label: 'Project', value: item => item.project_id || '—' },
        { label: 'Role', value: item => item.roles.join(', ') || '—' },
        { label: 'Source', value: item => item.source.join(', ') || '—' },
        { label: 'Expirations', value: item => item.expirations.length ? item.expirations.map(formatDate).join(', ') : '—' },
        { label: 'Last login', value: item => formatDate(item.last_login_at) },
      ], items, item => allowed('authorization.explain') ? [
        button('Sprawdź', () => {
          selectedUserId = item.user_id;
          activeTab = 'simulator';
          enterpriseIamView().catch(error => toast(error.message, 'error'));
        }),
      ] : [])));
}

async function enterpriseIamView() {
  let body;
  if (activeTab === 'groups') body = await groupsPanel();
  else if (activeTab === 'simulator') body = await simulatorPanel();
  else if (activeTab === 'review') body = await reviewPanel();
  else body = await assignmentsPanel();

  const actions = [];
  if ((allowed('rbac.assignments.manage') || allowed('roles.assign')) && activeTab === 'assignments') {
    actions.push(button('Assign access', () => accessAssignmentForm(selectedUserId).catch(error => toast(error.message, 'error')), 'primary'));
  }
  if (allowed('roles.read')) actions.push(button('Role i permission catalog', () => navigate('roles')));

  dom.content.replaceChildren(
    heading('Enterprise IAM: scope-aware RBAC, explicit DENY, ABAC, temporal access i diagnostyka.', actions),
    tabBar(),
    body);
}

registerRoutedForm({
  id: 'iam-user-access',
  pattern: /^\/access\/users\/(?<id>\d+)\/iam$/,
  parent: 'iam-access',
  permission: 'rbac.assignments.read',
  label: 'Dostępy',
}, async match => {
  selectedUserId = Number(match.params.id);
  activeTab = 'assignments';
  await enterpriseIamView();
});

registerView({
  id: 'iam-access',
  label: 'IAM / Dostępy',
  icon: 'A',
  permission: 'rbac.assignments.read',
  order: 21,
}, enterpriseIamView);

registerCommand('iam.assign', () => accessAssignmentForm(selectedUserId));
document.addEventListener('cloudportal:app-hidden', () => {
  activeTab = 'assignments';
  selectedUserId = null;
});
})();
