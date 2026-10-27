'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const parseQuickMoney = require('./quick_parse.js');

// Execute the real quick-entry handler with form fields, without opening private data.
const appSource = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
const start = appSource.indexOf('function parseQuick() {');
const end = appSource.indexOf('function selectFilter(', start);
assert.ok(start >= 0 && end > start, 'Quick-entry handler must be present');
const handler = appSource.slice(start, end);

const defaultCategories = ['Shopping','Food & groceries','Transport','Home & bills','Health','Learning','Travel','Other'].map(name => ({name,subcategories:[]}));

function draft(input, initialKind = 'expense', categories = defaultCategories) {
  const fields = new Map();
  const field = id => {
    if (!fields.has(id)) fields.set(id, {value:'', textContent:'', checked:false, reset() {}});
    return fields.get(id);
  };
  field('quick-entry').value = input;
  field('entry-form').reset = () => {
    for (const [id, element] of fields) if (id.startsWith('entry-') && id !== 'entry-form') element.value = '';
    field('has-reminder').checked = false;
  };
  let selectedSubcategory = '';
  const context = {
    $:field,
    kind:initialKind,
    editing:null,
    state:{categories},
    parseQuickMoney,
    parseQuickCategory:parseQuickMoney.parseQuickCategory,
    quickDraft:null,
    today:() => '2026-10-24',
    setKind(value) { context.kind = value; },
    updateSubcategories(prefix, value = '') { selectedSubcategory = value; },
    updateDue() {},
    Date,
    Intl
  };
  vm.createContext(context);
  vm.runInContext(handler + '\nparseQuick();', context);
  return {fields:field, kind:context.kind, selectedSubcategory,
    change(text) { field('quick-entry').value=text; vm.runInContext('markQuickDraftChanged()',context); },
    refill() { vm.runInContext('parseQuick()',context); }
  };
}

test('expense draft fills amount, title, category, and original note', () => {
  const result = draft('petrol for rupees 200');
  assert.equal(result.kind, 'expense');
  assert.equal(result.fields('entry-amount').value, '200');
  assert.equal(result.fields('entry-title').value, 'petrol');
  assert.equal(result.fields('entry-category').value, 'Transport');
  assert.equal(result.fields('entry-notes').value, 'petrol for rupees 200');
});

test('custom subcategory still matches the original sentence', () => {
  const result = draft('Spent 200 rupees on diet coke', 'expense',
    [{name:'Food & groceries', subcategories:['Diet Coke']}]);
  assert.equal(result.fields('entry-amount').value, '200');
  assert.equal(result.fields('entry-title').value, 'diet coke');
  assert.equal(result.fields('entry-category').value, 'Food & groceries');
  assert.equal(result.selectedSubcategory, 'Diet Coke');
});

test('loans, mandates, and reminders retain their form behavior', () => {
  const loan = draft('Lent ₹5,000 to Rahul');
  assert.equal(loan.kind, 'loan');
  assert.equal(loan.fields('entry-amount').value, '5000');
  assert.equal(loan.fields('entry-person').value, 'Rahul');
  assert.equal(loan.fields('entry-title').value, 'Loan to Rahul');

  const mandate = draft('Netflix monthly 499', 'mandate');
  assert.equal(mandate.kind, 'mandate');
  assert.equal(mandate.fields('entry-amount').value, '499');
  assert.equal(draft('Netflix 499 monthly', 'mandate').fields('entry-amount').value, '499');

  const reminder = draft('Remind me to pay rent tomorrow at 9am');
  assert.equal(reminder.kind, 'reminder');
  assert.equal(reminder.fields('entry-amount').value, '');
  assert.equal(reminder.fields('has-reminder').checked, true);
  assert.equal(reminder.fields('entry-due').value, '2026-10-25T09:00');
});

test('unclear amounts stay blank and explain what the user must enter', () => {
  for (const [input, message] of [
    ['Bought iPhone 14', 'Could not identify a clear amount'],
    ['Paid ₹200 for petrol and ₹50 for parking', 'More than one amount found']
  ]) {
    const result = draft(input);
    assert.equal(result.fields('entry-amount').value, '', input);
    assert.match(result.fields('parse-hint').textContent, new RegExp(message), input);
  }
});


test('pizza fills the amount and food category; unclear categories ask for review', () => {
  const pizza = draft('Ordered pizza for 2000');
  assert.equal(pizza.fields('entry-amount').value, '2000');
  assert.equal(pizza.fields('entry-category').value, 'Food & groceries');
  assert.equal(pizza.fields('entry-notes').value, 'Ordered pizza for 2000');
  for (const input of ['200 for something unusual', '200 for pizza and petrol']) {
    const result = draft(input);
    assert.equal(result.fields('entry-category').value, 'Other');
    assert.match(result.fields('parse-hint').textContent, /Category unclear/);
  }
});

test('changed text flags a stale draft without changing manual edits; refill updates it', () => {
  const result = draft('200 petrol');
  const initialHint = result.fields('parse-hint').textContent;
  result.fields('entry-title').value = 'My manual title';
  result.fields('entry-amount').value = '250';
  result.fields('entry-category').value = 'Shopping';
  result.change('Ordered pizza for 2000');
  assert.equal(result.fields('parse-hint').textContent, 'Text changed. Click Fill details to update.');
  assert.equal(result.fields('entry-title').value, 'My manual title');
  assert.equal(result.fields('entry-amount').value, '250');
  assert.equal(result.fields('entry-category').value, 'Shopping');
  result.change('200 petrol');
  assert.equal(result.fields('parse-hint').textContent, initialHint);
  result.change('Ordered pizza for 2000');
  result.refill();
  assert.equal(result.fields('entry-amount').value, '2000');
  assert.equal(result.fields('entry-category').value, 'Food & groceries');
  assert.equal(result.fields('entry-notes').value, 'Ordered pizza for 2000');
  assert.match(result.fields('parse-hint').textContent, /^Draft filled/);
  result.change('');
  assert.match(result.fields('parse-hint').textContent, /^Text changed/);
  assert.equal(result.fields('entry-amount').value, '2000');
  assert.ok(appSource.includes("$('quick-entry').addEventListener('input',markQuickDraftChanged)"));
});
