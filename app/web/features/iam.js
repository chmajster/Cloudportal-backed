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
  ['GLOBAL', 'Global'],
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

async function optionalApi(path, fallback) {
  try { return await api(path); }
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

function subjectField(users, groups, forcedUserId = null) {
  const type = selectField('Subject type', 'subject_type', [
    { value: 'USER', label: 'User' },
    { value: 'GROUP', label: 'Group' },
    { value: 'SERVICE_ACCOUNT', label: 'Service Account' },
    { value: 'API_TOKEN', label: 'API Token' },
  ], forcedUserId ? 'USER' : 'USER', { required: true });

  const wrap = node('label', { class: 'field' },
    node('span', { text: 'Subject' }),
    node('select', { name: 'subject_id', required: true }));

  const typeSelect = type.querySelector('select');
  const subjectSelect = wrap.querySelector('select');

  function refresh() {
    const kind = typeSelect.value;
    subjectSelect.replaceChildren();
    let choices = [];
    if (kind === 'USER') choices = users.filter(item => !item.is_service_account);
    else if (kind === 'SERVICE_ACCOUNT') choices = users.filter(item => item.is_service_account);
    else if (kind === 'GROUP') choices = groups;
    if (kind === 'API_TOKEN') {
      subjectSelect.replaceWith(node('input', {
        name: 'subject_id',
        type: 'number',
        min: 1,
        required: true,
        placeholder: 'Token ID',
      }));
      return;
    }
    for (const item of choices) {
      const value = item.id;
      const label = item.username || item.name || String(value);
      subjectSelect.append(node('option', {
        value,
        text: label,
        selected: forcedUserId && String(value) === String(forcedUserId),
      }));
    }
  }
  typeSelect.addEventListener('change', () => {
    const current = wrap.querySelector('[name="subject_id"]');
    if (current?.tagName !== 'SELECT') {
      current.replaceWith(node('select', { name: 'subject_id', required: true }));
    }
    refresh();
  });
  refresh();
  if (forcedUserId) typeSelect.disabled = true;
  return [type, wrap];
}

async function accessAssignmentForm(forcedUserId = null) {
  const [roleResult, userResult, groupResult, tenantResult, projectResult] = await Promise.all([
    api('/rbac/roles?limit=200'),
    optionalApi('/users?limit=500', { items: [] }),
    optionalApi('/rbac/groups?limit=500', { items: [] }),
    optionalApi('/tenants?limit=500', { items: [] }),
    optionalApi('/projects?limit=500', { items: [] }),
  ]);
  const roles = roleResult.items || [];
  const users = userResult.items || [];
  const groups = groupResult.items || [];
  const tenants = tenantResult.items || [];
  const projects = projectResult.items || [];

  const [subjectType, subject] = subjectField(users, groups, forcedUserId);
  const role = selectField('Rola', 'role_id', roles.map(item => ({
    value: item.id,
    label: item.name + (item.system_role ? ' · system' : ''),
  })), '', { required: true, placeholder: 'Wybierz rolę' });
  const effect = selectField('Effect', 'effect', [
    { value: 'ALLOW', label: 'ALLOW' },
    { value: 'DENY', label: 'DENY' },
  ], 'ALLOW', { required: true });
  const scopeType = selectField('Scope', 'scope_type', SCOPE_TYPES.map(([value, label]) => ({ value, label })), 'GLOBAL', { required: true });
  const tenant = selectField('Organization', 'tenant_id', tenants.map(item => ({
    value: item.id,
    label: item.name + (item.slug ? ' · ' + item.slug : ''),
  })), '', { placeholder: '—' });
  const project = selectField('Project', 'project_id', projects.map(item => ({
    value: item.id,
    label: item.name + (item.slug ? ' · ' + item.slug : ''),
  })), '', { placeholder: '—' });
  const apmid = field('APMID', 'apmid', { maxlength: 63 });
  const environment = field('ENV', 'environment', { maxlength: 32, placeholder: 'DEV / TEST / PROD' });
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

  const scopeSelect = scopeType.querySelector('select');
  function syncScope() {
    const kind = scopeSelect.value;
    const needsProject = !['GLOBAL', 'ORGANIZATION'].includes(kind);
    tenant.hidden = kind === 'GLOBAL';
    project.hidden = !needsProject;
    apmid.hidden = !['APMID', 'ENVIRONMENT'].includes(kind);
    environment.hidden = kind !== 'ENVIRONMENT';
    scopeId.hidden = !['RESOURCE_POOL', 'BLUEPRINT', 'DEPLOYMENT', 'RESOURCE', 'MACHINE'].includes(kind);
  }
  scopeSelect.addEventListener('change', syncScope);
  syncScope();

  const body = node('div', { class: 'form-grid' },
    subjectType, subject, role, effect, scopeType, tenant, project, apmid,
    environment, scopeId, validFrom, validUntil, inherit, approval, conditions);

  openModal({
    title: forcedUserId ? 'Przypisz dostęp użytkownikowi' : 'Nowy RoleAssignment',
    eyebrow: 'Enterprise IAM',
    body,
    wide: true,
    submitLabel: 'Przypisz dostęp',
    onSubmit: async (_data, form) => {
      let conditionTree = {};
      const rawConditions = form.elements.conditions.value.trim();
      if (rawConditions) {
        try { conditionTree = JSON.parse(rawConditions); }
        catch { throw new Error('Conditions JSON nie jest poprawnym JSON-em.'); }
      }
      const kind = form.elements.scope_type.value;
      const payload = {
        subject_type: forcedUserId ? 'USER' : form.elements.subject_type.value,
        subject_id: String(forcedUserId || form.elements.subject_id.value),
        role_id: Number(form.elements.role_id.value),
        effect: form.elements.effect.value,
        scope_type: kind,
        conditions: conditionTree,
        inherit: Boolean(form.elements.inherit?.checked),
        approval_required: Boolean(form.elements.approval_required?.checked),
        enabled: true,
      };
      if (kind !== 'GLOBAL' && form.elements.tenant_id?.value) payload.tenant_id = form.elements.tenant_id.value;
      if (!['GLOBAL', 'ORGANIZATION'].includes(kind) && form.elements.project_id?.value) payload.project_id = form.elements.project_id.value;
      if (kind === 'ORGANIZATION' && form.elements.tenant_id?.value) payload.scope_id = form.elements.tenant_id.value;
      if (kind === 'PROJECT' && form.elements.project_id?.value) payload.scope_id = form.elements.project_id.value;
      if (['APMID', 'ENVIRONMENT'].includes(kind) && form.elements.apmid?.value) payload.apmid = form.elements.apmid.value;
      if (kind === 'ENVIRONMENT' && form.elements.environment?.value) payload.environment = form.elements.environment.value;
      if (['RESOURCE_POOL', 'BLUEPRINT', 'DEPLOYMENT', 'RESOURCE', 'MACHINE'].includes(kind)) {
        payload.scope_id = form.elements.scope_id.value;
      }
      if (form.elements.valid_from?.value) payload.valid_from = new Date(form.elements.valid_from.value).toISOString();
      if (form.elements.valid_until?.value) payload.valid_until = new Date(form.elements.valid_until.value).toISOString();
      await api('/rbac/assignments', { method: 'POST', body: payload });
      toast('Dostęp przypisany.');
      activeTab = 'assignments';
      await enterpriseIamView();
    },
  });
}

async function assignmentsPanel() {
  const query = selectedUserId
    ? '?limit=200&subject_type=USER&subject_id=' + encodeURIComponent(selectedUserId)
    : '?limit=200';
  const [assignmentResult, userResult, groupResult] = await Promise.all([
    api('/rbac/assignments' + query),
    optionalApi('/users?limit=500', { items: [] }),
    optionalApi('/rbac/groups?limit=500', { items: [] }),
  ]);
  const assignments = assignmentResult.items || [];
  const users = userResult.items || [];
  const groups = groupResult.items || [];
  const selected = users.find(item => Number(item.id) === Number(selectedUserId));

  const actions = [];
  if (allowed('rbac.assignments.manage') || allowed('roles.assign')) {
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
        ...(allowed('rbac.assignments.manage') || allowed('roles.assign') ? [
          button('Usuń', () => confirmAction(
            'Usuń przypisanie',
            'Usunięcie natychmiast zmieni efektywny dostęp. Wpis pozostanie w Audit Log.',
            async () => {
              await api('/rbac/assignments/' + encodeURIComponent(item.id), { method: 'DELETE' });
              toast('Przypisanie usunięte.');
              await enterpriseIamView();
            }
          ), 'danger'),
        ] : []),
      ]) : node('div', { class: 'panel empty', text: 'Brak przypisań dla wybranego filtra.' })));
}

async function groupsPanel() {
  const result = await api('/rbac/groups?limit=500');
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
        await api('/rbac/groups', { method: 'POST', body: payload });
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
          await api('/rbac/groups/' + encodeURIComponent(item.id), { method: 'DELETE' });
          toast('Grupa usunięta.');
          await enterpriseIamView();
        }), 'danger'),
      ] : []) : node('div', { class: 'panel empty', text: 'Brak grup IAM.' })));
}

async function simulatorPanel() {
  const [usersResult, permissionResult, tenantResult, projectResult] = await Promise.all([
    optionalApi('/users?limit=500', { items: [] }),
    api('/rbac/permissions'),
    optionalApi('/tenants?limit=500', { items: [] }),
    optionalApi('/projects?limit=500', { items: [] }),
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
      const decision = await api('/authorization/simulate', { method: 'POST', body: payload });
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
  const result = await api('/rbac/access-review');
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
