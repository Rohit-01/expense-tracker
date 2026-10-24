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

if (typeof module !== 'undefined' && module.exports) module.exports = parseQuickMoney;
