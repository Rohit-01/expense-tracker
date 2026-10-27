'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const {parseQuickCategory} = require('./quick_parse.js');
const defaults = ['Shopping','Food & groceries','Transport','Home & bills','Health','Learning','Travel','Other'].map(name => ({name,subcategories:[]}));

test('common purchases match in different sentence orders and capitalization', () => {
  const cases = {
    'Food & groceries':['pizza','groceries','coffee','burgers','biryani','sandwiches'],
    'Transport':['petrol','taxis','Uber','scooty repair'],
    'Shopping':['clothing','electronics','headphones','shirts','iPhone'],
    'Home & bills':['electricity','bills','furniture','Netflix'],
    'Health':['medicines','doctor','pharmacy'],
    'Learning':['courses','books','tuition'],
    'Travel':['flights','hotel','trip']
  };
  for (const [category, terms] of Object.entries(cases)) for (const term of terms) {
    for (const text of [`Ordered ${term} for 2000`,`200 rupees for ${term}`,`Spent 200 on (${term.toUpperCase()})!`]) {
      assert.deepEqual(parseQuickCategory(text,defaults), {category,subcategory:'',issue:''},text);
    }
  }
});

test('custom subcategories take precedence over names, then names over keywords', () => {
  const categories = [...defaults,{name:'Treats',subcategories:['Diet Coke','C++']},{name:'Office',subcategories:[]}];
  assert.deepEqual(parseQuickCategory('Food & groceries: spent 200 on DIET   COKE!',categories),
    {category:'Treats',subcategory:'Diet Coke',issue:''});
  assert.deepEqual(parseQuickCategory('Office: ordered pizza for 2000',categories),
    {category:'Office',subcategory:'',issue:''});
  assert.equal(parseQuickCategory('200 for C++',categories).subcategory,'C++');
  assert.equal(parseQuickCategory('200 for Caa',categories).issue,'missing');
});

test('unrecognized words, substring collisions, and conflicts require review', () => {
  for (const text of ['200 for mystery item','200 for carpet','200 for bookshelf','200 for teaé','200 for hotelier']) {
    assert.deepEqual(parseQuickCategory(text,defaults),{category:'Other',subcategory:'',issue:'missing'},text);
  }
  for (const text of ['200 for pizza and petrol','200 for petrol and pizza','Shopping and Health 200']) {
    assert.deepEqual(parseQuickCategory(text,defaults),{category:'Other',subcategory:'',issue:'ambiguous'},text);
  }
  const duplicate = [...defaults,{name:'Treats',subcategories:['Coke']},{name:'Drinks',subcategories:['Coke']}];
  assert.equal(parseQuickCategory('200 for Coke',duplicate).issue,'ambiguous');
  const multiple = [...defaults,{name:'Treats',subcategories:['Coke','Pizza']}];
  assert.equal(parseQuickCategory('200 for Coke and Pizza',multiple).issue,'ambiguous');
  assert.equal(parseQuickCategory('pizza and coffee for 200',defaults).category,'Food & groceries');
});

test('only categories and subcategories from the supplied account can be selected', () => {
  const limited = [{name:'Other',subcategories:[]}];
  assert.deepEqual(parseQuickCategory('200 petrol',limited),{category:'Other',subcategory:'',issue:'missing'});
  assert.equal(parseQuickCategory('200 diet coke',defaults).subcategory,'');
  assert.deepEqual(parseQuickCategory('200 petrol',[]),{category:'',subcategory:'',issue:'missing'});
});
