'use strict';

const API = '/api/v1';
const SESSION_KEY = 'cloudportal.console.session';
const THEME_KEY = 'cloudportal.console.theme';
const state = { session: null, identity: null, view: 'dashboard', refreshPromise: null, consoleRfb: null, taskPollTimer: null, taskPollNonce: 0 };

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
  refreshView: document.querySelector('#refresh-view'),
  modal: document.querySelector('#modal'),
  modalTitle: document.querySelector('#modal-title'),
  modalEyebrow: document.querySelector('#modal-eyebrow'),
  modalBody: document.querySelector('#modal-body'),
  modalActions: document.querySelector('#modal-actions'),
  toastRegion: document.querySelector('#toast-region'),
};

const routes = [];
const views = Object.create(null);
const commands = Object.create(null);
const extensions = new Set();

function registerView(route, handler) {
  if (!route?.id || typeof handler !== 'function') throw new Error('Invalid UI feature registration');
  if (views[route.id]) throw new Error('Duplicate UI feature: ' + route.id);
  routes.push(Object.freeze({ order: 1000, ...route }));
  routes.sort((a, b) => a.order - b.order || a.id.localeCompare(b.id));
  views[route.id] = handler;
}

function registerCommand(name, handler) {
  if (!name || typeof handler !== 'function') throw new Error('Invalid UI command registration');
  if (commands[name]) throw new Error('Duplicate UI command: ' + name);
  commands[name] = handler;
}

function hasCommand(name) {
  return typeof commands[name] === 'function';
}

function runCommand(name, ...args) {
  if (!hasCommand(name)) throw new Error('UI command is not registered: ' + name);
  return commands[name](...args);
}

function registerExtension(name, initialize) {
  if (!name || typeof initialize !== 'function') throw new Error('Invalid UI extension registration');
  if (extensions.has(name)) throw new Error('Duplicate UI extension: ' + name);
  extensions.add(name);
  initialize();
}

function emitUiEvent(name, detail = {}) {
  document.dispatchEvent(new CustomEvent('cloudportal:' + name, { detail }));
}
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

const VALIDATION_FIELD_LABELS = {
  name: 'Nazwa', username: 'Użytkownik', password: 'Hasło', email: 'E-mail',
  endpoint: 'Adres', type: 'Typ', credentials_id: 'Dane dostępowe', provider_id: 'Platforma',
  template: 'Szablon', executor: 'Silnik IaC', variables: 'Parametry', operation: 'Operacja',
  deployment_id: 'Wdrożenie', next_run_at: 'Termin uruchomienia', interval_seconds: 'Interwał',
  pattern: 'Wzorzec', hostname: 'Hostname', resource_id: 'Zasób', address: 'Adres IP',
  preferred_address: 'Preferowany adres', cidr: 'CIDR', gateway: 'Brama', verify_ssl: 'Weryfikacja TLS',
  token_name: 'Nazwa tokenu', scopes: 'Zakres uprawnień', expires_at: 'Data wygaśnięcia',
};

function friendlyApiText(value) {
  let message = String(value || 'Operacja nie powiodła się.').replace(/^Value error,\s*/i, '');
  const exact = new Map([
    ['Field required', 'Pole jest wymagane.'],
    ['Resource not found', 'Nie znaleziono zasobu.'],
    ['Only a reserved hostname can be assigned', 'Można przypisać tylko zarezerwowany hostname.'],
    ['Only a reserved address can be assigned', 'Można przypisać tylko zarezerwowany adres IP.'],
    ['Address is already released', 'Adres IP został już zwolniony.'],
    ['Hostname is already released', 'Hostname został już zwolniony.'],
    ['Hostname scheme has reservation history; disable it instead', 'Schemat ma historię rezerwacji. Zamiast usuwać, wyłącz go.'],
    ['Provider has active deployments; create another provider', 'Platforma ma aktywne wdrożenia. Utwórz nowe połączenie zamiast zmieniać dane dostępowe.'],
    ['Provider has deployment history', 'Platforma ma historię wdrożeń i nie może zostać usunięta.'],
    ['Selected infrastructure provider does not match the Terraform template', 'Wybrana platforma nie pasuje do szablonu Terraform.'],
    ['Credential does not belong to the selected provider', 'Wybrane dane dostępowe nie należą do tej platformy.'],
    ['Missing execution or deployment permissions', 'Brak uprawnień wymaganych do wykonania tej operacji.'],
  ]);
  if (exact.has(message)) return exact.get(message);
  message = message
    .replace(/^String should have at least (\d+) characters?$/i, 'Wartość musi mieć co najmniej $1 znaków.')
    .replace(/^String should have at most (\d+) characters?$/i, 'Wartość może mieć maksymalnie $1 znaków.')
    .replace(/^Input should be greater than or equal to (.+)$/i, 'Wartość musi być większa lub równa $1.')
    .replace(/^Input should be less than or equal to (.+)$/i, 'Wartość musi być mniejsza lub równa $1.');
  return message;
}

