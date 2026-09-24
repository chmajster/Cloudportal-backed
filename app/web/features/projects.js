'use strict';

(() => {
  const labels = { active: 'Aktywny', suspended: 'Wstrzymany', disabled: 'Wyłączony' };
  const statuses = Object.keys(labels).map(value => ({ value, label: labels[value] }));
  const pageSize = 50;
  let generation = 0, listOffset = 0, listStatus = '', tenantFilter = '';

  function action(label, operation, kind = 'ghost', disabled = false) {
    return button(label, () => Promise.resolve().then(operation).catch(error => toast(error.message, 'error')), kind, disabled);
  }
  function scopedRoleOption(role, checked) {
    const permissions = (role.permissions || []).slice().sort();
    return node('div', { class: 'projects-role-option' },
      checkboxField(`${role.name} (#${role.id})`, 'role_' + role.id, checked),
      node('div', { class: 'field-help', text: permissions.length
        ? 'Uprawnienia: ' + permissions.join(', ')
        : 'Brak delegowalnych uprawnień w tym projekcie.' }));
  }

  function controls(page, reload) {
    return node('div', { class: 'projects-pagination' },
      action('Poprzednia', () => reload(Math.max(0, page.offset - page.limit)), 'ghost', page.offset === 0),
      node('span', { role: 'status', text: `${page.total ? page.offset + 1 : 0}–${Math.min(page.offset + page.items.length, page.total)} z ${page.total}` }),
      action('Następna', () => reload(page.offset + page.limit), 'ghost', page.offset + page.limit >= page.total));
  }
  function jsonObject(value) {
    const result = JSON.parse(value || '{}');
    if (!result || typeof result !== 'object' || Array.isArray(result)) throw new Error('Wymagany obiekt JSON.');
    return result;
  }
  function notice() {
    return node('p', { class: 'projects-notice', role: 'note', text:
      'Uprawnienia projektowe są sprawdzane przez backend. Ta warstwa administracji projektami nie włącza jeszcze izolacji istniejącej infrastruktury ani egzekwowania quota i policy.' });
  }

  async function projectsView() {
    const current = ++generation;
    const query = new URLSearchParams({ limit: pageSize, offset: listOffset });
    if (listStatus) query.set('status', listStatus);
    if (tenantFilter) query.set('tenant_id', tenantFilter);
    const [page, scopes] = await Promise.all([api('/projects?' + query), api('/project-context/creation-scopes?limit=1')]);
    const valid = () => current === generation && state.view === 'projects';
    const selection = globalThis.CPProjectContext ? await globalThis.CPProjectContext.panel(projectsView, valid) : null;
    if (!valid()) return;
    const filter = selectField('Status', 'project_status', [{ value: '', label: 'Wszystkie dostępne' }, ...statuses], listStatus);
    filter.querySelector('select').addEventListener('change', event => {
      listStatus = event.target.value; listOffset = 0; projectsView().catch(error => toast(error.message, 'error'));
    });
    const actions = scopes.total ? [action('Nowy projekt', () => projectForm(), 'primary')] : [];
    if (tenantFilter) actions.push(action('Wszystkie dostępne tenanty', () => { tenantFilter = ''; listOffset = 0; return projectsView(); }));
    dom.content.replaceChildren(heading('Projekty, członkostwa i uprawnienia w kontekście Tenant / Project.', actions), notice(), selection, filter,
      table([
        { label: 'Projekt', value: row => node('strong', { text: row.name }) },
        { label: 'Tenant ID', value: row => row.tenant_id },
        { label: 'Slug', value: row => row.slug },
        { label: 'Środowisko domyślne', value: row => row.default_environment },
        { label: 'Status', value: row => labels[row.status] },
        { label: 'Systemowy', value: row => row.is_system ? 'Tak — chroniony' : 'Nie' },
      ], page.items, row => [action('Szczegóły', () => details(row.id))]),
      controls(page, offset => { listOffset = offset; return projectsView(); }));
  }

  async function details(id) {
    const current = ++generation;
    const [item, scope] = await Promise.all([api(`/projects/${id}`), api(`/projects/${id}/permissions`)]);
    if (current !== generation || state.view !== 'projects') return;
    const permits = p => scope.permissions.includes(p);
    const actions = [];
    if (permits('projects.select') && globalThis.CPProjectContext) actions.push(action('Wybierz kontekst',
      () => globalThis.CPProjectContext.choose(item, () => current === generation && state.view === 'projects')));
    if (permits('projects.update')) actions.push(action('Edytuj', () => projectForm(item, scope)));
    if (permits('projects.members.read')) actions.push(action('Członkowie', () => members(id)));
    if (permits('projects.audit.read')) actions.push(action('Audyt', () => history(id)));
    if (!item.is_system && permits('projects.delete')) actions.push(action('Usuń pusty projekt', () => openModal({
      title: `Usuń projekt: ${item.name}`, danger: true, submitLabel: 'Usuń projekt',
      body: node('p', { text: 'Backend wymaga pustego projektu i zgodnej wersji. Historia audytu zostanie zachowana.' }),
      onSubmit: async () => {
        await api(`/projects/${id}?expected_version=${item.version}`, { method: 'DELETE' });
        await navigate('projects'); return false;
      },
    }), 'danger'));
    openModal({ title: item.name, eyebrow: 'Projekt / szczegóły', wide: true,
      body: node('div', { class: 'stack' }, notice(), node('div', { class: 'action-group' }, actions),
        node('p', { text: item.description || 'Brak opisu.' }),
        node('dl', { class: 'projects-overview' },
          node('dt', { text: 'Tenant' }), node('dd', { class: 'mono', text: item.tenant_id }),
          node('dt', { text: 'Project' }), node('dd', { class: 'mono', text: item.id }),
          node('dt', { text: 'Status' }), node('dd', { text: labels[item.status] }),
          node('dt', { text: 'Środowisko' }), node('dd', { text: item.default_environment }),
          node('dt', { text: 'Auto-approval Blueprintów' }), node('dd', { text: item.blueprint_auto_approve_for_executors == null ? 'Dziedziczone globalnie' : (item.blueprint_auto_approve_for_executors ? 'Włączone dla projektu' : 'Wyłączone dla projektu') }),
          node('dt', { text: 'Timeout approval Blueprintów' }), node('dd', { text: item.blueprint_approval_timeout_hours == null ? 'Dziedziczony globalnie' : item.blueprint_approval_timeout_hours + ' h' }),
          node('dt', { text: 'Wersja' }), node('dd', { text: item.version })),
        node('h3', { text: 'Efektywne uprawnienia projektowe' }),
        node('pre', { class: 'projects-json', text: scope.permissions.join('\n') }),
        node('h3', { text: 'Uprawnienia dziedziczone z tenanta' }),
        node('pre', { class: 'projects-json', text: scope.inherited_permissions.join('\n') || 'Brak' }),
        node('h3', { text: 'Etykiety / metadata' }),
        node('pre', { class: 'projects-json', text: JSON.stringify({ labels: item.labels, metadata: item.metadata }, null, 2) })),
    });
  }

  async function projectForm(item = null, scope = null) {
    const current = ++generation;
    const tenantSelect = selectField('Tenant', 'tenant_id', item ? [{ value: item.tenant_id, label: item.tenant_id }] : [], item?.tenant_id || '');
    const select = tenantSelect.querySelector('select'); select.required = true;
    let offset = 0, more;
    async function loadScopes() {
      const page = await api(`/project-context/creation-scopes?limit=${pageSize}&offset=${offset}`);
      if (current !== generation || state.view !== 'projects') return;
      page.items.forEach(t => select.append(node('option', { value: t.tenant_id, text: `${t.name} / ${t.slug}` })));
      offset += page.items.length; more.disabled = offset >= page.total;
    }
    more = action('Pokaż kolejne tenanty', loadScopes);
    if (item) select.disabled = true; else await loadScopes();
    if (current !== generation || state.view !== 'projects') return;
    const canDisable = scope?.global_administration || scope?.inherited_permissions.includes('projects.admin');
    const body = node('div', { class: 'form-grid' }, tenantSelect, item ? null : more,
      field('Nazwa', 'name', { value: item?.name || '', required: true, maxlength: 100 }),
      field('Slug', 'slug', { value: item?.slug || '', required: true, maxlength: 63 }),
      field('Środowisko domyślne', 'default_environment', { value: item?.default_environment || 'dev', required: true, maxlength: 32 }),
      selectField('Auto-approval Blueprintów', 'blueprint_auto_approve_for_executors', [
        { value: 'inherit', label: 'Dziedzicz ustawienie globalne' },
        { value: 'true', label: 'Włączone w tym projekcie' },
        { value: 'false', label: 'Wyłączone w tym projekcie' },
      ], item?.blueprint_auto_approve_for_executors == null ? 'inherit' : String(item.blueprint_auto_approve_for_executors)),
      field('Timeout approval Blueprintów (h)', 'blueprint_approval_timeout_hours', { type: 'number', min: 1, max: 720, value: item?.blueprint_approval_timeout_hours ?? '', help: 'Puste pole oznacza dziedziczenie wartości globalnej.' }),
      selectField('Status', 'status', canDisable ? statuses : statuses.filter(s => s.value !== 'disabled'), item?.status || 'active'),
      field('Opis', 'description', { tag: 'textarea', value: item?.description || '', maxlength: 4000, wide: true }),
      field('Etykiety — JSON', 'labels', { tag: 'textarea', value: JSON.stringify(item?.labels || {}, null, 2), wide: true }),
      field('Metadata — JSON', 'metadata', { tag: 'textarea', value: JSON.stringify(item?.metadata || {}, null, 2), wide: true, help: 'Nie wpisuj sekretów.' }));
    if (item?.is_system) for (const name of ['name', 'slug', 'status']) body.querySelector(`[name="${name}"]`).disabled = true;
    openModal({ title: item ? 'Edytuj projekt' : 'Nowy projekt', body, onSubmit: async data => {
      const values = {
        name: item?.is_system ? item.name : data.get('name'), slug: item?.is_system ? item.slug : data.get('slug'),
        status: item?.is_system ? item.status : data.get('status'), default_environment: data.get('default_environment'),
        blueprint_auto_approve_for_executors: data.get('blueprint_auto_approve_for_executors') === 'inherit' ? null : data.get('blueprint_auto_approve_for_executors') === 'true',
        blueprint_approval_timeout_hours: data.get('blueprint_approval_timeout_hours') ? Number(data.get('blueprint_approval_timeout_hours')) : null,
        description: data.get('description'), labels: jsonObject(data.get('labels')), metadata: jsonObject(data.get('metadata')),
      };
      if (item) values.expected_version = item.version; else values.tenant_id = data.get('tenant_id');
      await api(item ? `/projects/${item.id}` : '/projects', { method: item ? 'PUT' : 'POST', idempotent: !item, body: values });
      toast('Projekt zapisany.'); await navigate('projects'); return false;
    } });
  }

  async function members(id, offset = 0) {
    const current = ++generation;
    const [scope, page] = await Promise.all([api(`/projects/${id}/permissions`), api(`/projects/${id}/members?limit=${pageSize}&offset=${offset}`)]);
    if (current !== generation || state.view !== 'projects') return;
    const manager = scope.permissions.includes('projects.members.manage'), assigner = scope.permissions.includes('projects.roles.assign');
    const actions = [action('Szczegóły projektu', () => details(id))];
    if (manager) actions.push(action('Dodaj członka', () => memberForm(id, assigner), 'primary'));
    openModal({ title: 'Członkowie projektu', wide: true, body: node('div', { class: 'stack' },
      node('div', { class: 'action-group' }, actions), table([
        { label: 'Użytkownik', value: row => row.username }, { label: 'ID', value: row => row.user_id },
        { label: 'Status', value: row => labels[row.status] }, { label: 'Role (ID)', value: row => row.role_ids.join(', ') || 'Brak' },
      ], page.items, row => {
        const actions = [];
        if (assigner) actions.push(action('Role', () => memberForm(id, true, row)));
        if (manager) {
          actions.push(action(row.status === 'active' ? 'Wyłącz' : 'Włącz', async () => {
            await api(`/projects/${id}/members/${row.user_id}`, { method: 'PUT', body: { status: row.status === 'active' ? 'disabled' : 'active', expected_version: row.version } });
            await members(id, offset);
          }));
          actions.push(action('Usuń', () => openModal({ title: `Usuń członkostwo: ${row.username}`, danger: true,
            body: node('p', { text: 'Usuwa wyłącznie członkostwo w projekcie. Konto użytkownika i członkostwo tenanta pozostają.' }),
            onSubmit: async () => {
              await api(`/projects/${id}/members/${row.user_id}?expected_version=${row.version}`, { method: 'DELETE' });
              await members(id); return false;
            },
          }), 'danger'));
        }
        return actions;
      }), controls(page, next => members(id, next))) });
  }

  async function memberForm(id, assigner, member = null) {
    const current = ++generation;
    const roles = node('div', { class: 'projects-roles' });
    const selected = new Set(member?.role_ids || []), rendered = new Set();
    for (const roleId of selected) {
      roles.append(node('div', { class: 'projects-role-option', 'data-role-id': String(roleId) },
        checkboxField(`Obecna rola #${roleId}`, 'role_' + roleId, true),
        node('div', { class: 'field-help', text: 'Rola przypisana wcześniej; backend ponownie sprawdzi jej bieżący zakres.' })));
    }
    const userField = selectField('Członek tenanta', 'user_id', [], '');
    const select = userField.querySelector('select'); select.required = true;
    let roleOffset = 0, userOffset = 0, moreRoles, moreUsers;
    async function loadRoles() {
      const page = await api(`/projects/${id}/assignable-roles?limit=${pageSize}&offset=${roleOffset}`);
      if (current !== generation || state.view !== 'projects') return;
      for (const role of page.items) if (!rendered.has(role.id)) {
        const existingFallback = roles.querySelector('[data-role-id="' + role.id + '"]');
        const checked = existingFallback?.querySelector('input[type="checkbox"]')?.checked
          ?? selected.has(role.id);
        if (existingFallback) existingFallback.remove();
        const option = scopedRoleOption(role, checked);
        option.dataset.roleId = String(role.id);
        roles.append(option);
        rendered.add(role.id);
      }
      roleOffset += page.items.length; moreRoles.disabled = roleOffset >= page.total;
    }
    async function loadUsers() {
      const page = await api(`/projects/${id}/eligible-members?limit=${pageSize}&offset=${userOffset}`);
      if (current !== generation || state.view !== 'projects') return;
      for (const user of page.items) select.append(node('option', { value: user.user_id, text: `${user.username} (#${user.user_id})` }));
      userOffset += page.items.length; moreUsers.disabled = userOffset >= page.total;
    }
    moreRoles = action('Kolejne role', loadRoles); moreUsers = action('Kolejni członkowie tenanta', loadUsers);
    if (assigner) await loadRoles(); if (!member) await loadUsers();
    if (current !== generation || state.view !== 'projects') return;
    openModal({ title: member ? 'Role członka projektu' : 'Dodaj członka projektu', body: node('div', { class: 'stack' },
      member ? node('p', { text: member.username }) : userField, member ? null : moreUsers,
      node('p', { class: 'muted', text: 'Uprawnienia są nadawane przez role RBAC tylko w tym projekcie. Role projektowe nie rozszerzają uprawnień globalnych.' }),
      assigner ? roles : node('p', { text: 'Członkostwo bez roli — brak uprawnienia do przypisywania ról.' }), assigner ? moreRoles : null),
      onSubmit: async data => {
        const roleIds = new Set(member?.role_ids || []);
        roles.querySelectorAll('input[type="checkbox"]').forEach(input => {
          const roleId = Number(input.name.slice(5)); input.checked ? roleIds.add(roleId) : roleIds.delete(roleId);
        });
        const values = member ? { role_ids: [...roleIds], expected_version: member.version } : { user_id: Number(data.get('user_id')), role_ids: [...roleIds] };
        await api(member ? `/projects/${id}/members/${member.user_id}/roles` : `/projects/${id}/members`, { method: member ? 'PUT' : 'POST', idempotent: !member, body: values });
        await members(id); return false;
      },
    });
  }

  async function history(id, offset = 0) {
    const current = ++generation;
    const page = await api(`/projects/${id}/audit?limit=${pageSize}&offset=${offset}`);
    if (current !== generation || state.view !== 'projects') return;
    openModal({ title: 'Audyt projektu', wide: true, body: node('div', { class: 'stack' }, action('Szczegóły projektu', () => details(id)),
      table([{ label: 'Czas', value: row => formatDate(row.timestamp) }, { label: 'Operacja', value: row => row.action },
        { label: 'Użytkownik', value: row => row.user_id ?? 'System' }, { label: 'Obiekt', value: row => row.resource_id },
        { label: 'Wynik', value: row => row.result }], page.items), controls(page, next => history(id, next))) });
  }
  document.addEventListener('cloudportal:app-hidden', () => { generation++; listOffset = 0; tenantFilter = ''; listStatus = ''; });
  document.addEventListener('cloudportal:tenant-projects', event => {
    generation++; tenantFilter = String(event.detail?.tenantId || ''); listOffset = 0;
    navigate('projects').catch(error => toast(error.message, 'error'));
  });
  registerView({ id: 'projects', label: 'Projekty', icon: 'P', permission: null, order: 166 }, projectsView);
})();
