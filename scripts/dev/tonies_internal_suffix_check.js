#!/usr/bin/env node

function toniesDisplayTitle(s) {
  const raw = String(s || '').trim();
  return raw.replace(/\s\[[a-f0-9]{4,8}\]$/i, '').trim() || raw;
}

function stripInternalTokenSuffix(stem) {
  return String(stem || '').replace(/\s\[[a-f0-9]{4,8}\]$/i, '').trim() || String(stem || '');
}

function buildInternalUploadName(stem, suffix, token) {
  return `${stripInternalTokenSuffix(stem)} [${token}]${suffix}`;
}

function assert(cond, msg) { if (!cond) throw new Error(msg); }

function run() {
  const n1 = buildInternalUploadName('Party Freeze Dance Song', '.mp3', 'a1b2c3');
  const n2 = buildInternalUploadName('Party Freeze Dance Song', '.mp3', 'd4e5f6');
  assert(n1 !== n2, 'internal upload names should be unique by token');

  const fromSuffixed = buildInternalUploadName('Party Freeze Dance Song [111aaa]', '.mp3', 'b2c3d4');
  assert(fromSuffixed === 'Party Freeze Dance Song [b2c3d4].mp3', 'existing token suffix should be replaced, not stacked');

  assert(toniesDisplayTitle('Party Freeze Dance Song [a1b2c3]') === 'Party Freeze Dance Song', 'display title should hide internal token suffix');
  assert(toniesDisplayTitle('Party Freeze Dance Song [A1B2C3]') === 'Party Freeze Dance Song', 'display title token strip should be case-insensitive');
  assert(toniesDisplayTitle('Party Freeze Dance Song [live]') === 'Party Freeze Dance Song [live]', 'non-hex bracket suffix should remain visible');
  assert(toniesDisplayTitle('Party Freeze Dance Song') === 'Party Freeze Dance Song', 'plain titles should remain unchanged');

  console.log('PASS tonies_internal_suffix_check');
}

try { run(); } catch (e) { console.error('FAIL tonies_internal_suffix_check:', e.message || e); process.exit(1); }
