#!/usr/bin/env node

function toniesDisplayTitle(s) {
  const raw = String(s || '').trim();
  return raw.replace(/\s\[[a-f0-9]{4,8}\]$/i, '').trim() || raw;
}

function collectNameChanges(chapters, drafts) {
  const changes = [];
  for (let i = 0; i < chapters.length; i++) {
    const currentRaw = String(chapters[i]?.title || `Chapter ${i + 1}`);
    const currentDisplay = toniesDisplayTitle(currentRaw);
    const next = String(drafts[i] ?? currentDisplay).trim();
    if (next && next !== currentDisplay) {
      changes.push({ index: i, title: next, beforeTitle: currentRaw });
    }
  }
  return changes;
}

function assert(cond, msg) { if (!cond) throw new Error(msg); }

function run() {
  const chapters = [
    { title: 'Party Freeze Dance Song [a1b2c3]' },
    { title: 'Another Song [d4e5f6]' },
  ];

  const draftsNoEdit = {
    0: 'Party Freeze Dance Song',
    1: 'Another Song',
  };
  const noChanges = collectNameChanges(chapters, draftsNoEdit);
  assert(noChanges.length === 0, 'display-equivalent drafts should not produce rename changes');

  const draftsOneEdit = {
    0: 'Party Freeze Dance Song Remix',
    1: 'Another Song',
  };
  const changes = collectNameChanges(chapters, draftsOneEdit);
  assert(changes.length === 1, 'one edited display title should produce one change');
  assert(changes[0].index === 0, 'changed row index should be preserved');

  console.log('PASS tonies_edit_suffix_noop_check');
}

try { run(); } catch (e) { console.error('FAIL tonies_edit_suffix_noop_check:', e.message || e); process.exit(1); }