function errorMessage(data) {
  const detail = data && data.detail;
  if (Array.isArray(detail)) {
    return detail.map(item => {
      const path = Array.isArray(item?.loc) ? item.loc.filter(part => part !== 'body') : [];
      const rawField = path.at(-1);
      const location = rawField ? (VALIDATION_FIELD_LABELS[rawField] || String(rawField).replaceAll('_', ' ')) : '';
      const message = friendlyApiText(item?.msg || item || 'Błąd walidacji');
      return location ? `${location}: ${message}` : message;
    }).join('; ');
  }
  if (typeof detail === 'string') return friendlyApiText(detail);
  if (detail && typeof detail === 'object') return friendlyApiText(detail.message || JSON.stringify(detail));
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
    role: type === 'error' ? 'alert' : 'status',
    'aria-live': type === 'error' ? 'assertive' : 'polite',
  },
  node('span', { class: 'toast-message', text: message }),
  node('button', {
    class: 'toast-close',
    type: 'button',
    'aria-label': 'Zamknij komunikat',
    title: 'Zamknij',
    onClick: () => item.remove(),
  }, '×'));
  dom.toastRegion.append(item);
  window.setTimeout(() => item.remove(), type === 'error' ? 12000 : 5000);
}

function formatDate(value) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? String(value) : new Intl.DateTimeFormat('pl-PL', { dateStyle: 'short', timeStyle: 'short' }).format(date);
}

function toDateTimeLocal(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return '';
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 16);
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

function searchable(value) {
  return String(value ?? '')
    .toLocaleLowerCase('pl-PL')
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '');
}

