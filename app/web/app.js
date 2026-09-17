'use strict';

const API = '/api/v1';
const SESSION_KEY = 'cloudportal.console.session';
const state = { session: null, identity: null, view: 'dashboard', refreshPromise: null };

const dom = {
  loginView: document.querySelector('#login-view'),
  appView: document.querySelector('#app-view'),
  loginForm: document.querySelector('#login-form'),
  loginError: document.querySelector('#login-error'),
  navigation: document.querySelector('#navigation'),
  content: document.querySelector('#content'),
  pageTitle: document.querySelector('#page-title'),
  pageEyebrow: document.querySelector('#page-eyebrow'),
  currentUser: document.querySelector('#current-user'),
  currentRoles: document.querySelector('#current-roles'),
  apiStatus: document.querySelector('#api-status'),
  modal: document.querySelector('#modal'),
  modalTitle: document.querySelector('#modal-title'),
  modalEyebrow: document.querySelector('#modal-eyebrow'),
  modalBody: document.querySelector('#modal-body'),
  modalActions: document.querySelector('#modal-actions'),
  toastRegion: document.querySelector('#toast-region'),
};

const routes = [
  { id: 'dashboard', label: 'Dashboard', icon: '◫' },
  { id: 'users', label: 'Użytkownicy', icon: 'U', permission: 'users.read' },
  { id: 'roles', label: 'Role i RBAC', icon: 'R', permission: 'roles.read' },
  { id: 'tokens', label: 'Tokeny API', icon: 'T', permission: 'tokens.read' },
  { id: 'credentials', label: 'Credentiale', icon: 'K', permission: 'credentials.read' },
  { id: 'providers', label: 'Providery', icon: 'P', permission: 'providers.read' },
  { id: 'deployments', label: 'Deploymenty', icon: 'D', permission: 'deployments.read' },
  { id: 'jobs', label: 'Zadania', icon: 'J', permission: 'jobs.read' },
  { id: 'audit', label: 'Audyt', icon: 'A', permission: 'audit.read' },
  { id: 'account', label: 'Moje konto', icon: 'M' },
];

class ApiError extends Error {
  constructor(status, data) {
    super(errorMessage(data));
    this.status = status;
    this.data = data;
  }
}

function node(tag, attributes = {}, ...children) {
  const element = document.createElement(tag);
  for (const [key, value] of Object.entries(attributes)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'class') element.className = value;
    else if (key === 'text') element.textContent = value;
    else if (key.startsWith('on') && typeof value === 'function') element.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === 'checked' || key === 'selected' || key === 'disabled' || key === 'required') element[key] = Boolean(value);
    else element.setAttribute(key, String(value));
  }
  for (const child of children.flat()) {
    if (child === undefined || child === null || child === false) continue;
    element.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return element;
}

function errorMessage(data) {
  const detail = data && data.detail;
  if (Array.isArray(detail)) return detail.map(item => item.msg || String(item)).join('; ');
  return detail || 'Operacja nie powiodła się.';
}

function loadSession() {
  try { state.session = JSON.parse(sessionStorage.getItem(SESSION_KEY)); }
  catch { state.session = null; }
}

function saveSession(session) {
  state.session = session;
  sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
}

function clearSession() {
  state.session = null;
  state.identity = null;
  state.refreshPromise = null;
  sessionStorage.removeItem(SESSION_KEY);
}

