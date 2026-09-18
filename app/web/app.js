'use strict';

const API = '/api/v1';
const SESSION_KEY = 'cloudportal.console.session';
const THEME_KEY = 'cloudportal.console.theme';
const state = { session: null, identity: null, view: 'dashboard', refreshPromise: null, consoleRfb: null };

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
  sidebar: document.querySelector('#sidebar'),
  sidebarBackdrop: document.querySelector('#sidebar-backdrop'),
  menuToggle: document.querySelector('#menu-toggle'),
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
  { id: 'catalog', label: 'Katalog IaC', icon: 'C', permission: 'terraform.read' },
  { id: 'blueprints', label: 'Blueprinty', icon: 'B', permission: 'blueprints.read' },
  { id: 'hostnames', label: 'Hostname Manager', icon: 'H', permission: 'hostnames.read' },
  { id: 'ipam', label: 'IPAM', icon: 'I', permission: 'ipam.read' },
  { id: 'inventory', label: 'Inventory', icon: 'V', permission: 'inventory.read' },
  { id: 'deployments', label: 'Deploymenty', icon: 'D', permission: 'deployments.read' },
  { id: 'jobs', label: 'Zadania', icon: 'J', permission: 'jobs.read' },
  { id: 'schedules', label: 'Harmonogramy', icon: 'S', permission: 'schedules.read' },
  { id: 'webhooks', label: 'Webhooki', icon: 'W', permission: 'webhooks.read' },
  { id: 'observability', label: 'Monitoring', icon: 'O', permission: 'metrics.read' },
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
  if (Array.isArray(detail)) {
    return detail.map(item => {
      const location = Array.isArray(item?.loc) ? item.loc.filter(part => part !== 'body').join('.') : '';
      const message = String(item?.msg || item || 'Błąd walidacji').replace(/^Value error,\s*/i, '');
      return location ? `${location}: ${message}` : message;
    }).join('; ');
  }
  if (typeof detail === 'string') return detail.replace(/^Value error,\s*/i, '');
  if (detail && typeof detail === 'object') return detail.message || JSON.stringify(detail);
  return 'Operacja nie powiodła się.';
}

function updateThemeControls() {
  const dark = document.documentElement.dataset.theme === 'dark';
  document.querySelectorAll('[data-theme-toggle]').forEach(control => {
    const label = dark ? 'Włącz jasny motyw' : 'Włącz ciemny motyw';
    control.textContent = dark ? '☀' : '☾';
    control.setAttribute('aria-label', label);
    control.setAttribute('title', label);
    control.setAttribute('aria-pressed', String(dark));
  });
}

function setTheme(theme, persist = true) {
  const selected = theme === 'dark' ? 'dark' : 'light';
  document.documentElement.dataset.theme = selected;
  if (persist) {
    try { localStorage.setItem(THEME_KEY, selected); } catch { /* Storage may be disabled. */ }
  }
  updateThemeControls();
}

function loadTheme() {
  let selected = 'light';
  try {
    const stored = localStorage.getItem(THEME_KEY);
    if (stored === 'light' || stored === 'dark') selected = stored;
  } catch { /* Use the light default when storage is unavailable. */ }
  setTheme(selected, false);
}

function toggleTheme() {
  setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
}

function setMobileMenu(open) {
  const active = Boolean(open);
  dom.sidebar.classList.toggle('open', active);
  dom.appView.classList.toggle('menu-open', active);
  dom.menuToggle.setAttribute('aria-expanded', String(active));
  dom.menuToggle.setAttribute('aria-label', active ? 'Zamknij menu' : 'Otwórz menu');
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
  const headers = { 'X-Request-ID': crypto.randomUUID(), 'X-Portal-Source': 'Cloudportal-backed', ...(options.headers || {}) };
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

async function apiText(path, canRefresh = true) {
  const headers = { 'X-Request-ID': crypto.randomUUID(), 'X-Portal-Source': 'Cloudportal-backed' };
  if (state.session?.access_token) headers.Authorization = `Bearer ${state.session.access_token}`;
  const response = await fetch(`${API}${path}`, { headers });
  if (response.status === 401 && canRefresh && state.session?.refresh_token) {
    await refreshSession();
    return apiText(path, false);
  }
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new ApiError(response.status, data);
  }
  return response.text();
}

function allowed(permission) {
  return !permission || Boolean(state.identity?.permissions?.includes(permission));
}

function toast(message, type = '') {
  const item = node('div', {
    class: `toast ${type}`,
    text: message,
    role: type === 'error' ? 'alert' : 'status',
    'aria-live': type === 'error' ? 'assertive' : 'polite',
  });
  dom.toastRegion.append(item);
  window.setTimeout(() => item.remove(), type === 'error' ? 12000 : 5000);
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

function closeModal() {
  if (state.consoleRfb) {
    try { state.consoleRfb.disconnect(); } catch { /* Session may already be disconnected. */ }
    state.consoleRfb = null;
  }
  dom.modal.classList.remove('modal-console', 'modal-wide');
  if (dom.modal.open) dom.modal.close();
}

function openModal({ title, eyebrow = 'Cloudportal', body, submitLabel, onSubmit, danger = false, wide = false }) {
  dom.modalTitle.textContent = title;
  dom.modal.classList.toggle('modal-wide', wide);
  dom.modalEyebrow.textContent = eyebrow;
  dom.modalBody.replaceChildren();
  dom.modalActions.replaceChildren();
  const form = node('form', { id: 'modal-form', class: 'stack' });
  const formError = node('div', { class: 'form-error modal-form-error', role: 'alert', hidden: true });
  form.append(formError, body);
  dom.modalBody.append(form);
  dom.modalActions.append(button('Anuluj', closeModal));
  if (onSubmit) {
    const submit = node('button', { type: 'submit', form: 'modal-form', class: `button ${danger ? 'danger' : 'primary'}` }, submitLabel || 'Zapisz');
    dom.modalActions.append(submit);
    form.addEventListener('submit', async event => {
      event.preventDefault();
      formError.hidden = true;
      formError.textContent = '';
      submit.disabled = true;
      try {
        const shouldClose = await onSubmit(new FormData(form), form);
        if (shouldClose !== false) closeModal();
      }
      catch (error) {
        const message = error?.message || 'Operacja nie powiodła się.';
        formError.textContent = message;
        formError.hidden = false;
        formError.scrollIntoView({ block: 'nearest' });
        toast(message, 'error');
      }
      finally { submit.disabled = false; }
    });
  }
  if (!dom.modal.open) dom.modal.showModal();
  window.setTimeout(() => form.querySelector('input,select,textarea')?.focus(), 30);
}

async function copyText(value) {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(value);
      return;
    } catch { /* Fall back for HTTP/insecure contexts or denied clipboard access. */ }
  }
  const fallback = node('textarea', { class: 'clipboard-fallback', 'aria-hidden': 'true', tabindex: '-1' });
  fallback.value = value;
  document.body.append(fallback);
  fallback.focus();
  fallback.select();
  const copied = document.execCommand('copy');
  fallback.remove();
  if (!copied) throw new Error('Przeglądarka zablokowała kopiowanie do schowka.');
}

function showSecret(title, value, note = 'Ta wartość jest wyświetlana tylko raz. Skopiuj ją teraz.') {
  dom.modal.classList.remove('modal-console', 'modal-wide');
  const copy = button('Kopiuj', async () => {
    try {
      await copyText(value);
      toast('Skopiowano do schowka.');
    } catch (error) {
      toast(error.message, 'error');
    }
  }, 'primary');
  dom.modalTitle.textContent = title;
  dom.modalEyebrow.textContent = 'Sekret jednorazowy';
  dom.modalBody.replaceChildren(node('div', { class: 'stack' }, node('p', { class: 'muted', text: note }), node('div', { class: 'secret-box mono', text: value })));
  dom.modalActions.replaceChildren(button('Zamknij', closeModal), copy);
  if (!dom.modal.open) dom.modal.showModal();
}

function confirmAction(title, message, action) {
  openModal({ title, eyebrow: 'Potwierdzenie', body: node('p', { text: message }), submitLabel: 'Potwierdź', danger: true, onSubmit: action });
}

function setLoginMessage(message = '', type = 'error') {
  dom.loginError.textContent = message;
  dom.loginError.hidden = !message;
  dom.loginError.classList.toggle('success', Boolean(message) && type === 'success');
}

