"""Execute the feature registry/list rendering without depending on a browser."""
import subprocess
from pathlib import Path


def test_tenant_ui_uses_server_scope_and_safe_rendering():
    source = Path('app/web/features/tenancy.js').read_text()
    assert 'localStorage' not in source and '.innerHTML' not in source
    assert 'prompt(' not in source and 'alert(' not in source and 'confirm(' not in source
    script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const handlers = {}, listeners = {}, rendered = [];
let permission = 'unset', apiCalls = 0;
const node = (tag, attrs = {}, ...children) => ({tag, attrs, children,
  querySelector: () => ({addEventListener() {}})});
const context = {
  console, Promise, Set, JSON, String, Number, Math, encodeURIComponent,
  state: {view: 'tenants'},
  viewIs: id => context.state.view === id,
  document: {addEventListener: (event, handler) => {listeners[event] = handler;}},
  registerView: (route, handler) => { handlers[route.id] = handler; permission = route.permission; },
  registerRoutedForm: () => {},
  node, selectField: () => node('select'),
  heading: (text, actions) => node('heading', {text}, actions),
  table: (cols, rows, actions) => node('table', {}, rows.map(row => cols.map(col => col.value(row)))),
  button: (label, click, kind, disabled) => node('button', {label, click, kind, disabled}),
  badge: text => node('badge', {text}), formatDate: value => value,
  allowed: () => false, toast: () => {},
  api: async path => {
    apiCalls++;
    assert.equal(path, '/tenants?limit=50&offset=0');
    return {items: [{id: 'uuid', name: '<img src=x onerror=alert(1)>', slug: 'own', status: 'active',
      is_system: false, updated_at: '2026-01-01T00:00:00Z'}], total: 1, limit: 50, offset: 0};
  },
  dom: {content: {replaceChildren: (...items) => rendered.push(items)}},
};
vm.createContext(context);
vm.runInContext(fs.readFileSync('app/web/features/tenancy.js', 'utf8'), context);
(async () => {
  assert.equal(permission, null, 'Scoped grants must not be flattened into global identity permissions');
  await handlers.tenants();
  assert.equal(apiCalls, 1);
  assert.equal(rendered.length, 1);
  assert.ok(JSON.stringify(rendered).includes('<img src=x onerror=alert(1)>'));
  // It is a text value passed to node(), never an HTML assignment.
  context.state.view = 'account';
  await handlers.tenants();
  assert.equal(rendered.length, 1, 'An old request must not overwrite another route');
  assert.equal(typeof listeners['cloudportal:app-hidden'], 'function');
})().catch(error => {console.error(error); process.exitCode = 1;});
"""
    subprocess.run(['node', '-e', script], check=True, timeout=15)
