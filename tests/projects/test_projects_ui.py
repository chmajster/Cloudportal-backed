"""Execute frontend pagination and stale-response isolation in Node."""
from pathlib import Path
import subprocess


def test_project_ui_server_scope_safe_text_and_stale_results():
    source = Path('app/web/features/projects.js').read_text()
    for unsafe in ('localStorage', '.innerHTML', 'prompt(', 'confirm(', 'alert('):
        assert unsafe not in source
    assert "async function renameProject(item)" in source
    assert "async function deleteProject(item)" in source
    assert "action('Zmień nazwę', () => renameProject(row))" in source
    assert "action('Usuń', () => deleteProject(row), 'danger')" in source
    assert "pattern: /^\\/projects\\/(?<id>[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})$/" in source
    assert "details(match.params.id)" in source
    assert "await navigate('projects');" in source
    assert "const id = Number(match.params.id);" not in source
    script = r'''
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const handlers = {}, listeners = {}, rendered = [], calls = [];
const element = (tag, attrs = {}, ...children) => ({tag, attrs, children, querySelector: () => ({addEventListener(){}})});
const context = {
 console, Promise, Set, JSON, String, Number, Math, URLSearchParams,
 state: {view:'projects'}, viewIs: id => context.state.view === id, document: {addEventListener: (event, cb) => {listeners[event] = cb;}},
 node: element, selectField: () => element('select'), heading: (text, actions) => element('heading', {text}, actions),
 table: (cols, rows) => element('table', {}, rows.map(row => cols.map(col => col.value(row)))),
 button: (label, click, kind, disabled) => element('button', {label,click,kind,disabled}), toast(){},
 dom: {content: {replaceChildren: (...items) => rendered.push(items)}},
 registerView: (route, cb) => {assert.equal(route.permission, null); handlers[route.id] = cb;},
 registerRoutedForm: () => {},
 api: async path => {
   calls.push(path);
   if (path.startsWith('/project-context/creation-scopes')) return {items:[], total:0};
   assert.equal(path, '/projects?limit=50&offset=0');
   return {items:[{id:'id',tenant_id:'own',name:'<img onerror=bad()>',slug:'own',status:'active',default_environment:'dev'}],total:1,limit:50,offset:0};
 },
};
vm.createContext(context); vm.runInContext(fs.readFileSync('app/web/features/projects.js','utf8'),context);
(async () => {
 await handlers.projects(); assert.equal(rendered.length, 1); assert.equal(calls.length, 2);
 assert.ok(JSON.stringify(rendered).includes('<img onerror=bad()>'));
 context.state.view='account'; await handlers.projects(); assert.equal(rendered.length,1);
 assert.equal(typeof listeners['cloudportal:app-hidden'],'function');
 assert.equal(typeof listeners['cloudportal:tenant-projects'],'function');
})().catch(error => {console.error(error);process.exitCode=1;});
'''
    subprocess.run(['node', '-e', script], check=True, timeout=15)


def test_project_member_picker_is_searchable_and_loads_all_tenant_members():
    source = Path('app/web/features/projects.js').read_text()
    shared = Path('app/web/shared/searchable-select.js').read_text()
    core = Path('app/web/core.js').read_text()

    assert "searchableSelectField('Członek tenanta'" in source
    assert "placeholder: 'Wpisz login członka tenanta…'" in source
    assert "eligible-members?limit=200&offset=" in source
    assert "while (offset < total)" in source
    assert "userPicker.setChoices(users.map(user => ({" in source
    assert "Wybierz członka tenanta z listy." in source
    assert "Kolejni członkowie tenanta" not in source
    assert "function searchableSelectField" in shared
    assert "window.searchableSelectField = searchableSelectField" in shared
    assert "function searchableSelectField" not in core