function showLogin(message = '', type = 'error') {
  clearSession();
  setMobileMenu(false);
  dom.appView.hidden = true;
  dom.loginView.hidden = false;
  setLoginMessage(message, type);
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
  setMobileMenu(false);
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

const CREDENTIAL_TYPE_CONFIG = {
  proxmox: {
    label: 'Proxmox VE',
    description: 'Połączenie z API Proxmox VE. Adres może być samym IP/hostem albo jawnym URL HTTP/HTTPS.',
    endpoint: {
      label: 'Adres Proxmox (IP / host / URL)',
      placeholder: '192.168.1.10',
      required: true,
      help: 'Bez http:// lub https:// backend najpierw sprawdzi HTTPS, potem HTTP. Dla samego IP/hosta używany jest domyślny port 8006.',
    },
    username: { label: 'Użytkownik / realm', placeholder: 'root@pam', required: true },
    tls: true,
    defaultAuth: 'token',
    authModes: {
      token: { label: 'API token (zalecane)', fields: [
        { key: 'token_id', label: 'Token ID', placeholder: 'root@pam!cloudportal', required: true },
        { key: 'token_secret', label: 'Token secret', type: 'password', required: true, autocomplete: 'new-password' },
      ]},
      generate_token: { label: 'Login + hasło → wygeneruj token', createOnly: true, fields: [
        { key: 'password', label: 'Hasło Proxmox (nie będzie zapisane)', type: 'password', required: true, autocomplete: 'current-password' },
        { key: 'token_name', label: 'Nazwa nowego tokenu', value: 'cloudportal', placeholder: 'cloudportal', required: true },
      ]},
      password: { label: 'Użytkownik i hasło', fields: [
        { key: 'password', label: 'Hasło', type: 'password', required: true, autocomplete: 'new-password' },
      ]},
    },
  },
  vmware: {
    label: 'VMware vCenter',
    description: 'Połączenie z vCenter przez vSphere REST API.',
    endpoint: { label: 'Endpoint vCenter HTTPS', placeholder: 'https://vcenter.example.com', required: true },
    username: { label: 'Użytkownik', placeholder: 'administrator@vsphere.local', required: true },
    tls: true, defaultAuth: 'password',
    authModes: { password: { label: 'Login i hasło', fields: [
      { key: 'password', label: 'Hasło', type: 'password', required: true, autocomplete: 'new-password' },
    ]}},
  },
  ssh: {
    label: 'SSH / Linux',
    description: 'Credential do Ansible i SSH. Weryfikacja known_hosts jest obowiązkowa.',
    endpoint: { label: 'Endpoint SSH (opcjonalnie)', placeholder: 'ssh://server.example.com:22', required: false, help: 'Używany przez Testuj; workflow Ansible korzysta z inventory.' },
    username: { label: 'Użytkownik SSH', placeholder: 'clouduser', required: true },
    tls: false, defaultAuth: 'private_key',
    authModes: {
      private_key: { label: 'Klucz prywatny', fields: [
        { key: 'private_key', label: 'Klucz prywatny SSH', tag: 'textarea', required: true, wide: true, placeholder: '-----BEGIN OPENSSH PRIVATE KEY-----' },
        { key: 'known_hosts', label: 'known_hosts', tag: 'textarea', required: true, wide: true, placeholder: 'host.example.com ssh-ed25519 AAAA...' },
      ]},
      password: { label: 'Hasło', fields: [
        { key: 'password', label: 'Hasło SSH', type: 'password', required: true, autocomplete: 'new-password' },
        { key: 'known_hosts', label: 'known_hosts', tag: 'textarea', required: true, wide: true, placeholder: 'host.example.com ssh-ed25519 AAAA...' },
      ]},
    },
  },
  winrm: {
    label: 'WinRM / Windows',
    description: 'Credential do Windows przez WinRM/NTLM.',
    endpoint: { label: 'Endpoint WinRM HTTPS', placeholder: 'https://server.example.com:5986/wsman', required: true },
    username: { label: 'Użytkownik', placeholder: 'DOMAIN\\svc-cloudportal', required: true },
    tls: true, defaultAuth: 'password',
    authModes: { password: { label: 'Login i hasło', fields: [
      { key: 'password', label: 'Hasło', type: 'password', required: true, autocomplete: 'new-password' },
    ]}},
  },
  aws: {
    label: 'Amazon Web Services',
    description: 'Klucze IAM. Test wykonuje STS GetCallerIdentity.',
    endpoint: null, username: null, tls: false, defaultAuth: 'keys',
    authModes: { keys: { label: 'Access keys', fields: [
      { key: 'access_key_id', label: 'Access Key ID', placeholder: 'AKIA...', required: true },
      { key: 'secret_access_key', label: 'Secret Access Key', type: 'password', required: true, autocomplete: 'new-password' },
      { key: 'session_token', label: 'Session Token (opcjonalnie)', tag: 'textarea', wide: true },
    ]}},
  },
  azure: {
    label: 'Microsoft Azure',
    description: 'Service Principal / App Registration używany także przez Terraform.',
    endpoint: null, username: null, tls: false, defaultAuth: 'service_principal',
    authModes: { service_principal: { label: 'Service Principal', fields: [
      { key: 'tenant_id', label: 'Tenant ID', required: true },
      { key: 'client_id', label: 'Client ID', required: true },
      { key: 'client_secret', label: 'Client Secret', type: 'password', required: true, autocomplete: 'new-password' },
      { key: 'subscription_id', label: 'Subscription ID', required: true },
    ]}},
  },
  openstack: {
    label: 'OpenStack',
    description: 'Keystone v3 password auth z projektem.',
    endpoint: { label: 'Endpoint Keystone HTTPS', placeholder: 'https://openstack.example.com:5000/v3', required: true },
    username: { label: 'Użytkownik', placeholder: 'cloudportal', required: true },
    tls: true, defaultAuth: 'password',
    authModes: { password: { label: 'Password auth', fields: [
      { key: 'password', label: 'Hasło', type: 'password', required: true, autocomplete: 'new-password' },
      { key: 'project_name', label: 'Projekt', required: true },
      { key: 'domain_name', label: 'Domena', value: 'Default', placeholder: 'Default' },
    ]}},
  },
  other: {
    label: 'Inny sekret',
    description: 'Ogólny zaszyfrowany sekret bez adaptera testującego.',
    endpoint: { label: 'Endpoint HTTPS (opcjonalnie)', placeholder: 'https://service.example.com', required: false },
    username: { label: 'Użytkownik (opcjonalnie)', placeholder: 'service-user', required: false },
    tls: true, defaultAuth: 'secret',
    authModes: { secret: { label: 'Sekret', fields: [
      { key: 'secret', label: 'Sekret', type: 'password', required: true, autocomplete: 'new-password', wide: true },
    ]}},
  },
};

function credentialTypeChoices() {
  return Object.entries(CREDENTIAL_TYPE_CONFIG).map(([value, config]) => ({ value, label: config.label }));
}

function credentialTypeLabel(type) {
  return CREDENTIAL_TYPE_CONFIG[type]?.label || type;
}

function credentialSecretField(spec, enabled) {
  const wrapper = field(spec.label, 'secret_' + spec.key, {
    type: spec.type || 'text', tag: spec.tag, required: enabled && spec.required,
    value: enabled ? (spec.value || '') : '', placeholder: spec.placeholder,
    autocomplete: spec.autocomplete || 'off', wide: spec.wide, help: spec.help,
  });
  wrapper.classList.add('credential-secret-field');
  const input = wrapper.querySelector('input,textarea');
  input.dataset.secretKey = spec.key;
  input.disabled = !enabled;
  return wrapper;
}

function renderCredentialSecretFields(container, config, authMode, enabled) {
  const mode = config.authModes[authMode] || config.authModes[config.defaultAuth];
  container.replaceChildren(...mode.fields.map(spec => credentialSecretField(spec, enabled)));
}

function renderCredentialDynamic(container, type, item) {
  const config = CREDENTIAL_TYPE_CONFIG[type] || CREDENTIAL_TYPE_CONFIG.other;
  const sameType = Boolean(item && item.type === type);
  const mustReplace = Boolean(item && !sameType);
  const identity = node('div', { class: 'form-grid credential-identity' });
  if (config.endpoint) identity.append(field(config.endpoint.label, 'endpoint', {
    value: sameType ? item.endpoint : '', required: config.endpoint.required,
    placeholder: config.endpoint.placeholder, wide: true, help: config.endpoint.help,
  }));
  if (config.username) identity.append(field(config.username.label, 'username', {
    value: sameType ? item.username : '', required: config.username.required, placeholder: config.username.placeholder,
  }));
  if (config.tls) {
    identity.append(node('div', { class: 'credential-tls-control' },
      checkboxField('Akceptuj certyfikat self-signed / niezaufany', 'accept_untrusted_tls', sameType ? !item.verify_ssl : false),
      node('div', { class: 'field-help', text: 'Włączenie tej opcji wyłącza weryfikację CA i nazwy hosta dla tego credentiala.' })));
  }

  const typeCard = node('div', { class: 'credential-type-card' },
    node('div', {}, node('strong', { text: config.label }), badge(type, 'info')),
    node('p', { text: config.description }));
  const secretPanel = node('section', { class: 'credential-secret-panel' },
    node('div', { class: 'credential-secret-header' },
      node('div', {}, node('strong', { text: item ? 'Zapisany sekret' : 'Dane uwierzytelniające' }),
        node('p', { text: item ? 'Sekrety nie są odczytywane z backendu. Włącz wymianę tylko gdy chcesz je zastąpić.' : 'Pola są wysyłane wyłącznie przy zapisie.' })),
      badge(item?.configured ? 'configured' : 'new', item?.configured ? 'ok' : 'info')));

  let replaceInput = null;
  if (item) {
    const replace = checkboxField(mustReplace ? 'Zmiana typu wymaga nowych sekretów' : 'Zastąp zapisane sekrety', 'replace_secrets', mustReplace);
    replaceInput = replace.querySelector('input');
    if (mustReplace) replaceInput.disabled = true;
    secretPanel.append(replace);
  }
  const choices = Object.entries(config.authModes).filter(([, mode]) => !mode.createOnly || !item).map(([value, mode]) => ({ value, label: mode.label }));
  const authWrapper = selectField('Metoda uwierzytelnienia', 'auth_mode', choices, config.defaultAuth, { required: true, wide: true });
  const authSelect = authWrapper.querySelector('select');
  const secretGrid = node('div', { class: 'form-grid credential-secret-grid' });
  secretPanel.append(authWrapper, secretGrid);
  const refresh = () => {
    const enabled = !item || mustReplace || Boolean(replaceInput?.checked);
    authSelect.disabled = !enabled;
    renderCredentialSecretFields(secretGrid, config, authSelect.value || config.defaultAuth, enabled);
  };
  authSelect.addEventListener('change', refresh);
  replaceInput?.addEventListener('change', refresh);
  refresh();
  container.replaceChildren(typeCard, identity, secretPanel);
}

async function credentialsView() {
  const credentials = (await api('/credentials?limit=200')).items;
  const actions = allowed('credentials.create') ? [button('Dodaj credential', () => credentialForm(), 'primary')] : [];
  dom.content.replaceChildren(heading('Sekrety są szyfrowane i nigdy nie wracają do przeglądarki.', actions),
    table([
      { label: 'Nazwa', value: item => node('strong', { text: item.name }) },
      { label: 'Typ', value: item => badge(credentialTypeLabel(item.type), 'info') },
      { label: 'Endpoint', value: item => node('span', { class: 'mono', text: item.endpoint || 'zarządzany przez dostawcę' }) },
      { label: 'Użytkownik', value: item => item.username || '—' },
      { label: 'Sekret', value: item => item.configured ? badge('configured', 'ok') : badge('missing', 'danger') },
      { label: 'Wygasa', value: item => item.expires_at ? formatDate(item.expires_at) : '—' },
      { label: 'Rotacja', value: item => item.rotation_due_at ? formatDate(item.rotation_due_at) : '—' },
      { label: 'Sekret zmieniono', value: item => item.secret_updated_at ? formatDate(item.secret_updated_at) : '—' },
    ], credentials, item => credentialActions(item)));
}

function credentialActions(item) {
  const actions = [];
  if (allowed('credentials.test') && item.type !== 'other' && (item.type !== 'ssh' || item.endpoint)) actions.push(button('Testuj', async () => {
    try {
      const result = await api('/credentials/' + item.id + '/test', { method: 'POST' });
      toast('Połączenie działa' + (result.version ? ' (' + result.version + ')' : '') + '.');
    } catch (error) { toast(error.message, 'error'); }
  }));
  if (allowed('credentials.update')) actions.push(button('Edytuj', () => credentialForm(item)));
  if (allowed('credentials.delete')) actions.push(button('Usuń', () => confirmAction('Usuń credential', 'Credential ' + item.name + ' zostanie trwale usunięty.', async () => {
    await api('/credentials/' + item.id, { method: 'DELETE' }); toast('Credential usunięty.'); navigate('credentials');
  }), 'danger'));
  return actions;
}

function credentialForm(item = null) {
  const dynamic = node('div', { class: 'credential-dynamic wide' });
  const typeField = selectField('Typ', 'type', credentialTypeChoices(), item?.type || 'proxmox', { required: true });
  const fields = node('div', { class: 'form-grid' },
    field('Nazwa', 'name', { required: true, value: item?.name || '' }), typeField,
    field('Credential wygasa (opcjonalnie)', 'expires_at', { type: 'datetime-local', value: item?.expires_at ? new Date(item.expires_at).toISOString().slice(0, 16) : '' }),
    field('Rotacja wymagana do (opcjonalnie)', 'rotation_due_at', { type: 'datetime-local', value: item?.rotation_due_at ? new Date(item.rotation_due_at).toISOString().slice(0, 16) : '' }),
    dynamic);
  if (allowed('credentials.test')) fields.append(checkboxField('Po zapisaniu przetestuj połączenie', 'test_after_save', false));

  const typeSelect = typeField.querySelector('select');
  const render = () => renderCredentialDynamic(dynamic, typeSelect.value, item);
  typeSelect.addEventListener('change', render);
  render();

  openModal({
    title: item ? 'Edytuj credential: ' + item.name : 'Nowy credential',
    eyebrow: 'Sekrety infrastruktury', body: fields,
    submitLabel: item ? 'Zapisz zmiany' : 'Dodaj credential', wide: true,
    onSubmit: async (data, form) => {
      const type = data.get('type');
      const config = CREDENTIAL_TYPE_CONFIG[type] || CREDENTIAL_TYPE_CONFIG.other;
      const sameType = Boolean(item && item.type === type);
      const endpoint = form.elements.endpoint ? form.elements.endpoint.value : (sameType ? item.endpoint : '');
      const username = form.elements.username ? form.elements.username.value : (sameType ? item.username : '');
      const verifySsl = config.tls ? !data.has('accept_untrusted_tls') : (sameType ? item.verify_ssl : true);
      const replaceSecrets = !item || !sameType || data.has('replace_secrets');
      const identityChanged = Boolean(item && (item.type !== type || item.endpoint !== endpoint || item.username !== username || item.verify_ssl !== verifySsl));
      if (identityChanged && !replaceSecrets) throw new Error('Zmiana typu, endpointu, użytkownika lub TLS wymaga zastąpienia sekretów.');

      const payload = {
        name: data.get('name'), type, endpoint, username, verify_ssl: verifySsl,
        expires_at: data.get('expires_at') ? new Date(data.get('expires_at')).toISOString() : null,
        rotation_due_at: data.get('rotation_due_at') ? new Date(data.get('rotation_due_at')).toISOString() : null,
      };
      const authMode = form.elements.auth_mode?.value || config.defaultAuth;
      if (!item && type === 'proxmox' && authMode === 'generate_token') {
        const password = form.querySelector('[data-secret-key="password"]')?.value || '';
        const tokenName = form.querySelector('[data-secret-key="token_name"]')?.value || '';
        if (!password || !tokenName) throw new Error('Podaj hasło Proxmox i nazwę tokenu.');
        const saved = await api('/credentials/proxmox/bootstrap', { method: 'POST', body: {
          name: payload.name, endpoint, username, password, token_name: tokenName,
          verify_ssl: verifySsl, privilege_separation: false,
          expires_at: payload.expires_at, rotation_due_at: payload.rotation_due_at,
        }});
        if (data.has('test_after_save')) {
          try {
            const result = await api('/credentials/' + saved.id + '/test', { method: 'POST' });
            toast('Token Proxmox wygenerowany i zapisany. Test działa' + (result.version ? ' (' + result.version + ')' : '') + '.');
          } catch (error) { toast('Token wygenerowany i zapisany, ale test nie powiódł się: ' + error.message, 'error'); }
        } else toast('Token Proxmox wygenerowany i zapisany. Hasło nie zostało zachowane.');
        navigate('credentials');
        return;
      }
      if (replaceSecrets) {
        const secrets = {};
        form.querySelectorAll('[data-secret-key]').forEach(input => {
          if (!input.disabled && input.value !== '') secrets[input.dataset.secretKey] = input.value;
        });
        if (!Object.keys(secrets).length) throw new Error('Wprowadź wymagane dane uwierzytelniające.');
        payload.secrets = secrets;
      }
      const saved = await api(item ? '/credentials/' + item.id : '/credentials', { method: item ? 'PUT' : 'POST', body: payload });
      if (data.has('test_after_save') && type !== 'other' && (type !== 'ssh' || endpoint)) {
        try {
          const result = await api('/credentials/' + saved.id + '/test', { method: 'POST' });
          toast('Credential zapisany. Test działa' + (result.version ? ' (' + result.version + ')' : '') + '.');
        } catch (error) { toast('Credential zapisany, ale test nie powiódł się: ' + error.message, 'error'); }
      } else toast('Credential zapisany.');
      navigate('credentials');
    },
  });
}

async function providersView() {
  const providers = (await api('/providers?limit=200')).items;
  const actions = allowed('providers.create') ? [button('Dodaj provider', () => providerForm(), 'primary')] : [];
  dom.content.replaceChildren(heading('Providery wiążą zatwierdzony typ infrastruktury z konkretnym zaszyfrowanym credentialem.', actions),
    table([
      { label: 'Nazwa', value: item => node('strong', { text: item.name }) },
      { label: 'Typ', value: item => badge(item.type, 'info') },
      { label: 'Credential ID', value: item => item.credentials_id },
      { label: 'Aktualizacja', value: item => formatDate(item.updated_at) },
    ], providers, item => {
      const actions = [];
      actions.push(button('Discovery', () => discoverProvider(item)));
      if (allowed('providers.update')) actions.push(button('Edytuj', () => providerForm(item)));
      if (allowed('providers.delete')) actions.push(button('Usuń', () => confirmAction('Usuń provider', 'Provider ' + item.name + ' zostanie usunięty.', async () => {
        await api('/providers/' + item.id, { method: 'DELETE' });
        toast('Provider usunięty.');
        navigate('providers');
      }), 'danger'));
      return actions;
    }));
}

async function providerForm(item = null) {
  try {
    const credentials = (await api('/credentials?limit=200')).items;
    const providerTypes = ['proxmox', 'vmware', 'aws', 'azure', 'openstack'];
    const typeField = selectField('Typ providera', 'type', providerTypes.map(value => ({
      value, label: CREDENTIAL_TYPE_CONFIG[value]?.label || value,
    })), item?.type || 'proxmox', { required: true });
    const credentialField = selectField('Credential', 'credentials_id', [], item?.credentials_id || '', {
      required: true, placeholder: 'Wybierz credential tego samego typu',
    });
    const credentialSelect = credentialField.querySelector('select');
    const typeSelect = typeField.querySelector('select');

    const refreshCredentials = () => {
      const selectedType = typeSelect.value;
      const matching = credentials.filter(value => value.type === selectedType);
      const previous = String(item?.credentials_id || credentialSelect.value || '');
      credentialSelect.replaceChildren(node('option', { value: '', text: 'Wybierz credential' }));
      matching.forEach(value => credentialSelect.append(node('option', {
        value: value.id,
        text: value.name + ' (#' + value.id + ')',
        selected: String(value.id) === previous,
      })));
      if (!matching.length) credentialSelect.append(node('option', { value: '', text: 'Brak credentiali tego typu', disabled: true }));
    };
    typeSelect.addEventListener('change', refreshCredentials);
    refreshCredentials();

    const fields = node('div', { class: 'form-grid' },
      field('Nazwa', 'name', { required: true, value: item?.name || '' }),
      typeField,
      credentialField,
      node('div', { class: 'wide field-help', text: 'Provider i credential muszą mieć ten sam typ. Providery mają read-only discovery; provisioning wielochmurowy jest wykonywany przez zatwierdzony katalog Terraform.' })
    );

    openModal({
      title: item ? 'Edytuj provider' : 'Nowy provider',
      eyebrow: 'Infrastruktura',
      body: fields,
      onSubmit: async data => {
        if (!data.get('credentials_id')) throw new Error('Wybierz credential zgodny z typem providera.');
        await api(item ? '/providers/' + item.id : '/providers', {
          method: item ? 'PUT' : 'POST',
          body: {
            name: data.get('name'),
            type: data.get('type'),
            credentials_id: Number(data.get('credentials_id')),
          },
        });
        toast('Provider zapisany.');
        navigate('providers');
      },
    });
  } catch (error) { toast(error.message, 'error'); }
}

async function discoverProvider(provider) {
  const resources = [
    { value: 'vms', label: 'VM / instances' }, { value: 'templates', label: 'Templates / images' },
    { value: 'networks', label: 'Networks' }, { value: 'storages', label: 'Storage / volumes' },
    { value: 'nodes', label: 'Nodes / locations' }, { value: 'pools', label: 'Pools / groups' },
  ];
  const fields = node('div', { class: 'form-grid' },
    selectField('Zasób', 'resource', resources, provider.type === 'proxmox' ? 'nodes' : 'vms', { required: true }));
  if (provider.type === 'aws') fields.append(field('Region AWS', 'node', { value: 'eu-central-1', required: true, placeholder: 'eu-central-1' }));
  openModal({
    title: 'Discovery: ' + provider.name, eyebrow: provider.type, body: fields, submitLabel: 'Pobierz',
    onSubmit: async data => {
      const resource = data.get('resource');
      const scope = data.get('node');
      const suffix = scope ? '?node=' + encodeURIComponent(scope) : '';
      const rows = (await api('/providers/' + provider.id + '/' + resource + suffix)).items;
      const columns = Object.keys(rows[0] || { id: '' }).slice(0, 8).map(key => ({ label: key, value: row => String(row[key] ?? '—') }));
      dom.modalTitle.textContent = 'Zasoby: ' + provider.name;
      dom.modalEyebrow.textContent = resource + (scope ? ' / ' + scope : '');
      dom.modalBody.replaceChildren(rows.length ? table(columns, rows) : node('p', { class: 'muted', text: 'Brak zasobów.' }));
      dom.modalActions.replaceChildren(button('Zamknij', closeModal));
      return false;
    },
  });
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
    const [providers, credentials, templates] = await Promise.all([
      api('/providers?limit=200'), api('/credentials?limit=200'), api('/templates'),
    ]);
    const defaultVariables = {
      name: 'vm01', node: 'pve01', template_id: 9000, cpu: 2, memory: 4096,
      disk: 40, network: 'vmbr0', storage: 'local-lvm', ssh_username: 'clouduser',
    };
    const fields = node('div', { class: 'form-grid' },
      field('Nazwa deploymentu', 'name', { required: true }),
      selectField('Provider', 'provider_id', providers.items.map(item => ({ value: item.id, label: `${item.name} [${item.type}] (#${item.id})` })), '', { required: true, placeholder: 'Wybierz provider' }),
      selectField('Credential', 'credentials_id', credentials.items.map(item => ({ value: item.id, label: `${item.name} [${item.type}] (#${item.id})` })), '', { required: true, placeholder: 'Wybierz credential' }),
      selectField('Template', 'template', templates.items.map(item => ({ value: item.id, label: `${item.name} · v${item.version} [${item.provider}]` })), 'proxmox-vm', { required: true }),
      selectField('Executor', 'executor', [{ value: 'terraform', label: 'Terraform' }, { value: 'opentofu', label: 'OpenTofu' }], 'terraform'),
      field('Zmienne template JSON', 'variables', { tag: 'textarea', required: true, wide: true, value: jsonValue(defaultVariables), help: 'Schemat wymaganych pól sprawdzisz w zakładce Katalog IaC.' }));
    openModal({ title: 'Nowy deployment', eyebrow: 'Terraform / OpenTofu', body: fields, submitLabel: 'Utwórz i uruchom', onSubmit: async data => {
      await api('/deployments', { method: 'POST', idempotent: true, body: {
        name: data.get('name'),
        provider_id: Number(data.get('provider_id')),
        template: data.get('template'),
        credentials_id: Number(data.get('credentials_id')),
        executor: data.get('executor'),
        variables: parseObject(data.get('variables'), 'Zmienne template'),
      } });
      toast('Deployment i zadanie apply zostały utworzone.');
      navigate('deployments');
    }});
  } catch (error) { toast(error.message, 'error'); }
}