async function refreshSession() {
  if (!state.session?.refresh_token) throw new ApiError(401, { detail: 'Sesja wygasła.' });
  if (state.refreshPromise) return state.refreshPromise;
  state.refreshPromise = (async () => {
    const response = await fetch(`${API}/auth/refresh`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Request-ID': crypto.randomUUID() },
      body: JSON.stringify({ refresh_token: state.session.refresh_token }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new ApiError(response.status, data);
    saveSession(data);
    state.identity = { user: data.user, roles: data.roles, permissions: data.permissions, token_type: 'session' };
    return data;
  })().finally(() => { state.refreshPromise = null; });
  return state.refreshPromise;
}

async function api(path, options = {}, canRefresh = true) {
  const method = options.method || 'GET';
  const headers = { 'X-Request-ID': crypto.randomUUID(), ...(options.headers || {}) };
  if (options.auth !== false && state.session?.access_token) headers.Authorization = `Bearer ${state.session.access_token}`;
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';
  if (options.idempotent) headers['Idempotency-Key'] = crypto.randomUUID();
  const response = await fetch(`${API}${path}`, {
    method, headers, body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  if (response.status === 401 && canRefresh && options.auth !== false && state.session?.refresh_token) {
    try { await refreshSession(); }
    catch (error) { showLogin('Sesja wygasła. Zaloguj się ponownie.'); throw error; }
    return api(path, options, false);
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok && !(options.allow || []).includes(response.status)) throw new ApiError(response.status, data);
  return data;
}

function allowed(permission) {
  return !permission || Boolean(state.identity?.permissions?.includes(permission));
}

function toast(message, type = '') {
  const item = node('div', { class: `toast ${type}`, text: message });
  dom.toastRegion.append(item);
  window.setTimeout(() => item.remove(), 5000);
}

function formatDate(value) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? String(value) : new Intl.DateTimeFormat('pl-PL', { dateStyle: 'short', timeStyle: 'short' }).format(date);
}

function short(value, length = 12) {
  if (!value) return '—';
  return String(value).length > length ? `${String(value).slice(0, length)}…` : String(value);
}

function badge(text, kind = '') { return node('span', { class: `badge ${kind}`, text }); }
function statusKind(status) {
  if (['ok', 'active', 'successful', 'configured'].includes(status)) return 'ok';
  if (['queued', 'running', 'degraded'].includes(status)) return 'warning';
  if (['failed', 'locked', 'revoked', 'inactive'].includes(status)) return 'danger';
  return 'info';
}

function button(label, onClick, kind = 'ghost', disabled = false) {
  return node('button', { type: 'button', class: `button small ${kind}`, onClick, disabled }, label);
}

function heading(description, actions = []) {
  return node('div', { class: 'page-actions' }, node('p', { text: description }), node('div', { class: 'action-group' }, actions));
}

function loading() {
  dom.content.replaceChildren(node('div', { class: 'loading' }, node('div', { class: 'spinner', 'aria-label': 'Ładowanie' })));
}

function table(columns, rows, actions) {
  if (!rows.length) return node('div', { class: 'table-wrap' }, node('div', { class: 'empty', text: 'Brak danych do wyświetlenia.' }));
  const head = node('tr');
  columns.forEach(column => head.append(node('th', { text: column.label })));
  if (actions) head.append(node('th', { text: 'Akcje' }));
  const body = node('tbody');
  rows.forEach(row => {
    const tr = node('tr');
    columns.forEach(column => {
      const value = column.value(row);
      tr.append(node('td', { class: column.class || '' }, value instanceof Node ? value : String(value ?? '—')));
    });
    if (actions) tr.append(node('td', {}, node('div', { class: 'row-actions' }, actions(row))));
    body.append(tr);
  });
  return node('div', { class: 'table-wrap' }, node('table', {}, node('thead', {}, head), body));
}

function field(labelText, name, options = {}) {
  const input = node(options.tag || 'input', {
    name, type: options.type || 'text', value: options.value ?? '', required: options.required,
    min: options.min, max: options.max, minlength: options.minlength, maxlength: options.maxlength, autocomplete: options.autocomplete,
    placeholder: options.placeholder, step: options.step,
  });
  if (options.tag === 'textarea') input.textContent = options.value ?? '';
  const label = node('label', {}, labelText, input);
  if (options.help) label.append(node('span', { class: 'field-help', text: options.help }));
  if (options.wide) label.classList.add('wide');
  return label;
}

function selectField(labelText, name, choices, value, options = {}) {
  const select = node('select', { name, required: options.required });
  if (options.placeholder) select.append(node('option', { value: '', text: options.placeholder }));
  choices.forEach(choice => select.append(node('option', { value: choice.value, text: choice.label, selected: String(choice.value) === String(value) })));
  const label = node('label', {}, labelText, select);
  if (options.wide) label.classList.add('wide');
  return label;
}

function checkboxField(labelText, name, checked = false) {
  return node('label', { class: 'checkbox' }, node('input', { type: 'checkbox', name, checked }), labelText);
}

function closeModal() { if (dom.modal.open) dom.modal.close(); }

function openModal({ title, eyebrow = 'Cloudportal', body, submitLabel, onSubmit, danger = false }) {
  dom.modalTitle.textContent = title;
  dom.modalEyebrow.textContent = eyebrow;
  dom.modalBody.replaceChildren();
  dom.modalActions.replaceChildren();
  const form = node('form', { id: 'modal-form', class: 'stack' });
  form.append(body);
  dom.modalBody.append(form);
  dom.modalActions.append(button('Anuluj', closeModal));
  if (onSubmit) {
    const submit = node('button', { type: 'submit', form: 'modal-form', class: `button ${danger ? 'danger' : 'primary'}` }, submitLabel || 'Zapisz');
    dom.modalActions.append(submit);
    form.addEventListener('submit', async event => {
      event.preventDefault();
      submit.disabled = true;
      try {
        const shouldClose = await onSubmit(new FormData(form), form);
        if (shouldClose !== false) closeModal();
      }
      catch (error) { toast(error.message, 'error'); }
      finally { submit.disabled = false; }
    });
  }
  if (!dom.modal.open) dom.modal.showModal();
  window.setTimeout(() => form.querySelector('input,select,textarea')?.focus(), 30);
}

function showSecret(title, value, note = 'Ta wartość jest wyświetlana tylko raz. Skopiuj ją teraz.') {
  const copy = button('Kopiuj', async () => {
    await navigator.clipboard.writeText(value);
    toast('Skopiowano do schowka.');
  }, 'primary');
  dom.modalTitle.textContent = title;
  dom.modalEyebrow.textContent = 'Sekret jednorazowy';
  dom.modalBody.replaceChildren(node('div', { class: 'stack' }, node('p', { class: 'muted', text: note }), node('div', { class: 'secret-box mono', text: value })));
  dom.modalActions.replaceChildren(button('Zamknij', closeModal), copy);
  dom.modal.showModal();
}

function confirmAction(title, message, action) {
  openModal({ title, eyebrow: 'Potwierdzenie', body: node('p', { text: message }), submitLabel: 'Potwierdź', danger: true, onSubmit: action });
}

function showLogin(message = '') {
  clearSession();
  dom.appView.hidden = true;
  dom.loginView.hidden = false;
  dom.loginError.hidden = !message;
  dom.loginError.textContent = message;
  dom.loginForm.querySelector('[name="password"]').value = '';
  dom.loginForm.querySelector('[name="username"]').focus();
}

function showApp() {
  dom.loginView.hidden = true;
  dom.appView.hidden = false;
  dom.currentUser.textContent = state.identity.user.username;
  dom.currentRoles.textContent = state.identity.roles.map(role => role.name).join(', ') || 'Brak roli';
  renderNavigation();
  const mustChangePassword = state.identity.user.must_change_password;
  navigate(mustChangePassword ? 'account' : location.hash.slice(1) || 'dashboard');
  if (mustChangePassword) window.setTimeout(() => changePassword(true), 0);
}

function renderNavigation() {
  dom.navigation.replaceChildren();
  routes.filter(route => allowed(route.permission) && (!state.identity.user.must_change_password || route.id === 'account')).forEach(route => {
    const item = node('button', { class: `nav-link ${state.view === route.id ? 'active' : ''}`, type: 'button', onClick: () => navigate(route.id) },
      node('span', { class: 'nav-icon', text: route.icon }), route.label);
    item.dataset.route = route.id;
    dom.navigation.append(item);
  });
}

async function navigate(view) {
  const available = routes.filter(item => allowed(item.permission) && (!state.identity.user.must_change_password || item.id === 'account'));
  const route = available.find(item => item.id === view) || available[0];
  state.view = route.id;
  location.hash = route.id;
  dom.pageTitle.textContent = route.label;
  dom.pageEyebrow.textContent = route.id === 'dashboard' ? 'Stan systemu' : 'Zarządzanie lokalne';
  dom.navigation.querySelectorAll('.nav-link').forEach(item => item.classList.toggle('active', item.dataset.route === route.id));
  document.querySelector('.sidebar').classList.remove('open');
  loading();
  try { await views[route.id](); }
  catch (error) {
    dom.content.replaceChildren(node('div', { class: 'panel' }, node('h2', { text: 'Nie udało się załadować widoku' }), node('p', { class: 'form-error', text: error.message }), button('Spróbuj ponownie', () => navigate(route.id), 'primary')));
  }
  dom.content.focus();
}

async function dashboardView() {
  const resources = [
    ['users', 'users.read', '/users'], ['credentials', 'credentials.read', '/credentials'],
    ['deployments', 'deployments.read', '/deployments'], ['jobs', 'jobs.read', '/jobs'],
  ];
  const health = await api('/health', { auth: false, allow: [503] });
  const counts = {};
  await Promise.all(resources.map(async ([key, permission, path]) => {
    if (!allowed(permission)) return;
    try { counts[key] = (await api(`${path}?limit=200`)).items.length; } catch { counts[key] = '—'; }
  }));
  const metrics = node('div', { class: 'metrics' },
    metric('Stan backendu', health.status === 'ok' ? 'OK' : 'Degraded', `${health.checks.workers.online}/${health.checks.workers.expected} workerów`),
    metric('Użytkownicy', counts.users ?? '—', allowed('users.read') ? 'widoczne konta' : 'brak uprawnienia'),
    metric('Deploymenty', counts.deployments ?? '—', 'łącznie'),
    metric('Zadania', counts.jobs ?? '—', 'ostatnie 200'),
  );
  const checks = node('div', { class: 'checks' });
  Object.entries(health.checks).forEach(([name, value]) => {
    const ok = typeof value === 'object' ? value.online >= value.expected : Boolean(value);
    checks.append(node('div', { class: 'check' }, node('span', { text: name }), node('strong', { text: ok ? 'OK' : 'Problem' })));
  });
  const recent = allowed('jobs.read') ? (await api('/jobs?limit=8')).items : [];
  dom.content.replaceChildren(metrics, node('div', { class: 'panels' },
    node('section', { class: 'panel' }, node('div', { class: 'panel-header' }, node('h2', { text: 'Ostatnie zadania' }), button('Wszystkie', () => navigate('jobs'))),
      recent.length ? table([{ label: 'Operacja', value: row => row.operation }, { label: 'Status', value: row => badge(row.status, statusKind(row.status)) }, { label: 'Utworzono', value: row => formatDate(row.created_at) }], recent) : node('div', { class: 'empty', text: 'Brak dostępnych zadań.' })),
    node('section', { class: 'panel' }, node('div', { class: 'panel-header' }, node('h2', { text: 'Komponenty' }), badge(health.status, statusKind(health.status))), checks),
  ));
  setApiStatus(health.status === 'ok');
}

function metric(label, value, detail) {
  return node('section', { class: 'metric' }, node('span', { text: label }), node('strong', { text: String(value) }), node('small', { text: detail }));
}

async function usersView() {
  const users = (await api('/users?limit=200')).items;
  const actions = [];
  if (allowed('users.create')) actions.push(button('Dodaj użytkownika', createUser, 'primary'));
  dom.content.replaceChildren(heading('Konta ludzi i konta serwisowe. Uprawnienia wynikają wyłącznie z przypisanych ról.', actions),
    table([
      { label: 'Użytkownik', value: user => node('div', {}, node('strong', { text: user.username }), node('div', { class: 'muted', text: user.email })) },
      { label: 'Typ', value: user => badge(user.is_service_account ? 'serwisowe' : 'osobowe', user.is_service_account ? 'info' : '') },
      { label: 'Status', value: user => badge(user.is_locked ? 'locked' : user.is_active ? 'active' : 'inactive', statusKind(user.is_locked ? 'locked' : user.is_active ? 'active' : 'inactive')) },
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
    if (!user.is_service_account && user.is_active) actions.push(button('Reset hasła', () => resetUserPassword(user)));
  }
  if (allowed('users.delete')) actions.push(button('Usuń', () => confirmAction('Usuń użytkownika', `Konto ${user.username} zostanie zanonimizowane i utraci dostęp.`, async () => { await api(`/users/${user.id}`, { method: 'DELETE' }); toast('Użytkownik usunięty.'); navigate('users'); }), 'danger'));
  return actions;
}

function createUser() {
  const fields = node('div', { class: 'form-grid' },
    field('Login', 'username', { required: true, maxlength: 63 }), field('E-mail', 'email', { type: 'email', required: true }),
    field('Imię', 'first_name', { maxlength: 100 }), field('Nazwisko', 'last_name', { maxlength: 100 }),
    field('Hasło początkowe', 'password', { type: 'password', required: true, minlength: 12, autocomplete: 'new-password', wide: true }),
    checkboxField('Konto serwisowe (bez logowania hasłem)', 'is_service_account'));
  openModal({ title: 'Nowy użytkownik', eyebrow: 'Tożsamość', body: fields, submitLabel: 'Utwórz', onSubmit: async data => {
    await api('/users', { method: 'POST', body: { username: data.get('username'), email: data.get('email'), password: data.get('password'), first_name: data.get('first_name'), last_name: data.get('last_name'), is_service_account: data.has('is_service_account') } });
    toast('Użytkownik utworzony.'); navigate('users');
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
  dom.content.replaceChildren(heading('Role grupują granularne permissions. System chroni ostatniego aktywnego administratora.', actions),
    table([{ label: 'Rola', value: role => node('strong', { text: role.name }) }, { label: 'Uprawnienia', value: role => node('span', { class: 'muted', text: role.permissions.join(', ') || 'Brak' }) }], roles, role => {
      const actions = [];
      if (allowed('roles.update')) actions.push(button('Edytuj', () => roleForm(role)));
      if (allowed('roles.delete')) actions.push(button('Usuń', () => confirmAction('Usuń rolę', `Rola ${role.name} zostanie trwale usunięta.`, async () => { await api(`/roles/${role.id}`, { method: 'DELETE' }); toast('Rola usunięta.'); navigate('roles'); }), 'danger'));
      return actions;
    }));
}

async function roleForm(role = null) {
  try {
    const permissions = (await api('/permissions')).items;
    const grid = node('div', { class: 'permission-grid wide' });
    permissions.forEach(permission => grid.append(node('label', {}, node('input', { type: 'checkbox', name: 'permission', value: permission, checked: role?.permissions.includes(permission) }), permission)));
    const fields = node('div', { class: 'form-grid' }, field('Nazwa roli', 'name', { required: true, value: role?.name || '', wide: true }), grid);
    openModal({ title: role ? 'Edytuj rolę' : 'Nowa rola', eyebrow: 'RBAC', body: fields, submitLabel: role ? 'Zapisz' : 'Utwórz', onSubmit: async (_data, form) => {
      const payload = { name: form.elements.name.value, permissions: [...form.querySelectorAll('[name="permission"]:checked')].map(input => input.value) };
      await api(role ? `/roles/${role.id}` : '/roles', { method: role ? 'PUT' : 'POST', body: payload }); toast('Rola zapisana.'); navigate('roles');
    }});
  } catch (error) { toast(error.message, 'error'); }
}

async function tokensView() {
  const tokens = (await api('/tokens?limit=200')).items;
  const actions = allowed('tokens.create') ? [button('Utwórz token', createToken, 'primary')] : [];
  dom.content.replaceChildren(heading('Tokeny API mają jawny, ograniczony scope. Sekret jest dostępny wyłącznie po utworzeniu.', actions),
    table([
      { label: 'Nazwa', value: token => node('div', {}, node('strong', { text: token.name }), node('div', { class: 'mono muted', text: token.token_prefix })) },
      { label: 'Użytkownik ID', value: token => token.user_id },
      { label: 'Scopes', value: token => node('span', { class: 'muted', text: token.scopes.join(', ') }) },
      { label: 'Status', value: token => badge(token.revoked_at ? 'revoked' : token.expires_at && new Date(token.expires_at) < new Date() ? 'expired' : 'active', token.revoked_at ? 'danger' : 'ok') },
      { label: 'Ostatnio użyty', value: token => formatDate(token.last_used_at) },
    ], tokens, token => allowed('tokens.revoke') && !token.revoked_at ? [button('Unieważnij', () => confirmAction('Unieważnij token', `Token ${token.name} natychmiast przestanie działać.`, async () => { await api(`/tokens/${token.id}/revoke`, { method: 'POST' }); toast('Token unieważniony.'); navigate('tokens'); }), 'danger')] : []));
}

async function createToken() {
  try {
    let permissions = state.identity.permissions;
    if (allowed('roles.read')) permissions = (await api('/permissions')).items.filter(permission => allowed(permission));
    const grid = node('div', { class: 'permission-grid wide' });
    permissions.forEach(permission => grid.append(node('label', {}, node('input', { type: 'checkbox', name: 'scope', value: permission }), permission)));
    const fields = node('div', { class: 'form-grid' }, field('Nazwa', 'name', { required: true }), field('Użytkownik ID (opcjonalnie)', 'user_id', { type: 'number', min: 1 }), field('Wygasa (opcjonalnie)', 'expires_at', { type: 'datetime-local', wide: true }), grid);
    openModal({ title: 'Nowy token API', eyebrow: 'Dostęp programowy', body: fields, submitLabel: 'Utwórz token', onSubmit: async (_data, form) => {
      const payload = { name: form.elements.name.value, scopes: [...form.querySelectorAll('[name="scope"]:checked')].map(input => input.value) };
      if (form.elements.user_id.value) payload.user_id = Number(form.elements.user_id.value);
      if (form.elements.expires_at.value) payload.expires_at = new Date(form.elements.expires_at.value).toISOString();
      const result = await api('/tokens', { method: 'POST', body: payload });
      showSecret('Nowy token API', result.token); navigate('tokens');
      return false;
    }});
  } catch (error) { toast(error.message, 'error'); }
}

async function credentialsView() {
  const credentials = (await api('/credentials?limit=200')).items;
  const actions = allowed('credentials.create') ? [button('Dodaj credential', () => credentialForm(), 'primary')] : [];
  dom.content.replaceChildren(heading('Sekrety są szyfrowane i nigdy nie wracają do przeglądarki. Edycja bez pola secrets zachowuje obecną wartość.', actions),
    table([
      { label: 'Nazwa', value: item => node('strong', { text: item.name }) }, { label: 'Typ', value: item => badge(item.type, 'info') },
      { label: 'Endpoint', value: item => node('span', { class: 'mono', text: item.endpoint || '—' }) }, { label: 'Użytkownik', value: item => item.username || '—' },
      { label: 'TLS', value: item => item.verify_ssl ? badge('verify', 'ok') : badge('disabled', 'warning') },
    ], credentials, item => credentialActions(item)));
}

function credentialActions(item) {
  const actions = [];
  if (allowed('credentials.test')) actions.push(button('Testuj', async () => { try { const result = await api(`/credentials/${item.id}/test`, { method: 'POST' }); toast(`Połączenie działa${result.version ? ` (${result.version})` : ''}.`); } catch (error) { toast(error.message, 'error'); } }));
  if (allowed('credentials.update')) actions.push(button('Edytuj', () => credentialForm(item)));
  if (allowed('credentials.delete')) actions.push(button('Usuń', () => confirmAction('Usuń credential', `Credential ${item.name} zostanie trwale usunięty.`, async () => { await api(`/credentials/${item.id}`, { method: 'DELETE' }); toast('Credential usunięty.'); navigate('credentials'); }), 'danger'));
  return actions;
}

function credentialForm(item = null) {
  const types = ['proxmox', 'vmware', 'ssh', 'winrm', 'aws', 'azure', 'openstack', 'other'].map(value => ({ value, label: value }));
  const fields = node('div', { class: 'form-grid' },
    field('Nazwa', 'name', { required: true, value: item?.name || '' }), selectField('Typ', 'type', types, item?.type || 'proxmox', { required: true }),
    field('Endpoint HTTPS / SSH', 'endpoint', { value: item?.endpoint || '', wide: true }), field('Użytkownik', 'username', { value: item?.username || '' }),
    checkboxField('Weryfikuj certyfikat TLS', 'verify_ssl', item ? item.verify_ssl : true),
    field(item ? 'Nowe secrets JSON (puste = zachowaj)' : 'Secrets JSON', 'secrets', { tag: 'textarea', required: !item, wide: true, placeholder: '{"token_id":"...","token_secret":"..."}', help: 'Dozwolone klucze obejmują password, token_id, token_secret, private_key, known_hosts, access_key_id, secret_access_key, tenant_id, client_id i client_secret.' }));
  openModal({ title: item ? 'Edytuj credential' : 'Nowy credential', eyebrow: 'Sekrety infrastruktury', body: fields, onSubmit: async data => {
    const payload = { name: data.get('name'), type: data.get('type'), endpoint: data.get('endpoint'), username: data.get('username'), verify_ssl: data.has('verify_ssl') };
    if (data.get('secrets').trim()) {
      try { payload.secrets = JSON.parse(data.get('secrets')); }
      catch { throw new Error('Pole secrets musi zawierać poprawny obiekt JSON.'); }
    } else if (!item) throw new Error('Sekrety są wymagane przy tworzeniu credentiala.');
    await api(item ? `/credentials/${item.id}` : '/credentials', { method: item ? 'PUT' : 'POST', body: payload }); toast('Credential zapisany.'); navigate('credentials');
  }});
}

async function providersView() {
  const providers = (await api('/providers?limit=200')).items;
  const actions = allowed('providers.create') ? [button('Dodaj provider', () => providerForm(), 'primary')] : [];
  dom.content.replaceChildren(heading('Providery wiążą adapter infrastruktury z konkretnym, zaszyfrowanym credentialem.', actions),
    table([{ label: 'Nazwa', value: item => node('strong', { text: item.name }) }, { label: 'Typ', value: item => badge(item.type, 'info') }, { label: 'Credential ID', value: item => item.credentials_id }, { label: 'Aktualizacja', value: item => formatDate(item.updated_at) }], providers, item => {
      const actions = [button('Zasoby', () => discoverProvider(item))];
      if (allowed('providers.update')) actions.push(button('Edytuj', () => providerForm(item)));
      if (allowed('providers.delete')) actions.push(button('Usuń', () => confirmAction('Usuń provider', `Provider ${item.name} zostanie usunięty.`, async () => { await api(`/providers/${item.id}`, { method: 'DELETE' }); toast('Provider usunięty.'); navigate('providers'); }), 'danger'));
      return actions;
    }));
}

async function providerForm(item = null) {
  try {
    const credentials = (await api('/credentials?limit=200')).items.filter(value => value.type === 'proxmox');
    const fields = node('div', { class: 'form-grid' }, field('Nazwa', 'name', { required: true, value: item?.name || '' }), selectField('Credential Proxmox', 'credentials_id', credentials.map(value => ({ value: value.id, label: `${value.name} (#${value.id})` })), item?.credentials_id, { required: true, placeholder: 'Wybierz credential' }));
    openModal({ title: item ? 'Edytuj provider' : 'Nowy provider', eyebrow: 'Infrastruktura', body: fields, onSubmit: async data => {
      await api(item ? `/providers/${item.id}` : '/providers', { method: item ? 'PUT' : 'POST', body: { name: data.get('name'), type: 'proxmox', credentials_id: Number(data.get('credentials_id')) } }); toast('Provider zapisany.'); navigate('providers');
    }});
  } catch (error) { toast(error.message, 'error'); }
}

async function discoverProvider(provider) {
  dom.modalTitle.textContent = `Zasoby: ${provider.name}`;
  dom.modalEyebrow.textContent = 'Discovery Proxmox';
  dom.modalBody.replaceChildren(node('div', { class: 'loading' }, node('div', { class: 'spinner' })));
  dom.modalActions.replaceChildren(button('Zamknij', closeModal));
  dom.modal.showModal();
  try {
    const nodes = (await api(`/providers/${provider.id}/nodes`)).items;
    dom.modalBody.replaceChildren(table(Object.keys(nodes[0] || { node: '' }).slice(0, 6).map(key => ({ label: key, value: row => String(row[key] ?? '—') })), nodes));
  } catch (error) { dom.modalBody.replaceChildren(node('p', { class: 'form-error', text: error.message })); }
}

async function deploymentsView() {
  const deployments = (await api('/deployments?limit=200')).items;
  const actions = allowed('deployments.create') ? [button('Nowy deployment', createDeployment, 'primary')] : [];
  dom.content.replaceChildren(heading('Kontrolowane wdrożenia Terraform/OpenTofu. Każda operacja tworzy audytowalne zadanie.', actions),
    table([
      { label: 'Nazwa', value: item => node('div', {}, node('strong', { text: item.name }), node('div', { class: 'mono muted', text: short(item.id, 18) })) },
      { label: 'Provider', value: item => `${item.provider} #${item.provider_id}` }, { label: 'Executor', value: item => badge(item.executor, 'info') },
      { label: 'Status', value: item => badge(item.status, statusKind(item.status)) }, { label: 'Aktualizacja', value: item => formatDate(item.updated_at) },
    ], deployments, item => deploymentActions(item)));
}

function deploymentActions(item) {
  const actions = [];
  if (allowed('jobs.execute') && allowed('terraform.execute') && !item.active_job_id && item.status !== 'destroyed') {
    actions.push(button('Plan', () => createTerraformJob(item, 'terraform.plan')));
    actions.push(button('Apply', () => createTerraformJob(item, 'terraform.apply')));
  }
  if (allowed('deployments.destroy') && allowed('jobs.execute') && !item.active_job_id && item.status !== 'destroyed') actions.push(button('Destroy', () => confirmAction('Zniszcz deployment', `Terraform zniszczy zasoby deploymentu ${item.name}.`, async () => { await api(`/deployments/${item.id}/destroy`, { method: 'POST', body: {}, idempotent: true }); toast('Zadanie destroy utworzone.'); navigate('deployments'); }), 'danger'));
  return actions;
}

async function createTerraformJob(item, operation) {
  try { await api('/jobs', { method: 'POST', body: { operation, deployment_id: item.id }, idempotent: true }); toast(`Zadanie ${operation} utworzone.`); navigate('jobs'); }
  catch (error) { toast(error.message, 'error'); }
}

async function createDeployment() {
  try {
    const [providers, credentials] = await Promise.all([api('/providers?limit=200'), api('/credentials?limit=200')]);
    const fields = node('div', { class: 'form-grid' },
      field('Nazwa deploymentu', 'name', { required: true }), selectField('Provider', 'provider_id', providers.items.map(item => ({ value: item.id, label: `${item.name} (#${item.id})` })), '', { required: true, placeholder: 'Wybierz provider' }),
      selectField('Credential', 'credentials_id', credentials.items.filter(item => item.type === 'proxmox').map(item => ({ value: item.id, label: `${item.name} (#${item.id})` })), '', { required: true, placeholder: 'Wybierz credential' }), selectField('Executor', 'executor', [{ value: 'terraform', label: 'Terraform' }, { value: 'opentofu', label: 'OpenTofu' }], 'terraform'),
      field('Nazwa VM', 'vm_name', { required: true }), field('Węzeł', 'node', { required: true }), field('Template VMID', 'template_id', { type: 'number', min: 100, required: true }), field('Węzeł template (opcjonalnie)', 'template_node'),
      field('CPU', 'cpu', { type: 'number', min: 1, max: 128, value: 2, required: true }), field('RAM MiB', 'memory', { type: 'number', min: 512, value: 4096, required: true }),
      field('Dysk GiB', 'disk', { type: 'number', min: 1, value: 40, required: true }), field('Storage', 'storage', { required: true }),
      field('Bridge', 'network', { value: 'vmbr0', required: true }), field('VLAN ID (opcjonalnie)', 'vlan_id', { type: 'number', min: 1, max: 4094 }),
      field('Użytkownik SSH', 'ssh_username', { value: 'clouduser', required: true }), field('Klucz publiczny SSH (opcjonalnie)', 'ssh_public_key', { tag: 'textarea', wide: true }));
    openModal({ title: 'Nowy deployment', eyebrow: 'Terraform', body: fields, submitLabel: 'Utwórz i uruchom', onSubmit: async data => {
      const variables = { name: data.get('vm_name'), node: data.get('node'), template_id: Number(data.get('template_id')), cpu: Number(data.get('cpu')), memory: Number(data.get('memory')), disk: Number(data.get('disk')), network: data.get('network'), storage: data.get('storage'), ssh_username: data.get('ssh_username') };
      if (data.get('template_node')) variables.template_node = data.get('template_node');
      if (data.get('vlan_id')) variables.vlan_id = Number(data.get('vlan_id'));
      if (data.get('ssh_public_key')) variables.ssh_public_key = data.get('ssh_public_key');
      await api('/deployments', { method: 'POST', idempotent: true, body: { name: data.get('name'), provider_id: Number(data.get('provider_id')), template: 'proxmox-vm', credentials_id: Number(data.get('credentials_id')), executor: data.get('executor'), variables } });
      toast('Deployment i zadanie apply zostały utworzone.'); navigate('deployments');
    }});
  } catch (error) { toast(error.message, 'error'); }
}

async function jobsView() {
  const jobs = (await api('/jobs?limit=200')).items;
  dom.content.replaceChildren(heading('Historia i bieżący stan wykonania. Logi są redagowane po stronie backendu.'),
    table([
      { label: 'ID', class: 'mono', value: item => short(item.id, 18) }, { label: 'Operacja', value: item => item.operation },
      { label: 'Status', value: item => badge(item.status, statusKind(item.status)) }, { label: 'Deployment', class: 'mono', value: item => short(item.deployment_id, 14) },
      { label: 'Utworzono', value: item => formatDate(item.created_at) }, { label: 'Błąd', value: item => node('span', { class: item.error ? 'form-error' : 'muted', text: item.error || '—' }) },
    ], jobs, item => {
      const actions = [button('Logi', () => showJobLogs(item))];
      if (allowed('jobs.cancel') && ['queued', 'running'].includes(item.status)) actions.push(button('Anuluj', () => confirmAction('Anuluj zadanie', `Zadanie ${short(item.id)} otrzyma żądanie anulowania.`, async () => { await api(`/jobs/${item.id}/cancel`, { method: 'POST' }); toast('Zadanie anulowane.'); navigate('jobs'); }), 'danger'));
      return actions;
    }));
}

async function showJobLogs(job) {
  dom.modalTitle.textContent = `Logi ${short(job.id, 18)}`;
  dom.modalEyebrow.textContent = job.operation;
  dom.modalBody.replaceChildren(node('div', { class: 'loading' }, node('div', { class: 'spinner' })));
  dom.modalActions.replaceChildren(button('Zamknij', closeModal));
  dom.modal.showModal();
  try {
    const result = await api(`/jobs/${job.id}/logs?limit=200`);
    const text = result.items.map(item => `[${formatDate(item.timestamp)}] ${item.message}`).join('\n') || 'Brak logów.';
    dom.modalBody.replaceChildren(node('div', { class: 'log-output mono', text }));
  } catch (error) { dom.modalBody.replaceChildren(node('p', { class: 'form-error', text: error.message })); }
}

async function auditView(requestId = '') {
  const result = await api(`/audit?limit=200${requestId ? `&request_id=${encodeURIComponent(requestId)}` : ''}`);
  const search = node('form', { class: 'action-group', onSubmit: event => { event.preventDefault(); auditView(event.currentTarget.elements.request_id.value.trim()); } }, field('Request ID', 'request_id', { value: requestId, placeholder: 'UUID korelacji' }), node('button', { class: 'button primary', type: 'submit' }, 'Filtruj'));
  dom.content.replaceChildren(heading('Niezmienna historia operacji bezpieczeństwa i infrastruktury.', [search]),
    table([
      { label: 'Czas', value: item => formatDate(item.timestamp) }, { label: 'Akcja', value: item => node('strong', { text: item.action }) },
      { label: 'Zasób', value: item => `${item.resource || '—'} ${item.resource_id || ''}` }, { label: 'Wynik', value: item => badge(item.result, item.result === 'success' ? 'ok' : 'danger') },
      { label: 'Użytkownik', value: item => item.user_id ?? '—' }, { label: 'Request ID', class: 'mono', value: item => short(item.request_id, 18) },
    ], result.items));
}

async function accountView() {
  const user = state.identity.user;
  const details = node('section', { class: 'panel' }, node('div', { class: 'panel-header' }, node('h2', { text: 'Tożsamość' }), badge(state.identity.token_type, 'info')),
    node('div', { class: 'checks' },
      info('Login', user.username), info('E-mail', user.email), info('Role', state.identity.roles.map(role => role.name).join(', ') || 'Brak'), info('Ostatnie logowanie', formatDate(user.last_login_at)),
    ));
  const passwordMessage = user.must_change_password
    ? 'Konto używa początkowego hasła admin. Zmień je, aby odblokować panel administracyjny.'
    : 'Zmiana hasła unieważnia wszystkie sesje i tokeny resetu. Po zapisaniu wymagane jest ponowne logowanie.';
  const security = node('section', { class: 'panel' }, node('div', { class: 'panel-header' }, node('h2', { text: 'Bezpieczeństwo konta' }), user.must_change_password ? badge('wymagana zmiana', 'warning') : ''), node('p', { class: user.must_change_password ? 'form-error' : 'muted', text: passwordMessage }), button(user.must_change_password ? 'Ustaw nowe hasło' : 'Zmień hasło', () => changePassword(user.must_change_password), 'primary'));
  dom.content.replaceChildren(heading('Twoja sesja i skuteczne uprawnienia.'), node('div', { class: 'panels' }, details, security), node('section', { class: 'panel' }, node('div', { class: 'panel-header' }, node('h2', { text: 'Permissions' })), node('p', { class: 'mono muted', text: state.identity.permissions.join(' · ') || 'Brak uprawnień' })));
}

function info(label, value) { return node('div', { class: 'check' }, node('span', { text: label }), node('strong', { text: value })); }

function changePassword(required = false) {
  const fields = node('div', { class: 'form-grid' }, field('Obecne hasło', 'current_password', { type: 'password', autocomplete: 'current-password', required: true, wide: true }), field('Nowe hasło', 'password', { type: 'password', autocomplete: 'new-password', required: true, minlength: 12 }), field('Powtórz nowe hasło', 'confirm', { type: 'password', autocomplete: 'new-password', required: true, minlength: 12 }));
  openModal({ title: required ? 'Zmień hasło początkowe' : 'Zmień hasło', eyebrow: 'Moje konto', body: fields, submitLabel: 'Zmień hasło', onSubmit: async data => {
    if (data.get('password') !== data.get('confirm')) throw new Error('Nowe hasła nie są identyczne.');
    await api('/auth/change-password', { method: 'POST', body: { current_password: data.get('current_password'), password: data.get('password') } });
    showLogin('Hasło zmienione. Zaloguj się ponownie.');
  }});
}

const views = {
  dashboard: dashboardView, users: usersView, roles: rolesView, tokens: tokensView,
  credentials: credentialsView, providers: providersView, deployments: deploymentsView,
  jobs: jobsView, audit: auditView, account: accountView,
};

function setApiStatus(ok) {
  dom.apiStatus.replaceChildren(node('span', { class: `status-dot ${ok ? 'ok' : 'bad'}` }), node('span', { text: ok ? 'API działa' : 'API zdegradowane' }));
}

dom.loginForm.addEventListener('submit', async event => {
  event.preventDefault();
  const submit = dom.loginForm.querySelector('button[type="submit"]');
  submit.disabled = true;
  dom.loginError.hidden = true;
  try {
    const data = new FormData(dom.loginForm);
    const pair = await api('/auth/login', { method: 'POST', auth: false, body: { username: data.get('username'), password: data.get('password') } }, false);
    saveSession(pair);
    state.identity = { user: pair.user, roles: pair.roles, permissions: pair.permissions, token_type: 'session' };
    showApp();
  } catch (error) {
    dom.loginError.textContent = error.message;
    dom.loginError.hidden = false;
  } finally { submit.disabled = false; }
});

document.querySelector('#reset-open').addEventListener('click', () => {
  const fields = node('div', { class: 'form-grid' }, field('Token resetu', 'token', { required: true, wide: true }), field('Nowe hasło', 'password', { type: 'password', required: true, minlength: 12 }), field('Powtórz hasło', 'confirm', { type: 'password', required: true, minlength: 12 }));
  openModal({ title: 'Ustaw nowe hasło', eyebrow: 'Reset hasła', body: fields, submitLabel: 'Zapisz hasło', onSubmit: async data => {
    if (data.get('password') !== data.get('confirm')) throw new Error('Hasła nie są identyczne.');
    await api('/auth/reset-password', { method: 'POST', auth: false, body: { token: data.get('token'), password: data.get('password') } }, false);
    dom.loginError.textContent = 'Hasło zostało zmienione. Możesz się zalogować.';
    dom.loginError.hidden = false;
  }});
});

document.querySelector('#logout').addEventListener('click', async () => {
  try { await api('/auth/logout', { method: 'POST' }); } catch { /* Local logout still clears the session. */ }
  showLogin('Wylogowano.');
});
document.querySelector('#menu-toggle').addEventListener('click', () => document.querySelector('.sidebar').classList.toggle('open'));
dom.modal.querySelector('header .icon-button').addEventListener('click', closeModal);
dom.modal.addEventListener('click', event => { if (event.target === dom.modal) closeModal(); });
window.addEventListener('hashchange', () => { if (!dom.appView.hidden && location.hash.slice(1) !== state.view) navigate(location.hash.slice(1)); });

(async function boot() {
  loadSession();
  if (!state.session?.access_token) return showLogin();
  try { state.identity = await api('/auth/me'); showApp(); }
  catch { showLogin('Sesja wygasła. Zaloguj się ponownie.'); }
})();
