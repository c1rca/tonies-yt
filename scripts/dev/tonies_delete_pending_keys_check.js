#!/usr/bin/env node

function normalizeToniesTitle(s) {
  return String(s || '').toLowerCase().replace(/\.[a-z0-9]{2,5}$/i, '').replace(/[^a-z0-9]+/g, ' ').trim();
}

function toniesDeleteKey(chapters, index, forcedNorm = '') {
  const list = Array.isArray(chapters) ? chapters : [];
  const i = Math.max(0, Number(index) || 0);
  const norm = forcedNorm || normalizeToniesTitle(list[i]?.title || `chapter ${i + 1}`);
  let occ = 0;
  for (let x = 0; x <= i && x < list.length; x++) {
    const n = normalizeToniesTitle(list[x]?.title || '');
    if (n === norm) occ += 1;
  }
  return `${norm}#${occ}`;
}

function nextNormCounts(chapters) {
  const m = new Map();
  for (const c of chapters || []) {
    const n = normalizeToniesTitle(c?.title || '');
    if (!n) continue;
    m.set(n, (m.get(n) || 0) + 1);
  }
  return m;
}

function shouldClearPendingByExpectedCount({ pendingKey, expectedMax, nextChapters }) {
  const nextKeys = new Set((nextChapters || []).map((c, i) => toniesDeleteKey(nextChapters, i, normalizeToniesTitle(c?.title || ''))));
  const norm = String(pendingKey || '').split('#')[0];
  const counts = nextNormCounts(nextChapters || []);
  const nextCount = Number(counts.get(norm) || 0);
  const keyMissing = !nextKeys.has(pendingKey);
  const countReducedAsExpected = Number.isFinite(expectedMax) && Number.isFinite(nextCount) && nextCount <= expectedMax;
  return keyMissing || countReducedAsExpected;
}

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

function run() {
  const before = [
    { title: 'Party Freeze Dance Song' },
    { title: 'Party Freeze Dance Song' },
    { title: 'Another Song' },
  ];

  // User deletes the FIRST duplicate (key #1). After remote save, remaining duplicate
  // shifts and still has key #1, so key-presence alone cannot detect success.
  const pendingKey = toniesDeleteKey(before, 0, normalizeToniesTitle(before[0].title)); // party freeze dance song#1
  const expectedMax = 1; // from 2 duplicates -> should shrink to <= 1

  const after = [
    { title: 'Party Freeze Dance Song' },
    { title: 'Another Song' },
  ];

  assert(shouldClearPendingByExpectedCount({ pendingKey, expectedMax, nextChapters: after }) === true,
    'pending key should clear when duplicate count shrinks even if key string still exists');

  const unchanged = [
    { title: 'Party Freeze Dance Song' },
    { title: 'Party Freeze Dance Song' },
    { title: 'Another Song' },
  ];

  assert(shouldClearPendingByExpectedCount({ pendingKey, expectedMax, nextChapters: unchanged }) === false,
    'pending key should stay when duplicate count did not shrink');

  console.log('PASS tonies_delete_pending_keys_check');
}

try {
  run();
} catch (err) {
  console.error('FAIL tonies_delete_pending_keys_check:', err.message || err);
  process.exit(1);
}
