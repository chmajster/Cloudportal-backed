'use strict';

(() => {
async function usersView() {
  const users = (await api('/users?limit=200')).items;
  const actions = [];
  if (allowed('users.create')) actions.push(button('Dodaj użytkownika', createUser, 'primary'));
  dom.content.replaceChildren(heading('Konta ludzi i konta serwisowe. Uprawnienia wynikają wyłącznie z przypisanych ról.', actions),
    table([
      { label: 'Użytkownik', value: user => node('div', {}, node('strong', { text: user.username }), node('div', { class: 'muted', text: user.email })) },
      { label: 'Typ', value: user => badge(user.is_service_account ? 'serwisowe' : 'osobowe', user.is_service_account ? 'info' : '') },
      { label: 'Logowanie', value: user => badge(user.auth_source === 'ldap' ? 'LDAP' : 'Lokalne', user.auth_source === 'ldap' ? 'info' : '') },
      { label: 'Status', value: user => { const status = user.is_locked ? 'locked' : user.is_active ? 'active' : 'inactive'; return badge(statusLabel(status), statusKind(status)); } },
      { label: 'Ostatnie logowanie', value: user => formatDate(user.last_login_at) },
    ], users, user => userActions(user)));
}

function userActions(user) {
  const actions = [];
  if (allowed('roles.assign') && allowed('roles.read')) actions.push(button('Role', () => assignUserRoles(user)));
  if (allowed('users.update')) {
    actions.push(button('Edytuj', () => editUser(user)));
    if (user.is_locked) actions.push(button('Odblokuj', () => userCommand(user, 'unlock')));
    actions.push(button(user.is_active ? 'Wyłącz' : 'Włącz', () => userCommand(user, user.is_active ? 'disable' : 'enable')));
    if (!user.is_service_account && user.is_active && user.auth_source !== 'ldap') actions.push(button('Reset hasła', () => resetUserPassword(user)));
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
  try {
    const [all, assigned] = await Promise.all([api('/roles?limit=200'), api(`/users/${user.id}/roles`)]);
    const assignedIds = new Set(assigned.items.map(role => role.id));
    const grid = node('div', { class: 'permission-grid' });
    all.items.forEach(role => {
      const checkbox = node('input', { type: 'checkbox', name: 'role_id', value: role.id, checked: assignedIds.has(role.id) });
      const summary = node('span', {}, role.name, node('small', { class: 'muted', text: ` · ${role.permissions.length} uprawnień` }));
      grid.append(node('label', {}, checkbox, summary));
    });
    openModal({ title: `Role: ${user.username}`, eyebrow: 'RBAC', body: grid, onSubmit: async (_data, form) => {
      const role_ids = [...form.querySelectorAll('[name="role_id"]:checked')].map(input => Number(input.value));
      await api(`/users/${user.id}/roles`, { method: 'PUT', body: { role_ids } }); toast('Role przypisane.'); navigate('users');
    }});
  } catch (error) { toast(error.message, 'error'); }
}

function resetUserPassword(user) {
  confirmAction('Wydaj token resetu', `Aktywne sesje użytkownika ${user.username} zostaną unieważnione.`, async () => {
    const result = await api(`/users/${user.id}/reset-password`, { method: 'POST' });
    showSecret('Token resetu hasła', result.reset_token, 'Token jest ważny przez 15 minut. Przekaż go użytkownikowi bezpiecznym kanałem.');
    return false;
  });
}

async function rolesView() {
  const roles = (await api('/roles?limit=200')).items;
  const actions = allowed('roles.create') ? [button('Dodaj rolę', () => roleForm(), 'primary')] : [];
  dom.content.replaceChildren(heading('Role grupują uprawnienia do poszczególnych modułów. System chroni ostatniego aktywnego administratora.', actions),
    table([{ label: 'Rola', value: role => node('strong', { text: role.name }) }, { label: 'Uprawnienia', value: role => badge(`${role.permissions.length} uprawnień`, role.permissions.length ? 'info' : '') }], roles, role => {
      const actions = [];
      if (allowed('roles.update')) actions.push(button('Edytuj', () => roleForm(role)));
      if (allowed('roles.delete')) actions.push(button('Usuń', () => confirmAction('Usuń rolę', `Rola ${role.name} zostanie trwale usunięta.`, async () => { await api(`/roles/${role.id}`, { method: 'DELETE' }); toast('Rola usunięta.'); navigate('roles'); }), 'danger'));
      return actions;
    }));
}

async function roleForm(role = null) {
  try {
    const permissions = (await api('/permissions')).items;
    const picker = permissionPicker(permissions, role?.permissions || [], 'permission');
    const fields = node('div', { class: 'form-grid' },
      field('Nazwa roli', 'name', { required: true, value: role?.name || '', wide: true }),
      formSection('Uprawnienia', 'Uprawnienia są pogrupowane według modułów. Zaznacz tylko zakres potrzebny tej roli.', picker));
    openModal({ title: role ? 'Edytuj rolę' : 'Nowa rola', eyebrow: 'RBAC', body: fields, submitLabel: role ? 'Zapisz' : 'Utwórz', wide: true, onSubmit: async (_data, form) => {
      const payload = { name: form.elements.name.value, permissions: [...form.querySelectorAll('[name="permission"]:checked')].map(input => input.value) };
      await api(role ? `/roles/${role.id}` : '/roles', { method: role ? 'PUT' : 'POST', body: payload });
      toast('Rola zapisana.');
      navigate('roles');
    }});
  } catch (error) { toast(error.message, 'error'); }
}

async function tokensView() {
  const [tokenResult, userResult] = await Promise.all([
    api('/tokens?limit=200'),
    allowed('users.read') ? api('/users?limit=200') : Promise.resolve({ items: [] }),
  ]);
  const tokens = tokenResult.items;
  const users = new Map(userResult.items.map(user => [Number(user.id), user.username]));
  const actions = allowed('tokens.create') ? [button('Utwórz token', createToken, 'primary')] : [];
  dom.content.replaceChildren(heading('Tokeny API mają jawny, ograniczony zakres. Sekret jest dostępny wyłącznie po utworzeniu.', actions),
    table([
      { label: 'Nazwa', value: token => node('div', {}, node('strong', { text: token.name }), node('div', { class: 'mono muted', text: token.token_prefix })) },
      { label: 'Właściciel', value: token => users.get(Number(token.user_id)) || `Użytkownik #${token.user_id}` },
      { label: 'Zakres', value: token => badge(`${token.scopes.length} uprawnień`, token.scopes.length ? 'info' : '') },
      { label: 'Status', value: token => { const status = token.revoked_at ? 'revoked' : token.expires_at && new Date(token.expires_at) < new Date() ? 'expired' : 'active'; return badge(statusLabel(status), status === 'active' ? 'ok' : 'danger'); } },
      { label: 'Ostatnio użyty', value: token => formatDate(token.last_used_at) },
    ], tokens, token => {
      const result = [button('Zakres', () => showPermissionSummary(`Zakres: ${token.name}`, token.scopes))];
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

  const passwordMessage = user.must_change_password
    ? 'Konto używa początkowego hasła administratora. Ustaw własne hasło, aby odblokować pełny dostęp.'
    : 'Zmiana hasła unieważni aktywne sesje i tokeny resetu. Po zapisaniu zalogujesz się ponownie.';

  const security = node('section', { class: 'panel account-card account-security-card' },
    node('div', { class: 'account-card-heading' },
      node('div', { class: 'account-security-icon', 'aria-hidden': 'true', text: '✓' }),
      node('div', {},
        node('span', { class: 'account-kicker', text: 'Ochrona dostępu' }),
        node('h2', { text: 'Bezpieczeństwo konta' })),
      user.must_change_password ? badge('Wymagana zmiana', 'warning') : badge('Hasło ustawione', 'ok')),
    node('p', { class: user.must_change_password ? 'form-error account-security-copy' : 'muted account-security-copy', text: passwordMessage }),
    node('div', { class: 'account-security-actions' },
      button(user.must_change_password ? 'Ustaw nowe hasło' : 'Zmień hasło', () => changePassword(user.must_change_password), 'primary')));

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

registerCommand('users.create', createUser);
registerCommand('tokens.create', createToken);
registerView({ id: 'users', label: 'Użytkownicy', icon: 'U', permission: 'users.read', order: 10 }, usersView);
registerView({ id: 'roles', label: 'Role i RBAC', icon: 'R', permission: 'roles.read', order: 20 }, rolesView);
registerView({ id: 'tokens', label: 'Tokeny API', icon: 'T', permission: 'tokens.read', order: 30 }, tokensView);
registerView({ id: 'account', label: 'Moje konto', icon: 'M', order: 170 }, accountView);
})();
