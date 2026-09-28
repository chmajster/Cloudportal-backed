'use strict';

(() => {
  const PAGE_SIZE = 200;

  function action(label, operation, kind = 'ghost', disabled = false) {
    return button(
      label,
      () => Promise.resolve().then(operation).catch(error => toast(error.message, 'error')),
      kind,
      disabled
    );
  }

  async function pagedItems(path) {
    const items = [];
    let offset = 0;
    while (true) {
      const separator = path.includes('?') ? '&' : '?';
      const page = await api(path + separator + 'limit=' + PAGE_SIZE + '&offset=' + offset);
      const rows = page.items || [];
      items.push(...rows);
      offset += rows.length;
      if (!rows.length || offset >= Number(page.total || 0)) return items;
    }
  }

  async function membershipForUser(tenantId, userId) {
    let offset = 0;
    while (true) {
      const page = await api(
        '/tenants/' + encodeURIComponent(tenantId)
        + '/members?limit=' + PAGE_SIZE + '&offset=' + offset
      );
      const rows = page.items || [];
      const member = rows.find(row => Number(row.user_id) === Number(userId));
      if (member) return member;
      offset += rows.length;
      if (!rows.length || offset >= Number(page.total || 0)) return null;
    }
  }

  function roleOption(role, checked, disabled = false) {
    const permissions = (role.permissions || []).slice().sort();
    return node('label', {
      class: 'rbac-role-option' + (checked ? ' selected' : ''),
      'data-role-id': String(role.id),
    },
      node('input', {
        type: 'checkbox',
        name: 'tenant_role_id',
        value: String(role.id),
        checked,
        disabled,
      }),
      node('span', { class: 'rbac-role-option-copy' },
        node('strong', { text: role.name }),
        node('small', { class: 'muted', text: permissions.length
          ? permissions.join(', ')
          : 'Brak delegowalnych uprawnień.' })));
  }

  function fallbackRoleOption(roleId, disabled = false) {
    return node('label', {
      class: 'rbac-role-option selected',
      'data-role-id': String(roleId),
    },
      node('input', {
        type: 'checkbox',
        name: 'tenant_role_id',
        value: String(roleId),
        checked: true,
        disabled,
      }),
      node('span', { class: 'rbac-role-option-copy' },
        node('strong', { text: 'Obecna rola #' + roleId }),
        node('small', { class: 'muted', text:
          'Rola jest już przypisana, ale nie znajduje się na bieżącej liście delegowalnych ról. Można ją odznaczyć i usunąć z tego zakresu.' })));
  }

  function selectedRoleIds(root) {
    return [...root.querySelectorAll('[name="tenant_role_id"]:checked')]
      .map(input => Number(input.value))
      .filter(value => Number.isInteger(value) && value > 0);
  }

  function rolePicker(roles, assignedRoleIds, editable) {
    const assigned = new Set((assignedRoleIds || []).map(Number));
    const known = new Set((roles || []).map(role => Number(role.id)));
    const options = [
      ...(roles || []).map(role => roleOption(role, assigned.has(Number(role.id)), !editable)),
      ...[...assigned]
        .filter(roleId => !known.has(roleId))
        .map(roleId => fallbackRoleOption(roleId, !editable)),
    ];
    const root = node('div', { class: 'rbac-role-options' },
      ...(options.length
        ? options
        : [node('div', { class: 'panel empty', text: editable
            ? 'Brak ról możliwych do przypisania w tej organizacji.'
            : 'Brak przypisanych ról w tej organizacji.' })]));
    root.querySelectorAll('input[type="checkbox"]').forEach(input => {
      input.addEventListener('change', () => {
        input.closest('.rbac-role-option')?.classList.toggle('selected', input.checked);
      });
    });
    return root;
  }

  async function openUserOrganizationAccess(user) {
    const tenants = await pagedItems('/tenants');
    const tenantField = selectField(
      'Organizacja',
      'tenant_id',
      tenants.map(tenant => ({
        value: String(tenant.id),
        label: tenant.name + (tenant.slug ? ' (' + tenant.slug + ')' : ''),
      })),
      '',
      { required: true, placeholder: 'Wybierz organizację' }
    );
    const tenantSelect = tenantField.querySelector('select');
    const accessHost = node('div', { class: 'stack' },
      node('div', { class: 'panel empty', text:
        tenants.length
          ? 'Wybierz organizację. Członkostwo i role są niezależne dla każdej organizacji.'
          : 'Brak organizacji dostępnych do zarządzania.' }));

    const body = node('div', { class: 'stack' },
      node('section', { class: 'panel' },
        node('strong', { text: user.username }),
        node('p', { class: 'muted', text:
          'Użytkownik może należeć do wielu organizacji. Każda organizacja ma własny zestaw ról; zmiana tutaj nie modyfikuje ról globalnych ani dostępu w innych organizacjach.' })),
      tenantField,
      accessHost);

    openModal({
      title: 'Organizacje i role: ' + user.username,
      eyebrow: 'Dostęp organizacyjny',
      wide: true,
      body,
    });

    let renderGeneration = 0;

    async function renderTenantAccess(tenantId) {
      const renderId = ++renderGeneration;
      if (!tenantId) {
        accessHost.replaceChildren(node('div', { class: 'panel empty', text: 'Wybierz organizację.' }));
        return;
      }

      const tenant = tenants.find(item => String(item.id) === String(tenantId));
      if (!tenant) return;

      accessHost.replaceChildren(node('div', { class: 'panel', text: 'Ładowanie dostępu organizacyjnego…' }));

      let scope;
      let membership;
      try {
        [scope, membership] = await Promise.all([
          api('/tenants/' + encodeURIComponent(tenantId) + '/permissions'),
          membershipForUser(tenantId, user.id),
        ]);
      } catch (error) {
        if (renderId !== renderGeneration) return;
        accessHost.replaceChildren(node('section', { class: 'panel' },
          node('strong', { text: tenant.name }),
          node('p', { class: 'muted', text:
            'Nie można odczytać członkostwa w tej organizacji: ' + error.message })));
        return;
      }
      if (renderId !== renderGeneration) return;

      const editableTenant = tenant.status === 'active' || scope.global_administration;
      const canManageMembership = editableTenant && scope.permissions.includes('tenants.members.manage');
      const canAssignRoles = editableTenant && scope.permissions.includes('tenants.roles.assign');
      let roles = [];

      if (canAssignRoles) {
        try {
          roles = await pagedItems('/tenants/' + encodeURIComponent(tenantId) + '/assignable-roles');
        } catch (error) {
          if (renderId !== renderGeneration) return;
          accessHost.replaceChildren(node('section', { class: 'panel' },
            node('strong', { text: tenant.name }),
            node('p', { class: 'muted', text:
              'Nie można pobrać ról możliwych do przypisania: ' + error.message })));
          return;
        }
      }
      if (renderId !== renderGeneration) return;

      const picker = rolePicker(roles, membership?.role_ids || [], canAssignRoles);
      const membershipState = membership
        ? badge(
            membership.status === 'active' ? 'Członek aktywny' : 'Członkostwo wyłączone',
            membership.status === 'active' ? 'ok' : 'warning'
          )
        : badge('Brak członkostwa', 'warning');
      const actionBar = node('div', { class: 'action-group' });

      if (!membership && canManageMembership) {
        actionBar.append(action('Dodaj do organizacji', async () => {
          const roleIds = canAssignRoles ? selectedRoleIds(picker) : [];
          await api('/tenants/' + encodeURIComponent(tenantId) + '/members', {
            method: 'POST',
            idempotent: true,
            body: {
              user_id: Number(user.id),
              status: 'active',
              role_ids: roleIds,
            },
          });
          toast('Użytkownik został przypisany do organizacji.');
          await renderTenantAccess(tenantId);
        }, 'primary'));
      }

      if (membership && canAssignRoles) {
        actionBar.append(action('Zapisz role w organizacji', async () => {
          await api(
            '/tenants/' + encodeURIComponent(tenantId)
            + '/members/' + encodeURIComponent(user.id) + '/roles',
            {
              method: 'PUT',
              body: {
                role_ids: selectedRoleIds(picker),
                expected_version: membership.version,
              },
            }
          );
          toast('Role użytkownika w organizacji zostały zapisane.');
          await renderTenantAccess(tenantId);
        }, 'primary'));
      }

      if (membership && canManageMembership) {
        actionBar.append(action(
          membership.status === 'active' ? 'Wyłącz członkostwo' : 'Włącz członkostwo',
          async () => {
            await api(
              '/tenants/' + encodeURIComponent(tenantId)
              + '/members/' + encodeURIComponent(user.id),
              {
                method: 'PUT',
                body: {
                  status: membership.status === 'active' ? 'disabled' : 'active',
                  expected_version: membership.version,
                },
              }
            );
            toast('Status członkostwa został zmieniony.');
            await renderTenantAccess(tenantId);
          }
        ));

        actionBar.append(action('Usuń z organizacji', () => confirmAction(
          'Usuń członkostwo w organizacji',
          'Usuniesz wyłącznie dostęp użytkownika ' + user.username + ' do organizacji ' + tenant.name
            + '. Konto użytkownika oraz dostępy w innych organizacjach pozostaną bez zmian.',
          async () => {
            await api(
              '/tenants/' + encodeURIComponent(tenantId)
              + '/members/' + encodeURIComponent(user.id)
              + '?expected_version=' + encodeURIComponent(membership.version),
              { method: 'DELETE' }
            );
            toast('Członkostwo w organizacji zostało usunięte.');
            await renderTenantAccess(tenantId);
          }
        ), 'danger'));
      }

      const roleSummary = membership?.role_ids?.length
        ? 'Aktualne role: ' + membership.role_ids.map(roleId => {
            const role = roles.find(item => Number(item.id) === Number(roleId));
            return role ? role.name : '#' + roleId;
          }).join(', ')
        : 'Aktualne role: brak';

      accessHost.replaceChildren(
        node('section', { class: 'panel rbac-assignment-editor' },
          node('div', { class: 'rbac-section-heading' },
            node('div', {},
              node('span', { class: 'rbac-kicker', text: 'Zakres organizacji' }),
              node('h2', { text: tenant.name }),
              node('p', { class: 'muted', text:
                'Role zapisane tutaj obowiązują tylko w tej organizacji. Ten sam użytkownik może mieć inny zestaw ról w każdej kolejnej organizacji.' })),
            membershipState),
          node('p', { class: 'muted', text: roleSummary }),
          canAssignRoles
            ? picker
            : node('p', { class: 'muted', text:
                membership
                  ? 'Brak uprawnienia tenants.roles.assign — role są tylko do odczytu.'
                  : 'Brak uprawnienia tenants.roles.assign. Członkostwo można utworzyć bez roli, jeśli masz tenants.members.manage.' }),
          actionBar.childNodes.length
            ? actionBar
            : node('div', { class: 'panel empty', text:
                membership
                  ? 'Brak uprawnień do zmiany członkostwa lub ról w tej organizacji.'
                  : 'Brak uprawnień do przypisania użytkownika do tej organizacji.' })));
    }

    tenantSelect?.addEventListener('change', event => {
      renderTenantAccess(event.currentTarget.value).catch(error => toast(error.message, 'error'));
    });
  }

  registerRoutedForm({
    id: 'users-organization-access',
    pattern: /^\/access\/users\/(?<id>\d+)\/organizations$/,
    parent: 'users',
    permission: 'users.read',
    label: 'Użytkownicy',
  }, async match => {
    const users = await pagedItems('/users');
    const user = users.find(item => Number(item.id) === Number(match.params.id));
    if (!user) throw new Error('Nie znaleziono użytkownika.');
    await openUserOrganizationAccess(user);
  });

  registerExtension('identity-organization-access', () => {});
})();