async function jobsView() {
  const jobs = (await api('/jobs?limit=200')).items;
  dom.content.replaceChildren(heading('Historia i bieżący stan wykonania. Logi są redagowane po stronie backendu.'),
    table([
      { label: 'ID', class: 'mono', value: item => short(item.id, 18) }, { label: 'Operacja', value: item => item.operation },
      { label: 'Status', value: item => badge(item.status, statusKind(item.status)) }, { label: 'Źródło', value: item => item.source }, { label: 'Deployment', class: 'mono', value: item => short(item.deployment_id, 14) },
      { label: 'Utworzono', value: item => formatDate(item.created_at) }, { label: 'Błąd', value: item => node('span', { class: item.error ? 'form-error' : 'muted', text: item.error || '—' }) },
    ], jobs, item => {
      const actions = [button('Logi', () => showJobLogs(item))];
      if (allowed('jobs.cancel') && ['queued', 'running'].includes(item.status)) actions.push(button('Anuluj', () => confirmAction('Anuluj zadanie', `Zadanie ${short(item.id)} otrzyma żądanie anulowania.`, async () => { await api(`/jobs/${item.id}/cancel`, { method: 'POST' }); toast('Zadanie anulowane.'); navigate('jobs'); }), 'danger'));
      if (allowed('jobs.execute') && ['failed', 'cancelled'].includes(item.status)) actions.push(button('Retry', async () => {
        await api(`/jobs/${item.id}/retry`, { method: 'POST', idempotent: true });
        toast('Utworzono retry job.');
        navigate('jobs');
      }));
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
      { label: 'Zasób', value: item => `${item.resource || '—'} ${item.resource_id || ''}` }, { label: 'Źródło', value: item => item.source }, { label: 'Wynik', value: item => badge(item.result, item.result === 'success' ? 'ok' : 'danger') },
      { label: 'Użytkownik', value: item => item.user_id ?? '—' }, { label: 'Request ID', class: 'mono', value: item => short(item.request_id, 18) },
    ], result.items));
}

