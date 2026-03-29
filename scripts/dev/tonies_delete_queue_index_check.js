#!/usr/bin/env node

function normalizeToniesTitle(s) {
  return String(s || '').toLowerCase().replace(/\.[a-z0-9]{2,5}$/i, '').replace(/[^a-z0-9]+/g, ' ').trim();
}

function findToniesIndexByDeleteKey(chapters, deleteEntry) {
  const rawKey = typeof deleteEntry === 'string' ? String(deleteEntry) : String(deleteEntry?.key || '');
  const targetNorm = typeof deleteEntry === 'string'
    ? String(deleteEntry).split('#')[0]
    : String(deleteEntry?.norm || '');
  const preferredIndex = Number(deleteEntry?.originalIndex);
  const keyOcc = Number(String(rawKey || '').split('#')[1] || '0');

  const matches = [];
  for (let i = 0; i < chapters.length; i++) {
    const norm = normalizeToniesTitle(chapters[i]?.title || '');
    if (norm === targetNorm) matches.push(i);
  }
  if (!matches.length) return -1;
  if (Number.isFinite(keyOcc) && keyOcc > 0 && keyOcc <= matches.length) return matches[keyOcc - 1];
  if (!Number.isNaN(preferredIndex)) matches.sort((a, b) => Math.abs(a - preferredIndex) - Math.abs(b - preferredIndex));
  return matches[0];
}

function assert(cond, msg) { if (!cond) throw new Error(msg); }

function run() {
  const chapters = [
    { title: 'Party Freeze Dance Song' },
    { title: 'Another Song' },
    { title: 'Party Freeze Dance Song' },
    { title: 'Party Freeze Dance Song' },
  ];

  const norm = normalizeToniesTitle('Party Freeze Dance Song');
  assert(findToniesIndexByDeleteKey(chapters, { key: `${norm}#1`, norm, originalIndex: 3 }) === 0, 'occurrence #1 should map to first duplicate');
  assert(findToniesIndexByDeleteKey(chapters, { key: `${norm}#2`, norm, originalIndex: 0 }) === 2, 'occurrence #2 should map to second duplicate');
  assert(findToniesIndexByDeleteKey(chapters, { key: `${norm}#3`, norm, originalIndex: 0 }) === 3, 'occurrence #3 should map to third duplicate');

  console.log('PASS tonies_delete_queue_index_check');
}

try { run(); } catch (e) { console.error('FAIL tonies_delete_queue_index_check:', e.message || e); process.exit(1); }
