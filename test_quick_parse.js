'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const parseQuickMoney = require('./quick_parse.js');

test('common sentence orders and rupee spellings create the same expense draft', () => {
  const phrases = [
    '200 petrol', 'petrol 200', '200 rupees petrol', 'petrol for 200 rupees',
    'petrol for rupees 200', 'petrol for ₹200', '₹200 petrol', '200₹ petrol',
    'Rs 200 petrol', 'petrol for Rs. 200', 'INR 200 petrol', 'petrol for 200 INR',
    'spent 200 on petrol', 'I paid 200 for petrol', 'petrol cost me 200'
  ];
  for (const input of phrases) {
    assert.deepEqual(parseQuickMoney(input), {amount:'200', title:'petrol', issue:''}, input);
  }
});

test('different items, decimals, Indian grouping, and compact amounts', () => {
  const cases = [
    ['Bought headphones for ₹2,500', '2500', 'headphones'],
    ['Spent 14k on scooty repair', '14000', 'scooty repair'],
    ['Coffee for Rs. 89.50', '89.5', 'Coffee'],
    ['Spent 200 rupees on diet coke', '200', 'diet coke'],
    ['Uber for ₹62', '62', 'Uber'],
    ['Groceries worth ₹1,20,000', '120000', 'Groceries'],
    ['Lent 5k to Rahul', '5000', 'to Rahul'],
    ['Netflix monthly 499', '499', 'Netflix monthly'],
    ['Netflix 499 monthly', '499', 'Netflix monthly'],
    ['coffee 80', '80', 'coffee'],
    ['50 snacks', '50', 'snacks'],
    ['Spent 1.5 lakh on scooter', '150000', 'scooter'],
    ['iPhone 14 for 25000', '25000', 'iPhone 14']
  ];
  for (const [input, amount, title] of cases) {
    assert.deepEqual(parseQuickMoney(input), {amount, title, issue:''}, input);
  }
});

test('quantity and product numbers do not replace a clear money amount', () => {
  const cases = [
    ['Bought 2 pens for ₹20', '20', '2 pens'],
    ['2 litres of petrol for ₹200', '200', '2 litres of petrol'],
    ['₹200 for 2 litres petrol', '200', '2 litres petrol'],
    ['Bought iPhone 14', '', 'iPhone 14'],
    ['I bought iPhone 14', '', 'iPhone 14'],
    ['Petrol on 2026-10-24', '', 'Petrol on 2026-10-24']
  ];
  for (const [input, amount, title] of cases) {
    assert.deepEqual(parseQuickMoney(input), {amount, title, issue:amount?'':'missing'}, input);
  }
});

test('multiple or invalid amounts stay empty for manual review', () => {
  const cases = [
    ['Paid ₹200 for petrol and ₹50 for parking', 'multiple'],
    ['Paid 200 for petrol and 50 for parking', 'multiple'],
    ['₹0 petrol', 'missing'],
    ['₹1,000,000,000 petrol', 'missing']
  ];
  for (const [input, issue] of cases) {
    const result = parseQuickMoney(input);
    assert.equal(result.amount, '', input);
    assert.equal(result.issue, issue, input);
  }
});