async function blueprintsView() {
  const blueprints = (await api('/blueprints?limit=200')).items;
  const actions = allowed('blueprints.create') ? [button('Nowy Blueprint', () => blueprintForm(), 'primary')] : [];
  dom.content.replaceChildren(heading('Wersjonowane definicje self-service. DAG, formularz zmiennych i provisioning są wykonywane przez wspólną warstwę API.', actions),
    table([
      { label: 'Blueprint', value: item => node('div', {}, node('strong', { text: item.name }), node('div', { class: 'mono muted', text: `${item.slug} · v${item.version}` })) },
      { label: 'Status', value: item => badge(item.is_active ? 'active' : 'inactive', item.is_active ? 'ok' : 'danger') },
      { label: 'Widoczność', value: item => Object.entries(item.visibility).filter(([, value]) => value).map(([key]) => key).join(', ') || '—' },
      { label: 'Kroki', value: item => item.workflow.length },
      { label: 'Governance', value: item => node('div', { class: 'row-actions' }, item.requires_approval ? badge('approval', 'warning') : badge('standard', 'info'), item.recovery_policy === 'destroy_on_failure' ? badge('auto-cleanup', 'danger') : badge('preserve', 'info')) },
      { label: 'Aktualizacja', value: item => formatDate(item.updated_at) },
    ], blueprints, item => {
      const result = [];
      if (allowed('blueprints.execute') && (!item.requires_approval || allowed('blueprints.approve')) && item.is_active && item.visibility.backend) result.push(button('Uruchom', () => executeBlueprint(item), 'primary'));
      if (allowed('blueprints.update')) result.push(button('Edytuj', () => blueprintForm(item)));
      if (allowed('blueprints.delete')) result.push(button('Usuń', () => confirmAction('Usuń Blueprint', `Definicja ${item.name} zostanie usunięta. Istniejące deploymenty zachowają snapshot.`, async () => { await api(`/blueprints/${item.id}`, { method: 'DELETE' }); toast('Blueprint usunięty.'); navigate('blueprints'); }), 'danger'));
      return result;
    }));
}

function jsonValue(value) { return JSON.stringify(value, null, 2); }
function parseObject(value, label) {
  try { const parsed = JSON.parse(value); if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error(); return parsed; }
  catch { throw new Error(`${label} musi zawierać poprawny obiekt JSON.`); }
}
function parseArray(value, label) {
  try { const parsed = JSON.parse(value); if (!Array.isArray(parsed)) throw new Error(); return parsed; }
  catch { throw new Error(`${label} musi zawierać poprawną tablicę JSON.`); }
}

function blueprintForm(item = null) {
  const exampleVariables = { environment: { type: 'select', required: true, options: ['dev', 'test', 'prod'] }, cpu: { type: 'integer', default: 2, min: 1, max: 8 } };
  const exampleDeployment = { name: '{{ hostname }}', provider_id: 1, credentials_id: 1, hostname_scheme_id: 1, template: 'proxmox-vm', executor: 'terraform', variables: { name: '{{ hostname }}', node: 'pve', template_id: 9000, cpu: '{{ cpu }}', memory: 4096, disk: 40, network: 'vmbr0', storage: 'local-lvm', ssh_username: 'clouduser' } };
  const exampleWorkflow = [{ id: 'hostname', type: 'generate_hostname' }, { id: 'clone', type: 'clone_vm', depends_on: ['hostname'] }, { id: 'apply', type: 'terraform_apply', depends_on: ['clone'] }];
  const fields = node('div', { class: 'form-grid' },
    field('Slug', 'slug', { required: true, value: item?.slug || '' }), field('Nazwa', 'name', { required: true, value: item?.name || '' }),
    field('Opis', 'description', { tag: 'textarea', value: item?.description || '', wide: true }),
    checkboxField('Aktywny', 'is_active', item?.is_active ?? true), checkboxField('Dostępny w panelu backendu', 'visibility_backend', item?.visibility.backend ?? true),
    checkboxField('Dostępny w CloudPortal', 'visibility_cloudportal', item?.visibility.cloudportal ?? false), checkboxField('Dostępny przez API', 'visibility_api', item?.visibility.api ?? true),
    checkboxField('Wymaga blueprints.approve przy uruchomieniu', 'requires_approval', item?.requires_approval ?? false),
    selectField('Recovery policy', 'recovery_policy', [{ value: 'preserve', label: 'Preserve state/resources' }, { value: 'destroy_on_failure', label: 'Destroy on failed apply' }], item?.recovery_policy || 'preserve'),
    field('Dozwolone role ID (przecinki, puste = wszyscy)', 'role_ids', { value: (item?.allowed_role_ids || []).join(',') }),
    field('Dozwoleni użytkownicy ID (przecinki)', 'user_ids', { value: (item?.allowed_user_ids || []).join(',') }),
    field('Schemat zmiennych JSON', 'variables_schema', { tag: 'textarea', required: true, wide: true, value: jsonValue(item?.variables_schema || exampleVariables) }),
    field('Definicja deploymentu JSON', 'deployment', { tag: 'textarea', required: true, wide: true, value: jsonValue(item?.deployment || exampleDeployment) }),
    field('Workflow DAG JSON', 'workflow', { tag: 'textarea', required: true, wide: true, value: jsonValue(item?.workflow || exampleWorkflow) }));
  openModal({ title: item ? `Edytuj ${item.name}` : 'Nowy Blueprint', eyebrow: 'Automation Designer', body: fields, submitLabel: item ? 'Zapisz nową wersję' : 'Utwórz', onSubmit: async data => {
    const ids = value => value.split(',').map(part => part.trim()).filter(Boolean).map(Number);
    const payload = { slug: data.get('slug'), name: data.get('name'), description: data.get('description'), is_active: data.has('is_active'),
      visibility: { backend: data.has('visibility_backend'), cloudportal: data.has('visibility_cloudportal'), api: data.has('visibility_api') },
      allowed_role_ids: ids(data.get('role_ids')), allowed_user_ids: ids(data.get('user_ids')),
      variables_schema: parseObject(data.get('variables_schema'), 'Schemat zmiennych'), deployment: parseObject(data.get('deployment'), 'Deployment'), workflow: parseArray(data.get('workflow'), 'Workflow'),
      requires_approval: data.has('requires_approval'), recovery_policy: data.get('recovery_policy') };
    await api(item ? `/blueprints/${item.id}` : '/blueprints', { method: item ? 'PUT' : 'POST', body: payload });
    toast(item ? 'Utworzono nową wersję Blueprintu.' : 'Blueprint utworzony.'); navigate('blueprints');
  }});
}

function executeBlueprint(item) {
  const fields = node('div', { class: 'form-grid' });
  for (const [name, definition] of Object.entries(item.variables_schema)) {
    if (definition.type === 'select') fields.append(selectField(definition.label || name, name, definition.options.map(value => ({ value, label: value })), definition.default, { required: definition.required }));
    else if (definition.type === 'boolean') fields.append(checkboxField(definition.label || name, name, Boolean(definition.default)));
    else fields.append(field(definition.label || name, name, { type: definition.type === 'integer' ? 'number' : 'text', value: definition.default ?? '', min: definition.min, max: definition.max, required: definition.required }));
  }
  fields.append(field('Wartości hostname JSON', 'hostname_values', { tag: 'textarea', wide: true, value: '{}', help: 'Np. {"location":"wro","env":"prod","role":"web"}' }));
  openModal({ title: `Uruchom ${item.name}`, eyebrow: `Blueprint v${item.version}`, body: fields, submitLabel: 'Utwórz serwer', onSubmit: async (data, form) => {
    const variables = {};
    for (const [name, definition] of Object.entries(item.variables_schema)) {
      const control = form.elements[name];
      if (definition.type === 'boolean') variables[name] = control.checked;
      else if (control.value !== '') variables[name] = definition.type === 'integer' ? Number(control.value) : control.value;
    }
    const result = await api(`/blueprints/${item.id}/execute`, { method: 'POST', idempotent: true, body: { variables, hostname_values: parseObject(data.get('hostname_values') || '{}', 'Wartości hostname') } });
    toast(`Deployment ${result.name} utworzony. Zadanie ${short(result.job.id)}.`); navigate('jobs');
  }});
}

