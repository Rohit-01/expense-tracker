'use strict';
const test=require('node:test'), assert=require('node:assert/strict'), vm=require('node:vm'), fs=require('node:fs');
const source=fs.readFileSync(__dirname+'/app.js','utf8');
const block=(from,to)=>source.slice(source.indexOf(from),source.indexOf(to,source.indexOf(from)));
function fixture(page='') {
  const fields=new Map();
  const field=id=>{
    if(!fields.has(id))fields.set(id,{value:'',textContent:'',hidden:false,options:[],
      replaceChildren(){this.options=[];this.value='';},append(child){this.options.push(child);if(this.options.length===1)this.value=child.value;},
      scrollIntoView(){},focus(){},reset(){for(const [id,f] of fields) if(id.startsWith('entry-'))f.value='';}
    });
    return fields.get(id);
  };
  const c={$:field,node:()=>({}),tripPageId:page,tripsLoaded:true,entryTripInitialized:false,editing:null,quickDraft:null,
    state:{activeTripId:'goa',trips:[{id:'goa',name:'Goa',completed:false},{id:'vizag',name:'Vizag',completed:false},{id:'old',name:'Old trip',completed:true}]},
    today:()=>'2026-10-28',setKind(){},updateDue(){},updateSubcategories(){},error(){},
  };
  vm.createContext(c);
  vm.runInContext(block('function syncTripSelectors(', 'function tripDates(')+block('function resetForm(', 'function render()'),c);
  return {c,field};
}
test('main and popup forms preselect the account default once; detail page preselects its own trip',()=>{
  for(const page of ['', 'vizag', 'old']) {
    const {c,field}=fixture(page);c.resetForm();assert.equal(field('entry-trip').value,page||'goa');
    assert.equal(field('journal-trip').hidden,Boolean(page));
    c.state.activeTripId='vizag';c.syncTripSelectors();assert.equal(field('entry-trip').value,page||'goa');
  }
});
test('explicit No trip, named trip and journal filters survive background refresh and category updates',()=>{
  const {c,field}=fixture();c.resetForm();
  for(const selected of ['', 'old', 'goa']) {
    field('entry-trip').value=selected;field('journal-trip').value='none';
    c.state.activeTripId='vizag';c.syncTripSelectors();
    assert.equal(field('entry-trip').value,selected);assert.equal(field('journal-trip').value,'none');
  }
  c.resetForm();assert.equal(field('entry-trip').value,'vizag');
});
test('initial response sets a default after loading but later responses preserve the draft',()=>{
  const {c,field}=fixture();c.tripsLoaded=false;c.state={trips:[],activeTripId:null};c.resetForm();
  assert.equal(field('entry-trip').disabled,true);assert.equal(c.entryTripInitialized,false);
  c.state={trips:[{id:'goa',name:'Goa'}],activeTripId:'goa'};c.tripsLoaded=true;c.syncTripSelectors();
  assert.equal(field('entry-trip').value,'goa');assert.equal(field('entry-trip').disabled,false);
  field('entry-trip').value='';c.state.activeTripId=null;c.syncTripSelectors();assert.equal(field('entry-trip').value,'');
});
test('editing preserves original trip or no trip, including completed trips; cancel uses current default',()=>{
  const {c,field}=fixture('goa');c.resetForm();
  for(const tripId of [undefined,'old','vizag']) {
    c.editEntry({id:'entry',kind:'expense',title:'Dinner',amountMinor:20000,tripId,notes:'',person:'',date:'2026-10-28'});
    assert.equal(field('entry-trip').value,tripId||'');
    c.syncTripSelectors();assert.equal(field('entry-trip').value,tripId||'');
    assert.equal(c.editing,'entry');
  }
  c.resetForm();assert.equal(field('entry-trip').value,'goa');assert.equal(c.editing,null);
});
test('type switching hides trip control for mandates without losing selection',()=>{
  const {c,field}=fixture();c.document={querySelectorAll:()=>[]};
  vm.runInContext(block('function setKind(', 'function updateDue('),c);
  field('entry-trip').value='goa';c.setKind('mandate');assert.equal(field('trip-field').hidden,true);
  c.setKind('loan');assert.equal(field('trip-field').hidden,false);assert.equal(field('entry-trip').value,'goa');
  c.tripPageId='goa';c.setKind('mandate');assert.equal(c.kind,'expense');
});
