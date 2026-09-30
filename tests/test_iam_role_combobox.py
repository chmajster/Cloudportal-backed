"""Regression coverage for the IAM role searchable select and assignment role edits."""
from pathlib import Path
import subprocess

from conftest import new_user


def test_iam_role_field_uses_existing_searchable_select_contract():
    iam = Path('app/web/features/iam.js').read_text()
    shared = Path('app/web/shared/searchable-select.js').read_text()
    styles = Path('app/web/styles/features/identity.css').read_text()

    assert "assignable: 'true'" in iam
    assert "api('/rbac/roles?' + query.toString())" in iam
    assert "scope_type: kind" in iam
    assert "while (offset < total)" in iam
    assert "searchableSelectField('Rola', 'role_id'" in iam
    assert "placeholder: 'Wyszukaj rolę…'" in iam
    assert "emptyText: 'Brak pasujących ról'" in iam
    assert "role.searchableSelect.setState('loading', 'Ładowanie ról…')" in iam
    assert "role.searchableSelect.setChoices([], '');\n    role.searchableSelect.setState('loading', 'Ładowanie ról…')" in iam
    assert "role.searchableSelect.setState('error', 'Nie udało się pobrać listy ról.')" in iam
    assert "console.error('Nie udało się pobrać listy ról IAM.'" in iam
    assert "value: item.id" in iam
    assert "item.system_role ? 'system' : ''" in iam
    assert "...scopeTerms" in iam
    assert "availableRoleIds.has(roleId)" in iam
    assert "role_id: roleId" in iam
    assert "role.searchableSelect.setChoices(roles.map(roleChoice), preferred)" in iam
    assert "await loadAssignableRoles(assignment?.role_id || '')" in iam
    assert "button('Edytuj', () => accessAssignmentForm(null, item)" in iam
    assert "method: 'PATCH'" in iam

    assert "role: 'combobox'" in shared
    assert "'aria-expanded': 'false'" in shared
    assert "'aria-controls': listboxId" in shared
    assert "role: 'listbox'" in shared
    assert "role: 'option'" in shared
    assert "searchInput.setAttribute('aria-activedescendant', active.id)" in shared
    assert "event.key === 'ArrowDown' || event.key === 'ArrowUp'" in shared
    assert "event.key === 'Enter'" in shared
    assert "event.key === 'Escape'" in shared
    assert "event.key === 'Tab'" in shared
    assert "normalizeSearchText(choice.searchText).includes(query)" in shared
    assert "updateValidity();" in shared[shared.index("function clearSelection()"):shared.index("function setChoices(")]

    assert "role.classList.add('iam-role-searchable-select')" in iam
    assert ".iam-role-searchable-select .searchable-select-options" in styles
    assert "z-index: 130" in styles
    assert "max-height: min(240px, calc(100dvh - 96px))" in styles
    assert ".iam-role-searchable-select .searchable-select-option > span" in styles
    assert "text-overflow: ellipsis" in styles


def test_searchable_select_runtime_filters_keyboard_and_rejects_free_text():
    script = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');

class FakeClassList {
  constructor(owner) { this.owner = owner; }
  values() { return String(this.owner.className || '').split(/\s+/).filter(Boolean); }
  toggle(name, force) {
    const values = new Set(this.values());
    const enabled = force === undefined ? !values.has(name) : Boolean(force);
    if (enabled) values.add(name); else values.delete(name);
    this.owner.className = [...values].join(' ');
    return enabled;
  }
}