async function hostnamesView() {
  const [schemes, reservations] = await Promise.all([api('/hostname-schemes?limit=200'), api('/hostnames?limit=200')]);
  const actions = [];
  if (allowed('hostnames.create')) actions.push(button('Nowy schemat', hostnameSchemeForm, 'primary'));
  if (allowed('hostnames.reserve')) actions.push(button('Generuj hostname', () => generateHostname(schemes.items)));
  dom.content.replaceChildren(heading('Centralne generowanie nazw z blokadą sekwencji, wykrywaniem kolizji i historią rezerwacji.', actions),
    node('section', { class: 'panel' }, node('div', { class: 'panel-header' }, node('h2', { text: 'Schematy' })), table([
      { label: 'Nazwa', value: item => item.name }, { label: 'Wzorzec', value: item => node('span', { class: 'mono', text: item.pattern }) },
      { label: 'Następny numer', value: item => item.next_number }, { label: 'Status', value: item => badge(item.is_active ? 'active' : 'inactive', item.is_active ? 'ok' : 'danger') },
    ], schemes.items, item => allowed('hostnames.update') ? [button('Edytuj', () => hostnameSchemeForm(item))] : [])),
    node('section', { class: 'panel' }, node('div', { class: 'panel-header' }, node('h2', { text: 'Rezerwacje' })), table([
      { label: 'Hostname', value: item => node('strong', { class: 'mono', text: item.hostname }) }, { label: 'Status', value: item => badge(item.status, statusKind(item.status)) },
      { label: 'Zasób', value: item => short(item.resource_id, 18) }, { label: 'Utworzono', value: item => formatDate(item.created_at) },
    ], reservations.items, item => allowed('hostnames.release') && item.status !== 'released' ? [button('Zwolnij', () => confirmAction('Zwolnij hostname', `${item.hostname} będzie ponownie dostępny po wygaśnięciu historii kolizji.`, async () => { await api(`/hostnames/${item.id}/release`, { method: 'POST' }); navigate('hostnames'); }), 'danger')] : [])));
}

function hostnameSchemeForm(item = null) {
  const fields = node('div', { class: 'form-grid' }, field('Nazwa', 'name', { required: true, value: item?.name || '' }), field('Wzorzec', 'pattern', { required: true, value: item?.pattern || '{location}-{env}-{role}-{number}', wide: true }), field('Następny numer', 'next_number', { type: 'number', min: 1, value: item?.next_number || 1 }), field('Dopełnienie', 'padding', { type: 'number', min: 1, max: 9, value: item?.padding || 3 }), checkboxField('Aktywny', 'is_active', item?.is_active ?? true));
  openModal({ title: item ? 'Edytuj schemat hostname' : 'Nowy schemat hostname', eyebrow: 'Hostname Manager', body: fields, onSubmit: async data => {
    await api(item ? `/hostname-schemes/${item.id}` : '/hostname-schemes', { method: item ? 'PUT' : 'POST', body: { name: data.get('name'), pattern: data.get('pattern'), next_number: Number(data.get('next_number')), padding: Number(data.get('padding')), is_active: data.has('is_active') } });
    toast('Schemat hostname zapisany.'); navigate('hostnames');
  }});
}

function generateHostname(schemes) {
  const fields = node('div', { class: 'form-grid' }, selectField('Schemat', 'scheme_id', schemes.filter(item => item.is_active).map(item => ({ value: item.id, label: `${item.name} — ${item.pattern}` })), '', { required: true, placeholder: 'Wybierz schemat' }), field('Wartości JSON', 'values', { tag: 'textarea', wide: true, required: true, value: '{"location":"wro","env":"prod","role":"web"}' }), checkboxField('Zarezerwuj nazwę', 'reserve', true));
  openModal({ title: 'Generuj hostname', eyebrow: 'Hostname Manager', body: fields, submitLabel: 'Generuj', onSubmit: async data => {
    const result = await api('/hostnames/generate', { method: 'POST', body: { scheme_id: Number(data.get('scheme_id')), values: parseObject(data.get('values'), 'Wartości'), reserve: data.has('reserve') } });
    toast(`Wygenerowano ${result.hostname}.`); navigate('hostnames');
  }});
}


function showJson(title, value, eyebrow = 'Szczegóły') {
  dom.modalTitle.textContent = title;
  dom.modalEyebrow.textContent = eyebrow;
  dom.modalBody.replaceChildren(node('pre', { class: 'log-output mono', text: JSON.stringify(value, null, 2) }));
  dom.modalActions.replaceChildren(button('Zamknij', closeModal));
  if (!dom.modal.open) dom.modal.showModal();
}

function splitValues(value) {
  return String(value || '').split(/[\n,]+/).map(item => item.trim()).filter(Boolean);
}

async function catalogView() {
  const [templates, playbooks] = await Promise.all([api('/templates'), api('/ansible/playbooks')]);
  dom.content.replaceChildren(
    heading('Zatwierdzony, wersjonowany katalog IaC. API nie przyjmuje arbitralnego HCL ani dowolnych playbooków.'),
    node('section', { class: 'panel' },
      node('div', { class: 'panel-header' }, node('h2', { text: 'Terraform / OpenTofu templates' })),
      table([
        { label: 'Template', value: item => node('div', {}, node('strong', { text: item.name }), node('div', { class: 'mono muted', text: item.id })) },
        { label: 'Provider', value: item => badge(item.provider, 'info') },
        { label: 'Wersja', value: item => `v${item.version}` },
        { label: 'Import', value: item => badge(item.importable ? 'importable' : 'create-only', item.importable ? 'ok' : 'info') },
      ], templates.items, item => [button('Schema', () => showJson(`Schema ${item.id}`, item.variables_schema, `Template v${item.version}`))])
    ),
    node('section', { class: 'panel' },
      node('div', { class: 'panel-header' }, node('h2', { text: 'Approved Ansible playbooks' })),
      table([
        { label: 'Playbook', value: item => node('strong', { text: item.name }) },
        { label: 'ID', class: 'mono', value: item => item.id },
        { label: 'Transport', value: item => badge(item.transport, 'info') },
        { label: 'Zmienne', value: item => item.variables.join(', ') || '—' },
      ], playbooks.items)
    )
  );
}

async function ipamView() {
  const [pools, allocations] = await Promise.all([
    api('/ipam/pools?limit=200'),
    api('/ipam/allocations?limit=200'),
  ]);
  const actions = allowed('ipam.create') ? [button('Nowa pula', () => ipamPoolForm(), 'primary')] : [];
  dom.content.replaceChildren(
    heading('Centralne pule IPv4, rezerwacje i przypisania do deploymentów.', actions),
    node('section', { class: 'panel' },
      node('div', { class: 'panel-header' }, node('h2', { text: 'Pule adresowe' })),
      table([
        { label: 'Nazwa', value: item => node('strong', { text: item.name }) },
        { label: 'CIDR', class: 'mono', value: item => item.cidr },
        { label: 'Gateway', class: 'mono', value: item => item.gateway || '—' },
        { label: 'DNS', value: item => (item.dns_servers || []).join(', ') || '—' },
        { label: 'Status', value: item => badge(item.is_active ? 'active' : 'inactive', item.is_active ? 'ok' : 'danger') },
      ], pools.items, item => {
        const result = [];
        if (allowed('ipam.allocate') && item.is_active) result.push(button('Przydziel IP', () => allocateIp(item), 'primary'));
        if (allowed('ipam.update')) result.push(button('Edytuj', () => ipamPoolForm(item)));
        if (allowed('ipam.delete')) result.push(button('Usuń', () => confirmAction('Usuń pulę IPAM', `Pula ${item.name} zostanie usunięta tylko jeśli nie ma historii alokacji.`, async () => {
          await api(`/ipam/pools/${item.id}`, { method: 'DELETE' });
          navigate('ipam');
        }), 'danger'));
        return result;
      })
    ),
    node('section', { class: 'panel' },
      node('div', { class: 'panel-header' }, node('h2', { text: 'Alokacje' })),
      table([
        { label: 'Adres', class: 'mono', value: item => `${item.address}/${item.prefix_length}` },
        { label: 'Status', value: item => badge(item.status, statusKind(item.status)) },
        { label: 'Hostname', value: item => item.hostname || '—' },
        { label: 'Zasób', class: 'mono', value: item => short(item.resource_id, 18) },
        { label: 'Utworzono', value: item => formatDate(item.created_at) },
      ], allocations.items, item => allowed('ipam.release') && item.status !== 'released'
        ? [button('Zwolnij', () => confirmAction('Zwolnij adres', `${item.address} wróci do puli.`, async () => {
            await api(`/ipam/allocations/${item.id}/release`, { method: 'POST' });
            navigate('ipam');
          }), 'danger')]
        : [])
    )
  );
}

function ipamPoolForm(item = null) {
  const fields = node('div', { class: 'form-grid' },
    field('Nazwa', 'name', { required: true, value: item?.name || '' }),
    field('CIDR', 'cidr', { required: true, value: item?.cidr || '192.0.2.0/24' }),
    field('Gateway', 'gateway', { value: item?.gateway || '' }),
    field('DNS (przecinki lub nowe linie)', 'dns_servers', { tag: 'textarea', value: (item?.dns_servers || []).join('\n') }),
    field('Wykluczenia IP/CIDR', 'excluded_addresses', { tag: 'textarea', wide: true, value: (item?.excluded_addresses || []).join('\n') }),
    checkboxField('Aktywna', 'is_active', item?.is_active ?? true));
  openModal({ title: item ? 'Edytuj pulę IPAM' : 'Nowa pula IPAM', eyebrow: 'IPAM', body: fields, onSubmit: async data => {
    await api(item ? `/ipam/pools/${item.id}` : '/ipam/pools', {
      method: item ? 'PUT' : 'POST',
      body: {
        name: data.get('name'), cidr: data.get('cidr'), gateway: data.get('gateway') || null,
        dns_servers: splitValues(data.get('dns_servers')), excluded_addresses: splitValues(data.get('excluded_addresses')),
        is_active: data.has('is_active'),
      },
    });
    toast('Pula IPAM zapisana.');
    navigate('ipam');
  }});
}

function allocateIp(pool) {
  const fields = node('div', { class: 'form-grid' },
    field('Preferowany adres (opcjonalnie)', 'preferred_address'),
    field('Hostname (opcjonalnie)', 'hostname'));
  openModal({ title: `Przydziel IP z ${pool.name}`, eyebrow: pool.cidr, body: fields, submitLabel: 'Rezerwuj', onSubmit: async data => {
    const result = await api(`/ipam/pools/${pool.id}/allocate`, { method: 'POST', idempotent: true, body: {
      preferred_address: data.get('preferred_address') || null, hostname: data.get('hostname') || null,
    } });
    toast(`Zarezerwowano ${result.address}.`);
    navigate('ipam');
  }});
}