function tablePreferenceKey(columns) {
  const signature = [state.view, ...columns.map(column => column.label)].join('|');
  let hash = 2166136261;
  for (let index = 0; index < signature.length; index += 1) {
    hash ^= signature.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return `cloudportal.console.table.${(hash >>> 0).toString(16)}`;
}

function readTablePreferences(key) {
  try {
    const value = JSON.parse(localStorage.getItem(key) || '{}');
    return value && typeof value === 'object' ? value : {};
  } catch {
    return {};
  }
}

function writeTablePreferences(key, preferences) {
  try { localStorage.setItem(key, JSON.stringify(preferences)); } catch { /* Storage may be unavailable. */ }
}

function table(columns, rows, actions) {
  if (!rows.length) return node('div', { class: 'table-wrap' }, node('div', { class: 'empty', text: 'Brak danych do wyświetlenia.' }));

  const preferenceKey = tablePreferenceKey(columns);
  const saved = readTablePreferences(preferenceKey);
  const hiddenColumns = new Set(
    Array.isArray(saved.hidden_columns)
      ? saved.hidden_columns.filter(index => Number.isInteger(index) && index >= 0 && index < columns.length)
      : []
  );
  let sortIndex = Number.isInteger(saved.sort_index) && saved.sort_index >= 0 && saved.sort_index < columns.length
    ? saved.sort_index : null;
  let sortDirection = saved.sort_direction === 'desc' ? 'desc' : 'asc';
  let pageSize = [10, 25, 50, 100, 0].includes(Number(saved.page_size)) ? Number(saved.page_size) : 25;
  let density = saved.density === 'compact' ? 'compact' : 'normal';
  let currentPage = 1;

  const head = node('tr');
  const headerCells = [];
  columns.forEach((column, columnIndex) => {
    const sortButton = node('button', {
      class: 'table-sort-button',
      type: 'button',
      disabled: column.sortable === false,
      onClick: () => {
        if (column.sortable === false) return;
        if (sortIndex === columnIndex) sortDirection = sortDirection === 'asc' ? 'desc' : 'asc';
        else {
          sortIndex = columnIndex;
          sortDirection = 'asc';
        }
        currentPage = 1;
        persist();
        apply();
      },
    },
    node('span', { text: column.label }),
    node('span', { class: 'table-sort-indicator', 'aria-hidden': 'true', text: '↕' }));
    const th = node('th', { 'data-column-index': columnIndex }, sortButton);
    th.hidden = hiddenColumns.has(columnIndex);
    headerCells.push(th);
    head.append(th);
  });
  if (actions) head.append(node('th', { class: 'table-actions-column', text: 'Akcje' }));

  const body = node('tbody');
  const records = rows.map((row, rowIndex) => {
    const tr = node('tr');
    const sortValues = [];
    const searchableValues = [];
    columns.forEach((column, columnIndex) => {
      const value = column.value(row);
      const cell = node('td', { class: column.class || '', 'data-column-index': columnIndex }, value instanceof Node ? value : String(value ?? '—'));
      cell.hidden = hiddenColumns.has(columnIndex);
      const textValue = cell.textContent?.trim() || String(value ?? '');
      sortValues.push(column.sortValue ? String(column.sortValue(row) ?? '') : textValue);
      searchableValues.push(textValue);
      tr.append(cell);
    });
    if (actions) tr.append(node('td', { class: 'table-actions-column' }, node('div', { class: 'row-actions' }, actions(row))));
    const raw = (() => { try { return JSON.stringify(row); } catch { return ''; } })();
    return {
      originalIndex: rowIndex,
      element: tr,
      search: searchable([raw, ...searchableValues].join(' ')),
      sortValues,
    };
  });
  records.forEach(record => body.append(record.element));

  const noResults = node('tr', { class: 'table-search-empty', hidden: true },
    node('td', { colspan: columns.length + (actions ? 1 : 0), class: 'empty', text: 'Brak wyników.' }));
  body.append(noResults);

  const tableElement = node('table', {}, node('thead', {}, head), body);
  const scroll = node('div', { class: 'table-scroll' }, tableElement);
  const wrapper = node('div', { class: `table-wrap advanced-table ${density === 'compact' ? 'compact' : ''}` });

  const search = node('input', {
    class: 'table-search',
    type: 'search',
    placeholder: 'Szukaj w tabeli…',
    'aria-label': 'Szukaj w tabeli',
  });
  const count = node('span', { class: 'table-count', 'aria-live': 'polite' });
  const pageInfo = node('span', { class: 'table-page-info', 'aria-live': 'polite' });
  const previousPage = button('←', () => { currentPage -= 1; apply(); }, 'ghost');
  const nextPage = button('→', () => { currentPage += 1; apply(); }, 'ghost');
  previousPage.setAttribute('aria-label', 'Poprzednia strona');
  nextPage.setAttribute('aria-label', 'Następna strona');

  const pageSizeSelect = node('select', { class: 'table-page-size', 'aria-label': 'Liczba wierszy na stronę' },
    ...[
      [10, '10 / strona'], [25, '25 / strona'], [50, '50 / strona'], [100, '100 / strona'], [0, 'Wszystkie'],
    ].map(([value, label]) => node('option', { value, text: label, selected: Number(value) === pageSize })));
  pageSizeSelect.addEventListener('change', () => {
    pageSize = Number(pageSizeSelect.value);
    currentPage = 1;
    persist();
    apply();
  });

  const densitySelect = node('select', { class: 'table-density', 'aria-label': 'Gęstość tabeli' },
    node('option', { value: 'normal', text: 'Normalna', selected: density === 'normal' }),
    node('option', { value: 'compact', text: 'Kompaktowa', selected: density === 'compact' }));
  densitySelect.addEventListener('change', () => {
    density = densitySelect.value === 'compact' ? 'compact' : 'normal';
    wrapper.classList.toggle('compact', density === 'compact');
    persist();
  });

  const columnPickerBody = node('div', { class: 'table-column-picker-body' });
  columns.forEach((column, columnIndex) => {
    const checkbox = node('input', { type: 'checkbox', checked: !hiddenColumns.has(columnIndex) });
    checkbox.addEventListener('change', () => {
      if (!checkbox.checked && columns.length - hiddenColumns.size <= 1) {
        checkbox.checked = true;
        toast('Co najmniej jedna kolumna musi pozostać widoczna.', 'error');
        return;
      }
      if (checkbox.checked) hiddenColumns.delete(columnIndex);
      else hiddenColumns.add(columnIndex);
      headerCells[columnIndex].hidden = hiddenColumns.has(columnIndex);
      records.forEach(record => {
        const cell = record.element.querySelector(`[data-column-index="${columnIndex}"]`);
        if (cell) cell.hidden = hiddenColumns.has(columnIndex);
      });
      persist();
    });
    columnPickerBody.append(node('label', { class: 'table-column-option' }, checkbox, node('span', { text: column.label })));
  });
  const columnPicker = node('details', { class: 'table-column-picker' },
    node('summary', { text: 'Kolumny' }),
    columnPickerBody);

  const pagination = node('div', { class: 'table-pagination' }, previousPage, pageInfo, nextPage);
  const toolbar = node('div', { class: 'table-toolbar' },
    node('div', { class: 'table-toolbar-main' }, search, count),
    node('div', { class: 'table-toolbar-controls' },
      densitySelect, pageSizeSelect, columnPicker));
  const footer = node('div', { class: 'table-footer' }, count.cloneNode(true), pagination);

  function persist() {
    writeTablePreferences(preferenceKey, {
      hidden_columns: [...hiddenColumns],
      sort_index: sortIndex,
      sort_direction: sortDirection,
      page_size: pageSize,
      density,
    });
  }

  function apply() {
    const phrase = searchable(search.value.trim());
    let filtered = records.filter(record => !phrase || record.search.includes(phrase));
    if (sortIndex !== null) {
      filtered = [...filtered].sort((left, right) => {
        const result = left.sortValues[sortIndex].localeCompare(right.sortValues[sortIndex], 'pl', {
          numeric: true, sensitivity: 'base',
        });
        return sortDirection === 'desc' ? -result : result;
      });
    } else {
      filtered = [...filtered].sort((left, right) => left.originalIndex - right.originalIndex);
    }

    records.forEach(record => { record.element.hidden = true; });
    filtered.forEach(record => body.append(record.element));
    body.append(noResults);

    const effectivePageSize = pageSize || Math.max(filtered.length, 1);
    const totalPages = Math.max(1, Math.ceil(filtered.length / effectivePageSize));
    currentPage = Math.max(1, Math.min(currentPage, totalPages));
    const start = (currentPage - 1) * effectivePageSize;
    const visible = filtered.slice(start, start + effectivePageSize);
    visible.forEach(record => { record.element.hidden = false; });

    noResults.hidden = filtered.length > 0;
    const visibleStart = filtered.length ? start + 1 : 0;
    const visibleEnd = filtered.length ? Math.min(start + effectivePageSize, filtered.length) : 0;
    count.textContent = phrase ? `${filtered.length} z ${rows.length} pozycji` : `${rows.length} pozycji`;
    footer.firstChild.textContent = filtered.length ? `${visibleStart}–${visibleEnd} z ${filtered.length}` : '0 pozycji';
    pageInfo.textContent = `${currentPage} / ${totalPages}`;
    previousPage.disabled = currentPage <= 1;
    nextPage.disabled = currentPage >= totalPages;
    pagination.hidden = totalPages <= 1;

    headerCells.forEach((th, index) => {
      const buttonElement = th.querySelector('.table-sort-button');
      const indicator = th.querySelector('.table-sort-indicator');
      const active = sortIndex === index;
      th.setAttribute('aria-sort', active ? (sortDirection === 'asc' ? 'ascending' : 'descending') : 'none');
      if (indicator) indicator.textContent = active ? (sortDirection === 'asc' ? '↑' : '↓') : '↕';
      buttonElement?.classList.toggle('active', active);
    });
  }

  search.addEventListener('input', () => {
    currentPage = 1;
    apply();
  });

  wrapper.append(toolbar, scroll, footer);
  apply();
  return wrapper;
}

function formFieldLabel(labelText, required = false) {
  return node('span', { class: 'field-label' },
    labelText,
    required ? node('span', { class: 'required-mark', 'aria-hidden': 'true', text: ' *' }) : null);
}

function field(labelText, name, options = {}) {
  const input = node(options.tag || 'input', {
    name, type: options.type || 'text', value: options.value ?? '', required: options.required,
    min: options.min, max: options.max, minlength: options.minlength, maxlength: options.maxlength, autocomplete: options.autocomplete,
    placeholder: options.placeholder, step: options.step,
  });
  if (options.tag === 'textarea') input.textContent = options.value ?? '';
  const label = node('label', {}, formFieldLabel(labelText, Boolean(options.required)), input);
  if (options.help) label.append(node('span', { class: 'field-help', text: options.help }));
  if (options.wide) label.classList.add('wide');
  return label;
}

function selectField(labelText, name, choices, value, options = {}) {
  const select = node('select', { name, required: options.required });
  if (options.placeholder) select.append(node('option', { value: '', text: options.placeholder }));
  choices.forEach(choice => select.append(node('option', { value: choice.value, text: choice.label, selected: String(choice.value) === String(value) })));
  const label = node('label', {}, formFieldLabel(labelText, Boolean(options.required)), select);
  if (options.wide) label.classList.add('wide');
  return label;
}

function checkboxField(labelText, name, checked = false) {
  return node('label', { class: 'checkbox' }, node('input', { type: 'checkbox', name, checked }), labelText);
}

function multiCheckboxField(labelText, name, choices, selectedValues = [], options = {}) {
  const selected = new Set((selectedValues || []).map(value => String(value)));
  const grid = node('div', { class: 'choice-grid' });
  choices.forEach(choice => grid.append(node('label', { class: 'choice-option' },
    node('input', { type: 'checkbox', name, value: choice.value, checked: selected.has(String(choice.value)) }),
    node('span', { text: choice.label }))));
  const wrapper = node('fieldset', { class: `choice-fieldset ${options.wide ? 'wide' : ''}` },
    node('legend', { text: labelText }),
    choices.length ? grid : node('p', { class: 'muted', text: options.empty || 'Brak dostępnych pozycji.' }));
  if (options.help) wrapper.append(node('span', { class: 'field-help', text: options.help }));
  return wrapper;
}

function formSection(title, description, ...children) {
  return node('section', { class: 'form-section wide' },
    node('div', { class: 'form-section-header' },
      node('h3', { text: title }),
      description ? node('p', { text: description }) : null),
    ...children);
}

const STATUS_LABELS = {
  ok: 'OK', active: 'Aktywny', inactive: 'Nieaktywny', configured: 'Skonfigurowany', locked: 'Zablokowany',
  revoked: 'Unieważniony', expired: 'Wygasły', queued: 'W kolejce', running: 'W trakcie',
  successful: 'Zakończony', failed: 'Błąd', cancelled: 'Anulowany', destroyed: 'Usunięty',
  degraded: 'Ograniczony', missing: 'Brak', present: 'Dostępny', defined: 'Zdefiniowany',
  released: 'Zwolniony', reserved: 'Zarezerwowany', assigned: 'Przypisany', disabled: 'Wyłączony',
  stopped: 'Zatrzymany', started: 'Uruchomiony', available: 'Dostępny', external: 'Zewnętrzny',
  terraform: 'Terraform', pending: 'Oczekuje', delivered: 'Dostarczony', retrying: 'Ponawianie',
  success: 'Sukces', failure: 'Błąd', critical: 'Krytyczny', warning: 'Ostrzeżenie', down: 'Niedostępny',
};

const OPERATION_LABELS = {
  'terraform.plan': 'Terraform: plan',
  'terraform.apply': 'Terraform: zastosuj',
  'terraform.destroy': 'Terraform: usuń',
  'terraform.import': 'Terraform: import',
  'ansible.execute': 'Ansible: wykonaj',
};

function statusLabel(value) {
  const key = String(value || '').toLowerCase();
  return STATUS_LABELS[key] || value || '—';
}

function operationLabel(value) {
  return OPERATION_LABELS[value] || value || '—';
}

const WEBHOOK_EVENT_LABELS = {
  'job.successful': 'Zadanie zakończone',
  'job.failed': 'Zadanie zakończone błędem',
  'job.cancelled': 'Zadanie anulowane',
  'recovery.queued': 'Odzyskiwanie dodane do kolejki',
  'recovery.successful': 'Odzyskiwanie zakończone',
  'recovery.failed': 'Odzyskiwanie zakończone błędem',
  'system.alert': 'Alert systemowy',
};

function webhookEventLabel(value) {
  return WEBHOOK_EVENT_LABELS[value] || value || '—';
}

const AUDIT_RESOURCE_LABELS = {
  users: 'Użytkownik', roles: 'Rola', tokens: 'Token', credentials: 'Dane dostępowe',
  providers: 'Platforma', deployments: 'Wdrożenie', jobs: 'Zadanie', blueprints: 'Blueprint',
  hostnames: 'Hostname', hostname_schemes: 'Schemat hostname', ip_pools: 'Pula IPAM',
  ip_allocations: 'Adres IP', managed_vms: 'VM', schedules: 'Harmonogram', webhooks: 'Webhook',
  vms: 'VM',
};

const AUDIT_VERB_LABELS = {
  created: 'utworzono', updated: 'zaktualizowano', deleted: 'usunięto', revoked: 'unieważniono',
  assigned: 'przypisano', released: 'zwolniono', reserved: 'zarezerwowano', disabled: 'wyłączono',
  enabled: 'włączono', unlocked: 'odblokowano', tested: 'przetestowano', generated: 'wygenerowano',
  executed: 'uruchomiono', cancelled: 'anulowano', retried: 'ponowiono', migrated: 'zmigrowano',
  cloned: 'sklonowano', restored: 'przywrócono', started: 'uruchomiono', stopped: 'zatrzymano',
};

function auditActionLabel(value) {
  const parts = String(value || '').split('.');
  const verb = AUDIT_VERB_LABELS[parts.at(-1)];
  if (!verb) return value || '—';
  const resourceKey = parts.slice(0, -1).join('_') || parts[0];
  const resource = AUDIT_RESOURCE_LABELS[resourceKey] || AUDIT_RESOURCE_LABELS[parts[0]] || parts[0];
  return `${resource}: ${verb}`;
}

const PERMISSION_GROUP_LABELS = {
  users: 'Użytkownicy', roles: 'Role i RBAC', tokens: 'Tokeny API', credentials: 'Dane dostępowe',
  providers: 'Platformy', deployments: 'Wdrożenia', jobs: 'Zadania', terraform: 'Terraform / OpenTofu',
  ansible: 'Ansible', blueprints: 'Blueprinty', hostnames: 'Hostname Manager', ipam: 'IPAM',
  inventory: 'Zasoby', vms: 'Maszyny wirtualne', snapshots: 'Snapshoty', backups: 'Backupy',
  schedules: 'Harmonogramy', webhooks: 'Webhooki', metrics: 'Monitoring', audit: 'Audyt',
  portal: 'Portal', catalog: 'Katalog',
};

const PERMISSION_ACTION_LABELS = {
  read: 'Odczyt', create: 'Tworzenie', update: 'Edycja', delete: 'Usuwanie', execute: 'Wykonywanie',
  assign: 'Przypisywanie', revoke: 'Unieważnianie', test: 'Testowanie połączenia', rotate: 'Rotacja',
  destroy: 'Usuwanie zasobów', adopt: 'Przejmowanie zasobów', approve: 'Akceptowanie',
  allocate: 'Przydzielanie', release: 'Zwalnianie', console: 'Konsola', power: 'Zasilanie',
  configure: 'Konfiguracja', snapshot: 'Snapshot', restore: 'Przywracanie', clone: 'Klonowanie',
  migrate: 'Migracja', resize: 'Zmiana rozmiaru', backup: 'Backup',
};

function permissionLabel(permission) {
  const [group, action, ...rest] = String(permission).split('.');
  const readableAction = PERMISSION_ACTION_LABELS[action] || action || permission;
  return rest.length ? `${readableAction} · ${rest.join('.')}` : readableAction;
}

function permissionPicker(permissions, selectedValues = [], name = 'permission') {
  const selected = new Set((selectedValues || []).map(String));
  const grouped = new Map();
  permissions.forEach(permission => {
    const group = String(permission).split('.')[0] || 'other';
    if (!grouped.has(group)) grouped.set(group, []);
    grouped.get(group).push(permission);
  });
  const wrapper = node('div', { class: 'permission-groups wide' });
  [...grouped.entries()].sort(([a], [b]) => a.localeCompare(b, 'pl')).forEach(([group, values]) => {
    const grid = node('div', { class: 'permission-grid' });
    values.sort((a, b) => a.localeCompare(b, 'pl')).forEach(permission => {
      grid.append(node('label', {},
        node('input', { type: 'checkbox', name, value: permission, checked: selected.has(permission) }),
        node('span', {}, node('strong', { text: permissionLabel(permission) }), node('small', { class: 'muted mono', text: permission }))));
    });
    wrapper.append(node('details', { class: 'permission-group', open: true },
      node('summary', {},
        node('span', { text: PERMISSION_GROUP_LABELS[group] || group }),
        badge(String(values.length), 'info')),
      grid));
  });
  return wrapper;
}

function permissionSummary(permissions) {
  if (!permissions?.length) return node('p', { class: 'muted', text: 'Brak uprawnień.' });
  const grouped = new Map();
  permissions.forEach(permission => {
    const group = String(permission).split('.')[0] || 'other';
    if (!grouped.has(group)) grouped.set(group, []);
    grouped.get(group).push(permission);
  });
  const wrapper = node('div', { class: 'permission-groups permission-summary' });
  [...grouped.entries()].sort(([a], [b]) => a.localeCompare(b, 'pl')).forEach(([group, values]) => {
    wrapper.append(node('details', { class: 'permission-group', open: true },
      node('summary', {},
        node('span', { text: PERMISSION_GROUP_LABELS[group] || group }),
        badge(String(values.length), 'info')),
      node('div', { class: 'permission-chip-list' },
        ...values.sort((a, b) => a.localeCompare(b, 'pl')).map(permission =>
          node('span', { class: 'permission-chip', title: permission, text: permissionLabel(permission) })))));
  });
  return wrapper;
}

function showPermissionSummary(title, permissions) {
  dom.modal.classList.remove('modal-console');
  dom.modal.classList.add('modal-wide');
  dom.modalTitle.textContent = title;
  dom.modalEyebrow.textContent = 'Uprawnienia';
  dom.modalBody.replaceChildren(permissionSummary(permissions));
  dom.modalActions.replaceChildren(button('Zamknij', closeModal));
  if (!dom.modal.open) dom.modal.showModal();
}

function displayValue(value) {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'boolean') return value ? 'Tak' : 'Nie';
  if (Array.isArray(value)) return value.map(displayValue).join(', ');
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function formatBytes(value) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes < 0) return '—';
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KiB', 'MiB', 'GiB', 'TiB', 'PiB'];
  let amount = bytes;
  let unit = -1;
  do { amount /= 1024; unit += 1; } while (amount >= 1024 && unit < units.length - 1);
  return `${amount >= 10 ? amount.toFixed(0) : amount.toFixed(1)} ${units[unit]}`;
}