class FakeElement {
  constructor(tag, attrs = {}, children = []) {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.listeners = {};
    this.attributes = {};
    this.className = attrs.class || '';
    this.classList = new FakeClassList(this);
    this.value = attrs.value == null ? '' : String(attrs.value);
    this.type = attrs.type || '';
    this.name = attrs.name || '';
    this.id = attrs.id || '';
    this.hidden = Boolean(attrs.hidden);
    this.disabled = Boolean(attrs.disabled);
    this.required = Boolean(attrs.required);
    this.placeholder = attrs.placeholder || '';
    this.customValidity = '';
    this.textContent = attrs.text || '';
    for (const [key, value] of Object.entries(attrs)) {
      if (key === 'class' || key === 'text' || key.startsWith('on')) continue;
      this.attributes[key] = String(value);
    }
    if (attrs.onClick) this.addEventListener('click', attrs.onClick);
    if (attrs.onMouseDown) this.addEventListener('mousedown', attrs.onMouseDown);
    children.flat(Infinity).filter(value => value != null).forEach(child => this.append(child));
  }
  append(child) { this.children.push(child); return child; }
  prepend(child) { this.children.unshift(child); return child; }
  replaceChildren(...children) { this.children = children.flat(Infinity).filter(value => value != null); }
  addEventListener(type, callback) { (this.listeners[type] ||= []).push(callback); }
  dispatch(type, extra = {}) {
    const event = {
      key: extra.key,
      preventDefault() { this.defaultPrevented = true; },
      stopPropagation() { this.propagationStopped = true; },
      ...extra,
    };
    for (const callback of this.listeners[type] || []) callback(event);
    return event;
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name]; }
  removeAttribute(name) { delete this.attributes[name]; }
  setCustomValidity(value) { this.customValidity = String(value); }
  scrollIntoView() {}
  select() {}
  matches(selector) {
    if (selector.startsWith('.')) return this.classList.values().includes(selector.slice(1));
    if (selector.startsWith('#')) return this.id === selector.slice(1);
    if (selector.includes('[name="')) {
      const name = selector.match(/\[name="([^"]+)"\]/)?.[1];
      return this.name === name;
    }
    return this.tagName === selector.toUpperCase();
  }
  querySelectorAll(selector) {
    const result = [];
    const visit = node => {
      if (!(node instanceof FakeElement)) return;
      if (node.matches(selector)) result.push(node);
      node.children.forEach(visit);
    };
    this.children.forEach(visit);
    return result;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

const sandbox = {
  console,
  String,
  Array,
  Set,
  Object,
  Math,
  node: (tag, attrs = {}, ...children) => new FakeElement(tag, attrs, children),
  formFieldLabel: text => String(text),
};
sandbox.window = sandbox;
sandbox.window.setTimeout = callback => callback();
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync('app/web/shared/searchable-select.js', 'utf8'), sandbox);

const field = sandbox.searchableSelectField('Rola', 'role_id', [
  {value: 15, label: 'Administrator · system', searchText: 'Administrator system GLOBAL'},
  {value: 16, label: 'Blueprint Administrator · system', searchText: 'Blueprint Administrator system GLOBAL'},
  {value: 17, label: 'Project Operator · project', searchText: 'Project Operator project PROJECT'},
], '', {
  required: true,
  placeholder: 'Wyszukaj rolę…',
  emptyText: 'Brak pasujących ról',
  selectFirst: false,
  disableWhenEmpty: false,
});

const input = field.querySelector('.searchable-select-input');
const hidden = field.querySelector('[name="role_id"]');
const listbox = field.querySelector('.searchable-select-options');

input.dispatch('focus');
assert.equal(listbox.hidden, false);
assert.equal(listbox.querySelectorAll('.searchable-select-option').length, 3);

input.value = 'admin';
input.dispatch('input');
assert.equal(listbox.querySelectorAll('.searchable-select-option').length, 2);

input.value = 'blueprint';
input.dispatch('input');
assert.equal(listbox.querySelectorAll('.searchable-select-option').length, 1);
assert.equal(listbox.querySelectorAll('.searchable-select-option')[0]._searchableChoice.value, '16');

input.value = '  SYSTEM  ';
input.dispatch('input');
assert.equal(listbox.querySelectorAll('.searchable-select-option').length, 2);
assert.equal(hidden.value, '');
assert.notEqual(input.customValidity, '');

input.dispatch('keydown', {key: 'ArrowDown'});
assert.ok(input.getAttribute('aria-activedescendant'));
input.dispatch('keydown', {key: 'Enter'});
assert.equal(hidden.value, '15');
assert.equal(input.value, 'Administrator · system');
assert.equal(input.getAttribute('aria-expanded'), 'false');
assert.equal(input.customValidity, '');

input.dispatch('focus');
input.value = 'PROJECT';
input.dispatch('input');
assert.equal(listbox.querySelectorAll('.searchable-select-option').length, 1);
input.dispatch('keydown', {key: 'ArrowUp'});
input.dispatch('keydown', {key: 'Enter'});
assert.equal(hidden.value, '17');

