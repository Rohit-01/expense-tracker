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

function draft(input, initialKind = 'expense', categories = []) {
  const fields = new Map();
  const field = id => {
    if (!fields.has(id)) fields.set(id, {value:'', textContent:'', checked:false, reset() {}});
    return fields.get(id);
  };
  field('quick-entry').value = input;
  let selectedSubcategory = '';
  const context = {
    $:field,
    kind:initialKind,
    editing:null,
    state:{categories},
    parseQuickMoney,
    today:() => '2026-10-24',
    setKind(value) { context.kind = value; },
    updateSubcategories(prefix, value = '') { selectedSubcategory = value; },
    updateDue() {},
    Date,
    Intl
  };
  vm.runInNewContext(handler + '\nparseQuick();', context);
  return {fields:field, kind:context.kind, selectedSubcategory};
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
