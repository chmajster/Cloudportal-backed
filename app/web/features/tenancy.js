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

  function scopedRoleOption(role, checked) {
    const permissions = (role.permissions || []).slice().sort();
    return node('div', { class: 'tenancy-role-option' },
      checkboxField(`${role.name} (#${role.id})`, 'role_' + role.id, checked),
      node('div', { class: 'field-help', text: permissions.length
        ? 'Uprawnienia: ' + permissions.join(', ')
        : 'Brak delegowalnych uprawnień w tym zakresie.' }));
  }

  function pageControls(page, reload) {
    return node('div', { class: 'tenancy-pagination' },
      action('← Poprzednia', () => reload(Math.max(0, page.offset - page.limit)), 'ghost', page.offset === 0),
      node('span', { role: 'status', text: `${page.total ? page.offset + 1 : 0}–${Math.min(page.offset + page.items.length, page.total)} z ${page.total}` }),
      action('Następna →', () => reload(page.offset + page.limit), 'ghost', page.offset + page.limit >= page.total));
  }

  function foundationNotice() {
    return node('p', { class: 'tenancy-notice', role: 'note', text:
      'Etap fundamentu governance: uprawnienia poniżej dotyczą zarządzania tenantami. Istniejące VM, deploymenty i credentials nie są jeszcze izolowane przez ten moduł.' });
  }

  async function tenantsView() {
    const current = ++generation;
    const page = await api(`/tenants?limit=${pageSize}&offset=${listOffset}${listStatus ? '&status=' + encodeURIComponent(listStatus) : ''}`);
    if (current !== generation || !viewIs('tenants')) return;
    const filter = selectField('Status', 'tenant_status', [{ value: '', label: 'Wszystkie dostępne' }, ...states], listStatus);
    filter.querySelector('select').addEventListener('change', event => {
      listStatus = event.target.value; listOffset = 0; tenantsView().catch(error => toast(error.message, 'error'));
    });
    const actions = allowed('tenants.admin') && allowed('tenants.create')
      ? [action('Nowy tenant', () => navigate('/tenants/new'), 'primary')] : [];
    dom.content.replaceChildren(heading('Tenanci i delegowane uprawnienia. Listę i dostęp do obiektów filtruje backend.', actions),
      foundationNotice(), filter,
      table([
        { label: 'Nazwa', value: item => node('strong', { text: item.name }) },
        { label: 'Slug', value: item => item.slug },
        { label: 'Status', value: item => badge(labels[item.status], item.status === 'active' ? 'ok' : 'warning') },
        { label: 'Systemowy', value: item => item.is_system ? 'Tak — chroniony' : 'Nie' },
        { label: 'Aktualizacja', value: item => formatDate(item.updated_at) },
      ], page.items, item => [action('Szczegóły', () => tenantDetails(item.id))]),
      pageControls(page, offset => { listOffset = offset; return tenantsView(); }));
  }

  async function tenantDetails(id) {
    const current = ++generation;
    const [item, scope] = await Promise.all([api(`/tenants/${id}`), api(`/tenants/${id}/permissions`)]);
    if (current !== generation || !viewIs('tenants')) return;
    const permits = permission => scope.permissions.includes(permission);
    const editable = item.status === 'active' || scope.global_administration;
    const actions = [];
    if (permits('tenants.update') && editable) actions.push(action('Edytuj', () => navigate('/tenants/edit/' + encodeURIComponent(item.id) + '/' + encodeURIComponent(item.slug || item.name || 'tenant'))));
    if (permits('tenants.members.read')) actions.push(action('Członkowie', () => membersView(item.id, scope)));
    if (permits('projects.read')) actions.push(action('Projekty', () => document.dispatchEvent(new CustomEvent('cloudportal:tenant-projects', { detail: { tenantId: item.id } }))));
    if (permits('tenants.audit.read')) actions.push(action('Historia audytu', () => auditView(item.id)));
    if (!item.is_system && scope.global_administration && permits('tenants.delete')) {
      actions.push(action('Usuń tenant', () => openModal({
        title: `Usuń tenant: ${item.name}`, danger: true, submitLabel: 'Usuń pusty tenant',
        body: node('p', { text: 'Backend wymaga pustego tenanta i zgodnej wersji. Historia audytu pozostanie zachowana.' }),
        onSubmit: async () => {
          await api(`/tenants/${item.id}?expected_version=${item.version}`, { method: 'DELETE' });
          listOffset = 0; await navigate('tenants'); return false;
        },
      }), 'danger'));
    }
    openModal({ title: item.name, eyebrow: 'Tenant / szczegóły', wide: true,
      body: node('div', { class: 'stack' }, foundationNotice(),
        node('div', { class: 'action-group' }, actions),
        node('p', { text: item.description || 'Brak opisu.' }),
        node('dl', { class: 'tenancy-overview' },
          node('dt', { text: 'Tenant ID' }), node('dd', { class: 'mono', text: item.id }),
          node('dt', { text: 'Status' }), node('dd', { text: labels[item.status] }),
          node('dt', { text: 'Wersja' }), node('dd', { text: item.version }),
          node('dt', { text: 'Typ dostępu' }), node('dd', { text: scope.global_administration ? 'Jawne uprawnienia globalne' : 'Delegacja w tym tenancie' })),
        node('h3', { text: 'Efektywne uprawnienia w tym scope' }),
        node('pre', { class: 'tenancy-json', text: scope.permissions.join('\n') }),
        node('h3', { text: 'Etykiety / metadata' }),
        node('pre', { class: 'tenancy-json', text: JSON.stringify({ labels: item.labels, metadata: item.metadata }, null, 2) })),
    });
  }

  function jsonObject(value, label) {
    const parsed = JSON.parse(value || '{}');
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error(`${label}: wymagany obiekt JSON.`);
    return parsed;
  }

  function tenantForm(item = null, scope = null) {
    generation++;
    const statuses = scope && !scope.global_administration ? states.filter(row => row.value !== 'disabled') : states;
    const body = node('div', { class: 'form-grid' },
      field('Nazwa', 'name', { value: item?.name || '', required: true, maxlength: 100 }),
      field('Slug', 'slug', { value: item?.slug || '', required: true, maxlength: 63, help: 'Małe litery, cyfry i pojedyncze myślniki.' }),
      selectField('Status', 'status', statuses, item?.status || 'active'),
      field('Opis', 'description', { tag: 'textarea', value: item?.description || '', maxlength: 4000, wide: true }),
      field('Etykiety — JSON', 'labels', { tag: 'textarea', value: JSON.stringify(item?.labels || {}, null, 2), wide: true }),
      field('Metadata — JSON', 'metadata', { tag: 'textarea', value: JSON.stringify(item?.metadata || {}, null, 2), wide: true,
        help: 'Maksymalnie 16 KiB i 8 poziomów zagnieżdżenia. Nie wpisuj sekretów.' }));
    if (item?.is_system) {
      for (const name of ['name', 'slug', 'status']) body.querySelector(`[name="${name}"]`).disabled = true;
    }
    openModal({ title: item ? 'Edytuj tenant' : 'Nowy tenant', eyebrow: 'Administracja tenantami', body,
      onSubmit: async data => {
        const values = {
          name: item?.is_system ? item.name : data.get('name'),
          slug: item?.is_system ? item.slug : data.get('slug'),
          status: item?.is_system ? item.status : data.get('status'),
          description: data.get('description'), labels: jsonObject(data.get('labels'), 'Etykiety'),
          metadata: jsonObject(data.get('metadata'), 'Metadata'),
        };
        if (item) values.expected_version = item.version;
        await api(item ? `/tenants/${item.id}` : '/tenants', { method: item ? 'PUT' : 'POST', idempotent: !item, body: values });
        toast('Tenant zapisany.'); await navigate('tenants'); return false;
      },
    });
  }

  async function membersView(id, _previousScope = null, offset = 0) {
    const current = ++generation;
    const [item, scope, page] = await Promise.all([api(`/tenants/${id}`), api(`/tenants/${id}/permissions`),
      api(`/tenants/${id}/members?limit=${pageSize}&offset=${offset}`)]);
    if (current !== generation || !viewIs('tenants')) return;
    const permits = permission => scope.permissions.includes(permission);
    const editable = item.status === 'active' || scope.global_administration;
    const manager = editable && permits('tenants.members.manage');
    const assigner = editable && permits('tenants.roles.assign');
    const actions = [action('Szczegóły tenanta', () => tenantDetails(id))];
    if (manager && allowed('users.read')) actions.push(action('Dodaj członka', () => memberForm(id, assigner), 'primary'));
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
            await api(`/tenants/${id}/members/${row.user_id}`, { method: 'PUT', body: {
              status: row.status === 'active' ? 'disabled' : 'active', expected_version: row.version,
            } }); await membersView(id, null, offset);
          }));
          rowActions.push(action('Usuń członkostwo', () => openModal({ title: `Usuń członkostwo: ${row.username}`,
            danger: true, submitLabel: 'Usuń członkostwo', body: node('p', { text: 'Usuwa dostęp w tym tenancie, nie konto użytkownika. Backend chroni ostatniego menedżera.' }),
            onSubmit: async () => {
              await api(`/tenants/${id}/members/${row.user_id}?expected_version=${row.version}`, { method: 'DELETE' });
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
    const choices = node('div', { class: 'tenancy-role-options' });
    const picker = node('fieldset', {}, node('legend', { text: 'Role w tym tenancie' }), choices);
    let offset = 0;
    const selected = new Set((member?.role_ids || []).map(String));
    const renderedRoles = new Set();
    // Existing assignments may reference a role that is no longer delegable.
    // Still render a removable checkbox; never trap a member in such a role.
    (member?.role_ids || []).forEach(roleId => {
      const fallback = node('div', { class: 'tenancy-role-option', 'data-role-id': String(roleId) },
        checkboxField(`Obecna rola #${roleId}`, 'role_' + roleId, true),
        node('div', { class: 'field-help', text: 'Rola przypisana wcześniej; jej aktualne uprawnienia zostaną zweryfikowane przez backend.' }));
      choices.append(fallback);
    });
    let more;
    async function loadRoles() {
      const page = await api(`/tenants/${id}/assignable-roles?limit=${pageSize}&offset=${offset}`);
      if (current !== generation || !viewIs('tenants')) return;
      page.items.forEach(role => {
        if (renderedRoles.has(role.id)) return;
        const existingFallback = choices.querySelector('[data-role-id="' + role.id + '"]');
        const checked = existingFallback?.querySelector('input[type="checkbox"]')?.checked
          ?? selected.has(String(role.id));
        if (existingFallback) existingFallback.remove();
        const option = scopedRoleOption(role, checked);
        option.dataset.roleId = String(role.id);
        choices.append(option);
        renderedRoles.add(role.id);
      });
      offset += page.items.length;
      more.disabled = offset >= page.total;
    }
    more = action('Pokaż kolejne role', loadRoles);
    picker.append(more);
    if (assigner) await loadRoles();
    let userField = null;
    if (!member) {
      const users = await api('/users?limit=200');
      if (current !== generation || !viewIs('tenants')) return;
      const candidates = (users.items || []).filter(user => user.is_active !== false && user.is_locked !== true);
      userField = selectField('Użytkownik', 'user_id',
        candidates.map(user => ({
          value: String(user.id),
          label: user.username + (user.email ? ' — ' + user.email : '') + ' (#' + user.id + ')',
        })),
        '', { required: true, placeholder: 'Wybierz użytkownika' });
      userField.append(node('span', { class: 'field-help',
        text: 'Po dodaniu użytkownika wybierz role RBAC. Backend nie pozwoli nadać uprawnień szerszych niż Twoje w tej organizacji.' }));
    }
    if (current !== generation || !viewIs('tenants')) return;
    const body = node('div', { class: 'stack' },
      member ? node('p', { text: `${member.username} / ID ${member.user_id}` }) : userField,
      node('p', { class: 'muted', text: 'Uprawnienia są nadawane przez role RBAC w tej organizacji; nie zmieniają globalnych uprawnień konta.' }),
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
      await api(member ? `/tenants/${id}/members/${member.user_id}/roles` : `/tenants/${id}/members`, {
        method: member ? 'PUT' : 'POST', idempotent: !member, body: values,
      });
      await membersView(id); return false;
    } });
  }

  async function auditView(id, offset = 0) {
    const current = ++generation;
    const page = await api(`/tenants/${id}/audit?limit=${pageSize}&offset=${offset}`);
    if (current !== generation || !viewIs('tenants')) return;
    openModal({ title: 'Historia tenanta', eyebrow: 'Istniejący audit / wybrany scope', wide: true,
      body: node('div', { class: 'stack' }, action('Szczegóły tenanta', () => tenantDetails(id)),
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
  registerRoutedForm({
    id: 'tenants-create',
    pattern: /^\/tenants\/new$/,
    parent: 'tenants',
    permission: null,
    label: 'Tenanci',
  }, async () => {
    if (!allowed('tenants.admin') || !allowed('tenants.create')) throw new Error('Brak uprawnienia do tworzenia tenantów.');
    tenantForm();
  });
  registerRoutedForm({
    id: 'tenants-edit',
    pattern: /^\/tenants\/edit\/(?<id>\d+)(?:\/[^/]+)?$/,
    parent: 'tenants',
    permission: null,
    label: 'Tenanci',
  }, async match => {
    const [item, scope] = await Promise.all([
      api('/tenants/' + match.params.id),
      api('/tenants/' + match.params.id + '/permissions'),
    ]);
    const editable = item.status === 'active' || scope.global_administration;
    if (!editable || !scope.permissions.includes('tenants.update')) throw new Error('Brak uprawnienia do edycji tego tenanta.');
    tenantForm(item, scope);
  });

  // Global identity permissions intentionally do not include tenant grants.
  // An authenticated account can open the view; backend SQL decides what is visible.
  registerView({ id: 'tenants', label: 'Tenanci', icon: 'T', permission: null, order: 165 }, tenantsView);
})();