async function inventoryView() {
  const [vms, resources] = await Promise.all([
    api('/inventory/vms?refresh=true&limit=200'),
    api('/inventory/resources?limit=200'),
  ]);
  const actions = allowed('inventory.import') ? [button('Importuj istniejącą VM', importInventoryVm, 'primary')] : [];
  dom.content.replaceChildren(
    heading('Katalog zasobów odkrytych i zarządzanych przez Terraform. Adoption jest zawsze import + plan-only.', actions),
    node('section', { class: 'panel' },
      node('div', { class: 'panel-header' }, node('h2', { text: 'Proxmox VM inventory' })),
      table([
        { label: 'VM', value: item => node('div', {}, node('strong', { text: item.name || `VM ${item.vm_id}` }), node('div', { class: 'mono muted', text: `${item.node}/${item.vm_id}` })) },
        { label: 'Tryb', value: item => badge(item.management_mode, item.management_mode === 'terraform' ? 'ok' : 'info') },
        { label: 'Lifecycle', value: item => badge(item.lifecycle_status, statusKind(item.lifecycle_status)) },
        { label: 'Provider', value: item => `#${item.provider_id}` },
        { label: 'Live', value: item => item.live ? badge(item.live.status || 'present', statusKind(item.live.status)) : badge('missing', 'danger') },
      ], vms.items, item => inventoryVmActions(item))
    ),
    node('section', { class: 'panel' },
      node('div', { class: 'panel-header' }, node('h2', { text: 'Managed resources' })),
      table([
        { label: 'Nazwa', value: item => node('strong', { text: item.name }) },
        { label: 'Provider', value: item => badge(item.provider, 'info') },
        { label: 'External ID', class: 'mono', value: item => short(item.external_id, 26) },
        { label: 'IP', class: 'mono', value: item => item.primary_ip || '—' },
        { label: 'Status', value: item => badge(item.lifecycle_status, statusKind(item.lifecycle_status)) },
        { label: 'Deployment', class: 'mono', value: item => short(item.deployment_id, 18) },
      ], resources.items, item => [button('Metadata', () => showJson(item.name, item.metadata_json || {}, 'Managed resource'))])
    )
  );
}

function inventoryVmActions(item) {
  const actions = [];
  if (allowed('vms.read') && item.lifecycle_status === 'active') actions.push(button('VM', () => openVmManager(item), 'primary'));
  if (allowed('inventory.update')) actions.push(button('Reconcile', async () => {
    await api(`/inventory/vms/${item.id}/reconcile`, { method: 'POST' });
    toast('Inventory odświeżone.');
    navigate('inventory');
  }));
  if (allowed('deployments.adopt') && item.management_mode === 'external' && item.lifecycle_status === 'active' && !item.deployment_id) actions.push(button('Adopt', () => adoptInventoryVm(item)));
  if (allowed('inventory.delete') && (item.management_mode !== 'terraform' || item.lifecycle_status === 'destroyed')) actions.push(button('Unmanage', () => confirmAction('Usuń z inventory', 'Zasób nie zostanie usunięty z providera.', async () => {
    await api(`/inventory/vms/${item.id}`, { method: 'DELETE' });
    navigate('inventory');
  }), 'danger'));
  return actions;
}

async function importInventoryVm() {
  const providers = (await api('/providers?limit=200')).items.filter(item => item.type === 'proxmox');
  const fields = node('div', { class: 'form-grid' },
    selectField('Provider Proxmox', 'provider_id', providers.map(item => ({ value: item.id, label: `${item.name} (#${item.id})` })), '', { required: true, placeholder: 'Wybierz provider' }),
    field('VMID', 'vm_id', { type: 'number', min: 100, required: true }));
  openModal({ title: 'Importuj istniejącą VM', eyebrow: 'Inventory', body: fields, submitLabel: 'Dodaj do katalogu', onSubmit: async data => {
    await api('/inventory/vms/import', { method: 'POST', idempotent: true, body: {
      provider_id: Number(data.get('provider_id')), vm_id: Number(data.get('vm_id')),
    } });
    toast('VM dodana jako external.');
    navigate('inventory');
  }});
}

async function adoptInventoryVm(item) {
  try {
    const preview = await api(`/inventory/vms/${item.id}/adoption-preview`);
    const fields = node('div', { class: 'form-grid' },
      field('Template', 'template', { value: preview.template.id, required: true }),
      selectField('Executor', 'executor', [{ value: 'terraform', label: 'Terraform' }, { value: 'opentofu', label: 'OpenTofu' }], 'terraform'),
      field('Desired variables JSON', 'variables', { tag: 'textarea', wide: true, required: true, value: jsonValue(preview.suggested_variables), help: 'Import wykona wyłącznie plan. Apply wymaga osobnej decyzji po analizie driftu.' }),
      field('Live config', 'live', { tag: 'textarea', wide: true, value: jsonValue(preview.live) }));
    fields.querySelector('[name="live"]').disabled = true;
    openModal({ title: `Adopt ${item.name || item.vm_id}`, eyebrow: 'Terraform import + plan only', body: fields, submitLabel: 'Importuj state i wykonaj plan', onSubmit: async data => {
      const result = await api(`/inventory/vms/${item.id}/adopt`, { method: 'POST', idempotent: true, body: {
        template: data.get('template'), executor: data.get('executor'), variables: parseObject(data.get('variables'), 'Desired variables'),
      } });
      toast(`Utworzono import job ${short(result.job.id)}.`);
      navigate('jobs');
    }});
  } catch (error) { toast(error.message, 'error'); }
}

function vmBase(item) {
  return `/providers/${item.provider_id}/vms/${encodeURIComponent(item.node)}/${item.vm_id}`;
}

async function openVmManager(item) {
  try {
    const base = vmBase(item);
    const status = await api(`${base}/status`);
    const snapshots = allowed('snapshots.read') ? (await api(`${base}/snapshots`)).items : [];
    dom.modalTitle.textContent = item.name || `VM ${item.vm_id}`;
    dom.modalEyebrow.textContent = `${item.node} / VMID ${item.vm_id}`;
    const statusPanel = node('div', { class: 'checks' },
      info('Status', status.status || '—'), info('CPU', status.cpus ?? status.cpu ?? '—'),
      info('RAM', status.mem && status.maxmem ? `${Math.round(status.mem / 1024 / 1024)} / ${Math.round(status.maxmem / 1024 / 1024)} MiB` : '—'),
      info('Uptime', status.uptime ?? '—'));
    const snapshotTable = allowed('snapshots.read') ? table([
      { label: 'Snapshot', value: snap => snap.name },
      { label: 'Opis', value: snap => snap.description || '—' },
      { label: 'RAM', value: snap => snap.vmstate ? 'Tak' : 'Nie' },
    ], snapshots, snap => allowed('snapshots.delete') && snap.name !== 'current' ? [button('Usuń', () => confirmAction('Usuń snapshot', snap.name, async () => {
      await api(`${base}/snapshots/${encodeURIComponent(snap.name)}`, { method: 'DELETE', idempotent: true });
      openVmManager(item);
    }), 'danger'), ...(allowed('snapshots.rollback') ? [button('Rollback', () => confirmAction('Rollback snapshot', `VM zostanie przywrócona do ${snap.name}.`, async () => {
      await api(`${base}/snapshots/${encodeURIComponent(snap.name)}/rollback`, { method: 'POST', idempotent: true });
      toast('Rollback uruchomiony.');
      closeModal();
    }), 'danger')] : [])] : []) : node('p', { class: 'muted', text: 'Brak uprawnienia do snapshotów.' });
    dom.modalBody.replaceChildren(node('div', { class: 'stack' }, statusPanel, node('h3', { text: 'Snapshoty' }), snapshotTable));
    const actions = [button('Zamknij', closeModal)];
    if (allowed('vms.power')) {
      const powerActions = [
        ['start', 'Start'], ['shutdown', 'Shutdown'], ['reboot', 'Reboot'],
        ['suspend', 'Suspend'], ['resume', 'Resume'], ['reset', 'Reset'], ['stop', 'Stop'],
      ];
      for (const [action, label] of powerActions) {
        actions.push(button(label, () => vmPower(item, action), ['stop', 'reset'].includes(action) ? 'danger' : 'ghost'));
      }
    }
    if (allowed('snapshots.create')) actions.push(button('Snapshot', () => createVmSnapshot(item)));
    if (allowed('backups.create')) actions.push(button('Backup', () => backupVm(item)));
    if (allowed('backups.read')) actions.push(button('Backupy', () => listVmBackups(item)));
    if (allowed('vms.console')) actions.push(button('Konsola', () => showVmConsole(item), 'primary'));
    if (allowed('vms.update')) {
      actions.push(button('CPU/RAM', () => configureVm(item)));
      actions.push(button('Dysk +', () => resizeVmDisk(item)));
    }
    if (allowed('vms.migrate')) actions.push(button('Migracja', () => migrateVm(item)));
    if (allowed('vms.clone')) actions.push(button('Clone', () => cloneVm(item)));
    if (allowed('vms.template')) actions.push(button('→ Template', () => confirmAction('Konwertuj do template', 'Operacja zmieni VM w template.', async () => {
      await api(`${base}/template`, { method: 'POST', idempotent: true });
      closeModal(); toast('Konwersja uruchomiona.');
    })));
    if (allowed('vms.delete')) actions.push(button('Usuń VM', () => deleteVm(item), 'danger'));
    dom.modalActions.replaceChildren(...actions);
    if (!dom.modal.open) dom.modal.showModal();
  } catch (error) { toast(error.message, 'error'); }
}

async function vmPower(item, action) {
  await api(`${vmBase(item)}/power`, { method: 'POST', idempotent: true, body: { action } });
  toast(`Polecenie ${action} wysłane.`);
  closeModal();
}

function deleteVm(item) {
  const fields = node('div', { class: 'form-grid' },
    node('p', { class: 'wide field-help', text: 'Operacja usuwa VM bezpośrednio w Proxmox. Dla zasobów zarządzanych przez Terraform używaj Destroy deploymentu.' }),
    checkboxField('Purge z konfiguracji HA/backup/replication', 'purge'),
    checkboxField('Usuń niepodpięte dyski', 'destroy_unreferenced_disks'));
  openModal({
    title: 'Usuń VM', eyebrow: `${item.node} / VMID ${item.vm_id}`, body: fields,
    submitLabel: 'Usuń VM', danger: true,
    onSubmit: async data => {
      const query = new URLSearchParams({
        purge: data.has('purge') ? 'true' : 'false',
        destroy_unreferenced_disks: data.has('destroy_unreferenced_disks') ? 'true' : 'false',
      });
      await api(`${vmBase(item)}?${query.toString()}`, { method: 'DELETE', idempotent: true });
      closeModal();
      toast('Usuwanie VM uruchomione.');
      navigate('inventory');
    },
  });
}

function createVmSnapshot(item) {
  const fields = node('div', { class: 'form-grid' },
    field('Nazwa snapshotu', 'snapname', { required: true }),
    field('Opis', 'description', { wide: true }),
    checkboxField('Dołącz stan RAM', 'include_ram'));
  openModal({ title: 'Nowy snapshot', eyebrow: item.name || String(item.vm_id), body: fields, onSubmit: async data => {
    await api(`${vmBase(item)}/snapshots`, { method: 'POST', idempotent: true, body: {
      snapname: data.get('snapname'), description: data.get('description'), include_ram: data.has('include_ram'),
    } });
    toast('Snapshot uruchomiony.');
  }});
}

