'use strict';

(() => {
let rbacTab = 'overview';
let rbacSelectedUserId = null;

const RBAC_TABS = [
  { id: 'overview', label: 'Przegląd' },
  { id: 'assignments', label: 'Przypisania' },
  { id: 'roles', label: 'Role' },
  { id: 'analysis', label: 'Analiza dostępu' },
  { id: 'governance', label: 'Zakresy i polityki' },
];

function authSourceBadge(user) {
  if (user.auth_source === 'ldap') return badge('LDAP', 'info');
  if (user.auth_source === 'oidc') return badge('SSO / OIDC', 'ok');
  return badge('Lokalne', '');
}

function setRbacTab(tab) {
  rbacTab = RBAC_TABS.some(item => item.id === tab) ? tab : 'overview';
  return rolesView();
}

function rbacTabs() {
  return node('div', { class: 'rbac-tabs', role: 'tablist', 'aria-label': 'Role i dostęp' },
    ...RBAC_TABS.map(item => node('button', {
      type: 'button',
      class: `rbac-tab ${rbacTab === item.id ? 'active' : ''}`,
      role: 'tab',
      'aria-selected': String(rbacTab === item.id),
      onClick: () => setRbacTab(item.id).catch(error => toast(error.message, 'error')),
    }, item.label)));
}

function rbacStat(label, value, note = '') {
  return node('section', { class: 'panel rbac-stat' },
    node('span', { class: 'rbac-stat-label', text: label }),
    node('strong', { class: 'rbac-stat-value', text: String(value) }),
    note ? node('small', { class: 'muted', text: note }) : null);
}

function roleModuleNames(role) {
  return [...new Set((role.permissions || []).map(permission => {
    const key = String(permission).split('.')[0] || 'other';
    return PERMISSION_GROUP_LABELS[key] || key;
  }))];
}

function roleCard(role) {
  const modules = roleModuleNames(role);
  const actions = [
    button('Uprawnienia', () => showPermissionSummary(role.name, role.permissions)),
  ];
  if (allowed('roles.update')) {
    actions.push(button('Edytuj', () => navigate('/access/roles/edit/' + encodeURIComponent(role.id) + '/' + encodeURIComponent(role.name || 'role')), 'primary'));
  }
  if (allowed('roles.delete')) {
    actions.push(button('Usuń', () => confirmAction(
      'Usuń rolę',
      `Rola ${role.name} zostanie trwale usunięta. Najpierw usuń wszystkie jej przypisania.`,
      async () => {
        await api(`/rbac/roles/${role.id}`, { method: 'DELETE' });
        toast('Rola usunięta.');
        await rolesView();
      }
    ), 'danger'));
  }
  return node('article', { class: 'panel rbac-role-card' },
    node('div', { class: 'rbac-role-card-head' },
      node('div', {},
        node('span', { class: 'rbac-kicker', text: 'Rola RBAC' }),
        node('h3', { text: role.name })),
      badge(`${role.permissions.length} uprawnień`, role.permissions.length ? 'info' : 'warning')),
    node('p', { class: 'muted rbac-role-modules', text: modules.length ? modules.slice(0, 5).join(' · ') : 'Brak przypisanych modułów' }),
    modules.length > 5 ? node('small', { class: 'muted', text: `+${modules.length - 5} kolejnych modułów` }) : null,
    node('div', { class: 'rbac-card-actions' }, actions));
}

function rbacOverviewPanel(roles, users, permissions) {
  const rolePreview = roles.slice(0, 6);
  return node('div', { class: 'rbac-stack' },
    node('section', { class: 'panel rbac-explainer' },
      node('div', {},
        node('span', { class: 'rbac-kicker', text: 'Model dostępu' }),
        node('h2', { text: 'Kto → rola → zakres → polityka' }),
        node('p', { class: 'muted', text: 'Rola definiuje co wolno. Przypisanie wskazuje komu nadajesz rolę. Tenant, projekt i Policy Engine ograniczają gdzie i w jakich warunkach dostęp obowiązuje.' })),
      node('div', { class: 'rbac-flow', 'aria-label': 'Model RBAC' },
        node('span', { text: 'Użytkownik / grupa' }),
        node('strong', { text: '→' }),
        node('span', { text: 'Rola' }),
        node('strong', { text: '→' }),
        node('span', { text: 'Zakres' }),
        node('strong', { text: '→' }),
        node('span', { text: 'Policy Engine' }))),
    node('section', { class: 'rbac-quick-grid' },
      node('button', { type: 'button', class: 'panel rbac-quick-card', onClick: () => setRbacTab('assignments').catch(error => toast(error.message, 'error')) },
        node('strong', { text: 'Nadaj dostęp' }),
        node('span', { text: 'Wybierz użytkownika i przypisz mu jedną lub więcej ról.' })),
      node('button', { type: 'button', class: 'panel rbac-quick-card', onClick: () => setRbacTab('roles').catch(error => toast(error.message, 'error')) },
        node('strong', { text: 'Zarządzaj rolami' }),
        node('span', { text: 'Buduj role z pogrupowanych uprawnień zamiast pojedynczych technicznych bindingów.' })),
      node('button', { type: 'button', class: 'panel rbac-quick-card', onClick: () => setRbacTab('analysis').catch(error => toast(error.message, 'error')) },
        node('strong', { text: 'Sprawdź dostęp' }),
        node('span', { text: 'Zobacz z jakiej roli wynika konkretne uprawnienie użytkownika.' })),
      node('button', { type: 'button', class: 'panel rbac-quick-card', onClick: () => setRbacTab('governance').catch(error => toast(error.message, 'error')) },
        node('strong', { text: 'Zakresy i polityki' }),
        node('span', { text: 'Przejdź do organizacji, projektów i reguł Policy Engine.' }))),
    node('section', { class: 'rbac-section' },
      node('div', { class: 'rbac-section-heading' },
        node('div', {},
          node('span', { class: 'rbac-kicker', text: 'Najważniejsze role' }),
          node('h2', { text: 'Role w systemie' })),
        button('Pokaż wszystkie', () => setRbacTab('roles').catch(error => toast(error.message, 'error')))),
      rolePreview.length
        ? node('div', { class: 'rbac-role-grid' }, ...rolePreview.map(roleCard))
        : node('div', { class: 'panel empty', text: 'Nie ma jeszcze żadnych ról.' })),
    node('section', { class: 'panel rbac-footnote' },
      node('strong', { text: 'Stan katalogu' }),
      node('span', { class: 'muted', text: `${roles.length} ról · ${users?.length ?? '—'} użytkowników · ${permissions.length} zdefiniowanych uprawnień` })));
}

async function rbacFetchAll(path) {
  const items = [];
  let offset = 0;
  while (true) {
    const separator = path.includes('?') ? '&' : '?';
    const page = await api(path + separator + 'limit=200&offset=' + offset);
    const rows = Array.isArray(page?.items) ? page.items : [];
    items.push(...rows);
    offset += rows.length;
    if (!rows.length || offset >= Number(page?.total || rows.length)) break;
  }
  return items;
}

function rbacScopeOption({ id, name, subtitle, checked, disabled, roleCount = 0, status = 'active', inputName }) {
  const input = node('input', {
    type: 'checkbox',
    name: inputName,
    value: id,
    checked,
    disabled,
  });
  const option = node('label', {
    class: 'rbac-scope-option' + (checked ? ' selected' : '') + (disabled ? ' disabled' : ''),
  },
    input,
    node('span', { class: 'rbac-scope-option-copy' },
      node('strong', { text: name }),
      node('small', { class: 'muted', text: subtitle || '' })),
    node('span', { class: 'rbac-scope-option-meta' },
      roleCount ? badge(roleCount + (roleCount === 1 ? ' rola' : ' role'), 'info') : badge('Bez roli', ''),
      status !== 'active' ? badge('Wyłączone', 'warning') : null));
  input.addEventListener('change', () => option.classList.toggle('selected', input.checked));
  return option;
}

function bindScopeFilter(fieldNode, container) {
  const input = fieldNode.querySelector('input');
  input.addEventListener('input', () => {
    const query = searchable(input.value);
    container.querySelectorAll('.rbac-scope-option').forEach(option => {
      option.hidden = Boolean(query) && !searchable(option.textContent).includes(query);
    });
  });
}

async function rbacScopeMembershipEditor(user, refresh) {
  const tenantReadable = allowed('tenants.admin') && allowed('tenants.read') && allowed('tenants.members.read');
  const tenantManageable = tenantReadable && allowed('tenants.members.manage');
  const projectReadable = allowed('projects.admin') && allowed('projects.read') && allowed('projects.members.read');
  const projectManageable = projectReadable && tenantReadable && allowed('projects.members.manage');

  if (!tenantReadable && !projectReadable) {
    return node('section', { class: 'rbac-scope-memberships' },
      node('div', { class: 'rbac-section-heading' },
        node('div', {},
          node('span', { class: 'rbac-kicker', text: 'Zakres dostępu' }),
          node('h3', { text: 'Organizacje i projekty' }),
          node('p', { class: 'muted', text: 'Do centralnego zarządzania członkostwami wymagane są globalne uprawnienia administracyjne tenantów lub projektów.' }))));
  }

  const [tenants, tenantMembershipPage, projects, projectMembershipPage] = await Promise.all([
    tenantReadable ? rbacFetchAll('/tenants') : Promise.resolve([]),
    tenantReadable ? rbacFetchAll('/users/' + encodeURIComponent(user.id) + '/tenant-memberships') : Promise.resolve([]),
    projectReadable ? rbacFetchAll('/projects') : Promise.resolve([]),
    projectReadable ? rbacFetchAll('/users/' + encodeURIComponent(user.id) + '/project-memberships') : Promise.resolve([]),
  ]);

  const tenantMemberships = new Map((tenantMembershipPage || []).map(item => [String(item.tenant_id), item]));
  const projectMemberships = new Map((projectMembershipPage || []).map(item => [String(item.project_id), item]));
  const activeTenantIds = new Set(
    [...tenantMemberships.values()].filter(item => item.status === 'active').map(item => String(item.tenant_id))
  );
  const tenantsById = new Map(tenants.map(item => [String(item.id), item]));

  const sections = [];

  if (tenantReadable) {
    const tenantSearch = field('Szukaj organizacji', 'tenant_membership_filter', {
      type: 'search',
      placeholder: 'Nazwa lub slug organizacji…',
      wide: true,
    });
    const tenantOptions = node('div', { class: 'rbac-scope-options' },
      ...tenants.map(tenant => {
        const membership = tenantMemberships.get(String(tenant.id));
        const checked = membership?.status === 'active';
        return rbacScopeOption({
          id: String(tenant.id),
          name: tenant.name,
          subtitle: tenant.slug,
          checked,
          disabled: !tenantManageable || (!checked && tenant.status !== 'active'),
          roleCount: membership?.role_ids?.length || 0,
          status: membership?.status || tenant.status,
          inputName: 'tenant_scope_id',
        });
      }));
    bindScopeFilter(tenantSearch, tenantOptions);

    const tenantForm = node('form', {
      class: 'panel rbac-scope-membership-card',
      onSubmit: async event => {
        event.preventDefault();
        if (!tenantManageable) return;
        const tenant_ids = [...tenantForm.querySelectorAll('[name="tenant_scope_id"]:checked')].map(input => input.value);
        const submit = tenantForm.querySelector('[type="submit"]');
        submit.disabled = true;
        try {
          await api('/users/' + encodeURIComponent(user.id) + '/tenant-memberships', {
            method: 'PUT',
            body: { tenant_ids },
          });
          toast('Członkostwa w organizacjach zapisane.');
          await refresh();
        } catch (error) {
          toast(error.message, 'error');
        } finally {
          submit.disabled = false;
        }
      },
    },
      node('div', { class: 'rbac-scope-membership-head' },
        node('div', {},
          node('span', { class: 'rbac-kicker', text: 'Organization / Tenant' }),
          node('h3', { text: 'Organizacje użytkownika' }),
          node('p', { class: 'muted', text: 'Użytkownik może należeć do wielu organizacji jednocześnie. Odznaczenie organizacji usuwa również jego członkostwa projektowe w tej organizacji.' })),
        tenantManageable ? node('button', { type: 'submit', class: 'button primary' }, 'Zapisz organizacje') : badge('Tylko odczyt', 'info')),
      tenantSearch,
      tenants.length ? tenantOptions : node('div', { class: 'empty', text: 'Brak dostępnych organizacji.' }));
    sections.push(tenantForm);
  }

  if (projectReadable) {
    const projectSearch = field('Szukaj projektu', 'project_membership_filter', {
      type: 'search',
      placeholder: 'Organizacja, projekt lub slug…',
      wide: true,
    });
    const projectOptions = node('div', { class: 'rbac-scope-options' },
      ...projects.map(project => {
        const membership = projectMemberships.get(String(project.id));
        const checked = membership?.status === 'active';
        const tenant = tenantsById.get(String(project.tenant_id));
        const parentActive = activeTenantIds.has(String(project.tenant_id));
        const unavailable = !checked && (!parentActive || project.status !== 'active' || tenant?.status === 'disabled');
        return rbacScopeOption({
          id: String(project.id),
          name: project.name,
          subtitle: (tenant?.name || project.tenant_id) + ' · ' + project.slug,
          checked,
          disabled: !projectManageable || unavailable,
          roleCount: membership?.role_ids?.length || 0,
          status: membership?.status || project.status,
          inputName: 'project_scope_id',
        });
      }));
    bindScopeFilter(projectSearch, projectOptions);

    const projectForm = node('form', {
      class: 'panel rbac-scope-membership-card',
      onSubmit: async event => {
        event.preventDefault();
        if (!projectManageable) return;
        const project_ids = [...projectForm.querySelectorAll('[name="project_scope_id"]:checked')].map(input => input.value);
        const submit = projectForm.querySelector('[type="submit"]');
        submit.disabled = true;
        try {
          await api('/users/' + encodeURIComponent(user.id) + '/project-memberships', {
            method: 'PUT',
            body: { project_ids },
          });
          toast('Członkostwa w projektach zapisane.');
          await refresh();
        } catch (error) {
          toast(error.message, 'error');
        } finally {
          submit.disabled = false;
        }
      },
    },
      node('div', { class: 'rbac-scope-membership-head' },
        node('div', {},
          node('span', { class: 'rbac-kicker', text: 'Project' }),
          node('h3', { text: 'Projekty użytkownika' }),
          node('p', { class: 'muted', text: 'Użytkownik może należeć do wielu projektów, także w różnych organizacjach. Projekt można przypisać dopiero po aktywnym członkostwie w jego organizacji.' })),
        projectManageable ? node('button', { type: 'submit', class: 'button primary' }, 'Zapisz projekty') : badge('Tylko odczyt', 'info')),
      projectSearch,
      projects.length ? projectOptions : node('div', { class: 'empty', text: 'Brak dostępnych projektów.' }));
    sections.push(projectForm);
  }

  return node('section', { class: 'rbac-scope-memberships' },
    node('div', { class: 'rbac-section-heading' },
      node('div', {},
        node('span', { class: 'rbac-kicker', text: 'Zakres dostępu' }),
        node('h2', { text: 'Organizacje i projekty' }),
        node('p', { class: 'muted', text: 'Członkostwo określa gdzie użytkownik może działać. Role zakresowe nadal są niezależne i mogą różnić się pomiędzy organizacjami i projektami.' }))),
    ...sections);
}

async function rbacAssignmentsPanel(roles, users) {
  if (!allowed('users.read')) {
    return node('section', { class: 'panel' },
      node('h2', { text: 'Przypisania' }),
      node('p', { class: 'muted', text: 'Do przeglądania przypisań wymagane jest uprawnienie users.read.' }));
  }

  const selection = node('section', { class: 'panel rbac-assignment-editor' },
    node('div', { class: 'rbac-section-heading' },
      node('div', {},
        node('span', { class: 'rbac-kicker', text: 'Przypisanie' }),
        node('h2', { text: 'Wybierz użytkownika' }),
        node('p', { class: 'muted', text: 'Role globalne są niezależne od członkostwa w tenantach i projektach.' }))),
    node('div', { class: 'rbac-empty-selection', text: 'Wybierz konto z tabeli poniżej, aby zobaczyć i zmienić jego role.' }));

  async function renderUser(user) {
    rbacSelectedUserId = Number(user.id);
    selection.replaceChildren(
      node('div', { class: 'rbac-section-heading' },
        node('div', {},
          node('span', { class: 'rbac-kicker', text: 'Dostęp użytkownika' }),
          node('h2', { text: user.username }),
          node('p', { class: 'muted', text: (user.email || 'Brak adresu e-mail') + ' · role globalne + wiele organizacji i projektów' })),
        button('Zmień użytkownika', () => {
          rbacSelectedUserId = null;
          rbacAssignmentsPanel(roles, users).then(panel => {
            const current = dom.content.querySelector('.rbac-tab-body');
            if (current) current.replaceWith(panel);
          }).catch(error => toast(error.message, 'error'));
        }))
    );

    const assigned = await api(`/users/${user.id}/roles`);
    const assignedIds = new Set((assigned.items || []).map(role => Number(role.id)));
    const roleOptions = node('div', { class: 'rbac-role-options' },
      ...roles.map(role => node('label', { class: `rbac-role-option ${assignedIds.has(Number(role.id)) ? 'selected' : ''}` },
        node('input', { type: 'checkbox', name: 'role_id', value: role.id, checked: assignedIds.has(Number(role.id)), disabled: !allowed('roles.assign') }),
        node('span', { class: 'rbac-role-option-copy' },
          node('strong', { text: role.name }),
          node('small', { class: 'muted', text: `${role.permissions.length} uprawnień · ${roleModuleNames(role).slice(0, 3).join(', ') || 'brak modułów'}` })))));
    roleOptions.querySelectorAll('input').forEach(input => input.addEventListener('change', () => {
      input.closest('.rbac-role-option')?.classList.toggle('selected', input.checked);
    }));

    const form = node('form', {
      class: 'rbac-assignment-form',
      onSubmit: async event => {
        event.preventDefault();
        if (!allowed('roles.assign')) return;
        const role_ids = [...form.querySelectorAll('[name="role_id"]:checked')].map(input => Number(input.value));
        const submit = form.querySelector('[type="submit"]');
        submit.disabled = true;
        try {
          await api(`/users/${user.id}/roles`, { method: 'PUT', body: { role_ids } });
          toast('Role globalne użytkownika zapisane.');
          await renderUser(user);
        } catch (error) {
          toast(error.message, 'error');
        } finally {
          submit.disabled = false;
        }
      },
    },
    node('div', { class: 'rbac-assignment-toolbar' },
      node('div', {},
        node('strong', { text: 'Role globalne użytkownika' }),
        node('span', { class: 'muted', text: allowed('roles.assign') ? 'Te role obowiązują globalnie, niezależnie od członkostw zakresowych.' : 'Tryb tylko do odczytu.' })),
      allowed('roles.assign') ? node('button', { type: 'submit', class: 'button primary' }, 'Zapisz role globalne') : null),
    roleOptions);
    selection.append(form);
    const scopeMemberships = await rbacScopeMembershipEditor(user, () => renderUser(user));
    selection.append(scopeMemberships);
  }

  const rows = users || [];
  const userTable = table([
    { label: 'Użytkownik', value: user => node('div', {}, node('strong', { text: user.username }), node('div', { class: 'muted', text: user.email })) },
    { label: 'Źródło', value: user => authSourceBadge(user) },
    { label: 'Typ', value: user => user.is_service_account ? 'Konto serwisowe' : 'Użytkownik' },
    { label: 'Status', value: user => badge(user.is_locked ? 'Zablokowany' : user.is_active ? 'Aktywny' : 'Wyłączony', user.is_locked || !user.is_active ? 'danger' : 'ok') },
  ], rows, user => [button(rbacSelectedUserId === Number(user.id) ? 'Wybrany' : 'Zarządzaj dostępem', () => renderUser(user).catch(error => toast(error.message, 'error')), rbacSelectedUserId === Number(user.id) ? 'primary' : 'ghost')]);

  const wrapper = node('div', { class: 'rbac-tab-body rbac-stack' },
    selection,
    node('section', { class: 'rbac-section' },
      node('div', { class: 'rbac-section-heading' },
        node('div', {},
          node('span', { class: 'rbac-kicker', text: 'Konta' }),
          node('h2', { text: 'Użytkownicy i konta serwisowe' }),
          node('p', { class: 'muted', text: 'Wyszukaj konto i otwórz jego przypisania. Tabela zachowuje filtry, sortowanie i gęstość widoku.' }))),
      userTable));

  const selected = rows.find(user => Number(user.id) === Number(rbacSelectedUserId));
  if (selected) await renderUser(selected);
  return wrapper;
}

function rbacRolesPanel(roles) {
  return node('div', { class: 'rbac-tab-body rbac-stack' },
    node('div', { class: 'rbac-section-heading' },
      node('div', {},
        node('span', { class: 'rbac-kicker', text: 'Definicje' }),
        node('h2', { text: 'Role' }),
        node('p', { class: 'muted', text: 'Rola odpowiada wyłącznie na pytanie „co wolno”. Zakres organizacji/projektu i polityki są zarządzane oddzielnie.' })),
      allowed('roles.create') ? button('Nowa rola', () => navigate('/access/roles/new'), 'primary') : null),
    roles.length
      ? node('div', { class: 'rbac-role-grid' }, ...roles.map(roleCard))
      : node('div', { class: 'panel empty', text: 'Brak ról. Utwórz pierwszą rolę RBAC.' }));
}

async function rbacAccessAnalysisPanel(roles, users, permissions) {
  if (!allowed('users.read')) {
    return node('section', { class: 'panel rbac-tab-body' },
      node('h2', { text: 'Analiza dostępu' }),
      node('p', { class: 'muted', text: 'Do analizy kont wymagane jest uprawnienie users.read.' }));
  }

  const userField = selectField('Użytkownik', 'user_id',
    (users || []).map(user => ({ value: String(user.id), label: `${user.username}${user.email ? ' — ' + user.email : ''}` })),
    rbacSelectedUserId ? String(rbacSelectedUserId) : '',
    { required: true, placeholder: 'Wybierz użytkownika' });
  const permissionField = selectField('Uprawnienie', 'permission',
    permissions.map(permission => ({ value: permission, label: `${permissionLabel(permission)} — ${permission}` })),
    '',
    { required: true, placeholder: 'Wybierz operację' });
  const result = node('div', { class: 'rbac-analysis-result' },
    node('div', { class: 'rbac-empty-selection', text: 'Wybierz użytkownika i uprawnienie, aby sprawdzić źródło dostępu.' }));

  const form = node('form', {
    class: 'panel rbac-analysis-form',
    onSubmit: async event => {
      event.preventDefault();
      const userId = Number(form.elements.user_id.value);
      const permission = form.elements.permission.value;
      if (!userId || !permission) return;
      const user = (users || []).find(item => Number(item.id) === userId);
      const assigned = await api(`/users/${userId}/roles`);
      const assignedRoles = assigned.items || [];
      const granting = assignedRoles.filter(role => (role.permissions || []).includes(permission));
      rbacSelectedUserId = userId;
      result.replaceChildren(
        node('section', { class: `panel rbac-decision ${granting.length ? 'allow' : 'deny'}` },
          node('div', { class: 'rbac-decision-head' },
            node('div', {},
              node('span', { class: 'rbac-kicker', text: 'Wynik globalnego RBAC' }),
              node('h2', { text: granting.length ? 'ALLOW' : 'DENY' }),
              node('p', { class: 'muted', text: `${user?.username || 'Użytkownik'} · ${permission}` })),
            badge(granting.length ? 'Dostęp nadany' : 'Brak uprawnienia', granting.length ? 'ok' : 'danger')),
          granting.length
            ? node('div', { class: 'rbac-reason-list' },
                node('strong', { text: 'Źródło dostępu' }),
                ...granting.map(role => node('div', { class: 'rbac-reason' },
                  node('span', { text: role.name }),
                  node('code', { class: 'mono', text: permission }))))
            : node('p', { text: 'Żadna z globalnie przypisanych ról nie zawiera tego uprawnienia.' }),
          node('p', { class: 'muted rbac-analysis-note', text: 'To wynik globalnego RBAC. Ostateczna decyzja dla konkretnego zasobu może zostać dodatkowo ograniczona przez tenant/projekt, scope zasobu oraz Policy Engine.' })),
        node('section', { class: 'panel' },
          node('div', { class: 'rbac-section-heading' },
            node('div', {},
              node('span', { class: 'rbac-kicker', text: 'Przypisane role' }),
              node('h3', { text: assignedRoles.length ? assignedRoles.map(role => role.name).join(', ') : 'Brak ról' }))),
          permissionSummary([...new Set(assignedRoles.flatMap(role => role.permissions || []))].sort())));
    },
  },
  node('div', { class: 'form-grid' }, userField, permissionField),
  node('div', { class: 'rbac-analysis-actions' }, node('button', { type: 'submit', class: 'button primary' }, 'Sprawdź dostęp')));

  return node('div', { class: 'rbac-tab-body rbac-stack' },
    form,
    result);
}

function rbacGovernancePanel() {
  const cards = [
    {
      title: 'Organizacje / tenanty',
      text: 'Członkostwo i delegowane role w obrębie organizacji.',
      action: () => navigate('tenants'),
      enabled: true,
    },
    {
      title: 'Projekty',
      text: 'Dostęp projektowy i zakres operacji przypisany do konkretnego projektu.',
      action: () => navigate('projects'),
      enabled: true,
    },
    {
      title: 'Policy Engine',
      text: 'Warunkowe ALLOW/DENY, ochrona PROD, approvals i reguły zależne od scope.',
      action: () => navigate('policies'),
      enabled: allowed('policies.read'),
    },
  ];
  return node('div', { class: 'rbac-tab-body rbac-stack' },
    node('section', { class: 'panel rbac-explainer' },
      node('div', {},
        node('span', { class: 'rbac-kicker', text: 'Governance' }),
        node('h2', { text: 'Zakres nie jest częścią definicji roli' }),
        node('p', { class: 'muted', text: 'Role pozostają wielokrotnego użytku. Organizacja, projekt, APMID, ENV i warunki Policy Engine powinny być nakładane przy przypisaniu lub wykonywaniu operacji.' }))),
    node('div', { class: 'rbac-governance-grid' },
      ...cards.map(card => node('article', { class: 'panel rbac-governance-card' },
        node('h3', { text: card.title }),
        node('p', { class: 'muted', text: card.text }),
        button('Otwórz', card.action, 'primary', !card.enabled)))),
    node('section', { class: 'panel rbac-roadmap-note' },
      node('strong', { text: 'Docelowy model scope' }),
      node('p', { class: 'muted', text: 'Organization → Project → APMID → Environment → Resource. Ten widok jest przygotowany pod zunifikowany Scope Builder, ale obecne źródła prawdy pozostają w modułach Tenants, Projects i Policy Engine.' })));
}

async function usersView() {
  const users = (await api('/users?limit=200')).items;
  const actions = [];
  if (allowed('users.create')) actions.push(button('Dodaj użytkownika', () => navigate('/access/users/new'), 'primary'));
  dom.content.replaceChildren(heading('Konta ludzi i konta serwisowe. Uprawnienia wynikają wyłącznie z przypisanych ról.', actions),
    table([
      { label: 'Użytkownik', value: user => node('div', {}, node('strong', { text: user.username }), node('div', { class: 'muted', text: user.email })) },
      { label: 'Typ', value: user => badge(user.is_service_account ? 'serwisowe' : 'osobowe', user.is_service_account ? 'info' : '') },
      { label: 'Logowanie', value: user => authSourceBadge(user) },
      { label: 'Status', value: user => { const status = user.is_locked ? 'locked' : user.is_active ? 'active' : 'inactive'; return badge(statusLabel(status), statusKind(status)); } },
      { label: 'Ostatnie logowanie', value: user => formatDate(user.last_login_at) },
    ], users, user => userActions(user)));
}

function userActions(user) {
  const actions = [];
  if (allowed('rbac.assignments.read')) {
    actions.push(button('Dostępy', () => navigate('/access/users/' + encodeURIComponent(user.id) + '/iam')));
  } else if (allowed('roles.read')) {
    actions.push(button('Role globalne', () => {
      rbacSelectedUserId = Number(user.id);
      rbacTab = 'assignments';
      navigate('roles');
    }));
  }
  if (allowed('users.update')) {
    actions.push(button('Edytuj', () => navigate('/access/users/edit/' + encodeURIComponent(user.id) + '/' + encodeURIComponent(user.username || 'user'))));
    if (user.is_locked) actions.push(button('Odblokuj', () => userCommand(user, 'unlock')));
    actions.push(button(user.is_active ? 'Wyłącz' : 'Włącz', () => userCommand(user, user.is_active ? 'disable' : 'enable')));
    if (!user.is_service_account && user.is_active && user.auth_source === 'local') actions.push(button('Reset hasła', () => resetUserPassword(user)));
  }
  if (allowed('users.delete')) actions.push(button('Usuń', () => confirmAction('Usuń użytkownika', `Konto ${user.username} zostanie zanonimizowane i utraci dostęp.`, async () => { await api(`/users/${user.id}`, { method: 'DELETE' }); toast('Użytkownik usunięty.'); navigate('users'); }), 'danger'));
  return actions;
}

function createUser() {
  const passwordField = field('Hasło początkowe (opcjonalnie)', 'password', {
    type: 'password',
    minlength: 12,
    autocomplete: 'new-password',
    wide: true,
    help: 'Pozostaw puste, aby utworzyć konto bez hasła. Hasło można ustawić później przez reset.',
  });
  const serviceAccountField = checkboxField('Konto serwisowe (bez logowania hasłem)', 'is_service_account');
  const passwordInput = passwordField.querySelector('[name="password"]');
  const serviceAccountInput = serviceAccountField.querySelector('[name="is_service_account"]');
  serviceAccountInput.addEventListener('change', () => {
    passwordInput.disabled = serviceAccountInput.checked;
    if (serviceAccountInput.checked) passwordInput.value = '';
  });

  const fields = node('div', { class: 'form-grid' },
    field('Login', 'username', { required: true, maxlength: 63 }), field('E-mail', 'email', { type: 'email', required: true }),
    field('Imię', 'first_name', { maxlength: 100 }), field('Nazwisko', 'last_name', { maxlength: 100 }),
    passwordField, serviceAccountField);
  openModal({ title: 'Nowy użytkownik', eyebrow: 'Tożsamość', body: fields, submitLabel: 'Utwórz', onSubmit: async data => {
    const payload = {
      username: data.get('username'),
      email: data.get('email'),
      first_name: data.get('first_name'),
      last_name: data.get('last_name'),
      is_service_account: data.has('is_service_account'),
    };
    const password = data.get('password');
    if (password) payload.password = password;
    await api('/users', { method: 'POST', body: payload });
    toast(password || payload.is_service_account
      ? 'Użytkownik utworzony.'
      : 'Użytkownik utworzony bez hasła. Ustaw je później przez „Reset hasła”.');
    navigate('users');
  }});
}

function editUser(user) {
  const fields = node('div', { class: 'form-grid' }, field('E-mail', 'email', { type: 'email', required: true, value: user.email, wide: true }), field('Imię', 'first_name', { value: user.first_name }), field('Nazwisko', 'last_name', { value: user.last_name }));
  openModal({ title: `Edytuj ${user.username}`, eyebrow: 'Użytkownik', body: fields, onSubmit: async data => {
    await api(`/users/${user.id}`, { method: 'PUT', body: { email: data.get('email'), first_name: data.get('first_name'), last_name: data.get('last_name') } });
    toast('Dane użytkownika zapisane.'); navigate('users');
  }});
}

async function userCommand(user, command) {
  try { await api(`/users/${user.id}/${command}`, { method: 'POST' }); toast('Status konta zmieniony.'); navigate('users'); }
  catch (error) { toast(error.message, 'error'); }
}

async function assignUserRoles(user) {
  rbacSelectedUserId = Number(user.id);
  rbacTab = 'assignments';
  await navigate('roles');
}

function resetUserPassword(user) {
  confirmAction('Wydaj token resetu', `Aktywne sesje użytkownika ${user.username} zostaną unieważnione.`, async () => {
    const result = await api(`/users/${user.id}/reset-password`, { method: 'POST' });
    showSecret('Token resetu hasła', result.reset_token, 'Token jest ważny przez 15 minut. Przekaż go użytkownikowi bezpiecznym kanałem.');
    return false;
  });
}

async function rolesView() {
  const [roleResult, permissionResult, userResult] = await Promise.all([
    api('/rbac/roles?limit=200'),
    api('/rbac/permissions'),
    allowed('users.read') ? api('/users?limit=200') : Promise.resolve({ items: [] }),
  ]);
  const roles = roleResult.items || [];
  const permissions = (permissionResult.items || []).map(item => typeof item === 'string' ? item : item.name);
  const users = userResult.items || [];

  const actions = [];
  if (allowed('roles.create')) actions.push(button('Nowa rola', () => navigate('/access/roles/new'), 'primary'));
  if (allowed('rbac.assignments.manage') || allowed('roles.assign')) {
    actions.push(button('Nadaj dostęp', () => navigate('iam-access')));
  }
  const stats = node('div', { class: 'rbac-stats' },
    rbacStat('Role', roles.length, 'definicje globalne'),
    rbacStat('Użytkownicy', allowed('users.read') ? users.length : '—', allowed('users.read') ? 'konta dostępne do przypisań' : 'brak users.read'),
    rbacStat('Permissiony', permissions.length, 'katalog uprawnień'),
    rbacStat('Twój dostęp', state.identity?.permissions?.length || 0, 'efektywne globalnie'));

  let body;
  if (rbacTab === 'assignments') body = await rbacAssignmentsPanel(roles, users);
  else if (rbacTab === 'roles') body = rbacRolesPanel(roles);
  else if (rbacTab === 'analysis') body = await rbacAccessAnalysisPanel(roles, users, permissions);
  else if (rbacTab === 'governance') body = rbacGovernancePanel();
  else body = rbacOverviewPanel(roles, users, permissions);

  dom.content.replaceChildren(
    heading('Jedno miejsce do definiowania ról, nadawania dostępu i sprawdzania z czego wynika efektywne uprawnienie.', actions),
    rbacTabs(),
    stats,
    body);
}

async function roleForm(role = null) {
  try {
    const permissionResult = await api('/rbac/permissions');
    const permissions = (permissionResult.items || []).map(item => typeof item === 'string' ? item : item.name);
    const picker = permissionPicker(permissions, role?.permissions || [], 'permission');
    picker.classList.add('rbac-permission-picker');

    const searchField = field('Filtr uprawnień', 'permission_filter', {
      type: 'search',
      placeholder: 'np. vm, blueprint, delete, snapshot',
      wide: true,
      help: 'Filtruje nazwy techniczne i czytelne etykiety bez zmiany zaznaczeń.',
    });
    const selectedCounter = node('strong', { class: 'rbac-selected-counter' });
    const readOnly = button('Tylko odczyt', () => {
      picker.querySelectorAll('[name="permission"]').forEach(input => {
        const permission = String(input.value);
        input.checked = permission.endsWith('.read') || permission.includes('.read.');
      });
      updateEditor();
    });
    const clear = button('Wyczyść', () => {
      picker.querySelectorAll('[name="permission"]').forEach(input => { input.checked = false; });
      updateEditor();
    });

    const toolbar = node('div', { class: 'rbac-permission-toolbar' },
      node('div', { class: 'rbac-permission-toolbar-copy' },
        selectedCounter,
        node('span', { class: 'muted', text: ' zaznaczonych uprawnień' })),
      node('div', { class: 'action-group' }, readOnly, clear));

    function updateEditor() {
      const query = searchable(searchField.querySelector('input').value.trim());
      let selected = 0;
      picker.querySelectorAll('.permission-group').forEach(group => {
        let visible = 0;
        group.querySelectorAll('.permission-grid label').forEach(label => {
          const input = label.querySelector('input');
          if (input?.checked) selected += 1;
          const show = !query || searchable(label.textContent).includes(query);
          label.hidden = !show;
          if (show) visible += 1;
        });
        group.hidden = visible === 0;
        if (query && visible) group.open = true;
      });
      selectedCounter.textContent = String(selected);
    }

    searchField.querySelector('input').addEventListener('input', updateEditor);
    picker.querySelectorAll('[name="permission"]').forEach(input => input.addEventListener('change', updateEditor));
    updateEditor();

    const scopeChoices = [
      'GLOBAL', 'ORGANIZATION', 'PROJECT', 'APMID', 'ENVIRONMENT',
      'RESOURCE_POOL', 'BLUEPRINT', 'DEPLOYMENT', 'RESOURCE', 'MACHINE',
    ].map(value => ({ value, label: value }));
    const fields = node('div', { class: 'form-grid rbac-role-form' },
      formSection('Rola', 'Nazwa opisuje odpowiedzialność. Role systemowe są chronione przed usunięciem.',
        field('Nazwa roli', 'name', { required: true, value: role?.name || '', wide: true, placeholder: 'np. VM Operator' }),
        field('Opis', 'description', { tag: 'textarea', value: role?.description || '', maxlength: 4000, wide: true })),
      formSection('Dozwolone scope', 'Ogranicza typy scope, na których tę rolę można delegować.',
        multiCheckboxField('Scope types', 'scope_type', scopeChoices, role?.scope_types || ['GLOBAL'])),
      formSection('Dziedziczenie', 'Kontroluje propagację assignmentu do scope potomnych.',
        checkboxField('Rola może być dziedziczona do scope potomnych', 'inheritance_enabled', role?.inheritance_enabled ?? true),
        checkboxField('Rola aktywna', 'enabled', role?.enabled ?? true)),
      formSection('Permission patterns', 'Opcjonalne kontrolowane wildcardy. Dozwolony jest wyłącznie końcowy namespace wildcard, np. machines.*.',
        field('Wildcard permissions', 'permission_patterns', {
          value: (role?.permission_patterns || []).join('\n'),
          tag: 'textarea',
          wide: true,
          placeholder: 'machines.*',
          help: 'Jeden pattern w linii.',
        })),
      formSection('Uprawnienia', 'Wybierz konkretne permissions. Scope i ABAC są nakładane przez RoleAssignment/Policy Engine.',
        searchField,
        toolbar,
        picker));

    openModal({
      title: role ? `Edytuj rolę: ${role.name}` : 'Nowa rola',
      eyebrow: 'Role i dostęp',
      body: fields,
      submitLabel: role ? 'Zapisz rolę' : 'Utwórz rolę',
      wide: true,
      onSubmit: async (_data, form) => {
        const scope_types = [...form.querySelectorAll('[name="scope_type"]:checked')].map(input => input.value);
        if (!scope_types.length) throw new Error('Wybierz co najmniej jeden typ scope.');
        const payload = {
          name: form.elements.name.value,
          description: form.elements.description.value,
          permissions: [...form.querySelectorAll('[name="permission"]:checked')].map(input => input.value),
          permission_patterns: String(form.elements.permission_patterns.value || '')
            .split(/\r?\n|,/)
            .map(value => value.trim())
            .filter(Boolean),
          scope_types,
          inheritance_enabled: Boolean(form.elements.inheritance_enabled?.checked),
          enabled: Boolean(form.elements.enabled?.checked),
        };
        await api(role ? `/rbac/roles/${role.id}` : '/rbac/roles', { method: role ? 'PATCH' : 'POST', body: payload });
        toast('Rola zapisana.');
        rbacTab = 'roles';
        navigate('roles');
      },
    });
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function tokensView() {
  const [tokenResult, userResult] = await Promise.all([
    api('/tokens?limit=200'),
    allowed('users.read') ? api('/users?limit=200') : Promise.resolve({ items: [] }),
  ]);
  const tokens = tokenResult.items;
  const users = new Map(userResult.items.map(user => [Number(user.id), user.username]));
  const actions = [
    button('Dokumentacja OpenAPI', () => {
      const docs = window.open('/docs', '_blank', 'noopener,noreferrer');
      if (docs) docs.opener = null;
    }, 'ghost'),
  ];
  if (allowed('tokens.create')) {
    actions.push(button('Utwórz token', () => navigate('/access/tokens/new'), 'primary'));
  }
  dom.content.replaceChildren(heading('Tokeny API mają jawny, ograniczony zakres. Sekret jest dostępny wyłącznie po utworzeniu.', actions),
    table([
      { label: 'Nazwa', value: token => node('div', {}, node('strong', { text: token.name }), node('div', { class: 'mono muted', text: token.token_prefix })) },
      { label: 'Właściciel', value: token => users.get(Number(token.user_id)) || `Użytkownik #${token.user_id}` },
      { label: 'Zakres', value: token => badge(`${token.scopes.length} uprawnień`, token.scopes.length ? 'info' : '') },
      { label: 'Status', value: token => { const status = token.revoked_at ? 'revoked' : token.expires_at && new Date(token.expires_at) < new Date() ? 'expired' : 'active'; return badge(statusLabel(status), status === 'active' ? 'ok' : 'danger'); } },
      { label: 'Ostatnio użyty', value: token => formatDate(token.last_used_at) },
    ], tokens, token => {
      const result = [button('Zakres', () => navigate('/access/tokens/' + encodeURIComponent(token.id) + '/scope'))];
      if (allowed('tokens.revoke') && !token.revoked_at) result.push(button('Unieważnij', () => confirmAction('Unieważnij token', `Token ${token.name} natychmiast przestanie działać.`, async () => {
        await api(`/tokens/${token.id}/revoke`, { method: 'POST' });
        toast('Token unieważniony.');
        navigate('tokens');
      }), 'danger'));
      return result;
    }));
}

async function createToken() {
  try {
    const [permissionResult, userResult] = await Promise.all([
      allowed('roles.read') ? api('/permissions') : Promise.resolve({ items: state.identity.permissions }),
      allowed('users.read') ? api('/users?limit=200') : Promise.resolve({ items: [] }),
    ]);
    const permissions = permissionResult.items.filter(permission => allowed(permission));
    const picker = permissionPicker(permissions, [], 'scope');

    const ownerChoices = [{ value: '', label: 'Moje konto' }].concat(userResult.items.map(user => ({
      value: user.id,
      label: `${user.username}${user.email ? ' · ' + user.email : ''}`,
    })));
    const fields = node('div', { class: 'form-grid' },
      field('Nazwa tokenu', 'name', { required: true, placeholder: 'np. Terraform CI' }),
      selectField('Właściciel', 'user_id', ownerChoices, '', { required: false }),
      field('Wygasa (opcjonalnie)', 'expires_at', { type: 'datetime-local', wide: true }),
      formSection('Zakres uprawnień', 'Zaznacz tylko uprawnienia potrzebne tej integracji.', picker));

    openModal({ title: 'Nowy token API', eyebrow: 'Dostęp programowy', body: fields, submitLabel: 'Utwórz token', wide: true, onSubmit: async (_data, form) => {
      const scopes = [...form.querySelectorAll('[name="scope"]:checked')].map(input => input.value);
      if (!scopes.length) throw new Error('Wybierz co najmniej jedno uprawnienie dla tokenu.');
      const payload = { name: form.elements.name.value, scopes };
      if (form.elements.user_id?.value) payload.user_id = Number(form.elements.user_id.value);
      if (form.elements.expires_at.value) payload.expires_at = new Date(form.elements.expires_at.value).toISOString();
      const result = await api('/tokens', { method: 'POST', body: payload });
      navigate('tokens');
      showSecret('Nowy token API', result.token);
      return false;
    }});
  } catch (error) { toast(error.message, 'error'); }
}

async function accountView() {
  const user = state.identity.user;
  const roles = state.identity.roles.map(role => role.name).join(', ') || 'Brak przypisanych ról';
  const initials = String(user.username || 'U').slice(0, 2).toUpperCase();

  const accountValue = (label, value, options = {}) => node('div', { class: 'account-meta-item' },
    node('span', { text: label }),
    node(options.mono ? 'code' : 'strong', { class: options.mono ? 'mono' : '', text: value || '—' }));

  const details = node('section', { class: 'panel account-card account-identity-card' },
    node('div', { class: 'account-profile-head' },
      node('div', { class: 'account-avatar', 'aria-hidden': 'true', text: initials }),
      node('div', { class: 'account-profile-copy' },
        node('span', { class: 'account-kicker', text: 'Zalogowane konto' }),
        node('h2', { text: user.username }),
        node('p', { class: 'muted', text: user.email })),
      badge(state.identity.token_type === 'session' ? 'Sesja' : state.identity.token_type, 'info')),
    node('div', { class: 'account-meta-grid' },
      accountValue('Login', user.username, { mono: true }),
      accountValue('E-mail', user.email),
      accountValue('Role', roles),
      accountValue('Ostatnie logowanie', formatDate(user.last_login_at))));

  const externalAuth = user.auth_source !== 'local';
  const passwordMessage = externalAuth
    ? (user.auth_source === 'oidc'
      ? 'Uwierzytelnianie i hasło są zarządzane przez dostawcę SSO / OIDC.'
      : 'Uwierzytelnianie i hasło są zarządzane przez katalog LDAP.')
    : user.must_change_password
      ? 'Konto używa początkowego hasła administratora. Ustaw własne hasło, aby odblokować pełny dostęp.'
      : 'Zmiana hasła unieważni aktywne sesje i tokeny resetu. Po zapisaniu zalogujesz się ponownie.';

  const security = node('section', { class: 'panel account-card account-security-card' },
    node('div', { class: 'account-card-heading' },
      node('div', { class: 'account-security-icon', 'aria-hidden': 'true', text: '✓' }),
      node('div', {},
        node('span', { class: 'account-kicker', text: 'Ochrona dostępu' }),
        node('h2', { text: 'Bezpieczeństwo konta' })),
      externalAuth ? authSourceBadge(user)
        : user.must_change_password ? badge('Wymagana zmiana', 'warning') : badge('Hasło ustawione', 'ok')),
    node('p', { class: !externalAuth && user.must_change_password ? 'form-error account-security-copy' : 'muted account-security-copy', text: passwordMessage }),
    externalAuth ? null : node('div', { class: 'account-security-actions' },
      button(user.must_change_password ? 'Ustaw nowe hasło' : 'Zmień hasło', () => navigate('/account/password' + (user.must_change_password ? '?required=1' : '')), 'primary')));

  const permissionPanel = node('section', { class: 'panel account-permissions-panel' },
    node('div', { class: 'account-permissions-header' },
      node('div', {},
        node('span', { class: 'account-kicker', text: 'RBAC' }),
        node('h2', { text: 'Skuteczne uprawnienia' }),
        node('p', { class: 'muted', text: 'Uprawnienia wynikające z przypisanych ról i aktywnej sesji.' })),
      node('div', { class: 'account-permission-count' },
        node('strong', { text: String(state.identity.permissions.length) }),
        node('span', { text: 'uprawnień' }))),
    node('div', { class: 'account-permissions-grid' }, permissionSummary(state.identity.permissions)));

  dom.content.replaceChildren(
    heading('Twoje konto, bezpieczeństwo i skuteczne uprawnienia.'),
    node('div', { class: 'account-overview-grid' }, details, security),
    permissionPanel);
}

function changePassword(required = false) {
  if (state.identity?.user?.auth_source !== 'local') {
    toast('Hasło tego konta jest zarządzane przez zewnętrznego dostawcę tożsamości.', 'error');
    navigate('account');
    return;
  }
  const currentPassword = field('Obecne hasło', 'current_password', {
    type: 'password',
    autocomplete: 'current-password',
    required: true,
    wide: true,
    help: required
      ? 'Wpisz hasło, którym zalogowałeś się do Cloudportal.'
      : 'Wpisz aktualne hasło do konta.',
  });
  const fields = node('div', { class: 'form-grid' },
    currentPassword,
    field('Nowe hasło', 'password', { type: 'password', autocomplete: 'new-password', required: true, minlength: 12 }),
    field('Powtórz nowe hasło', 'confirm', { type: 'password', autocomplete: 'new-password', required: true, minlength: 12 }));
  openModal({
    title: required ? 'Zmień hasło początkowe' : 'Zmień hasło',
    eyebrow: 'Moje konto',
    body: fields,
    submitLabel: 'Zmień hasło',
    onSubmit: async (data, form) => {
      if (data.get('password') !== data.get('confirm')) throw new Error('Nowe hasła nie są identyczne.');
      try {
        await api('/auth/change-password', {
          method: 'POST',
          body: {
            current_password: data.get('current_password'),
            password: data.get('password'),
          },
        });
      } catch (error) {
        const message = String(error?.message || '');
        if (error?.status === 403 && ['Current password required', 'Current password is incorrect'].includes(message)) {
          const input = form.elements.current_password;
          if (input) {
            input.value = '';
            input.focus();
          }
          throw new Error('Obecne hasło jest nieprawidłowe.');
        }
        if (error?.status === 403 && message === 'Browser session required') {
          throw new Error('Zmiana hasła wymaga ponownego zalogowania.');
        }
        throw error;
      }
      showLogin('Hasło zmienione. Zaloguj się ponownie.', 'success');
    },
  });
}

registerRoutedForm({
  id: 'tokens-scope',
  pattern: /^\/access\/tokens\/(?<id>\d+)\/scope$/,
  parent: 'tokens',
  permission: 'tokens.read',
  label: 'Tokeny API',
}, async match => {
  const tokens = (await api('/tokens?limit=200')).items;
  const token = tokens.find(item => Number(item.id) === Number(match.params.id));
  if (!token) throw new Error('Nie znaleziono tokenu.');
  showPermissionSummary('Zakres: ' + token.name, token.scopes);
});
registerRoutedForm({
  id: 'account-password',
  pattern: /^\/account\/password$/,
  parent: 'account',
  permission: null,
  label: 'Moje konto',
}, match => changePassword(match.searchParams.get('required') === '1' || Boolean(state.identity?.user?.must_change_password)));
registerRoutedForm({
  id: 'users-roles',
  pattern: /^\/access\/users\/(?<id>\d+)\/roles$/,
  parent: 'users',
  permission: 'roles.assign',
  label: 'Użytkownicy',
}, async match => {
  const users = (await api('/users?limit=200')).items;
  const user = users.find(item => Number(item.id) === Number(match.params.id));
  if (!user) throw new Error('Nie znaleziono użytkownika.');
  await assignUserRoles(user);
});
registerRoutedForm({
  id: 'users-create',
  pattern: /^\/access\/users\/new$/,
  parent: 'users',
  permission: 'users.create',
  label: 'Użytkownicy',
}, () => createUser());
registerRoutedForm({
  id: 'users-edit',
  pattern: /^\/access\/users\/edit\/(?<id>\d+)(?:\/[^/]+)?$/,
  parent: 'users',
  permission: 'users.update',
  label: 'Użytkownicy',
}, async match => {
  const users = (await api('/users?limit=200')).items;
  const user = users.find(item => Number(item.id) === Number(match.params.id));
  if (!user) throw new Error('Nie znaleziono użytkownika.');
  editUser(user);
});
registerRoutedForm({
  id: 'roles-create',
  pattern: /^\/access\/roles\/new$/,
  parent: 'roles',
  permission: 'roles.create',
  label: 'Role i RBAC',
}, () => roleForm());
registerRoutedForm({
  id: 'roles-edit',
  pattern: /^\/access\/roles\/edit\/(?<id>\d+)(?:\/[^/]+)?$/,
  parent: 'roles',
  permission: 'roles.update',
  label: 'Role i RBAC',
}, async match => {
  const roles = (await api('/rbac/roles?limit=200')).items;
  const role = roles.find(item => Number(item.id) === Number(match.params.id));
  if (!role) throw new Error('Nie znaleziono roli.');
  await roleForm(role);
});
registerRoutedForm({
  id: 'tokens-create',
  pattern: /^\/access\/tokens\/new$/,
  parent: 'tokens',
  permission: 'tokens.create',
  label: 'Tokeny API',
}, () => createToken());

registerCommand('users.create', () => navigate('/access/users/new'));
registerCommand('roles.create', () => navigate('/access/roles/new'));
registerCommand('tokens.create', () => navigate('/access/tokens/new'));
registerView({ id: 'users', label: 'Użytkownicy', icon: 'U', permission: 'users.read', order: 10 }, usersView);
registerView({ id: 'roles', label: 'Role i dostęp', icon: 'R', permission: 'roles.read', order: 20 }, rolesView);
registerView({ id: 'tokens', label: 'Tokeny API', icon: 'T', permission: 'tokens.read', order: 30 }, tokensView);
registerView({ id: 'account', label: 'Moje konto', icon: 'M', order: 170 }, accountView);
document.addEventListener('cloudportal:app-hidden', () => { rbacTab = 'overview'; rbacSelectedUserId = null; });
})();
