import subprocess
from pathlib import Path


def test_project_view_uses_scoped_results_and_rejects_stale_navigation():
    source=Path('app/web/features/projects.js').read_text()
    assert 'localStorage' not in source and '.innerHTML' not in source
    assert all(x+'(' not in source for x in ('prompt','alert','confirm'))
    script=r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const handlers={},rendered=[],listeners={};let count=0;
const node=(tag,attrs={},...children)=>({tag,attrs,children,querySelector:()=>({addEventListener(){}})});
const context={console,Promise,Set,JSON,String,Number,Math,encodeURIComponent,state:{view:'projects'},
 document:{addEventListener:(name,fn)=>listeners[name]=fn},
 registerView:(route,fn)=>{assert.equal(route.permission,null);handlers[route.id]=fn;},node,
 selectField:()=>node('select'),heading:(text,actions)=>node('heading',{text},actions),
 table:(cols,rows)=>node('table',{},rows.map(row=>cols.map(col=>col.value(row)))),
 button:(label,click,kind,disabled)=>node('button',{label,click,kind,disabled}),badge:text=>node('badge',{text}),
 formatDate:x=>x,allowed:()=>false,toast:()=>{},
 api:async path=>{count++;if(path==='/project-context')return{selected:null,version:0};
 if(path==='/project-creation-tenants?limit=1')return{items:[],total:0,limit:1,offset:0};
 assert.equal(path,'/projects?limit=50&offset=0');
 return{items:[{id:'uuid',name:'<script>evil()</script>',slug:'own',status:'active',is_system:false,updated_at:'2026-01-01'}],total:1,offset:0,limit:50};},
 dom:{content:{replaceChildren:(...x)=>rendered.push(x)}}};
vm.createContext(context);vm.runInContext(fs.readFileSync('app/web/features/projects.js','utf8'),context);
(async()=>{await handlers.projects();assert.equal(count,3);assert.equal(rendered.length,1);
assert.ok(JSON.stringify(rendered).includes('<script>evil()</script>'));
context.state.view='account';await handlers.projects();assert.equal(rendered.length,1);
listeners['cloudportal:app-hidden']();})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    subprocess.run(['node','-e',script],check=True,timeout=15)