function backupVm(item) {
  const fields = node('div', { class: 'form-grid' },
    field('Storage backup', 'storage', { required: true }),
    selectField('Tryb', 'mode', [{ value: 'snapshot', label: 'snapshot' }, { value: 'suspend', label: 'suspend' }, { value: 'stop', label: 'stop' }], 'snapshot'),
    selectField('Kompresja', 'compress', [{ value: 'zstd', label: 'zstd' }, { value: 'lzo', label: 'lzo' }, { value: 'gzip', label: 'gzip' }], 'zstd'),
    field('Notatka', 'notes', { wide: true }));
  openModal({ title: 'Backup VM', eyebrow: item.name || String(item.vm_id), body: fields, submitLabel: 'Uruchom backup', onSubmit: async data => {
    await api(`${vmBase(item)}/backups`, { method: 'POST', idempotent: true, body: {
      storage: data.get('storage'), mode: data.get('mode'), compress: data.get('compress'), notes: data.get('notes') || null,
    } });
    toast('Backup uruchomiony.');
  }});
}

function listVmBackups(item) {
  openModal({ title: 'Pokaż backupy', eyebrow: item.name || String(item.vm_id), body: field('Storage backup', 'storage', { required: true }), submitLabel: 'Pokaż', onSubmit: async data => {
    const storage = data.get('storage');
    const result = await api(`${vmBase(item)}/backups?storage=${encodeURIComponent(storage)}`);
    dom.modalTitle.textContent = 'Backupy VM';
    dom.modalEyebrow.textContent = storage;
    const rows = result.items || [];
    const backupTable = table([
      { label: 'Volume', value: backup => node('span', { class: 'mono', text: backup.volid || '—' }) },
      { label: 'Format', value: backup => backup.format || '—' },
      { label: 'Rozmiar', value: backup => backup.size ? String(backup.size) : '—' },
      { label: 'Utworzono', value: backup => backup.ctime ? new Date(backup.ctime * 1000).toLocaleString() : '—' },
      { label: 'Protected', value: backup => backup.protected ? badge('Tak', 'ok') : 'Nie' },
    ], rows, backup => allowed('backups.restore') ? [button('Restore', () => restoreVmFromBackup(item, backup), 'primary')] : []);
    dom.modalBody.replaceChildren(rows.length ? backupTable : node('p', { class: 'muted', text: 'Brak backupów dla tej VM.' }));
    dom.modalActions.replaceChildren(button('Zamknij', closeModal));
    return false;
  }});
}

function restoreVmFromBackup(item, backup) {
  if (!backup?.volid) { toast('Backup nie ma identyfikatora volume.', 'error'); return; }
  const fields = node('div', { class: 'form-grid' },
    field('Backup volume', 'archive', { value: backup.volid, required: true, wide: true }),
    field('Nowy VMID', 'vm_id', { type: 'number', min: 100, required: true }),
    field('Storage docelowy (opcjonalnie)', 'storage'),
    checkboxField('Nadaj unikalne parametry urządzeń / MAC', 'unique', true));
  fields.querySelector('[name="archive"]').readOnly = true;
  openModal({
    title: 'Restore VM z backupu', eyebrow: item.node, body: fields, submitLabel: 'Uruchom restore',
    onSubmit: async data => {
      const payload = {
        vm_id: Number(data.get('vm_id')),
        archive: backup.volid,
        unique: data.has('unique'),
      };
      if (data.get('storage')) payload.storage = data.get('storage');
      await api(`/providers/${item.provider_id}/restore/${encodeURIComponent(item.node)}`, {
        method: 'POST', idempotent: true, body: payload,
      });
      toast(`Restore VMID ${payload.vm_id} uruchomiony.`);
      closeModal();
    },
  });
}

async function showVmConsole(item) {
  try {
    closeModal();
    const result = await api(`${vmBase(item)}/console`, { method: 'POST' });
    if (result.mode !== 'novnc' || !result.rfb_module?.startsWith('/api/v1/') || !result.ws_path?.startsWith('/api/v1/')) {
      throw new Error('Backend nie zwrócił poprawnej sesji noVNC.');
    }

    const screen = node('div', { class: 'novnc-screen' });
    const status = node('p', { class: 'muted', text: 'Łączenie z konsolą przez Cloudportal-backed…' });
    dom.modalTitle.textContent = 'Konsola noVNC';
    dom.modalEyebrow.textContent = item.name || `${item.node} / ${item.vm_id}`;
    dom.modalBody.replaceChildren(status, screen);
    dom.modalActions.replaceChildren(button('Zamknij', closeModal));
    dom.modal.classList.add('modal-console');
    dom.modal.showModal();

    const module = await import(result.rfb_module);
    const RFB = module.default;
    if (typeof RFB !== 'function') throw new Error('Moduł noVNC nie udostępnia klienta RFB.');
    const websocketScheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const rfb = new RFB(
      screen,
      `${websocketScheme}//${window.location.host}${result.ws_path}`,
      { credentials: { password: result.password } },
    );
    state.consoleRfb = rfb;
    rfb.scaleViewport = true;
    rfb.resizeSession = true;
    rfb.addEventListener('connect', () => { status.textContent = 'Połączono przez backend proxy.'; });
    rfb.addEventListener('disconnect', event => {
      if (state.consoleRfb === rfb) state.consoleRfb = null;
      status.textContent = event.detail?.clean ? 'Konsola rozłączona.' : 'Połączenie konsoli zostało przerwane.';
    });
    rfb.addEventListener('credentialsrequired', () => {
      rfb.sendCredentials({ password: result.password });
    });
  } catch (error) {
    state.consoleRfb = null;
    if (dom.modal.open) closeModal();
    toast(error.message, 'error');
  }
}

function configureVm(item) {
  const fields = node('div', { class: 'form-grid' },
    field('Nazwa (opcjonalnie)', 'name'),
    field('CPU cores', 'cores', { type: 'number', min: 1 }),
    field('RAM MiB', 'memory', { type: 'number', min: 512 }),
    field('Tagi', 'tags'),
    selectField('On boot', 'onboot', [{ value: '', label: 'bez zmiany' }, { value: 'true', label: 'włącz' }, { value: 'false', label: 'wyłącz' }], ''));
  openModal({ title: 'Konfiguracja VM', eyebrow: item.name || String(item.vm_id), body: fields, onSubmit: async data => {
    const payload = {};
    if (data.get('name')) payload.name = data.get('name');
    if (data.get('cores')) payload.cores = Number(data.get('cores'));
    if (data.get('memory')) payload.memory = Number(data.get('memory'));
    if (data.get('tags')) payload.tags = data.get('tags');
    if (data.get('onboot')) payload.onboot = data.get('onboot') === 'true';
    if (!Object.keys(payload).length) throw new Error('Podaj co najmniej jedną zmianę.');
    await api(`${vmBase(item)}/config`, { method: 'PUT', idempotent: true, body: payload });
    toast('Zmiana konfiguracji uruchomiona.');
  }});
}

function resizeVmDisk(item) {
  const fields = node('div', { class: 'form-grid' },
    field('Dysk', 'disk', { value: 'scsi0', required: true }),
    field('Powiększ o GiB', 'grow_gib', { type: 'number', min: 1, required: true }));
  openModal({ title: 'Powiększ dysk', eyebrow: item.name || String(item.vm_id), body: fields, onSubmit: async data => {
    await api(`${vmBase(item)}/disk`, { method: 'PUT', idempotent: true, body: { disk: data.get('disk'), grow_gib: Number(data.get('grow_gib')) } });
    toast('Resize uruchomiony.');
  }});
}

function migrateVm(item) {
  const fields = node('div', { class: 'form-grid' },
    field('Docelowy node', 'target', { required: true }),
    checkboxField('Online migration', 'online'),
    checkboxField('Przenieś lokalne dyski', 'with_local_disks'));
  openModal({ title: 'Migracja VM', eyebrow: item.name || String(item.vm_id), body: fields, onSubmit: async data => {
    await api(`${vmBase(item)}/migrate`, { method: 'POST', idempotent: true, body: {
      target: data.get('target'), online: data.has('online'), with_local_disks: data.has('with_local_disks'),
    } });
    toast('Migracja uruchomiona.');
  }});
}

function cloneVm(item) {
  const fields = node('div', { class: 'form-grid' },
    field('Nowy VMID', 'new_vm_id', { type: 'number', min: 100, required: true }),
    field('Nazwa', 'name', { required: true }),
    field('Docelowy node', 'target'),
    field('Storage', 'storage'),
    field('Pool', 'pool'),
    checkboxField('Full clone', 'full', true));
  openModal({ title: 'Clone VM', eyebrow: item.name || String(item.vm_id), body: fields, onSubmit: async data => {
    await api(`${vmBase(item)}/clone`, { method: 'POST', idempotent: true, body: {
      new_vm_id: Number(data.get('new_vm_id')), name: data.get('name'), target: data.get('target') || null,
      storage: data.get('storage') || null, pool: data.get('pool') || null, full: data.has('full'),
    } });
    toast('Clone uruchomiony.');
  }});
}

async function schedulesView() {
  const schedules = (await api('/schedules?limit=200')).items;
  const actions = allowed('schedules.create') ? [button('Nowy harmonogram', () => scheduleForm(), 'primary')] : [];
  dom.content.replaceChildren(heading('Trwałe operacje Terraform uruchamiane przez dispatcher z ponowną kontrolą permissions.', actions),
    table([
      { label: 'Nazwa', value: item => node('strong', { text: item.name }) },
      { label: 'Operacja', value: item => item.operation },
      { label: 'Deployment', class: 'mono', value: item => short(item.deployment_id, 18) },
      { label: 'Następne', value: item => formatDate(item.next_run_at) },
      { label: 'Interwał', value: item => item.interval_seconds ? `${item.interval_seconds}s` : 'jednorazowo' },
      { label: 'Status', value: item => badge(item.is_active ? 'active' : 'disabled', item.is_active ? 'ok' : 'info') },
      { label: 'Błąd', value: item => item.last_error || '—' },
    ], schedules, item => {
      const result = [];
      if (allowed('schedules.update')) {
        result.push(button('Edytuj', () => scheduleForm(item)));
        if (item.is_active) result.push(button('Wyłącz', async () => { await api(`/schedules/${item.id}/disable`, { method: 'POST' }); navigate('schedules'); }));
      }
      if (allowed('schedules.delete')) result.push(button('Usuń', () => confirmAction('Usuń harmonogram', item.name, async () => {
        await api(`/schedules/${item.id}`, { method: 'DELETE' }); navigate('schedules');
      }), 'danger'));
      return result;
    }));
}