function formatDuration(value) {
  let seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds < 0) return '—';
  seconds = Math.floor(seconds);
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days) return `${days} d ${hours} h`;
  if (hours) return `${hours} h ${minutes} min`;
  if (minutes) return `${minutes} min`;
  return `${seconds} s`;
}

const FIELD_LABELS = {
  name: 'Nazwa',
  node: 'Węzeł / lokalizacja',
  template_id: 'VMID szablonu',
  template_node: 'Węzeł szablonu',
  cpu: 'CPU',
  cores: 'Rdzenie CPU',
  memory: 'RAM (MiB)',
  disk: 'Dysk (GiB)',
  network: 'Sieć / bridge',
  storage: 'Storage',
  vlan_id: 'VLAN ID',
  ssh_username: 'Użytkownik SSH',
  ssh_public_key: 'Klucz publiczny SSH',
  ipv4_address: 'Adres IPv4 z maską',
  ipv4_gateway: 'Brama IPv4',
  region: 'Region',
  ami: 'AMI',
  instance_type: 'Typ instancji',
  subnet_id: 'Subnet ID',
  security_group_ids: 'Security Group IDs',
  key_name: 'Nazwa klucza',
  root_volume_size: 'Dysk systemowy (GiB)',
  associate_public_ip: 'Publiczny adres IP',
  location: 'Lokalizacja',
  resource_group: 'Resource Group',
  vm_size: 'Rozmiar VM',
  admin_username: 'Administrator',
  image_publisher: 'Wydawca obrazu',
  image_offer: 'Oferta obrazu',
  image_sku: 'SKU obrazu',
  image_version: 'Wersja obrazu',
  os_disk_size_gb: 'Dysk systemowy (GiB)',
  image_name: 'Obraz',
  flavor_name: 'Flavor',
  network_name: 'Sieć',
  key_pair: 'Key pair',
  security_groups: 'Security groups',
  datacenter: 'Datacenter',
  datastore: 'Datastore',
  cluster: 'Klaster',
  template: 'Szablon',
  folder: 'Folder',
  vmid: 'VMID',
  status: 'Status',
  type: 'Typ',
  mem: 'Użycie RAM',
  maxmem: 'RAM',
  disk: 'Użycie dysku',
  maxdisk: 'Dysk',
  maxcpu: 'Maks. CPU',
  uptime: 'Czas działania',
  iface: 'Interfejs',
  bridge_ports: 'Porty bridge',
  content: 'Zawartość',
  nodes: 'Węzły',
  shared: 'Współdzielony',
  active: 'Aktywny',
  enabled: 'Włączony',
  used: 'Użyte',
  avail: 'Wolne',
  total: 'Pojemność',
  poolid: 'Pula',
  comment: 'Opis',
};

