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
