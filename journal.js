'use strict';

var pocketJournal = (() => {
  const searchText = value => String(value || '').toLocaleLowerCase();

  function months(state, currentMonth) {
    const values = new Set([currentMonth]);
    for (const entry of state.entries || []) if (/^\d{4}-\d{2}/.test(entry.date || '')) values.add(entry.date.slice(0, 7));
    for (const mandate of state.mandates || []) for (const occurrence of mandate.occurrences || []) values.add(occurrence.period);
    return [...values].sort().reverse();
  }

  function entries(state, type, query, month) {
    const needle = searchText(query).trim();
    return (state.entries || []).filter(entry => {
      const typeMatches = type === 'all' || (type === 'reminder' ? entry.kind === 'reminder' || entry.dueAt : entry.kind === type);
      const monthMatches = !month || (entry.date || '').slice(0, 7) === month;
      const haystack = [entry.title, entry.person, entry.notes, entry.category, entry.subcategory].map(searchText).join(' ');
      return typeMatches && monthMatches && haystack.includes(needle);
    });
  }

  function mandates(state, query, month) {
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

  return {months, entries, mandates, totalMinor};
})();

if (typeof module !== 'undefined' && module.exports) module.exports = pocketJournal;