function schemaVariant(spec = {}) {
  if (spec.type) return spec;
  if (Array.isArray(spec.anyOf)) return spec.anyOf.find(item => item.type && item.type !== 'null') || spec.anyOf[0] || spec;
  return spec;
}

function schemaType(spec = {}) {
  return schemaVariant(spec).type || 'string';
}

function schemaEnum(spec = {}) {
  return spec.enum || schemaVariant(spec).enum || null;
}

function templateVariableField(name, spec, value, required = false, options = {}) {
  const base = schemaVariant(spec);
  const type = schemaType(spec);
  const label = FIELD_LABELS[name] || spec.title || name;
  const hasValue = value !== undefined && value !== null;
  const current = hasValue ? value : spec.default;
  const helpParts = [];
  if (spec.description) helpParts.push(spec.description);
  if (base.minimum !== undefined || base.maximum !== undefined) {
    helpParts.push(`Zakres: ${base.minimum ?? '—'} – ${base.maximum ?? '—'}`);
  }
  const help = helpParts.join(' · ');
  const choices = schemaEnum(spec);
  let wrapper;
  if (choices) {
    wrapper = selectField(label, name, choices.map(item => ({ value: item, label: String(item) })), current ?? '', { required });
  } else if (type === 'boolean') {
    wrapper = checkboxField(label, name, Boolean(current));
  } else if (type === 'array') {
    const values = Array.isArray(current) ? current.join('\n') : (current || '');
    wrapper = field(label, name, { tag: 'textarea', value: values, required, wide: true, help: help || 'Jedna wartość w wierszu lub wartości oddzielone przecinkami.' });
  } else {
    const isLong = ['ssh_public_key', 'subnet_id'].includes(name);
    wrapper = field(label, name, {
      type: type === 'integer' || type === 'number' ? 'number' : 'text',
      value: current ?? '',
      required,
      min: base.minimum,
      max: base.maximum,
      step: type === 'number' ? 'any' : undefined,
      wide: isLong,
      help,
      placeholder: options.placeholder,
    });
  }
  wrapper.dataset.templateVariable = name;
  return wrapper;
}

