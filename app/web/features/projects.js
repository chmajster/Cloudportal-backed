'use strict';

(() => {
  const labels = { active: 'Aktywny', suspended: 'Wstrzymany', disabled: 'Wyłączony' };
  const states = ['active', 'suspended', 'disabled'].map(value => ({ value, label: labels[value] }));
  let listOffset = 0;
  let listStatus = '';
  let generation = 0;
  const pageSize = 50;

  function action(label, operation, kind = 'ghost', disabled = false) {
    return button(label, () => Promise.resolve().then(operation).catch(error => toast(error.message, 'error')), kind, disabled);
  }

  function pageControls(page, reload) {
    return node('div', { class: 'projects-pagination' },
      action('← Poprzednia', () => reload(Math.max(0, page.offset - page.limit)), 'ghost', page.offset === 0),
      node('span', { role: 'status', text: `${page.total ? page.offset + 1 : 0}–${Math.min(page.offset + page.items.length, page.total)} z ${page.total}` }),
      action('Następna →', () => reload(page.offset + page.limit), 'ghost', page.offset + page.limit >= page.total));
  }

  function foundationNotice() {
    return node('p', { class: 'projects-notice', role: 'note', text:
      'Projects: członkostwo, role i wybór kontekstu są sprawdzane przez backend. Integracja scope z istniejącymi operacjami infrastruktury wymaga osobnego etapu.' });
  }

  async function projectsView() {
    const current = ++generation;
    const [page, eligible, selected] = await Promise.all([
      api(`/projects?limit=${pageSize}&offset=${listOffset}${listStatus ? '&status=' + encodeURIComponent(listStatus) : ''}`),
      api('/project-creation-tenants?limit=1'),
      api('/project-context').catch(error => { if (error.status === 404) return { selected: null, version: 0, revoked: true }; throw error; }),
    ]);
    if (current !== generation || state.view !== 'projects') return;
    const filter = selectField('Status', 'project_status', [{ value: '', label: 'Wszystkie dostępne' }, ...states], listStatus);
    filter.querySelector('select').addEventListener('change', event => {
      listStatus = event.target.value; listOffset = 0; projectsView().catch(error => toast(error.message, 'error'));
    });
    const actions = eligible.total > 0
      ? [action('Nowy projekt', () => projectForm(), 'primary')] : [];
    dom.content.replaceChildren(heading('Projekty i delegowane uprawnienia. Listę i dostęp do obiektów filtruje backend.', actions),
      foundationNotice(), node('div', { class: 'projects-context' },
        node('p', { role: 'status', text: selected.selected ? `Wybrany projekt: ${selected.selected.name} / ${selected.selected.tenant_id}` : (selected.revoked ? 'Dostęp do poprzednio wybranego projektu został cofnięty.' : 'Projekt nie został wybrany.') }),
        action('Wyczyść wybór', async () => { await api('/project-context', { method: 'DELETE' }); await projectsView(); })), filter,
      table([
        { label: 'Nazwa', value: item => node('strong', { text: item.name }) },
        { label: 'Slug', value: item => item.slug },
        { label: 'Status', value: item => badge(labels[item.status], item.status === 'active' ? 'ok' : 'warning') },
        { label: 'Systemowy', value: item => item.is_system ? 'Tak — chroniony' : 'Nie' },
        { label: 'Aktualizacja', value: item => formatDate(item.updated_at) },
      ], page.items, item => [action('Szczegóły', () => projectDetails(item.id))]),
      pageControls(page, offset => { listOffset = offset; return projectsView(); }));
  }

  async function projectDetails(id) {
    const current = ++generation;
    const [item, scope] = await Promise.all([api(`/projects/${id}`), api(`/projects/${id}/permissions`)]);
    if (current !== generation || state.view !== 'projects') return;
    const permits = permission => scope.permissions.includes(permission);
    const editable = item.status === 'active' || scope.parent_administration;
    const actions = [];
    if (permits('projects.select')) actions.push(action('Wybierz jako aktywny projekt', async () => {
      const selected = await api('/project-context');
      await api('/project-context', { method: 'PUT', body: { tenant_id: item.tenant_id, project_id: id, expected_version: selected.version } });
      toast('Wybrano projekt.'); await navigate('projects');
    }, 'primary'));
    if (permits('projects.update') && editable) actions.push(action('Edytuj', () => projectForm(item, scope)));
    if (permits('projects.members.read')) actions.push(action('Członkowie', () => membersView(item.id, scope)));
    if (permits('projects.audit.read')) actions.push(action('Historia audytu', () => auditView(item.id)));
    if (!item.is_system && permits('projects.delete') && editable) {
      actions.push(action('Usuń projekt', () => openModal({
        title: `Usuń projekt: ${item.name}`, danger: true, submitLabel: 'Usuń pusty projekt',
        body: node('p', { text: 'Backend wymaga pustego projektu i zgodnej wersji. Historia audytu pozostanie zachowana.' }),
        onSubmit: async () => {
          await api(`/projects/${item.id}?expected_version=${item.version}`, { method: 'DELETE' });
          listOffset = 0; await navigate('projects'); return false;
        },
      }), 'danger'));
    }
    openModal({ title: item.name, eyebrow: 'Projekt / szczegóły', wide: true,
      body: node('div', { class: 'stack' }, foundationNotice(),
        node('div', { class: 'action-group' }, actions),
        node('p', { text: item.description || 'Brak opisu.' }),
        node('dl', { class: 'projects-overview' },
          node('dt', { text: 'Tenant ID' }), node('dd', { class: 'mono', text: item.tenant_id }),
          node('dt', { text: 'Projekt ID' }), node('dd', { class: 'mono', text: item.id }),
          node('dt', { text: 'Status' }), node('dd', { text: labels[item.status] }),
          node('dt', { text: 'Wersja' }), node('dd', { text: item.version }),
          node('dt', { text: 'Typ dostępu' }), node('dd', { text: scope.parent_administration ? 'Administracja nadrzędnego tenanta / platformy' : 'Delegacja w tym projekcie' })),
        node('h3', { text: 'Efektywne uprawnienia w tym scope' }),
        node('pre', { class: 'projects-json', text: scope.permissions.join('\n') }),
        node('h3', { text: 'Etykiety / metadata' }),
        node('pre', { class: 'projects-json', text: JSON.stringify({ labels: item.labels, metadata: item.metadata }, null, 2) })),
    });
  }

  function jsonObject(value, label) {
    const parsed = JSON.parse(value || '{}');
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error(`${label}: wymagany obiekt JSON.`);
    return parsed;
  }

  async function projectForm(item = null, scope = null) {
    const current = ++generation;
    const statuses = scope && !scope.parent_administration ? states.filter(row => row.value !== 'disabled') : states;
    const body = node('div', { class: 'form-grid' },
      field('Nazwa', 'name', { value: item?.name || '', required: true, maxlength: 100 }),
      field('Slug', 'slug', { value: item?.slug || '', required: true, maxlength: 63, help: 'Małe litery, cyfry i pojedyncze myślniki.' }),
      selectField('Status', 'status', statuses, item?.status || 'active'),
      field('Domyślne środowisko', 'default_environment', { value: item?.default_environment || 'dev', required: true, maxlength: 63 }),
      field('Opis', 'description', { tag: 'textarea', value: item?.description || '', maxlength: 4000, wide: true }),
      field('Etykiety — JSON', 'labels', { tag: 'textarea', value: JSON.stringify(item?.labels || {}, null, 2), wide: true }),
      field('Metadata — JSON', 'metadata', { tag: 'textarea', value: JSON.stringify(item?.metadata || {}, null, 2), wide: true,
        help: 'Maksymalnie 16 KiB i 8 poziomów zagnieżdżenia. Nie wpisuj sekretów.' }));
    if (!item) body.prepend(await paginatedPicker('Tenant', 'tenant_id', '/project-creation-tenants', row => row.id, row => row.name, current));
    if (current !== generation || state.view !== 'projects') return;
    if (item && scope && !scope.parent_administration) body.querySelector('[name="status"]').disabled = true;
    if (item?.is_system) {
      for (const name of ['name', 'slug', 'status']) body.querySelector(`[name="${name}"]`).disabled = true;
    }
    openModal({ title: item ? 'Edytuj projekt' : 'Nowy projekt', eyebrow: 'Administracja projektami', body,
      onSubmit: async data => {
        const values = {
          name: item?.is_system ? item.name : data.get('name'),
          slug: item?.is_system ? item.slug : data.get('slug'),
          status: item?.is_system || (item && scope && !scope.parent_administration) ? item.status : data.get('status'),
          default_environment: data.get('default_environment'),
          description: data.get('description'), labels: jsonObject(data.get('labels'), 'Etykiety'),
          metadata: jsonObject(data.get('metadata'), 'Metadata'),
        };
        if (item) values.expected_version = item.version;
        else values.tenant_id = data.get('tenant_id');
        await api(item ? `/projects/${item.id}` : '/projects', { method: item ? 'PUT' : 'POST', idempotent: !item, body: values });
        toast('Projekt zapisany.'); await navigate('projects'); return false;
      },
    });
  }

  async function membersView(id, _previousScope = null, offset = 0) {
    const current = ++generation;
    const [item, scope, page] = await Promise.all([api(`/projects/${id}`), api(`/projects/${id}/permissions`),
      api(`/projects/${id}/members?limit=${pageSize}&offset=${offset}`)]);
    if (current !== generation || state.view !== 'projects') return;
    const permits = permission => scope.permissions.includes(permission);
    const editable = item.status === 'active' || scope.parent_administration;
    const manager = editable && permits('projects.members.manage');
    const assigner = editable && permits('projects.roles.assign');
    const actions = [action('Szczegóły projektu', () => projectDetails(id))];
    if (manager) actions.push(action('Dodaj członka', () => memberForm(id, assigner), 'primary'));
    openModal({ title: `${item.name} — członkowie`, wide: true, body: node('div', { class: 'stack' },
      node('div', { class: 'action-group' }, actions),
      table([
        { label: 'Użytkownik', value: row => row.username },
        { label: 'ID', value: row => row.user_id },
        { label: 'Status', value: row => labels[row.status] },
        { label: 'Role (ID)', value: row => row.role_ids.join(', ') || 'Brak' },
        { label: 'Wersja', value: row => row.version },
      ], page.items, row => {
        const rowActions = [];
        if (assigner) rowActions.push(action('Przypisz role', () => memberForm(id, true, row)));
        if (manager) {
          rowActions.push(action(row.status === 'active' ? 'Wyłącz' : 'Włącz', async () => {
            await api(`/projects/${id}/members/${row.user_id}`, { method: 'PUT', body: {
              status: row.status === 'active' ? 'disabled' : 'active', expected_version: row.version,
            } }); await membersView(id, null, offset);
          }));
          rowActions.push(action('Usuń członkostwo', () => openModal({ title: `Usuń członkostwo: ${row.username}`,
            danger: true, submitLabel: 'Usuń członkostwo', body: node('p', { text: 'Usuwa dostęp w tym projekcie, nie konto użytkownika. Backend chroni ostatniego menedżera.' }),
            onSubmit: async () => {
              await api(`/projects/${id}/members/${row.user_id}?expected_version=${row.version}`, { method: 'DELETE' });
              await membersView(id); return false;
            },
          }), 'danger'));
        }
        return rowActions;
      }), pageControls(page, next => membersView(id, null, next))),
    });
  }

  async function memberForm(id, assigner, member = null) {
    const current = ++generation;
    const choices = node('div', { class: 'projects-role-options' });
    const picker = node('fieldset', {}, node('legend', { text: 'Role w tym projekcie' }), choices);
    let offset = 0;
    const selected = new Set((member?.role_ids || []).map(String));
    const renderedRoles = new Set();
    // Existing assignments may reference a role that is no longer delegable.
    // Still render a removable checkbox; never trap a member in such a role.
    (member?.role_ids || []).forEach(roleId => {
      choices.append(checkboxField(`Obecna rola #${roleId}`, 'role_' + roleId, true));
      renderedRoles.add(roleId);
    });
    let more;
    async function loadRoles() {
      const page = await api(`/projects/${id}/assignable-roles?limit=${pageSize}&offset=${offset}`);
      if (current !== generation || state.view !== 'projects') return;
      page.items.forEach(role => {
        if (renderedRoles.has(role.id)) return;
        choices.append(checkboxField(`${role.name} (${role.id})`, 'role_' + role.id, selected.has(String(role.id))));
        renderedRoles.add(role.id);
      });
      offset += page.items.length;
      more.disabled = offset >= page.total;
    }
    more = action('Pokaż kolejne role', loadRoles);
    picker.append(more);
    if (assigner) await loadRoles();
    if (current !== generation || state.view !== 'projects') return;
    const memberPicker = member ? null : await paginatedPicker('Członek tenanta', 'user_id', `/projects/${id}/eligible-members`, row => row.user_id, row => `${row.username} (#${row.user_id})`, current);
    if (current !== generation || state.view !== 'projects') return;
    const body = node('div', { class: 'stack' },
      member ? node('p', { text: `${member.username} / ID ${member.user_id}` }) : memberPicker,
      assigner ? picker : node('p', { text: 'Nie masz uprawnienia do nadawania ról. Członkostwo zostanie utworzone bez roli.' }),
      member ? node('p', { class: 'muted', text: 'Zapis zastępuje zestaw ról. Zaznacz role, które mają pozostać; lista ma paginację.' }) : null);
    openModal({ title: member ? 'Role członka' : 'Dodaj członka', body, onSubmit: async data => {
      const roleIds = new Set(member?.role_ids || []);
      choices.querySelectorAll('input[type="checkbox"]').forEach(input => {
        const roleId = Number(input.name.slice(5));
        input.checked ? roleIds.add(roleId) : roleIds.delete(roleId);
      });
      const values = member ? { role_ids: [...roleIds], expected_version: member.version }
        : { user_id: Number(data.get('user_id')), role_ids: [...roleIds] };
      await api(member ? `/projects/${id}/members/${member.user_id}/roles` : `/projects/${id}/members`, {
        method: member ? 'PUT' : 'POST', idempotent: !member, body: values,
      });
      await membersView(id); return false;
    } });
  }

  async function paginatedPicker(label, name, path, value, text, current) {
    const control = selectField(label, name, [{ value: '', label: 'Wybierz...' }], '');
    const select = control.querySelector('select');
    select.required = true;
    let offset = 0;
    const more = action('Pokaż kolejne', async () => load());
    async function load() {
      more.disabled = true;
      try {
        const page = await api(`${path}?limit=${pageSize}&offset=${offset}`);
        if (current !== generation || state.view !== 'projects') return;
        page.items.forEach(row => select.append(node('option', { value: value(row), text: text(row) })));
        offset += page.items.length;
        more.disabled = offset >= page.total;
      } catch (error) { more.disabled = false; throw error; }
    }
    await load();
    return node('div', { class: 'stack' }, control, more);
  }

  async function auditView(id, offset = 0) {
    const current = ++generation;
    const page = await api(`/projects/${id}/audit?limit=${pageSize}&offset=${offset}`);
    if (current !== generation || state.view !== 'projects') return;
    openModal({ title: 'Historia projektu', eyebrow: 'Istniejący audit / wybrany scope', wide: true,
      body: node('div', { class: 'stack' }, action('Szczegóły projektu', () => projectDetails(id)),
        table([
          { label: 'Czas', value: row => formatDate(row.timestamp) },
          { label: 'Operacja', value: row => row.action },
          { label: 'Aktor (ID)', value: row => row.user_id ?? 'System' },
          { label: 'Obiekt', value: row => row.resource_id },
          { label: 'Wynik', value: row => row.result },
          { label: 'Request ID', value: row => row.request_id },
        ], page.items), pageControls(page, next => auditView(id, next))),
    });
  }

  document.addEventListener('cloudportal:app-hidden', () => { generation++; listOffset = 0; listStatus = ''; });
  // Global identity permissions intentionally do not include project grants.
  // An authenticated account can open the view; backend SQL decides what is visible.
  registerView({ id: 'projects', label: 'Projekty', icon: 'P', permission: null, order: 166 }, projectsView);
})();
