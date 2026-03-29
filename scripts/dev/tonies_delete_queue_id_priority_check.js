#!/usr/bin/env node

function normalizeToniesTitle(s) {
  return String(s || '').toLowerCase().replace(/\.[a-z0-9]{2,5}$/i, '').replace(/[^a-z0-9]+/g, ' ').trim();
}

function findToniesIndexByDeleteKey(chapters, deleteEntry) {
  const rawKey = typeof deleteEntry === 'string' ? String(deleteEntry) : String(deleteEntry?.key || '');
  const targetNorm = typeof deleteEntry === 'string' ? String(deleteEntry).split('#')[0] : String(deleteEntry?.norm || '');
  const chapterId = String(deleteEntry?.chapterId || '').trim();
  const preferredIndex = Number(deleteEntry?.originalIndex);
  const keyOcc = Number(String(rawKey || '').split('#')[1] || '0');

  if (chapterId) {
    const byIdIndex = chapters.findIndex((c) => String(c?.chapter_id || c?.content_id || c?.id || '').trim() === chapterId);
    if (byIdIndex >= 0) return byIdIndex;
  }

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
    { chapter_id: 'c-1', title: 'Party Freeze Dance Song' },
    { chapter_id: 'c-2', title: 'Party Freeze Dance Song' },
    { chapter_id: 'c-3', title: 'Party Freeze Dance Song' },
  ];
  const norm = normalizeToniesTitle('Party Freeze Dance Song');

  const idx = findToniesIndexByDeleteKey(chapters, { key: `${norm}#1`, norm, chapterId: 'c-3', originalIndex: 0 });
  assert(idx === 2, 'chapterId should win over occurrence/index fallback');

  const idxFallback = findToniesIndexByDeleteKey(chapters, { key: `${norm}#2`, norm, chapterId: 'missing', originalIndex: 0 });
  assert(idxFallback === 1, 'when chapterId missing, occurrence fallback should be used');

  console.log('PASS tonies_delete_queue_id_priority_check');
}

try { run(); } catch (e) { console.error('FAIL tonies_delete_queue_id_priority_check:', e.message || e); process.exit(1); }
