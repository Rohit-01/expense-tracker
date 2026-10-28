'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const journal=require('./journal.js'),source=fs.readFileSync(__dirname+'/app.js','utf8');
const block=(from,to)=>source.slice(source.indexOf(from),source.indexOf(to,source.indexOf(from)));
const expense=(id,date,amount,tripId)=>({id,date,amountMinor:amount,tripId,kind:'expense',title:id,dueAt:'',reminderDone:false});
function data(){return {entries:[
  expense('goa-flight','2026-09-01',300000,'goa'),expense('goa-dinner','2026-10-01',20000,'goa'),expense('coffee','2026-10-02',10000,null),
  {...expense('loan','2026-10-03',50000,'goa'),kind:'loan',person:'Maya',dueAt:'2026-11-01T09:00'},
  {...expense('repaid','2026-10-03',50000,null),kind:'loan',dueAt:'2026-11-01T09:00'},
  {...expense('reminder','2026-09-03',0,'goa'),kind:'reminder',dueAt:'2026-10-20T09:00'},
  {...expense('done','2026-10-03',0,'goa'),kind:'reminder',dueAt:'2026-10-20T09:00',reminderDone:true}],
  repayments:[{entryId:'loan',amountMinor:20000},{entryId:'repaid',amountMinor:50000}],
  trips:[{id:'goa',name:'Goa',completed:false},{id:'vizag',name:'Vizag',completed:true}],
  trash:[{entry:expense('deleted','2026-10-02',999999,'goa')}],mandates:[]};}
