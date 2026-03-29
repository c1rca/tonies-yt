#!/usr/bin/env node

function normalizeToniesTitle(s) {
  return String(s || '').toLowerCase().replace(/\.[a-z0-9]{2,5}$/i, '').replace(/[^a-z0-9]+/g, ' ').trim();
}

function chapterFingerprint(chapter, index = 0) {
  const titleNorm = normalizeToniesTitle(chapter?.title || `chapter ${index + 1}`);
  const durNorm = String(chapter?.duration || '').trim();
  return `${titleNorm}|${durNorm}`;
}

function findToniesIndexByDeleteKey(chapters, deleteEntry) {
  const chapterId = String(deleteEntry?.chapterId || '').trim();
  const chapterFp = String(deleteEntry?.chapterFp || '').trim();
  const rawKey = String(deleteEntry?.key || '');
  const targetNorm = String(deleteEntry?.norm || '');
  const preferredIndex = Number(deleteEntry?.originalIndex);
  const keyOcc = Number(String(rawKey || '').split('#')[1] || '0');

  if (chapterId) {
    const byIdIndex = chapters.findIndex((c) => String(c?.chapter_id || c?.content_id || c?.id || '').trim() === chapterId);
    if (byIdIndex >= 0) return byIdIndex;
  }

  if (chapterFp) {
    const byFp = [];
    for (let i = 0; i < chapters.length; i++) {
      if (chapterFingerprint(chapters[i], i) === chapterFp) byFp.push(i);
    }
    if (byFp.length === 1) return byFp[0];
    if (byFp.length > 1 && !Number.isNaN(preferredIndex)) {
      byFp.sort((a, b) => Math.abs(a - preferredIndex) - Math.abs(b - preferredIndex));
      return byFp[0];
    }
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
    { title: 'Party Freeze Dance Song', duration: '00:01:00' },
    { title: 'Party Freeze Dance Song', duration: '00:02:00' },
    { title: 'Party Freeze Dance Song', duration: '00:03:00' },
  ];
  const norm = normalizeToniesTitle('Party Freeze Dance Song');

  const fp = chapterFingerprint(chapters[2], 2);
  const idx = findToniesIndexByDeleteKey(chapters, { key: `${norm}#1`, norm, chapterFp: fp, originalIndex: 0 });
  assert(idx === 2, 'fingerprint should resolve to exact duration-distinguished duplicate');

  console.log('PASS tonies_delete_queue_fingerprint_priority_check');
}

try { run(); } catch (e) { console.error('FAIL tonies_delete_queue_fingerprint_priority_check:', e.message || e); process.exit(1); }
