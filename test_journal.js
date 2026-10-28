'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const journal = require('./journal.js');

const expense = (id, date, amountMinor, category='Other', changes={}) => ({id,date,amountMinor,category,kind:'expense',title:id,person:'',notes:'',subcategory:'',...changes});
const loan = (id, date, amountMinor, changes={}) => ({id,date,amountMinor,category:'',kind:'loan',title:id,person:'Borrower',notes:'',subcategory:'',...changes});

function fixture() {
  return {
    entries:[
      expense('petrol','2026-10-03',20000,'Transport'),
      expense('taxi','2026-09-08',30000,'Transport'),
      loan('loan-open','2026-10-04',100000),
      loan('loan-repaid','2026-09-04',50000),
      {id:'reminder',date:'2026-10-05',amountMinor:0,kind:'reminder',title:'Call home',person:'',notes:'',category:'',subcategory:'',dueAt:'2026-10-06T09:00'}
    ],
    repayments:[
      {entryId:'loan-open',amountMinor:25000},
      {entryId:'loan-repaid',amountMinor:50000}
    ],
    mandates:[{
      id:'netflix',title:'Netflix',category:'Home & bills',subcategory:'',amountMinor:59900,
      occurrences:[
        {period:'2026-10',status:'unpaid'},
        {period:'2026-09',status:'paid',paidAmountMinor:49900}
      ]
    }]
  };
}

test('months are unique and newest first, including the current month', () => {
  assert.deepEqual(journal.months(fixture(),'2026-11'),['2026-11','2026-10','2026-09']);
});

test('entry type, search, and month filters combine', () => {
  const state=fixture();
  assert.deepEqual(journal.entries(state,'expense','transport','2026-10').map(e=>e.id),['petrol']);
  assert.deepEqual(journal.entries(state,'loan','','2026-09').map(e=>e.id),['loan-repaid']);
  assert.deepEqual(journal.entries(state,'reminder','','2026-10').map(e=>e.id),['reminder']);
  state.entries[0].dueAt='2026-10-10T09:00';
  assert.deepEqual(journal.entries(state,'reminder','petrol','2026-10').map(e=>e.id),['petrol']);
});

test('visible totals use expenses, outstanding loans, and zero for standalone reminders', () => {
  const state=fixture();
  const remaining=entry=>entry.amountMinor-state.repayments.filter(r=>r.entryId===entry.id).reduce((sum,r)=>sum+r.amountMinor,0);
  assert.equal(journal.totalMinor(journal.entries(state,'all','','2026-10'),null,remaining),95000);
  assert.equal(journal.totalMinor(journal.entries(state,'loan','',''),null,remaining),75000);
  assert.equal(journal.totalMinor(journal.entries(state,'reminder','','2026-10'),null,remaining),0);
  assert.equal(journal.totalMinor([],null,remaining),0);
});

test('mandate filtering and totals preserve paid historical amounts', () => {
  const state=fixture();
  const october=journal.mandates(state,'home','2026-10');
  assert.equal(october.length,1);
  assert.deepEqual(october[0].occurrences.map(o=>o.period),['2026-10']);
  assert.equal(journal.totalMinor([],october,()=>0),59900);
  assert.equal(journal.totalMinor([],journal.mandates(state,'',''),()=>0),109800);
  assert.deepEqual(journal.mandates(state,'transport','2026-10'),[]);
  assert.deepEqual(journal.mandates(state,'','2026-08'),[]);
});

test('trip filter combines with type, month and category search without duplicating records',()=>{
  const state=fixture();state.trips=[{id:'goa',name:'Goa',destination:'Panaji'},{id:'vizag',name:'Vizag'}];
  state.entries[0].tripId='goa';state.entries[1].tripId='goa';state.entries[2].tripId='vizag';
  assert.deepEqual(journal.entries(state,'expense','transport','2026-10','goa').map(e=>e.id),['petrol']);
  assert.deepEqual(journal.entries(state,'all','panaji','','goa').map(e=>e.id),['petrol','taxi']);
  assert.equal(journal.entries(state,'all','','','none').length,2);
  assert.equal(journal.entries(state,'all','','','').length,5);
  assert.equal(journal.entries(state,'all','','','unknown').length,0);
  assert.equal(journal.totalMinor(journal.entries(state,'expense','transport','','goa'),null,()=>0),50000);
  assert.deepEqual(journal.mandates(state,'','','goa'),[]);
  assert.equal(journal.mandates(state,'','','none').length,1);
});

test('trip summary includes every month and only outstanding loans and open reminders',()=>{
  const state=fixture();for(const e of state.entries)e.tripId='goa';
  state.entries[2].dueAt='2026-10-10T09:00';state.entries[3].dueAt='2026-10-10T09:00';
  assert.deepEqual(journal.tripSummary(state,'goa'),{spending:50000,outstanding:75000,reminders:2});
  const subset=journal.tripState(state,'goa');
  assert.equal(subset.entries.length,5);assert.equal(subset.mandates.length,0);
  assert.deepEqual(journal.months(subset,'2026-11'),['2026-11','2026-10','2026-09']);
  state.repayments.push({entryId:'loan-open',amountMinor:75000});state.entries[4].reminderDone=true;
  assert.deepEqual(journal.tripSummary(state,'goa'),{spending:50000,outstanding:0,reminders:0});
  assert.deepEqual(journal.tripSummary(state,'other'),{spending:0,outstanding:0,reminders:0});
  assert.equal(state.entries.length,5);
});