async function scheduleForm(item = null) {
  try {
    const deployments = (await api('/deployments?limit=200')).items.filter(row => row.status !== 'destroyed');
    const dateValue = item?.next_run_at ? new Date(item.next_run_at).toISOString().slice(0, 16) : new Date(Date.now() + 3600000).toISOString().slice(0, 16);
    const fields = node('div', { class: 'form-grid' },
      field('Nazwa', 'name', { required: true, value: item?.name || '' }),
      selectField('Deployment', 'deployment_id', deployments.map(row => ({ value: row.id, label: `${row.name} · ${short(row.id, 10)}` })), item?.deployment_id || '', { required: true, placeholder: 'Wybierz deployment' }),
      selectField('Operacja', 'operation', [{ value: 'terraform.plan', label: 'Plan' }, { value: 'terraform.apply', label: 'Apply' }, { value: 'terraform.destroy', label: 'Destroy' }], item?.operation || 'terraform.plan'),
      field('Następne uruchomienie', 'next_run_at', { type: 'datetime-local', required: true, value: dateValue }),
      field('Interwał sekund (puste = raz)', 'interval_seconds', { type: 'number', min: 60, value: item?.interval_seconds || '' }));
    openModal({ title: item ? 'Edytuj harmonogram' : 'Nowy harmonogram', eyebrow: 'Scheduler', body: fields, onSubmit: async data => {
      await api(item ? `/schedules/${item.id}` : '/schedules', { method: item ? 'PUT' : 'POST', body: {
        name: data.get('name'), deployment_id: data.get('deployment_id'), operation: data.get('operation'),
        next_run_at: new Date(data.get('next_run_at')).toISOString(),
        interval_seconds: data.get('interval_seconds') ? Number(data.get('interval_seconds')) : null,
      } });
      toast('Harmonogram zapisany.');
      navigate('schedules');
    }});
  } catch (error) { toast(error.message, 'error'); }
}

async function webhooksView() {
  const [hooks, deliveries] = await Promise.all([api('/webhooks?limit=200'), api('/webhook-deliveries?limit=200')]);
  const actions = allowed('webhooks.create') ? [button('Nowy webhook', () => webhookForm(), 'primary')] : [];
  dom.content.replaceChildren(heading('Podpisane HMAC dostawy HTTPS. Host musi znajdować się w allowliście backendu.', actions),
    node('section', { class: 'panel' },
      node('div', { class: 'panel-header' }, node('h2', { text: 'Endpointy' })),
      table([
        { label: 'Nazwa', value: item => node('strong', { text: item.name }) },
        { label: 'URL', class: 'mono', value: item => short(item.url, 48) },
        { label: 'Eventy', value: item => item.events.join(', ') },
        { label: 'Status', value: item => badge(item.is_active ? 'active' : 'inactive', item.is_active ? 'ok' : 'danger') },
      ], hooks.items, item => {
        const result = [];
        if (allowed('webhooks.update')) {
          result.push(button('Edytuj', () => webhookForm(item)));
          result.push(button('Rotuj sekret', async () => {
            const value = await api(`/webhooks/${item.id}/rotate-secret`, { method: 'POST' });
            showSecret('Nowy webhook secret', value.secret);
          }));
        }
        if (allowed('webhooks.delete')) result.push(button('Usuń', () => confirmAction('Usuń webhook', item.name, async () => {
          await api(`/webhooks/${item.id}`, { method: 'DELETE' }); navigate('webhooks');
        }), 'danger'));
        return result;
      })
    ),
    node('section', { class: 'panel' },
      node('div', { class: 'panel-header' }, node('h2', { text: 'Dostawy' })),
      table([
        { label: 'Event', value: item => item.event },
        { label: 'Zasób', class: 'mono', value: item => short(item.resource_id, 18) },
        { label: 'Status', value: item => badge(item.status, statusKind(item.status)) },
        { label: 'Próby', value: item => item.attempts },
        { label: 'Następna', value: item => formatDate(item.next_attempt_at) },
        { label: 'Błąd', value: item => item.last_error || '—' },
      ], deliveries.items)
    )
  );
}

function webhookForm(item = null) {
  const events = item?.events || ['job.successful', 'job.failed'];
  const fields = node('div', { class: 'form-grid' },
    field('Nazwa', 'name', { required: true, value: item?.name || '' }),
    field('HTTPS URL', 'url', { required: true, value: item?.url || '', wide: true }),
    checkboxField('job.successful', 'event_job_successful', events.includes('job.successful')),
    checkboxField('job.failed', 'event_job_failed', events.includes('job.failed')),
    checkboxField('job.cancelled', 'event_job_cancelled', events.includes('job.cancelled')),
    checkboxField('recovery.queued', 'event_recovery_queued', events.includes('recovery.queued')),
    checkboxField('recovery.successful', 'event_recovery_successful', events.includes('recovery.successful')),
    checkboxField('recovery.failed', 'event_recovery_failed', events.includes('recovery.failed')),
    checkboxField('system.alert', 'event_system_alert', events.includes('system.alert')),
    checkboxField('Aktywny', 'is_active', item?.is_active ?? true));
  openModal({ title: item ? 'Edytuj webhook' : 'Nowy webhook', eyebrow: 'Signed HMAC', body: fields, onSubmit: async data => {
    const selected = [];
    for (const event of ['job.successful', 'job.failed', 'job.cancelled', 'recovery.queued', 'recovery.successful', 'recovery.failed', 'system.alert']) {
      if (data.has('event_' + event.replace('.', '_'))) selected.push(event);
    }
    if (!selected.length) throw new Error('Wybierz co najmniej jeden event.');
    const result = await api(item ? `/webhooks/${item.id}` : '/webhooks', {
      method: item ? 'PUT' : 'POST', idempotent: !item, body: {
        name: data.get('name'), url: data.get('url'), events: selected, is_active: data.has('is_active'),
      },
    });
    if (!item && result.secret) {
      navigate('webhooks');
      showSecret('Webhook secret', result.secret);
    } else {
      toast('Webhook zapisany.');
      navigate('webhooks');
    }
    return item ? true : false;
  }});
}

async function observabilityView() {
  const [health, alerts, metricsText] = await Promise.all([
    api('/health', { auth: false, allow: [503] }),
    api('/alerts'),
    apiText('/metrics'),
  ]);
  const items = alerts.items || [];
  dom.content.replaceChildren(
    heading('Stan control plane, alerty oraz surowe metryki w formacie Prometheus.'),
    node('div', { class: 'metrics' },
      metric('Backend', health.status, 'health'),
      metric('Workers', `${health.checks.workers.online}/${health.checks.workers.expected}`, 'online/expected'),
      metric('Alerty', items.length, 'aktywne'),
      metric('Dispatcher', health.checks.dispatcher ? 'OK' : 'DOWN', 'heartbeat')),
    node('section', { class: 'panel' },
      node('div', { class: 'panel-header' }, node('h2', { text: 'Alerty' })),
      table([
        { label: 'Severity', value: item => badge(item.severity, item.severity === 'critical' ? 'danger' : 'warning') },
        { label: 'Kod', class: 'mono', value: item => item.code },
        { label: 'Zasób', class: 'mono', value: item => item.resource_id || '—' },
        { label: 'Opis', value: item => item.message },
      ], items)),
    node('section', { class: 'panel' },
      node('div', { class: 'panel-header' }, node('h2', { text: 'Prometheus exposition' }), badge('/api/v1/metrics', 'info')),
      node('pre', { class: 'log-output mono', text: metricsText }))
  );
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
    showLogin('Hasło zmienione. Zaloguj się ponownie.', 'success');
  }});
}

const views = {
  dashboard: dashboardView, users: usersView, roles: rolesView, tokens: tokensView,
  credentials: credentialsView, providers: providersView, catalog: catalogView,
  blueprints: blueprintsView, hostnames: hostnamesView, ipam: ipamView, inventory: inventoryView,
  deployments: deploymentsView, jobs: jobsView, schedules: schedulesView, webhooks: webhooksView,
  observability: observabilityView, audit: auditView, account: accountView,
};

function setApiStatus(ok) {
  dom.apiStatus.replaceChildren(node('span', { class: `status-dot ${ok ? 'ok' : 'bad'}` }), node('span', { text: ok ? 'API działa' : 'API zdegradowane' }));
}

dom.loginForm.addEventListener('submit', async event => {
  event.preventDefault();
  const submit = dom.loginForm.querySelector('button[type="submit"]');
  submit.disabled = true;
  setLoginMessage();
  try {
    const data = new FormData(dom.loginForm);
    const pair = await api('/auth/login', { method: 'POST', auth: false, body: { username: data.get('username'), password: data.get('password') } }, false);
    saveSession(pair);
    state.identity = { user: pair.user, roles: pair.roles, permissions: pair.permissions, token_type: 'session' };
    showApp();
  } catch (error) {
    setLoginMessage(error.message);
  } finally { submit.disabled = false; }
});

document.querySelector('#reset-open').addEventListener('click', () => {
  const fields = node('div', { class: 'form-grid' }, field('Token resetu', 'token', { required: true, wide: true }), field('Nowe hasło', 'password', { type: 'password', required: true, minlength: 12 }), field('Powtórz hasło', 'confirm', { type: 'password', required: true, minlength: 12 }));
  openModal({ title: 'Ustaw nowe hasło', eyebrow: 'Reset hasła', body: fields, submitLabel: 'Zapisz hasło', onSubmit: async data => {
    if (data.get('password') !== data.get('confirm')) throw new Error('Hasła nie są identyczne.');
    await api('/auth/reset-password', { method: 'POST', auth: false, body: { token: data.get('token'), password: data.get('password') } }, false);
    setLoginMessage('Hasło zostało zmienione. Możesz się zalogować.', 'success');
  }});
});

document.querySelector('#logout').addEventListener('click', async () => {
  try { await api('/auth/logout', { method: 'POST' }); } catch { /* Local logout still clears the session. */ }
  showLogin('Wylogowano.', 'success');
});
document.querySelectorAll('[data-theme-toggle]').forEach(control => control.addEventListener('click', toggleTheme));
dom.menuToggle.addEventListener('click', () => setMobileMenu(!dom.appView.classList.contains('menu-open')));
dom.sidebarBackdrop.addEventListener('click', () => setMobileMenu(false));
document.querySelector('#modal-close').addEventListener('click', closeModal);
dom.modal.addEventListener('cancel', event => {
  event.preventDefault();
  closeModal();
});
dom.modal.addEventListener('click', event => { if (event.target === dom.modal) closeModal(); });
window.addEventListener('resize', () => { if (window.innerWidth > 760) setMobileMenu(false); });
window.addEventListener('keydown', event => {
  if (event.key === 'Escape' && !dom.modal.open && dom.appView.classList.contains('menu-open')) setMobileMenu(false);
});
window.addEventListener('hashchange', () => { if (!dom.appView.hidden && location.hash.slice(1) !== state.view) navigate(location.hash.slice(1)); });

loadTheme();

(async function boot() {
  loadSession();
  if (!state.session?.access_token) return showLogin();
  try { state.identity = await api('/auth/me'); showApp(); }
  catch { showLogin('Sesja wygasła. Zaloguj się ponownie.'); }
})();
