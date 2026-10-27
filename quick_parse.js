'use strict';

// Quick entry is a draft: prefer explicit money cues, and leave ambiguous amounts for review.
function parseQuickMoney(text) {
  const source = String(text || '').trim();
  const number = '([0-9][0-9,]*(?:\\.[0-9]{1,2})?)';
  const scale = '(?:\\s*(k|lakhs?|crores?)\\b)?';
  const end = '(?![\\d.,A-Za-z]|-\\d)';
  const candidates = new Map();
  const multiplier = {k:1000, lakh:100000, lakhs:100000, crore:10000000, crores:10000000};

  function collect(pattern, strength, minimum = 0) {
    for (const match of source.matchAll(pattern)) {
      const raw = Number(match[1].replaceAll(',', ''));
      const value = Math.round(raw * (multiplier[(match[2] || '').toLowerCase()] || 1) * 100) / 100;
      if (!Number.isFinite(value) || value <= 0 || value > 999999999.99 || value < minimum) continue;
      const numberStart = match.index + match[0].indexOf(match[1]);
      const after = source.slice(numberStart + match[1].length);
      if (strength < 4 && /^\s*(?:kg|grams?|lit(?:er|re)s?|ml|pieces?|people|persons?|units?)\b/i.test(after)) continue;
      const current = candidates.get(numberStart);
      if (!current || strength > current.strength || (strength === current.strength && match[0].length > current.length)) {
        candidates.set(numberStart, {amount:String(value), start:match.index, length:match[0].length, strength});
      }
    }
  }

  collect(new RegExp(`(?:₹|\\brs\\.?|\\binr\\b|\\brupees?\\b)\\s*${number}${scale}${end}`, 'gi'), 4);
  collect(new RegExp(`${number}${scale}\\s*(?:rupees?\\b|inr\\b|rs\\.?(?=\\s|$)|₹)`, 'gi'), 4);
  collect(new RegExp(`\\b(?:for|spent|spend|paid|pay|lent|lend|loaned|cost(?:s|ing)?(?:\\s+me)?|price(?:d)?(?:\\s+at)?|of)\\s+${number}${scale}${end}`, 'gi'), 3);
  collect(new RegExp(`\\b${number}${scale}\\s+(?:for|on)\\b`, 'gi'), 3, 50);
  collect(new RegExp(`\\b${number}${scale}(?=\\s+(?:monthly|weekly|daily|yearly|annually|per month|per year)\\b)`, 'gi'), 3);
  collect(new RegExp(`\\b${number}\\s*(k|lakhs?|crores?)\\b`, 'gi'), 3);
  collect(new RegExp(`^\\s*${number}${scale}(?=\\s|$)`, 'gi'), 2);
  collect(new RegExp(`(?:^|\\s)${number}${scale}\\s*$`, 'gi'), 1, 50);

  const found = [...candidates.values()];
  const strong = found.filter(candidate => candidate.strength >= 3);
  const pool = strong.length ? strong : found;
  const issue = pool.length > 1 ? 'multiple' : pool.length ? '' : 'missing';
  const chosen = issue ? null : pool[0];
  let title = chosen ? source.slice(0, chosen.start) + ' ' + source.slice(chosen.start + chosen.length) : source;
  if (chosen) title = title.replace(/^\s*(?:i|we)\s+/i, '');
  title = title.replace(/\s+/g, ' ').trim()
    .replace(/^(?:remind me to|reminder to|remind me|reminder)\b\s*/i, '')
    .replace(/^(?:i|we)\s+(?=spent|spend|paid|bought|purchased|on|for)\b/i, '')
    .replace(/^(?:spent|spend|paid|bought|purchased)\b\s*/i, '')
    .replace(/^(?:on|for)\b\s*/i, '')
    .replace(/\s+(?:for|on|at|of|worth|cost(?:s|ing)?(?: me)?|price(?:d)?(?: at)?)\s*$/i, '')
    .trim();
  return {amount:chosen ? chosen.amount : '', title, issue};
}

// Only suggest categories supplied by the current account. Each tier must have
// one clear match; conflicts are left for review instead of depending on order.
function parseQuickCategory(text, categories) {
  const source = String(text || '').normalize('NFKC').replace(/\s+/gu, ' ').trim();
  const present = phrase => {
    const normalized = phrase.normalize('NFKC').replace(/\s+/gu, ' ').trim();
    if (!normalized) return false;
    const escaped = normalized.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    return new RegExp('(?:^|[^\\p{L}\\p{N}])' + escaped + '(?=$|[^\\p{L}\\p{N}])', 'iu').test(source);
  };
  const fallback = issue => ({category:categories.find(c => c.name.toLowerCase() === 'other')?.name || '', subcategory:'', issue});
  const choose = matches => matches.length === 1 ? {...matches[0], issue:''} : fallback('ambiguous');
  const subMatches = categories.flatMap(c => (c.subcategories || [])
    .filter(present).map(subcategory => ({category:c.name, subcategory})));
  if (subMatches.length) return choose(subMatches);
  const mainMatches = categories.filter(c => present(c.name)).map(c => ({category:c.name, subcategory:''}));
  if (mainMatches.length) return choose(mainMatches);

  const keywords = {
    'Food & groceries': 'grocer,groceries,grocery,food,lunch,dinner,breakfast,coffee,tea,meal,meals,restaurant,restaurants,pizza,pizzas,burger,burgers,sandwich,sandwiches,snack,snacks,biryani,dosa,idli,pasta,bread,milk,eggs,vegetables,fruit,fruits,coke',
    'Transport': 'uber,ola,taxi,taxis,cab,cabs,petrol,diesel,fuel,bus,buses,metro,auto rickshaw,parking,toll,tolls,scooter,scooty,car repair,bike repair',
    'Shopping': 'shopping,clothing,clothes,shirt,shirts,t shirt,t-shirt,jeans,trousers,dress,dresses,shoes,sneakers,headphones,earphones,earbuds,phone,iphone,smartphone,laptop,computer,electronics,keyboard,mouse,charger',
    'Home & bills': 'rent,electricity,bill,bills,wifi,wi-fi,internet,broadband,water bill,gas cylinder,furniture,sofa,curtains,detergent,cleaning supplies,netflix,subscription,subscriptions',
    'Health': 'medicine,medicines,medication,doctor,doctors,dentist,hospital,clinic,pharmacy,medical,gym,vitamins,health checkup',
    'Learning': 'book,books,course,courses,class,classes,tuition,school,college,training,textbook,textbooks,udemy',
    'Travel': 'flight,flights,hotel,hotels,trip,trips,holiday,holidays,vacation,airfare,resort,hostel'
  };
  const matches = categories.filter(c => (keywords[c.name] || '').split(',').some(present))
    .map(c => ({category:c.name, subcategory:''}));
  return matches.length ? choose(matches) : fallback('missing');
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = parseQuickMoney;
  module.exports.parseQuickCategory = parseQuickCategory;
}
