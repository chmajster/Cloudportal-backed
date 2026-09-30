"""Execute real preference UI callbacks and navigation guards without a browser."""
from pathlib import Path
import subprocess


def test_selection_ui_uses_live_versions_and_suppresses_stale_callbacks():
    source = Path('app/web/features/project-context.js').read_text()
    for unsafe in ('localStorage', '.innerHTML', 'prompt(', 'confirm(', 'alert('):
        assert unsafe not in source
    script = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const calls=[], notices=[];let active=true,revoked=false,version=7;
const sandbox={Object,console,registerExtension:(name,init)=>init(),
 node:(tag,attrs,...children)=>({tag,attrs,children}),button:(label,click)=>({label,click}),toast:text=>notices.push(text),
 api:async(path,options={})=>{calls.push([path,options]);if(!options.method){if(revoked)throw {status:404};return {selected:{name:'<img onerror=x()>',tenant_id:'t'},version};}return {};}};
vm.createContext(sandbox);vm.runInContext(fs.readFileSync('app/web/features/project-context.js','utf8'),sandbox);
(async()=>{
 const ui=sandbox.CPProjectContext,valid=()=>active;
 await ui.choose({tenant_id:'t',id:'p'},valid);
 assert.equal(calls[1][1].body.expected_version,7);assert.equal(calls[1][1].method,'PUT');
 const panel=await ui.panel(async()=>{},valid);assert.ok(JSON.stringify(panel).includes('<img onerror=x()>'));
 await panel.children[1].click();assert.equal(calls.at(-1)[0],'/project-context?expected_version=7');
 revoked=true;const recovery=await ui.panel(async()=>{},valid);await recovery.children[1].click();
 assert.equal(calls.at(-1)[0],'/project-context');assert.equal(calls.at(-1)[1].method,'DELETE');
 const before=calls.length;active=false;await recovery.children[1].click();await ui.choose({tenant_id:'t',id:'p'},valid);
 assert.equal(calls.length,before);
 active=true;revoked=false;sandbox.api=async()=>{active=false;return {version:7};};
 await ui.choose({tenant_id:'t',id:'p'},valid);assert.equal(notices.length,1);
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    subprocess.run(['node', '-e', script], check=True, timeout=15)

def test_global_context_switcher_drives_default_http_scope_and_blueprints():
    context_source = Path('app/web/features/project-context.js').read_text()
    http_source = Path('app/web/shared/http.js').read_text()
    blueprint_source = Path('app/web/features/blueprints.js').read_text()

    assert "global-context-trigger" in context_source
    assert "'Organizacja / Tenant'" in context_source
    assert "'Projekt'" in context_source
    assert "document.addEventListener('cloudportal:app-shown'" in context_source
    assert "'X-Tenant-ID': String(currentScope.tenant_id)" in context_source
    assert "'X-Project-ID': String(currentScope.id)" in context_source
    assert "globalThis.CPProjectContext.headers()" in http_source
    assert "...inheritedScope" in http_source
    assert "options.scope === false" in http_source
    assert "scopes.find(scope => String(scope.project_id) === contextProjectId)" in blueprint_source
    assert "globalThis.CPProjectContext.choose({" in blueprint_source
    assert "Zmiana tego pola aktualizuje również globalny kontekst pracy" in blueprint_source

def test_global_context_picker_uses_filterable_combobox():
    context_source = Path('app/web/features/project-context.js').read_text()
    shared_source = Path('app/web/shared/searchable-select.js').read_text()
    core_source = Path('app/web/core.js').read_text()
    styles = Path('app/web/styles/features/projects.css').read_text()

    assert 'function searchableSelectField' in shared_source
    assert "type: 'search'" in shared_source
    assert "'aria-autocomplete': 'list'" in shared_source
    assert 'normalizeSearchText(choice.searchText).includes(query)' in shared_source
    assert 'searchText: String(choice.searchText ?? choice.label ?? choice.value)' in shared_source
    assert 'window.searchableSelectField = searchableSelectField' in shared_source
    assert 'function searchableSelectField' not in context_source
    assert 'function searchableSelectField' not in core_source
    assert 'organization.searchableSelect' in context_source
    assert 'organizationSelect.onChange' in context_source
    assert '.searchable-select-options' in styles
    assert '.searchable-select-option.active' in styles

