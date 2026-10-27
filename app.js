'use strict';
const $ = id => document.getElementById(id);
let state = {entries: [], repayments: [], categories: [], mandates: [], sync: {}}, kind = 'expense', editing = null, filter = 'all', repayId = null, settleId = null, mandateEditing = null;
let toastTimer;
let quickDraft = null;
const expectedAccount = document.querySelector('meta[name="pocket-account"]')?.content;
let leaving = false;
function leaveAccount(destination = '/login') {
  leaving = true;
  state = {entries: [], repayments: [], categories: [], mandates: [], sync: {}};
  document.body.replaceChildren(node('p', 'notice', 'Opening your account…'));
  window.location.replace(destination);
}
const currency = new Intl.NumberFormat('en-IN', {style:'currency',currency:'INR',maximumFractionDigits:2});
const fmt = minor => currency.format(minor / 100);
function today() { return new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Kolkata',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date()); }
function shortDate(value) { return new Date(value.slice(0,10)+'T12:00:00+05:30').toLocaleDateString('en-IN',{day:'numeric',month:'short',timeZone:'Asia/Kolkata'}); }
function dueDate(value) { return new Date(value+':00+05:30'); }
function dueText(value) { return dueDate(value).toLocaleString('en-IN',{day:'numeric',month:'short',year:'numeric',hour:'numeric',minute:'2-digit',timeZone:'Asia/Kolkata'})+' IST'; }
function left(entry) { return entry.amountMinor - state.repayments.filter(r=>r.entryId===entry.id).reduce((sum,r)=>sum+r.amountMinor,0); }
function isActive(entry) { return entry.dueAt && !entry.reminderDone && (entry.kind !== 'loan' || left(entry)>0); }
function monthName(value) { return new Date(value+'-01T12:00:00+05:30').toLocaleDateString('en-IN',{month:'long',year:'numeric',timeZone:'Asia/Kolkata'}); }
function node(tag, className, text) { const el=document.createElement(tag); if(className) el.className=className; if(text!==undefined) el.textContent=text; return el; }
function action(text, callback) { const el=node('button','text-button',text); el.type='button'; el.addEventListener('click',callback); return el; }
function error(id,message) { if ($(id)) { $(id).textContent=message; $(id).hidden=!message; } }
function toast(message) { clearTimeout(toastTimer); $('toast').textContent=message; $('toast').hidden=false; toastTimer=setTimeout(()=>$('toast').hidden=true,5000); }
async function api(path, data) {
  const options=data?{method:'POST',headers:{'Content-Type':'application/json','X-LifeOS-Token':document.querySelector('meta[name="lifeos-token"]').content},body:JSON.stringify(data)}:{};
  const response=await fetch(path,options);
  if (response.status === 401 && expectedAccount) { leaveAccount(); throw new Error('Please log in again.'); }
  const result=await response.json();
  if (expectedAccount && ((result.account && result.account.id !== expectedAccount) || (result.ownerId && result.ownerId !== expectedAccount))) { leaveAccount('/'); throw new Error('Account changed.'); }
  if(!response.ok) throw new Error(result.error || 'Could not save. Please try again.');
  return result;
}
function saved(message) {
  clearJournalTotal();
  toast(state.storageLocation==='vps' ? message : message+(state.sync.ok?' · synced to Obsidian':' · saved on this Mac'));
  render();
}
function clearJournalTotal() { $('journal-total-value').hidden=true; $('journal-total-value').textContent=''; }
function syncMonthFilter() {
  const select=$('journal-month'), selected=select.value;
  select.replaceChildren();
  const all=node('option','','All months'); all.value=''; select.append(all);
  for(const value of pocketJournal.months(state,today().slice(0,7))) { const option=node('option','',monthName(value)); option.value=value; select.append(option); }
  select.value=[...select.options].some(option=>option.value===selected)?selected:'';
}
function visibleJournal() {
  const query=$('search').value, month=$('journal-month').value;
  return filter==='mandate'
    ? {entries:[], mandateViews:pocketJournal.mandates(state,query,month)}
    : {entries:pocketJournal.entries(state,filter,query,month), mandateViews:null};
}
function setKind(value) {
  kind=value;
  const mandate=kind==='mandate';
  document.querySelectorAll('[data-kind]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.kind===kind)));
  $('amount-field').hidden=kind==='reminder'; $('entry-amount').required=kind!=='reminder';
  $('person-field').hidden=kind!=='loan'; $('entry-person').required=kind==='loan';
  $('category-field').hidden=!['expense','mandate'].includes(kind);
  $('title-label').textContent=mandate?'Mandate name':kind==='reminder'?'What do you need to remember?':kind==='loan'?'What is the loan for?':'What was it for?';
  $('entry-title').placeholder=mandate?'e.g. Netflix or monthly SIP':kind==='reminder'?'e.g. Pay electricity bill':kind==='loan'?'e.g. Help with rent':'e.g. Headphones, groceries, dinner';
  $('amount-label').textContent=mandate?'Monthly amount · INR':'Amount · INR';
  $('date-label').textContent=mandate?'First due date':'Entry date';
  $('mandate-hint').hidden=!mandate;
  $('reminder-toggle').hidden=mandate; $('notes-field').hidden=mandate;
  $('has-reminder').disabled=kind==='reminder'||mandate;
  if(kind==='reminder') $('has-reminder').checked=true;
  if(mandate) $('has-reminder').checked=false;
  $('reminder-optional').textContent=kind==='reminder'?'Required':'Optional';
  updateDue();
  $('save-entry').textContent=editing?'Save changes':kind==='expense'?'Save expense ↗':kind==='loan'?'Save money lent ↗':kind==='reminder'?'Save reminder ↗':'Save mandate ↗';
}
function updateDue() { const active=kind!=='mandate'&&$('has-reminder').checked; $('due-field').hidden=!active; $('entry-due').required=active; }
function fillSelect(select, values, selected, emptyLabel) {
  select.replaceChildren();
  if(emptyLabel!==undefined) { const option=node('option','',emptyLabel); option.value=''; select.append(option); }
  for(const value of values) { const option=node('option','',value); option.value=value; select.append(option); }
  select.value=values.includes(selected)?selected:(emptyLabel!==undefined?'':values[0]||'');
}
function updateSubcategories(prefix, selected='') {
  const category=$(prefix+'-category').value;
  const names=(state.categories.find(c=>c.name===category)?.subcategories||[]);
  fillSelect($(prefix+'-subcategory'),names,selected,'None');
}
function renderCategories() {
  const names=state.categories.map(c=>c.name);
  for(const prefix of ['entry','mandate']) {
    const previous=$(prefix+'-category').value;
    const sub=$(prefix+'-subcategory').value;
    fillSelect($(prefix+'-category'),names,previous);
    updateSubcategories(prefix,sub);
  }
  const parent=$('category-parent').value;
  fillSelect($('category-parent'),names,parent,'New main category');
}
function resetForm() {
  quickDraft=null;
  editing=null; $('entry-form').reset(); $('entry-date').value=today(); $('quick-entry').value='';
  updateSubcategories('entry');
  $('cancel-edit').hidden=true; $('entry-heading').textContent='Write it down';
  $('parse-hint').textContent='Or fill in the fields below.'; error('form-error',''); setKind('expense');
}
function editEntry(entry) {
  quickDraft=null;
  editing=entry.id; setKind(entry.kind); $('entry-heading').textContent='Edit entry';
  $('entry-title').value=entry.title; $('entry-amount').value=entry.amountMinor?String(entry.amountMinor/100):'';
  $('entry-person').value=entry.person; $('entry-category').value=entry.category||'Other'; updateSubcategories('entry',entry.subcategory||''); $('entry-date').value=entry.date;
  $('entry-notes').value=entry.notes; $('has-reminder').checked=Boolean(entry.dueAt); $('entry-due').value=entry.dueAt;
  $('cancel-edit').hidden=false; updateDue(); error('form-error','');
  $('entry-heading').scrollIntoView({behavior:'smooth',block:'start'}); $('entry-title').focus({preventScroll:true});
}
function render() {
  if (leaving) return;
  if (!Array.isArray(state.categories) || !Array.isArray(state.mandates)) {
    $('sync-status').textContent='Restart Pocket';
    error('global-error','Pocket is still running the older server. Close the old Pocket process, start it again, then refresh this page.');
    return;
  }
  if (state.account) {
    $('account-controls').hidden = false;
    $('account-name').textContent = state.account.username;
    $('account-name').title = state.account.username;
    $('open-obsidian').hidden = !state.account.isOwner;
  }
  renderCategories();
  syncMonthFilter();
  const month=today().slice(0,7), expenses=state.entries.filter(e=>e.kind==='expense'&&e.date.startsWith(month));
  const loans=state.entries.filter(e=>e.kind==='loan'&&left(e)>0), reminders=state.entries.filter(isActive).sort((a,b)=>a.dueAt.localeCompare(b.dueAt));
  $('spent').textContent=fmt(expenses.reduce((s,e)=>s+e.amountMinor,0));
  $('spent-meta').textContent=expenses.length?`${expenses.length} expense${expenses.length===1?'':'s'} this month`:'No expenses this month';
  $('outstanding').textContent=fmt(loans.reduce((s,e)=>s+left(e),0));
  $('loan-meta').textContent=loans.length?`${loans.length} loan${loans.length===1?'':'s'} still open`:'No open loans';
  $('reminder-count').textContent=String(reminders.length);
  const overdue=reminders.filter(e=>dueDate(e.dueAt)<new Date()).length;
  $('reminder-meta').textContent=overdue?`${overdue} past due · ready to review`:reminders.length?'A little nudge for later':'Nothing on your mind';
  const showingMandates=filter==='mandate';
  const visible=visibleJournal(), visibleCount=showingMandates?visible.mandateViews.length:visible.entries.length;
  $('entry-count').textContent=showingMandates?`${visibleCount} ${visibleCount===1?'mandate':'mandates'}`:`${visibleCount} ${visibleCount===1?'entry':'entries'}`;
  $('journal-list').hidden=showingMandates;
  $('mandates-section').hidden=!showingMandates;
  $('reminders-section').hidden=showingMandates;
  $('search').placeholder=showingMandates?'Search mandates…':'Search your journal…';
  $('sync-status').textContent=state.sync.ok?(state.sync.remoteExport?'All changes saved':'Synced to Obsidian'):state.sync.enabled?'Saved · export needs attention':state.storageLocation==='vps'?'Saved on your VPS':'Saved on this Mac';
  $('sync-status').classList.toggle('warning',!state.sync.ok);
  error('global-error',state.sync.enabled&&!state.sync.ok?state.sync.message:'');
  const list=visible.entries;
  list.sort((a,b)=>b.date.localeCompare(a.date)||b.createdAt.localeCompare(a.createdAt));
  const journal=$('journal-list'); journal.replaceChildren();
  if(!showingMandates&&!list.length) {
    const box=node('div','empty'); box.append(node('span','empty-symbol','↗'),node('h3','',state.entries.length?'No matching entries':'A fresh page for your money'),node('p','',state.entries.length?'Try a different filter or search.':'Add your first expense, loan, or reminder. Small entries make the big picture clearer.')); journal.append(box);
  }
  for(const entry of list) {
    const item=node('article','journal-item'), icon=node('div','entry-icon '+entry.kind,entry.kind==='loan'?'↙':entry.kind==='reminder'?'◷':'↗'); icon.setAttribute('aria-hidden','true');
    const main=node('div','item-main'), amount=node('div','item-amount');
    main.append(node('div','item-title',entry.title));
    main.append(node('div','item-meta',[shortDate(entry.date),entry.kind==='loan'?`Lent to ${entry.person}`:entry.kind==='expense'?entry.category+(entry.subcategory?' → '+entry.subcategory:''):'Reminder',entry.mandateMonth?'Mandate '+entry.mandateMonth:'',entry.kind==='reminder'&&entry.reminderDone?'Completed':''].filter(Boolean).join(' · ')));
    if(entry.kind!=='reminder') { const balance=entry.kind==='loan'?left(entry):entry.amountMinor; amount.textContent=fmt(balance); amount.append(node('small',entry.kind==='loan'&&balance===0?'repaid-status':'',entry.kind==='loan'?(balance===0?'✓ Repaid':'still to collect'):'spent')); }
    else { amount.append(node('small','',entry.reminderDone?'Done':'Reminder')); }
    if(entry.notes) main.append(node('div','item-note',entry.notes));
    if(entry.kind==='loan') {
      const received=entry.amountMinor-left(entry), progress=node('div','loan-progress'), fill=node('span');
      fill.style.width=(received/entry.amountMinor*100)+'%'; progress.append(fill); progress.setAttribute('role','progressbar'); progress.setAttribute('aria-label','Loan repaid'); progress.setAttribute('aria-valuenow',String(Math.round(received/entry.amountMinor*100))); progress.setAttribute('aria-valuemin','0');progress.setAttribute('aria-valuemax','100');
      // Inline CSS properties are applied by the trusted local script, never user input.
      main.append(progress,node('div','item-meta',`${fmt(received)} received of ${fmt(entry.amountMinor)}`));
      const repayments=state.repayments.filter(r=>r.entryId===entry.id);
      if(repayments.length) { const details=node('details','repay-history'), ul=node('ul'); details.append(node('summary','',`${repayments.length} repayment${repayments.length===1?'':'s'}`)); for(const r of repayments) ul.append(node('li','',`${shortDate(r.date)} · ${fmt(r.amountMinor)}${r.notes?' · '+r.notes:''}`)); details.append(ul);main.append(details); }
    }
    if(entry.dueAt) main.append(node('div','item-meta',`${entry.reminderDone?'Reminder completed':'Reminder'} · ${dueText(entry.dueAt)}`));
    const actions=node('div','item-actions'); actions.append(action('Edit',()=>editEntry(entry)));
    if(entry.kind==='loan'&&left(entry)>0) actions.append(action('Record repayment',()=>openRepay(entry)),action('Mark repaid',()=>openSettle(entry)));
    if(entry.dueAt) {
      const link=node('a','text-button','Calendar event ↗'); link.href='/api/reminder.ics?id='+encodeURIComponent(entry.id);link.download='lifeos-reminder.ics';actions.append(link);
      actions.append(action(entry.reminderDone?'Reopen reminder':'Mark reminder done',()=>toggle(entry,!entry.reminderDone)));
    }
    item.append(icon,main,amount,actions);journal.append(item);
  }
  const radar=$('reminder-list'); radar.replaceChildren();
  if(!reminders.length) radar.append(node('div','reminder-empty','All clear. Add a reminder to an entry whenever you need a nudge.'));
  for(const entry of reminders) {
    const item=node('div','reminder-item'), tile=node('div','date-tile');
    const parts=entry.dueAt.slice(0,10).split('-'), d=new Date(parts.join('-')+'T12:00:00+05:30');
    tile.append(node('span','',d.toLocaleDateString('en-IN',{month:'short',timeZone:'Asia/Kolkata'}).toUpperCase()),node('strong','',String(Number(parts[2]))));
    const main=node('div','reminder-main');main.append(node('div','item-title',entry.kind==='loan'?`Collect from ${entry.person}`:entry.title),node('div','item-meta',dueText(entry.dueAt)));
    if(dueDate(entry.dueAt)<new Date()) main.append(node('span','due-label','Past due'));
    if(entry.kind==='loan') main.append(node('div','item-meta',`${fmt(left(entry))} outstanding`));
    const actions=node('div','item-actions');actions.append(action('Mark done',()=>toggle(entry,true)));
    const link=node('a','text-button','Calendar event ↗');link.href='/api/reminder.ics?id='+encodeURIComponent(entry.id);link.download='lifeos-reminder.ics';actions.append(link);main.append(actions);item.append(tile,main);radar.append(item);
  }
  renderMandates(visible.mandateViews);
}
function renderMandates(views) {
  const list=$('mandates-list'); list.replaceChildren();
  if(!views) return;
  if(!views.length) { list.append(node('div','reminder-empty',state.mandates.length?'No matching mandates. Try another search or month.':'No mandates yet. Add a monthly plan to keep track of payments.')); return; }
  for(const {mandate,occurrences} of views) {
    const card=node('article','mandate-card');
    const heading=node('div','mandate-heading'), text=node('div');
    text.append(node('h3','',mandate.title),node('p','item-meta',`${fmt(mandate.amountMinor)} monthly · ${mandate.category}${mandate.subcategory?' → '+mandate.subcategory:''}`));
    const controls=node('div','mandate-controls');
    controls.append(action('Edit',()=>openMandate(mandate)),action(mandate.activeWindows.at(-1).to===null?'Pause':'Resume',()=>toggleMandate(mandate)));
    heading.append(text,controls); card.append(heading);
    if(mandate.activeWindows.at(-1).to!==null) card.append(node('p','field-note','Paused · paid history is kept.'));
    const rows=node('div','mandate-months');
    if(!occurrences.length) rows.append(node('p','field-note','The first month has not arrived yet.'));
    for(const occurrence of occurrences) {
      const row=node('div','mandate-month');
      const details=node('div'); details.append(node('strong','',new Date(occurrence.dueDate+'T12:00:00+05:30').toLocaleDateString('en-IN',{month:'long',year:'numeric',timeZone:'Asia/Kolkata'})),node('span','item-meta',`Due ${shortDate(occurrence.dueDate)}${occurrence.status==='paid'?' · Recorded '+fmt(occurrence.paidAmountMinor):occurrence.dueDate<today()?' · Overdue':''}`));
      row.append(details);
      if(occurrence.status==='paid') row.append(node('span','mandate-paid','✓ Paid'));
      else { const button=node('button','button primary mandate-pay','Paid'); button.type='button'; button.setAttribute('aria-label',`Mark ${mandate.title} for ${occurrence.period} paid`); button.addEventListener('click',()=>payMandate(mandate,occurrence,button)); row.append(button); }
      rows.append(row);
    }
    card.append(rows); list.append(card);
  }
}
async function payMandate(mandate,occurrence,button) {
  button.disabled=true;
  try { state=await api('/api/mandate-payments',{id:mandate.id,period:occurrence.period}); saved('Payment recorded as expense'); }
  catch(e) { button.disabled=false; error('global-error',e.message); }
}
async function toggleMandate(mandate) {
  try { const paused=mandate.activeWindows.at(-1).to===null; state=await api('/api/mandate-status',{id:mandate.id,paused}); saved(paused?'Mandate paused':'Mandate resumed'); }
  catch(e) { error('global-error',e.message); }
}
function openMandate(mandate=null) {
  mandateEditing=mandate?.id||null; $('mandate-form').reset(); error('mandate-error','');
  $('mandate-form-heading').textContent=mandate?'Edit monthly mandate':'Add monthly mandate';
  $('mandate-first-date').value=mandate?.firstDueDate||today(); $('mandate-first-date').readOnly=Boolean(mandate);
  $('mandate-title').value=mandate?.title||''; $('mandate-amount').value=mandate?String(mandate.amountMinor/100):'';
  if(mandate) $('mandate-category').value=mandate.category;
  updateSubcategories('mandate',mandate?.subcategory||'');
  $('mandate-dialog').showModal(); $('mandate-title').focus();
}
function startMandate() {
  resetForm(); setKind('mandate');
  $('entry-heading').scrollIntoView({behavior:'smooth',block:'start'});
  $('entry-title').focus({preventScroll:true});
}
function openRepay(entry) {
  repayId=entry.id; $('repay-summary').textContent=`${entry.person} · ${fmt(left(entry))} outstanding`;
  $('repay-amount').value='';$('repay-amount').max=String(left(entry)/100);$('repay-date').value=today();$('repay-notes').value='';error('repay-error','');$('repay-dialog').showModal();$('repay-amount').focus();
}
function openSettle(entry) {
  settleId=entry.id; error('settle-error','');
  $('settle-summary').textContent=`${entry.person} · ${fmt(left(entry))} outstanding`;
  $('settle-dialog').showModal(); $('confirm-settle').focus();
}
function closeSettle() { settleId=null; $('settle-dialog').close(); }
async function settleLoan() {
  const button=$('confirm-settle'), entry=state.entries.find(item=>item.id===settleId&&item.kind==='loan'), amount=entry?left(entry):0;
  if(!entry||amount<=0) { error('settle-error','This loan is already repaid or no longer available.'); return; }
  button.disabled=true;
  try { state=await api('/api/repayments',{entryId:entry.id,amount:String(amount/100),date:today(),notes:'Marked fully repaid'}); closeSettle(); saved('Loan marked repaid'); }
  catch(e) { error('settle-error',e.message); }
  finally { button.disabled=false; }
}
async function toggle(entry,done) {
  try { state=await api('/api/reminders',{id:entry.id,done});saved(done?'Reminder marked done':'Reminder reopened'); }
  catch(e) {error('global-error',e.message);}
}
function parseQuick() {
  const text=$('quick-entry').value.trim(); if(!text) { $('parse-hint').textContent='Write a short sentence first, or use the fields below.';return; }
  if(editing) { $('parse-hint').textContent='Finish or cancel your edit before starting a new quick entry.';return; }
  const lower=text.toLowerCase();
  const parsedKind=kind==='mandate'?'mandate':/\b(remind|reminder)\b/.test(lower)?'reminder':/\b(lent|lend|loan|loaned)\b/.test(lower)?'loan':'expense';
  $('entry-form').reset();setKind(parsedKind);$('entry-date').value=today();
  const money=parsedKind==='reminder'?null:parseQuickMoney(text);
  if(money?.amount) $('entry-amount').value=money.amount;
  if(parsedKind==='loan') { const person=text.match(/\bto\s+(.+?)(?=\s+(?:for|on|by|due|tomorrow|today|remind|next)\b|[,;]|$)/i); if(person) $('entry-person').value=person[1].trim(); }
  let title=money?money.title:text.replace(/^\s*(?:remind me to|reminder to|remind me|reminder)\s+/i,'');
  if(parsedKind==='loan') title='Loan to '+($('entry-person').value||'someone');
  $('entry-title').value=title.slice(0,200);$('entry-notes').value=text;
  const category=['expense','mandate'].includes(parsedKind)?parseQuickCategory(text,state.categories):null;
  if(category) { $('entry-category').value=category.category; updateSubcategories('entry',category.subcategory); }
  let inferredDay='', assumedTime=false;
  const explicit=lower.match(/\b(\d{4}-\d{2}-\d{2})\b/);
  if(explicit) inferredDay=explicit[1];
  else if(/\btomorrow\b/.test(lower)) {const d=new Date(today()+'T12:00:00+05:30');d.setUTCDate(d.getUTCDate()+1);inferredDay=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Kolkata',year:'numeric',month:'2-digit',day:'2-digit'}).format(d);}
  else if(/\btoday\b/.test(lower)) inferredDay=today();
  if(inferredDay&&(parsedKind==='reminder'||parsedKind==='loan'||/\b(remind|due)\b/.test(lower))) {
    let hour=9,minute=0;
    const time=lower.match(/\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b/)||lower.match(/\bat\s+(\d{1,2}):(\d{2})\b/);
    if(time) {hour=Number(time[1]);minute=Number(time[2]||0);if(time[3]) hour=hour%12+(time[3]==='pm'?12:0);}
    else assumedTime=true;
    if(hour<24&&minute<60) { $('has-reminder').checked=true;$('entry-due').value=inferredDay+'T'+String(hour).padStart(2,'0')+':'+String(minute).padStart(2,'0');updateDue(); }
  } else if(inferredDay&&['expense','mandate'].includes(parsedKind)) $('entry-date').value=inferredDay;
  const amountHint=money?.issue==='multiple'?'More than one amount found. Enter the total amount yourself before saving.':money?.issue==='missing'?'Could not identify a clear amount. Enter the amount yourself before saving.':'Draft filled. Check the amount, person, and dates before saving.';
  const categoryHint=category?.issue?' Category unclear. Review '+(category.category||'the category')+' before saving.':'';
  $('parse-hint').textContent=amountHint+categoryHint+(assumedTime?' Reminder time defaults to 9:00 AM IST.':'')+(parsedKind==='reminder'&&!inferredDay?' Choose a reminder date and time below.':'');
  quickDraft={text,hint:$('parse-hint').textContent};
}
function markQuickDraftChanged() {
  if(!quickDraft || editing) return;
  $('parse-hint').textContent=$('quick-entry').value.trim()===quickDraft.text?quickDraft.hint:'Text changed. Click Fill details to update.';
}
function selectFilter(value) {
  filter=value; clearJournalTotal();
  document.querySelectorAll('[data-filter]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.filter===value)));
  render();
}
function calculateJournalTotal() {
  const visible=visibleJournal(), total=pocketJournal.totalMinor(visible.entries,visible.mandateViews,left);
  $('journal-total-value').textContent='Visible total · '+fmt(total); $('journal-total-value').hidden=false;
}
document.querySelectorAll('[data-kind]').forEach(b=>b.addEventListener('click',()=>{if(editing&&b.dataset.kind==='mandate') resetForm();setKind(b.dataset.kind);}));
document.querySelectorAll('[data-filter]').forEach(b=>b.addEventListener('click',()=>selectFilter(b.dataset.filter)));
document.querySelectorAll('[data-example]').forEach(b=>b.addEventListener('click',()=>{$('quick-entry').value={expense:'Bought headphones for ₹2,500',loan:'Lent ₹5,000 to Rahul',reminder:'Remind me to pay rent tomorrow at 9am'}[b.dataset.example];markQuickDraftChanged();$('quick-entry').focus();}));
$('quick-entry').addEventListener('input',markQuickDraftChanged);
$('parse-entry').addEventListener('click',parseQuick);$('has-reminder').addEventListener('change',updateDue);$('search').addEventListener('input',()=>{clearJournalTotal();render();});$('journal-month').addEventListener('change',()=>{clearJournalTotal();render();});$('calculate-total').addEventListener('click',calculateJournalTotal);$('cancel-edit').addEventListener('click',resetForm);$('close-repay').addEventListener('click',()=>$('repay-dialog').close());
$('close-settle').addEventListener('click',closeSettle);$('cancel-settle').addEventListener('click',closeSettle);$('confirm-settle').addEventListener('click',settleLoan);
$('settle-dialog').addEventListener('close',()=>{settleId=null;error('settle-error','');});
for(const prefix of ['entry','mandate']) $(prefix+'-category').addEventListener('change',()=>updateSubcategories(prefix));
$('open-category').addEventListener('click',()=>{renderCategories();$('category-dialog').showModal();$('category-name').focus();});
$('close-category').addEventListener('click',()=>$('category-dialog').close());
$('category-form').addEventListener('submit',async event=>{
  event.preventDefault(); error('category-error',''); $('save-category').disabled=true;
  const parent=$('category-parent').value, name=$('category-name').value.trim();
  try {state=await api('/api/categories',{parent,name});$('category-dialog').close();render();if(parent){$('entry-category').value=parent;updateSubcategories('entry',name);}else{$('entry-category').value=name;updateSubcategories('entry');}toast('Category saved');}
  catch(e){error('category-error',e.message);}finally{$('save-category').disabled=false;}
});
$('open-mandate').addEventListener('click',startMandate);
$('close-mandate').addEventListener('click',()=>$('mandate-dialog').close());
$('mandate-form').addEventListener('submit',async event=>{
  event.preventDefault();error('mandate-error','');$('save-mandate').disabled=true;
  try {state=await api('/api/mandates',{id:mandateEditing,title:$('mandate-title').value,amount:$('mandate-amount').value,firstDueDate:$('mandate-first-date').value,category:$('mandate-category').value,subcategory:$('mandate-subcategory').value});$('mandate-dialog').close();saved(mandateEditing?'Mandate updated':'Mandate added');}
  catch(e){error('mandate-error',e.message);}finally{$('save-mandate').disabled=false;}
});
$('entry-form').addEventListener('submit',async event=>{
  event.preventDefault();error('form-error',''); const button=$('save-entry');button.disabled=true;
  try {
    if(kind==='mandate') {
      state=await api('/api/mandates',{title:$('entry-title').value,amount:$('entry-amount').value,firstDueDate:$('entry-date').value,category:$('entry-category').value||'Other',subcategory:$('entry-subcategory').value});
      resetForm(); selectFilter('mandate'); toast('Mandate added'); return;
    }
    const wasEditing=Boolean(editing);
    state=await api('/api/entries',{id:editing,kind,title:$('entry-title').value,amount:$('entry-amount').value,person:$('entry-person').value,category:$('entry-category').value||'Other',subcategory:$('entry-subcategory').value,date:$('entry-date').value,dueAt:$('has-reminder').checked?$('entry-due').value:'',notes:$('entry-notes').value});
    resetForm();saved(wasEditing?'Entry updated':'Entry saved');
  } catch(e) {error('form-error',e.message);}
  finally {button.disabled=false;}
});
$('repay-form').addEventListener('submit',async event=>{
  event.preventDefault();error('repay-error','');$('save-repayment').disabled=true;
  try {state=await api('/api/repayments',{entryId:repayId,amount:$('repay-amount').value,date:$('repay-date').value,notes:$('repay-notes').value});$('repay-dialog').close();saved('Repayment recorded');}
  catch(e) {error('repay-error',e.message);}finally{$('save-repayment').disabled=false;}
});
$('today').textContent=new Date().toLocaleDateString('en-IN',{weekday:'short',day:'numeric',month:'short',year:'numeric',timeZone:'Asia/Kolkata'});
resetForm();
api('/api/state').then(data=>{state=data;clearJournalTotal();render();}).catch(e=>{if(!leaving){error('global-error','Could not connect. Check that Pocket is running, then refresh. '+e.message);$('sync-status').textContent='Offline';}});
setInterval(()=>{if(expectedAccount&&!document.hidden) api('/api/state').then(data=>{state=data;clearJournalTotal();render();}).catch(()=>{});else render();},60000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden) api('/api/state').then(data=>{state=data;clearJournalTotal();render();}).catch(()=>{});});
if (expectedAccount) {
  const channel = 'BroadcastChannel' in window ? new BroadcastChannel('pocket-auth') : null;
  if (channel) channel.onmessage = () => leaveAccount('/');
  $('logout').addEventListener('click', async () => {
    const button = $('logout'); button.disabled = true;
    try { await api('/logout', {}); channel?.postMessage('changed'); leaveAccount(); }
    catch (e) { if (!leaving) { error('global-error', e.message); button.disabled = false; } }
  });
  window.addEventListener('pagehide', () => { document.documentElement.style.visibility = 'hidden'; });
  window.addEventListener('pageshow', event => { if (event.persisted) leaveAccount('/'); });
}

// Owner-only connection controls. Tokens live only in this open dialog, never browser storage.
let syncDialogVersion = 0;
function clearSyncToken() {
  if (!$('obsidian-token-value')) return;
  $('obsidian-token-value').value = '';
  $('obsidian-token-once').hidden = true;
  $('copy-obsidian-token').textContent = 'Copy token';
}
function renderSyncSettings(data) {
  const expires = data.expiresAt ? new Date(data.expiresAt * 1000).toLocaleDateString(undefined, {dateStyle:'medium'}) : '';
  $('obsidian-token-status').textContent = data.active ? `Token active · expires ${expires}` : data.expiresAt ? 'Token expired · generate a new one' : 'No active token';
  $('generate-obsidian-token').textContent = data.active ? 'Replace token' : 'Generate token';
  $('revoke-obsidian-token').hidden = !data.active;
  $('obsidian-token-help').textContent = data.active ? 'Replacing or revoking stops future downloads with the current token. Replacing it requires updating Keychain on your Mac.' : 'Generate a token, then enter it into the Mac setup prompt. Keep it private.';
}
async function syncSettingsAction(path, data) {
  const version = ++syncDialogVersion;
  clearSyncToken(); error('obsidian-error', '');
  $('generate-obsidian-token').disabled = true;
  $('revoke-obsidian-token').disabled = true;
  try {
    const result = await api(path, data);
    if (leaving || version !== syncDialogVersion || !$('obsidian-dialog').open) return;
    renderSyncSettings(result);
    if (result.token) {
      $('obsidian-token-value').value = result.token;
      $('obsidian-token-once').hidden = false;
      $('obsidian-token-value').focus();
    }
    $('generate-obsidian-token').disabled = false;
    $('revoke-obsidian-token').disabled = false;
    if (data && !result.token) $('generate-obsidian-token').focus();
  } catch (e) {
    if (!leaving && version === syncDialogVersion) {
      error('obsidian-error', e.message + ' Close and reopen to check the connection.');
    }
  }
}
$('open-obsidian').addEventListener('click', () => {
  $('obsidian-dialog').showModal();
  $('obsidian-token-status').textContent = 'Loading…';
  $('revoke-obsidian-token').hidden = true;
  syncSettingsAction('/api/obsidian/token');
});
$('close-obsidian').addEventListener('click', () => $('obsidian-dialog').close());
$('obsidian-dialog').addEventListener('close', () => { syncDialogVersion++; clearSyncToken(); });
$('generate-obsidian-token').addEventListener('click', () => syncSettingsAction('/api/obsidian/token', {}));
$('revoke-obsidian-token').addEventListener('click', () => syncSettingsAction('/api/obsidian/token/revoke', {}));
$('copy-obsidian-token').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText($('obsidian-token-value').value); $('copy-obsidian-token').textContent = 'Copied'; }
  catch { error('obsidian-error', 'Select the token and copy it manually.'); }
});
window.addEventListener('pagehide', () => { syncDialogVersion++; clearSyncToken(); });
