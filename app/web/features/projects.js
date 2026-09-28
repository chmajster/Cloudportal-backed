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
    return node('div', { class: 'projects-notice', role: 'note' },
      node('span', { class: 'projects-notice-icon', 'aria-hidden': 'true', text: 'i' }),
      node('div', { class: 'projects-notice-copy' },
        node('strong', { text: 'Zakres działania' }),
        node('p', { text:
          'Backend weryfikuje uprawnienia projektowe przy każdej operacji. Izolacja istniejącej infrastruktury oraz egzekwowanie quota i policy nie są jeszcze włączone w tej warstwie.' })));
  }
  function projectStatusBadge(status) {
    const kind = status === 'active' ? 'ok' : status === 'suspended' ? 'warning' : 'danger';
    return node('span', { class: 'badge ' + kind, text: labels[status] || status });
  }
  function projectSystemBadge(item) {
    return node('span', {
      class: 'badge ' + (item.is_system ? 'info' : ''),
      text: item.is_system ? 'Systemowy' : 'Standardowy',
    });
  }
  function emptyProjects(message, actions = []) {
    return node('section', { class: 'projects-empty-state' },
      node('span', { class: 'projects-empty-icon', 'aria-hidden': 'true', text: 'P' }),
      node('div', { class: 'projects-empty-copy' },
        node('strong', { text: 'Brak projektów do wyświetlenia' }),
        node('p', { text: message })),
      actions.length ? node('div', { class: 'action-group projects-empty-actions' }, actions) : null);
  }

  function projectUpdateValues(item, name = item.name) {
    return {
      name,
      slug: item.slug,
      status: item.status,
      default_environment: item.default_environment,
      blueprint_auto_approve_for_executors: item.blueprint_auto_approve_for_executors,
      blueprint_approval_timeout_hours: item.blueprint_approval_timeout_hours,
      description: item.description || '',
      labels: item.labels || {},
      metadata: item.metadata || {},
      expected_version: item.version,
    };
  }

  async function renameProject(item) {
    const [current, scope] = await Promise.all([
      api('/projects/' + encodeURIComponent(item.id)),
      api('/projects/' + encodeURIComponent(item.id) + '/permissions'),
    ]);
    if (current.is_system) throw new Error('Projekt systemowy jest chroniony przed zmianą nazwy.');
    if (!scope.permissions.includes('projects.update')) throw new Error('Brak uprawnienia do zmiany nazwy projektu.');
    const body = node('div', { class: 'form-grid' },
      field('Nowa nazwa projektu', 'name', { value: current.name, required: true, maxlength: 100, autofocus: true }),
      node('p', { class: 'field-help', text: 'Slug projektu pozostanie bez zmian: ' + current.slug }));
    openModal({
      title: 'Zmień nazwę projektu',
      body,
      submitLabel: 'Zapisz nazwę',
      onSubmit: async data => {
        const name = String(data.get('name') || '').trim();
        if (!name) throw new Error('Nazwa projektu nie może być pusta.');
        await api('/projects/' + encodeURIComponent(current.id), {
          method: 'PUT',
          body: projectUpdateValues(current, name),
        });
        toast('Nazwa projektu została zmieniona.');
        await navigate('projects');
        return false;
      },
    });
  }

  async function deleteProject(item) {
    const [current, scope] = await Promise.all([
      api('/projects/' + encodeURIComponent(item.id)),
      api('/projects/' + encodeURIComponent(item.id) + '/permissions'),
    ]);
    if (current.is_system) throw new Error('Projekt systemowy jest chroniony przed usunięciem.');
    if (!scope.permissions.includes('projects.delete')) throw new Error('Brak uprawnienia do usunięcia projektu.');
    const body = node('div', { class: 'stack' },
      node('p', { text: 'Usunięcie projektu jest możliwe tylko wtedy, gdy nie ma członków, infrastruktury, historii ani przypisań dostępu. Historia audytu pozostanie zachowana.' }),
      field('Wpisz nazwę projektu, aby potwierdzić', 'confirmation', { required: true, maxlength: 100, autocomplete: 'off' }));
    openModal({
      title: 'Usuń projekt: ' + current.name,
      danger: true,
      submitLabel: 'Usuń projekt',
      body,
      onSubmit: async data => {
        if (String(data.get('confirmation') || '').trim() !== current.name) {
          throw new Error('Nazwa potwierdzająca nie zgadza się z nazwą projektu.');
        }
        await api('/projects/' + encodeURIComponent(current.id) + '?expected_version=' + encodeURIComponent(current.version), {
          method: 'DELETE',
        });
        toast('Projekt został usunięty.');
        listOffset = 0;
        await navigate('projects');
        return false;
      },
    });
  }

  async function projectsView() {
    const current = ++generation;
    const query = new URLSearchParams({ limit: pageSize, offset: listOffset });
    if (listStatus) query.set('status', listStatus);
    if (tenantFilter) query.set('tenant_id', tenantFilter);
    const [page, scopes] = await Promise.all([api('/projects?' + query), api('/project-context/creation-scopes?limit=1')]);
    const valid = () => current === generation && viewIs('projects');
    const selection = globalThis.CPProjectContext ? await globalThis.CPProjectContext.panel(projectsView, valid) : null;
    if (!valid()) return;
    const filter = selectField('Status projektu', 'project_status', [{ value: '', label: 'Wszystkie statusy' }, ...statuses], listStatus);
    filter.className = ((filter.className || '') + ' projects-filter').trim();
    filter.querySelector('select').addEventListener('change', event => {
      listStatus = event.target.value; listOffset = 0; projectsView().catch(error => toast(error.message, 'error'));
    });

    const actions = scopes.total ? [action('Nowy projekt', () => navigate('/projects/new'), 'primary')] : [];
    if (tenantFilter) actions.push(action('Wszystkie dostępne tenanty', () => { tenantFilter = ''; listOffset = 0; return projectsView(); }));

    const rangeStart = page.total ? page.offset + 1 : 0;
    const rangeEnd = Math.min(page.offset + page.items.length, page.total);
    const toolbar = node('section', { class: 'panel projects-toolbar' },
      node('div', { class: 'projects-toolbar-copy' },
        node('strong', { text: 'Dostępne projekty' }),
        node('span', { class: 'muted', text: `Pobrano z API: ${rangeStart}–${rangeEnd} z ${page.total}` }),
        tenantFilter ? node('span', { class: 'projects-scope-chip', text: 'Tenant: ' + tenantFilter }) : null),
      filter);

    const emptyActions = [];
    if (listStatus) emptyActions.push(action('Wyczyść filtr statusu', () => { listStatus = ''; listOffset = 0; return projectsView(); }));
    if (tenantFilter) emptyActions.push(action('Pokaż wszystkie tenanty', () => { tenantFilter = ''; listOffset = 0; return projectsView(); }));
    if (!listStatus && !tenantFilter && scopes.total) emptyActions.push(action('Utwórz projekt', () => navigate('/projects/new'), 'primary'));

    const projectList = page.items.length
      ? table([
        { label: 'Projekt', value: row => node('div', { class: 'projects-project-name' },
          node('strong', { text: row.name }),
          node('span', { class: 'mono muted', text: row.slug })) },
        { label: 'Tenant', class: 'mono', value: row => row.tenant_id },
        { label: 'Środowisko', value: row => node('span', { class: 'badge info', text: row.default_environment }) },
        { label: 'Status', value: row => projectStatusBadge(row.status) },
        { label: 'Typ', value: row => projectSystemBadge(row) },
      ], page.items, row => {
        const rowActions = [action('Szczegóły', () => navigate('/projects/' + encodeURIComponent(row.id)))];
        if (!row.is_system) {
          rowActions.push(action('Zmień nazwę', () => renameProject(row)));
          rowActions.push(action('Usuń', () => deleteProject(row), 'danger'));
        }
        return rowActions;
      })
      : emptyProjects(
        listStatus || tenantFilter
          ? 'Żaden projekt nie spełnia obecnie wybranych filtrów.'
          : 'Nie masz jeszcze dostępnych projektów w tym zakresie.',
        emptyActions);

    dom.content.replaceChildren(
      heading('Zarządzaj projektami, członkostwami i dostępem w obrębie tenantów.', actions),
      node('div', { class: 'projects-page-stack' },
        notice(),
        selection,
        toolbar,
        projectList,
        page.total > page.limit ? controls(page, offset => { listOffset = offset; return projectsView(); }) : null));
  }

  async function details(id) {
    const current = ++generation;
    const [item, scope] = await Promise.all([api(`/projects/${id}`), api(`/projects/${id}/permissions`)]);
    if (current !== generation || !viewIs('projects')) return;
    const permits = p => scope.permissions.includes(p);
    const actions = [];
    if (permits('projects.select') && globalThis.CPProjectContext) actions.push(action('Wybierz kontekst',
      () => globalThis.CPProjectContext.choose(item, () => current === generation && viewIs('projects'))));
    if (permits('projects.update')) {
      if (!item.is_system) actions.push(action('Zmień nazwę', () => renameProject(item)));
      actions.push(action('Edytuj', () => navigate('/projects/edit/' + encodeURIComponent(item.id) + '/' + encodeURIComponent(item.slug || item.name || 'project'))));
    }
    if (permits('projects.members.read')) actions.push(action('Członkowie', () => navigate('/projects/' + encodeURIComponent(id) + '/members')));
    if (permits('projects.audit.read')) actions.push(action('Audyt', () => navigate('/projects/' + encodeURIComponent(id) + '/audit')));
    if (!item.is_system && permits('projects.delete')) {
      actions.push(action('Usuń projekt', () => deleteProject(item), 'danger'));
    }
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
      if (current !== generation || !viewIs('projects')) return;
      page.items.forEach(t => select.append(node('option', { value: t.tenant_id, text: `${t.name} / ${t.slug}` })));
      offset += page.items.length; more.disabled = offset >= page.total;
    }
    more = action('Pokaż kolejne tenanty', loadScopes);
    if (item) select.disabled = true; else await loadScopes();
    if (current !== generation || !viewIs('projects')) return;
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
    if (current !== generation || !viewIs('projects')) return;
    const manager = scope.permissions.includes('projects.members.manage'), assigner = scope.permissions.includes('projects.roles.assign');
    const actions = [action('Szczegóły projektu', () => navigate('/projects/' + encodeURIComponent(id)))];
    if (manager) actions.push(action('Dodaj członka', () => navigate('/projects/' + encodeURIComponent(id) + '/members/new'), 'primary'));
    openModal({ title: 'Członkowie projektu', wide: true, body: node('div', { class: 'stack' },
      node('div', { class: 'action-group' }, actions), table([
        { label: 'Użytkownik', value: row => row.username }, { label: 'ID', value: row => row.user_id },
        { label: 'Status', value: row => labels[row.status] }, { label: 'Role (ID)', value: row => row.role_ids.join(', ') || 'Brak' },
      ], page.items, row => {
        const actions = [];
        if (assigner) actions.push(action('Role', () => navigate('/projects/' + encodeURIComponent(id) + '/members/' + encodeURIComponent(row.user_id) + '/roles')));
        if (manager) {
          actions.push(action(row.status === 'active' ? 'Wyłącz' : 'Włącz', async () => {
            await api(`/projects/${id}/members/${row.user_id}`, { method: 'PUT', body: { status: row.status === 'active' ? 'disabled' : 'active', expected_version: row.version } });
            await members(id, offset);
          }));
          actions.push(action('Usuń', () => openModal({ title: `Usuń członkostwo: ${row.username}`, danger: true,
            body: node('p', { text: 'Usuwa wyłącznie członkostwo w projekcie. Konto użytkownika i członkostwo tenanta pozostają.' }),
            onSubmit: async () => {
              await api(`/projects/${id}/members/${row.user_id}?expected_version=${row.version}`, { method: 'DELETE' });
              await navigate('/projects/' + encodeURIComponent(id) + '/members'); return false;
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
    const userField = searchableSelectField('Członek tenanta', 'user_id', [], '', {
      required: true,
      wide: true,
      placeholder: 'Wpisz login członka tenanta…',
      help: 'Wpisuj kolejne znaki, aby zawężać listę aktywnych członków tenanta.',
    });
    const userPicker = userField.searchableSelect;
    const noUsers = node('p', {
      class: 'form-error',
      hidden: true,
      text: 'Brak aktywnych członków tenanta dostępnych do dodania do projektu.',
    });
    let roleOffset = 0, moreRoles;
    async function loadRoles() {
      const page = await api(`/projects/${id}/assignable-roles?limit=${pageSize}&offset=${roleOffset}`);
      if (current !== generation || !viewIs('projects')) return;
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
      const users = [];
      let offset = 0;
      let total = 0;
      do {
        const page = await api(`/projects/${id}/eligible-members?limit=200&offset=${offset}`);
        if (current !== generation || !viewIs('projects')) return;
        users.push(...page.items);
        offset += page.items.length;
        total = Number(page.total || users.length);
        if (!page.items.length) break;
      } while (offset < total);
      userPicker.setChoices(users.map(user => ({
        value: String(user.user_id),
        label: `${user.username} (#${user.user_id})`,
      })));
      noUsers.hidden = users.length > 0;
    }
    moreRoles = action('Kolejne role', loadRoles);
    if (assigner) await loadRoles(); if (!member) await loadUsers();
    if (current !== generation || !viewIs('projects')) return;
    openModal({ title: member ? 'Role członka projektu' : 'Dodaj członka projektu', body: node('div', { class: 'stack' },
      member ? node('p', { text: member.username }) : userField, member ? null : noUsers,
      node('p', { class: 'muted', text: 'Uprawnienia są nadawane przez role RBAC tylko w tym projekcie. Role projektowe nie rozszerzają uprawnień globalnych.' }),
      assigner ? roles : node('p', { text: 'Członkostwo bez roli — brak uprawnienia do przypisywania ról.' }), assigner ? moreRoles : null),
      onSubmit: async data => {
        const roleIds = new Set(member?.role_ids || []);
        roles.querySelectorAll('input[type="checkbox"]').forEach(input => {
          const roleId = Number(input.name.slice(5)); input.checked ? roleIds.add(roleId) : roleIds.delete(roleId);
        });
        const selectedUserId = member ? member.user_id : Number(data.get('user_id'));
        if (!member && (!Number.isInteger(selectedUserId) || selectedUserId <= 0)) {
          throw new Error('Wybierz członka tenanta z listy.');
        }
        const values = member ? { role_ids: [...roleIds], expected_version: member.version } : { user_id: selectedUserId, role_ids: [...roleIds] };
        await api(member ? `/projects/${id}/members/${member.user_id}/roles` : `/projects/${id}/members`, { method: member ? 'PUT' : 'POST', idempotent: !member, body: values });
        await members(id); return false;
      },
    });
  }

  async function history(id, offset = 0) {
    const current = ++generation;
    const page = await api(`/projects/${id}/audit?limit=${pageSize}&offset=${offset}`);
    if (current !== generation || !viewIs('projects')) return;
    openModal({ title: 'Audyt projektu', wide: true, body: node('div', { class: 'stack' }, action('Szczegóły projektu', () => navigate('/projects/' + encodeURIComponent(id))),
      table([{ label: 'Czas', value: row => formatDate(row.timestamp) }, { label: 'Operacja', value: row => row.action },
        { label: 'Użytkownik', value: row => row.user_id ?? 'System' }, { label: 'Obiekt', value: row => row.resource_id },
        { label: 'Wynik', value: row => row.result }], page.items), controls(page, next => history(id, next))) });
  }
  registerRoutedForm({
    id: 'projects-details',
    pattern: /^\/projects\/(?<id>[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})$/,
    parent: 'projects',
    permission: null,
    label: 'Projekty',
  }, match => details(match.params.id));
  registerRoutedForm({
    id: 'projects-members',
    pattern: /^\/projects\/(?<id>[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})\/members$/,
    parent: 'projects',
    permission: null,
    label: 'Projekty',
  }, match => members(match.params.id));
  registerRoutedForm({
    id: 'projects-audit',
    pattern: /^\/projects\/(?<id>[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})\/audit$/,
    parent: 'projects',
    permission: null,
    label: 'Projekty',
  }, match => history(match.params.id));
  registerRoutedForm({
    id: 'projects-member-create',
    pattern: /^\/projects\/(?<id>[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})\/members\/new$/,
    parent: 'projects',
    permission: null,
    label: 'Projekty',
  }, async match => {
    const id = match.params.id;
    const scope = await api('/projects/' + id + '/permissions');
    if (!scope.permissions.includes('projects.members.manage')) throw new Error('Brak uprawnienia do dodawania członków projektu.');
    await memberForm(id, scope.permissions.includes('projects.roles.assign'));
  });
  registerRoutedForm({
    id: 'projects-member-roles',
    pattern: /^\/projects\/(?<id>[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})\/members\/(?<userId>\d+)\/roles$/,
    parent: 'projects',
    permission: null,
    label: 'Projekty',
  }, async match => {
    const id = match.params.id;
    const [scope, page] = await Promise.all([
      api('/projects/' + id + '/permissions'),
      api('/projects/' + id + '/members?limit=200&offset=0'),
    ]);
    if (!scope.permissions.includes('projects.roles.assign')) throw new Error('Brak uprawnienia do przypisywania ról projektowych.');
    const member = page.items.find(row => Number(row.user_id) === Number(match.params.userId));
    if (!member) throw new Error('Nie znaleziono członka projektu.');
    await memberForm(id, true, member);
  });

  registerRoutedForm({
    id: 'projects-create',
    pattern: /^\/projects\/new$/,
    parent: 'projects',
    permission: null,
    label: 'Projekty',
  }, () => projectForm());
  registerRoutedForm({
    id: 'projects-edit',
    pattern: /^\/projects\/edit\/(?<id>[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})(?:\/[^/]+)?$/,
    parent: 'projects',
    permission: null,
    label: 'Projekty',
  }, async match => {
    const [item, scope] = await Promise.all([
      api('/projects/' + match.params.id),
      api('/projects/' + match.params.id + '/permissions'),
    ]);
    if (!scope.permissions.includes('projects.update')) throw new Error('Brak uprawnienia do edycji tego projektu.');
    await projectForm(item, scope);
  });

  document.addEventListener('cloudportal:app-hidden', () => { generation++; listOffset = 0; tenantFilter = ''; listStatus = ''; });
  document.addEventListener('cloudportal:tenant-projects', event => {
    generation++; tenantFilter = String(event.detail?.tenantId || ''); listOffset = 0;
    navigate('projects').catch(error => toast(error.message, 'error'));
  });
  registerView({ id: 'projects', label: 'Projekty', icon: 'P', permission: null, order: 166 }, projectsView);
})();
