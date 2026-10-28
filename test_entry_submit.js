'use strict';
const test=require('node:test'), assert=require('node:assert/strict'), vm=require('node:vm'), fs=require('node:fs');
const app=fs.readFileSync(__dirname+'/app.js','utf8');
const submit=app.slice(app.indexOf('async function submitEntry('),app.indexOf("$('entry-form').addEventListener('submit',submitEntry)"));
const api=app.slice(app.indexOf('async function api('),app.indexOf('function saved('));

function form(kind='expense') {
  const fields=new Map(), calls=[];
  const field=id=>{if(!fields.has(id)) fields.set(id,{value:'',textContent:'',checked:false,disabled:false,focus(){}});return fields.get(id);};
  for(const [id,value] of Object.entries({'entry-title':'Test record','entry-amount':'200','entry-date':'2026-10-28','entry-category':'Transport','entry-subcategory':'Taxi','entry-person':'Maya','entry-notes':'My note','entry-due':'2026-10-29T09:00'}))field(id).value=value;
  field('entry-form').reportValidity=()=>true;
  const panel={inert:false,setAttribute(){},removeAttribute(){}};
  const c={kind,editing:null,entrySaving:false,leaving:false,accountReady:true,compactEntry:true,state:{},$:field,
    document:{querySelector(){return panel;}},
    error(id,message){field(id).textContent=message;},
    async api(path,body){calls.push({path,body});return {saved:true};},
    resetForm(){calls.push('reset');},saved(message){calls.push(message);},selectFilter(){throw Error('Popup must not change journal tabs');}
  };
  vm.createContext(c);vm.runInContext(submit,c);
  return {c,field,calls,panel,save:()=>c.submitEntry({preventDefault(){}})};
}
test('all four entry kinds use the existing save APIs and clear only after success',async()=>{
  for(const kind of ['expense','loan','reminder','mandate']){
    const f=form(kind);f.field('has-reminder').checked=kind==='reminder';await f.save();
    assert.equal(f.calls[0].path,kind==='mandate'?'/api/mandates':'/api/entries');
    assert.equal(f.calls[0].body.title,'Test record');
    assert.equal(f.calls[0].body.amount,'200');
    assert.equal(f.calls[0].body.subcategory,'Taxi');
    if(kind==='reminder')assert.equal(f.calls[0].body.dueAt,'2026-10-29T09:00');
    if(kind==='mandate')assert.equal(f.calls[0].body.firstDueDate,'2026-10-28');
    assert.equal(f.calls[1],'reset');assert.equal(f.c.entrySaving,false);assert.equal(f.panel.inert,false);
  }
});
test('concurrent clicks send one request and keep the form inert until confirmed',async()=>{
  const f=form();let complete,count=0;
  f.c.api=()=>{count++;return new Promise(resolve=>complete=resolve);};
  const save=f.save();await f.save();
  assert.equal(count,1);assert.equal(f.panel.inert,true);assert.equal(f.field('save-entry').disabled,true);
  assert.equal(f.calls.length,0);complete({});await save;
  assert.equal(f.calls[0],'reset');assert.equal(f.panel.inert,false);
});
test('failed or uncertain saves retain the draft and never automatically retry',async()=>{
  const f=form();let requests=0;
  f.c.api=async()=>{requests++;throw Error('Save could not be confirmed. Check your journal before retrying.');};
  await f.save();assert.equal(requests,1);assert.equal(f.calls.length,0);
  assert.equal(f.field('entry-title').value,'Test record');
  assert.match(f.field('form-error').textContent,/Check your journal/);
  assert.equal(f.panel.inert,false);assert.equal(f.field('save-entry').disabled,false);
});
test('offline, leaving, and invalid forms never submit',async()=>{
  const f=form();f.c.accountReady=false;await f.save();
  f.c.accountReady=true;f.c.leaving=true;await f.save();
  f.c.leaving=false;f.field('entry-form').reportValidity=()=>false;await f.save();
  assert.equal(f.calls.length,0);
});
test('API rejects expired sessions, changed accounts, and uncertain mutations',async()=>{
  const redirects=[], options=[];
  const c={AbortController,setTimeout,clearTimeout,expectedAccount:'owner',accountHome:'/quick-entry',mutationRevision:0,
    document:{querySelector(){return {content:'test-csrf'};}},leaveAccount(to){redirects.push(to);},
    fetch:async(url,opt)=>{options.push(opt);return {status:401};}
  };
  vm.createContext(c);vm.runInContext(api,c);
  await assert.rejects(c.api('/api/state'),/log in/);assert.equal(redirects.length,1);
  c.fetch=async()=>({status:200,ok:true,json:async()=>({account:{id:'other'}})});
  await assert.rejects(c.api('/api/state'),/Account changed/);assert.equal(redirects[1],'/quick-entry');
  c.fetch=async()=>{throw new TypeError('connection failed');};
  await assert.rejects(c.api('/api/entries',{}),/Check your journal/);
  assert.equal(options[0].cache,'no-store');
});
test('logout notifications clear private state once, even with concurrent expiry',()=>{
  const leave=app.slice(app.indexOf('function leaveAccount('),app.indexOf('const currency ='));
  let cleared=0,redirected=0;
  const c={accountLogin:'/login?next=/quick-entry',leaving:false,accountReady:true,quickDraft:{text:'private'},editing:'id',toastTimer:null,clearTimeout,
    state:{entries:[{title:'private'}]},$:()=>({value:'private'}),node(){return {};},
    document:{querySelectorAll(){return [];},body:{replaceChildren(){cleared++;}}},window:{location:{replace(){redirected++;}}}};
  vm.createContext(c);vm.runInContext(leave,c);c.leaveAccount();c.leaveAccount();
  assert.equal(cleared,1);assert.equal(redirected,1);assert.equal(c.state.entries.length,0);assert.equal(c.quickDraft,null);
});

test('save sends reviewed trip selection; mandates never submit a trip',async()=>{
  for(const kind of ['expense','loan','reminder','mandate']) for(const trip of ['', 'goa']) {
    const f=form(kind);f.field('entry-trip').value=trip;await f.save();
    if(kind==='mandate')assert.equal('tripId' in f.calls[0].body,false);
    else assert.equal(f.calls[0].body.tripId,trip||null);
  }
});