input.dispatch('focus');
input.value = 'does-not-exist';
input.dispatch('input');
assert.equal(listbox.querySelectorAll('.searchable-select-option').length, 0);
assert.equal(listbox.children[0].textContent, 'Brak pasujących ról');
assert.equal(hidden.value, '');
assert.notEqual(input.customValidity, '');

input.dispatch('keydown', {key: 'Escape'});
assert.equal(listbox.hidden, true);

input.dispatch('focus');
input.dispatch('keydown', {key: 'Tab'});
assert.equal(listbox.hidden, true);
"""
    subprocess.run(['node', '-e', script], check=True, timeout=15)


def test_assignment_role_can_be_changed_by_id_and_permission_ceiling_is_refreshed(client, headers):
    target = client.post('/api/v1/users', headers=headers, json={
        'username': 'iam-combobox-target',
        'email': 'iam-combobox-target@example.com',
        'password': 'strong-password-1234',
    })
    assert target.status_code == 201, target.text

    first = client.post('/api/v1/rbac/roles', headers=headers, json={
        'name': 'IAM Combobox First',
        'permissions': ['deployments.read'],
        'scope_types': ['GLOBAL'],
    })
    second = client.post('/api/v1/rbac/roles', headers=headers, json={
        'name': 'IAM Combobox Second',
        'permissions': ['users.read'],
        'scope_types': ['GLOBAL'],
    })
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text

    created = client.post('/api/v1/rbac/assignments', headers=headers, json={
        'subject_type': 'USER',
        'subject_id': str(target.json()['id']),
        'role_id': first.json()['id'],
        'effect': 'ALLOW',
        'scope_type': 'GLOBAL',
        'conditions': {},
        'inherit': True,
        'approval_required': False,
        'enabled': True,
    })
    assert created.status_code == 201, created.text
    assert created.json()['role_id'] == first.json()['id']
    assert created.json()['permission_ceiling'] == ['deployments.read']

    edited = client.patch(
        '/api/v1/rbac/assignments/' + created.json()['id'],
        headers=headers,
        json={'role_id': second.json()['id']},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()['role_id'] == second.json()['id']
    assert edited.json()['role_name'] == 'IAM Combobox Second'
    assert edited.json()['permission_ceiling'] == ['users.read']


def test_assignment_role_id_cannot_bypass_backend_delegation_boundary(client, headers):
    target = client.post('/api/v1/users', headers=headers, json={
        'username': 'iam-rbac-target',
        'email': 'iam-rbac-target@example.com',
        'password': 'strong-password-1234',
    })
    assert target.status_code == 201, target.text

    privileged = client.post('/api/v1/rbac/roles', headers=headers, json={
        'name': 'IAM Privileged Role',
        'permissions': ['users.delete'],
        'scope_types': ['GLOBAL'],
    })
    assert privileged.status_code == 201, privileged.text

    _operator, operator_headers = new_user(
        client,
        headers,
        username='iam-role-operator',
        permissions=['roles.read', 'roles.assign'],
    )

    roles = client.get('/api/v1/rbac/roles?limit=200', headers=operator_headers)
    assert roles.status_code == 200, roles.text
    assert privileged.json()['id'] in {item['id'] for item in roles.json()['items']}

    assignable = client.get(
        '/api/v1/rbac/roles?limit=200&assignable=true&scope_type=GLOBAL',
        headers=operator_headers,
    )
    assert assignable.status_code == 200, assignable.text
    assert privileged.json()['id'] not in {item['id'] for item in assignable.json()['items']}

    admin_assignable = client.get(
        '/api/v1/rbac/roles?limit=200&assignable=true&scope_type=GLOBAL',
        headers=headers,
    )
    assert admin_assignable.status_code == 200, admin_assignable.text
    assert privileged.json()['id'] in {item['id'] for item in admin_assignable.json()['items']}

    forged = client.post('/api/v1/rbac/assignments', headers=operator_headers, json={
        'subject_type': 'USER',
        'subject_id': str(target.json()['id']),
        'role_id': privileged.json()['id'],
        'effect': 'ALLOW',
        'scope_type': 'GLOBAL',
        'conditions': {},
        'inherit': True,
        'approval_required': False,
        'enabled': True,
    })
    assert forged.status_code == 403, forged.text
    detail = forged.json()['detail']
    assert detail['error'] == 'delegation_boundary_exceeded'
    assert 'users.delete' in detail['missing_permissions']