function renderTemplateVariables(container, template, values = {}) {
  const schema = template?.variables_schema || {};
  const properties = schema.properties || {};
  const required = new Set(schema.required || []);
  container.replaceChildren();
  Object.entries(properties).forEach(([name, spec]) => {
    container.append(templateVariableField(name, spec, values[name], required.has(name)));
  });
  if (!Object.keys(properties).length) {
    container.append(node('p', { class: 'muted wide', text: 'Ten szablon nie definiuje zmiennych konfiguracyjnych.' }));
  }
}

function readTemplateVariables(form, template) {
  const result = {};
  const schema = template?.variables_schema || {};
  const properties = schema.properties || {};
  const required = new Set(schema.required || []);
  for (const [name, spec] of Object.entries(properties)) {
    const control = form.elements[name];
    if (!control) continue;
    const type = schemaType(spec);
    if (type === 'boolean') {
      result[name] = control.checked;
      continue;
    }
    const raw = String(control.value ?? '').trim();
    if (!raw) {
      if (required.has(name) && spec.default === undefined) throw new Error(`Uzupełnij pole „${FIELD_LABELS[name] || spec.title || name}”.`);
      continue;
    }
    if (type === 'integer') result[name] = Number.parseInt(raw, 10);
    else if (type === 'number') result[name] = Number(raw);
    else if (type === 'array') result[name] = splitValues(raw);
    else result[name] = raw;
  }
  return result;
}