function fixture(){
  const fields=new Map(),calls=[];
  const field=id=>{if(!fields.has(id))fields.set(id,{value:'',textContent:'',hidden:false,options:[],disabled:false,open:false,
    replaceChildren(){this.options=[];this.value='';},append(child){this.options.push(child);},setAttribute(){},
    focus(){calls.push(['focus',id]);},showModal(){this.open=true;},close(){this.open=false;}});return fields.get(id);};
  const c={$:field,node:()=>({}),state:data(),pocketJournal:journal,summaryMonth:'2026-10',summaryTrip:'',tripPageId:'',deletingId:null,trashBusy:false,entrySaving:false,leaving:false,editing:null,
    today:()=>'2026-10-28',monthName:x=>x,fmt:x=>'INR '+x/100,dueDate:x=>new Date(x+':00+05:30'),document:{querySelectorAll:()=>[]},
    error(id,message){field(id).textContent=message;},saved(message){calls.push(['saved',message]);},resetForm(){calls.push(['reset']);},
    async api(path,body){calls.push([path,body]);return {...c.state};},left(entry){return entry.amountMinor-c.state.repayments.filter(r=>r.entryId===entry.id).reduce((sum,r)=>sum+r.amountMinor,0);}
  };
  vm.createContext(c);vm.runInContext(block('function renderSummary()', 'function setKind('),c);
  return {c,field,calls};
}
test('summary filters entry dates and current balances, excluding standalone reminder spending and Trash',()=>{
  const state=data(),cards=journal.summary(state,'2026-10','goa');
  assert.equal(cards.spending,20000);assert.equal(cards.outstanding,30000);assert.equal(cards.reminders.length,1);
  assert.equal(cards.loans.length,1);assert.equal(cards.expenses.length,1);
  const september=journal.summary(state,'2026-09','goa');assert.equal(september.spending,300000);assert.equal(september.reminders.length,1);
  assert.equal(journal.summary(state,'','goa').spending,320000);
  assert.equal(journal.summary(state,'2026-10','none').spending,10000);
  assert.equal(journal.summary(state,'2026-10','none').loans.length,0);
  assert.equal(journal.summary(state,'','').spending,330000);
  const empty=journal.summary(state,'2026-01','vizag');assert.equal(empty.spending,0);assert.equal(empty.outstanding,0);assert.equal(empty.reminders.length,0);
});
test('summary keeps empty selected months and does not add mandate or Trash dates',()=>{
  const state=data();state.mandates=[{occurrences:[{period:'2030-01'}]}];state.trash[0].entry.date='2025-01-01';
  assert.deepEqual(journal.summaryMonths(state,'2026-10','2026-08'),['2026-10','2026-09','2026-08']);
  assert.deepEqual(journal.summaryMonths(state,'2026-10','','vizag'),['2026-10']);
});
test('card render preserves journal controls, visible total and unfinished fields',()=>{
  const {c,field}=fixture();
  for(const id of ['entry-title','entry-trip','search','journal-trip','journal-month','journal-total-value']){field(id).value='untouched';field(id).textContent='untouched';}
  c.renderSummary();assert.equal(field('spent').textContent,'INR 300');assert.equal(field('summary-month').value,'2026-10');
  c.summaryTrip='goa';c.summaryMonth='2026-09';c.renderSummary();assert.equal(field('spent').textContent,'INR 3000');
  c.state.entries=[];c.renderSummary();assert.equal(field('spent').textContent,'INR 0');assert.equal(field('summary-month').value,'2026-09');assert.equal(field('summary-trip').value,'goa');
  for(const id of ['entry-title','entry-trip','search','journal-trip','journal-month','journal-total-value'])assert.equal(field(id).value,'untouched');
});
test('trip card scope fixes its trip and accepts all months or a selected month',()=>{
  const {c,field}=fixture();c.tripPageId='goa';c.summaryTrip='goa';c.summaryMonth='';c.renderSummary();
  assert.equal(field('summary-trip').disabled,true);assert.equal(field('summary-trip').options.length,1);assert.equal(field('spent').textContent,'INR 3200');
  c.summaryMonth='2026-10';c.renderSummary();assert.equal(field('spent').textContent,'INR 200');
});
test('delete confirmation describes a full loan and cancellation performs no mutation',()=>{
  const {c,field,calls}=fixture();const entry=c.state.entries.find(e=>e.id==='loan');c.openDelete(entry);
  assert.equal(field('delete-dialog').open,true);assert.match(field('delete-summary').textContent,/Loan|Money lent/);
  assert.match(field('delete-details').textContent,/All 1 repayment records/);assert.match(field('delete-details').textContent,/Goa/);
  c.closeDelete();assert.equal(c.deletingId,null);assert.equal(field('delete-dialog').open,false);assert.equal(calls.filter(x=>String(x[0]).startsWith('/api')).length,0);
});
test('mandate deletion explains unpaid reversal and no duplicate request can be submitted',async()=>{
  const {c,field,calls}=fixture();c.openDelete({...c.state.entries[0],mandateId:'netflix',mandateMonth:'2026-10'});
  assert.equal(field('delete-mandate').hidden,false);assert.match(field('delete-mandate').textContent,/return to unpaid/);
  let finish,count=0;c.api=()=>{count++;return new Promise(resolve=>finish=resolve);};
  const request=c.confirmDelete();assert.equal(field('confirm-delete').disabled,true);c.closeDelete();assert.equal(field('delete-dialog').open,true);
  await c.confirmDelete();assert.equal(count,1);finish(c.state);await request;
  assert.equal(c.trashBusy,false);assert.equal(field('delete-dialog').open,false);assert.ok(calls.some(x=>x[0]==='saved'));
});
test('failed deletes keep dialog and draft; successful delete resets only the deleted edit',async()=>{
  const {c,field,calls}=fixture();const entry=c.state.entries[0];c.openDelete(entry);c.editing='other-entry';
  const before=c.state;c.api=async()=>{throw Error('Save could not be confirmed. Check your journal before retrying.');};
  await c.confirmDelete();assert.equal(c.state,before);assert.equal(field('delete-dialog').open,true);assert.match(field('delete-error').textContent,/could not be confirmed/);assert.equal(calls.filter(x=>x[0]==='reset').length,0);
  c.api=async()=>({...before});await c.confirmDelete();assert.equal(calls.filter(x=>x[0]==='reset').length,0);
  c.openDelete(entry);c.editing=entry.id;await c.confirmDelete();assert.equal(calls.filter(x=>x[0]==='reset').length,1);
});
test('restoration failures retain Trash and explain conflicts; repeated clicks submit once',async()=>{
  const {c,field}=fixture();const before=c.state;let count=0;
  c.api=async()=>{count++;throw Error('This mandate month already has a replacement payment.');};
  await c.restoreRecord('deleted');assert.equal(count,1);assert.equal(c.state,before);assert.match(field('restore-error-deleted').textContent,/replacement payment/);assert.equal(c.trashBusy,false);
  let finish;c.api=()=>{count++;return new Promise(resolve=>finish=resolve);};
  const request=c.restoreRecord('deleted');await c.restoreRecord('deleted');assert.equal(count,2);finish({...before,trash:[]});await request;assert.equal(c.state.trash.length,0);
});

test('an in-flight Trash response cannot repopulate private state after logout',async()=>{
  for(const operation of ['delete','restore']){
    const {c,calls}=fixture();c.deletingId='goa-flight';let finish;
    c.api=()=>new Promise(resolve=>finish=resolve);
    const request=operation==='delete'?c.confirmDelete():c.restoreRecord('deleted');
    c.leaving=true;c.state={entries:[],trash:[]};finish(data());await request;
    assert.equal(c.state.entries.length,0);assert.equal(c.state.trash.length,0);assert.equal(calls.filter(x=>x[0]==='saved').length,0);
  }
});
