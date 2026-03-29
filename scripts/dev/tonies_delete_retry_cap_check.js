#!/usr/bin/env node

function advanceAttempts(state, key, maxRetries = 3) {
  const tries = Number(state.attempts.get(key) || 0) + 1;
  state.attempts.set(key, tries);
  if (tries >= maxRetries) {
    state.pendingKeys.delete(key);
    state.pendingConfirm.delete(key);
    state.normMax.delete(key);
    state.attempts.delete(key);
    return { shouldRetry: false, failedOut: true, tries };
  }
  return { shouldRetry: true, failedOut: false, tries };
}

function assert(cond, msg) { if (!cond) throw new Error(msg); }

function run() {
  const key = 'party freeze dance song#1';
  const state = {
    pendingKeys: new Set([key]),
    pendingConfirm: new Set([key]),
    normMax: new Map([[key, 1]]),
    attempts: new Map(),
  };

  const a1 = advanceAttempts(state, key, 3);
  assert(a1.shouldRetry === true && a1.tries === 1, 'first stuck sync should retry');
  assert(state.pendingConfirm.has(key), 'key should still be pending after first retry');

  const a2 = advanceAttempts(state, key, 3);
  assert(a2.shouldRetry === true && a2.tries === 2, 'second stuck sync should retry');
  assert(state.pendingConfirm.has(key), 'key should still be pending after second retry');

  const a3 = advanceAttempts(state, key, 3);
  assert(a3.failedOut === true && a3.shouldRetry === false, 'third stuck sync should fail out');
  assert(!state.pendingKeys.has(key), 'failed key should be removed from pending keys');
  assert(!state.pendingConfirm.has(key), 'failed key should be removed from pending confirm set');
  assert(!state.attempts.has(key), 'attempts should be cleaned up after fail-out');

  console.log('PASS tonies_delete_retry_cap_check');
}

try { run(); } catch (e) { console.error('FAIL tonies_delete_retry_cap_check:', e.message || e); process.exit(1); }