function stopTaskPolling() {
  if (state.taskPollTimer) window.clearTimeout(state.taskPollTimer);
  state.taskPollTimer = null;
  state.taskPollNonce += 1;
}

function closeModal() {
  stopTaskPolling();
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
  emitUiEvent('app-hidden');
}

function showApp() {
  dom.loginView.hidden = true;
  dom.appView.hidden = false;
  dom.currentUser.textContent = state.identity.user.username;
  dom.currentRoles.textContent = state.identity.roles.map(role => role.name).join(', ') || 'Brak roli';
  emitUiEvent('app-shown', { identity: state.identity });
  renderNavigation();
  const mustChangePassword = state.identity.user.must_change_password;
  navigate(mustChangePassword ? 'account' : location.hash.slice(1) || 'dashboard');
  if (mustChangePassword) window.setTimeout(() => changePassword(true), 0);
}

function navigationGroup(route) {
  const order = Number(route.order ?? 1000);
  if (order <= 0) return '';
  if (order <= 40) return 'Dostęp';
  if (order <= 100) return 'Infrastruktura';
  if (order <= 140) return 'Operacje';
  return 'System';
}

function renderNavigation() {
  dom.navigation.replaceChildren();
  const currentRoute = routes.find(route => route.id === state.view);
  const visibleRoutes = routes.filter(route =>
    route.navigation !== false
    && allowed(route.permission)
    && (!state.identity.user.must_change_password || route.id === 'account')
  );
  let previousGroup = null;
  visibleRoutes.forEach(route => {
    const group = navigationGroup(route);
    if (group && group !== previousGroup) {
      dom.navigation.append(node('div', { class: 'nav-group-label', 'aria-hidden': 'true', text: group }));
    }
    previousGroup = group;
    const exact = state.view === route.id;
    const active = exact || currentRoute?.navigationParent === route.id;
    const item = node('button', {
      class: `nav-link ${active ? 'active' : ''}`,
      type: 'button',
      title: route.label,
      'aria-current': exact ? 'page' : null,
      onClick: () => navigate(route.id),
    },
      node('span', { class: 'nav-icon', 'aria-hidden': 'true', text: route.icon }), route.label);
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
  dom.pageEyebrow.textContent = route.id === 'dashboard'
    ? 'Stan systemu'
    : route.navigationParent
      ? (routes.find(item => item.id === route.navigationParent)?.label || 'Narzędzia')
      : 'Zarządzanie lokalne';
  dom.navigation.querySelectorAll('.nav-link').forEach(item => {
    const navRoute = routes.find(candidate => candidate.id === item.dataset.route);
    const exact = item.dataset.route === route.id;
    item.classList.toggle('active', exact || route.navigationParent === navRoute?.id);
    if (exact) item.setAttribute('aria-current', 'page');
    else item.removeAttribute('aria-current');
  });
  setMobileMenu(false);
  loading();
  try { await views[route.id](); }
  catch (error) {
    dom.content.replaceChildren(node('div', { class: 'panel' }, node('h2', { text: 'Nie udało się załadować widoku' }), node('p', { class: 'form-error', text: error.message }), button('Spróbuj ponownie', () => navigate(route.id), 'primary')));
  }
  dom.content.focus();
}

function showObjectDetails(title, value, eyebrow = 'Szczegóły') {
  const rows = Object.entries(value || {}).map(([key, item]) => ({ key, value: item }));
  dom.modalTitle.textContent = title;
  dom.modalEyebrow.textContent = eyebrow;
  dom.modalBody.replaceChildren(rows.length ? table([
    { label: 'Pole', value: row => node('strong', { text: FIELD_LABELS[row.key] || row.key.replaceAll('_', ' ') }) },
    { label: 'Wartość', value: row => node('span', { class: typeof row.value === 'string' && row.value.length > 40 ? 'mono' : '', text: displayValue(row.value) }) },
  ], rows) : node('p', { class: 'muted', text: 'Brak dodatkowych danych.' }));
  dom.modalActions.replaceChildren(button('Zamknij', closeModal));
  if (!dom.modal.open) dom.modal.showModal();
}

function showTemplateFields(template) {
  const schema = template.variables_schema || {};
  const required = new Set(schema.required || []);
  const rows = Object.entries(schema.properties || {}).map(([name, spec]) => {
    const base = schemaVariant(spec);
    const type = schemaType(spec);
    const enums = schemaEnum(spec);
    let constraints = '—';
    if (enums?.length) constraints = enums.join(', ');
    else if (base.minimum !== undefined || base.maximum !== undefined) constraints = `${base.minimum ?? '—'} – ${base.maximum ?? '—'}`;
    return {
      name,
      label: FIELD_LABELS[name] || spec.title || name,
      type,
      required: required.has(name),
      default: spec.default,
      constraints,
    };
  });
  const typeLabels = { string: 'Tekst', integer: 'Liczba całkowita', number: 'Liczba', boolean: 'Tak / nie', array: 'Lista' };
  dom.modal.classList.add('modal-wide');
  dom.modalTitle.textContent = `Pola: ${template.name}`;
  dom.modalEyebrow.textContent = `Szablon v${template.version}`;
  dom.modalBody.replaceChildren(rows.length ? table([
    { label: 'Pole', value: row => node('div', {}, node('strong', { text: row.label }), node('div', { class: 'mono muted', text: row.name })) },
    { label: 'Typ', value: row => typeLabels[row.type] || row.type },
    { label: 'Wymagane', value: row => row.required ? badge('Tak', 'warning') : 'Nie' },
    { label: 'Domyślnie', value: row => displayValue(row.default) },
    { label: 'Opcje / zakres', value: row => row.constraints },
  ], rows) : node('p', { class: 'muted', text: 'Szablon nie ma parametrów wejściowych.' }));
  dom.modalActions.replaceChildren(button('Zamknij', closeModal));
  if (!dom.modal.open) dom.modal.showModal();
}

function splitValues(value) {
  return String(value || '').split(/[\n,]+/).map(item => item.trim()).filter(Boolean);
}

function info(label, value) { return node('div', { class: 'check' }, node('span', { text: label }), node('strong', { text: value })); }

function setApiStatus(ok) {
  dom.apiStatus.replaceChildren(node('span', { class: `status-dot ${ok ? 'ok' : 'bad'}` }), node('span', { text: ok ? 'API działa' : 'API zdegradowane' }));
}
