'use strict';

var pocketJournal = (() => {
  const searchText = value => String(value || '').toLocaleLowerCase();

  function months(state, currentMonth) {
    const values = new Set([currentMonth]);
    for (const entry of state.entries || []) if (/^\d{4}-\d{2}/.test(entry.date || '')) values.add(entry.date.slice(0, 7));
    for (const mandate of state.mandates || []) for (const occurrence of mandate.occurrences || []) values.add(occurrence.period);
    return [...values].sort().reverse();
  }

  function entries(state, type, query, month, tripId = '') {
    const needle = searchText(query).trim();
    return (state.entries || []).filter(entry => {
      const typeMatches = type === 'all' || (type === 'reminder' ? entry.kind === 'reminder' || entry.dueAt : entry.kind === type);
      const monthMatches = !month || (entry.date || '').slice(0, 7) === month;
      const tripMatches = !tripId || (tripId === 'none' ? !entry.tripId : entry.tripId === tripId);
      const trip = (state.trips || []).find(t => t.id === entry.tripId);
      const haystack = [entry.title, entry.person, entry.notes, entry.category, entry.subcategory, trip?.name, trip?.destination].map(searchText).join(' ');
      return typeMatches && monthMatches && tripMatches && haystack.includes(needle);
    });
  }

  function mandates(state, query, month, tripId = '') {
    if (tripId && tripId !== 'none') return [];
    const needle = searchText(query).trim();
    return (state.mandates || []).filter(mandate =>
      [mandate.title, mandate.category, mandate.subcategory].map(searchText).join(' ').includes(needle)
    ).map(mandate => ({
      mandate,
      occurrences:(mandate.occurrences || []).filter(occurrence => !month || occurrence.period === month)
    })).filter(view => !month || view.occurrences.length);
  }

  function totalMinor(entryList, mandateViews, remaining) {
    if (mandateViews) return mandateViews.reduce((total, view) => total + view.occurrences.reduce((sum, occurrence) =>
      sum + (occurrence.status === 'paid' ? occurrence.paidAmountMinor : view.mandate.amountMinor), 0), 0);
    return entryList.reduce((total, entry) => total + (entry.kind === 'expense' ? entry.amountMinor : entry.kind === 'loan' ? remaining(entry) : 0), 0);
  }

  function tripState(state, tripId) {
    return {...state, entries:(state.entries || []).filter(e => e.tripId === tripId), mandates:[]};
  }

  function tripSummary(state, tripId) {
    const linked = tripState(state, tripId).entries;
    const remaining = e => e.amountMinor - (state.repayments || []).filter(r => r.entryId === e.id).reduce((sum,r) => sum+r.amountMinor,0);
    return {
      spending:linked.filter(e => e.kind === 'expense').reduce((sum,e) => sum+e.amountMinor,0),
      outstanding:linked.filter(e => e.kind === 'loan').reduce((sum,e) => sum+remaining(e),0),
      reminders:linked.filter(e => e.dueAt && !e.reminderDone && (e.kind !== 'loan' || remaining(e)>0)).length
    };
  }

  function summary(state, month, tripId) {
    const selected=entries(state,'all','',month,tripId);
    const remaining=e=>e.amountMinor-(state.repayments||[]).filter(r=>r.entryId===e.id).reduce((sum,r)=>sum+r.amountMinor,0);
    const expenses=selected.filter(e=>e.kind==='expense');
    const loans=selected.filter(e=>e.kind==='loan'&&remaining(e)>0);
    const reminders=selected.filter(e=>e.dueAt&&!e.reminderDone&&(e.kind!=='loan'||remaining(e)>0));
    return {expenses,loans,reminders,spending:expenses.reduce((sum,e)=>sum+e.amountMinor,0),outstanding:loans.reduce((sum,e)=>sum+remaining(e),0)};
  }

  function summaryMonths(state,currentMonth,selectedMonth='',tripId='') {
    const values=new Set([currentMonth]);
    if(selectedMonth) values.add(selectedMonth); // Keep an empty selection after its last entry is trashed.
    for(const entry of entries(state,'all','','',tripId)) if(/^\d{4}-\d{2}/.test(entry.date||'')) values.add(entry.date.slice(0,7));
    return [...values].sort().reverse();
  }

  return {months, entries, mandates, totalMinor, tripState, tripSummary, summary, summaryMonths};
})();

if (typeof module !== 'undefined' && module.exports) module.exports = pocketJournal;
