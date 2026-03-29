#!/usr/bin/env node

function advanceFallbackRetry(state, url, hasPendingDeleteRows, maxRetries = 3) {
  const retryCount = Number(state[url] || 0);
  if (retryCount < maxRetries) {
    state[url] = retryCount + 1;
    return { shouldRetry: true, warned: false, next: state[url] };
  }
  state[url] = 0;
  return { shouldRetry: false, warned: !!hasPendingDeleteRows, next: 0 };
}

function assert(cond, msg) { if (!cond) throw new Error(msg); }

function run() {
  const state = {};
  const url = 'u1';

  const r1 = advanceFallbackRetry(state, url, true, 3);
  assert(r1.shouldRetry && r1.next === 1, 'retry #1 expected');
  const r2 = advanceFallbackRetry(state, url, true, 3);
  assert(r2.shouldRetry && r2.next === 2, 'retry #2 expected');
  const r3 = advanceFallbackRetry(state, url, true, 3);
  assert(r3.shouldRetry && r3.next === 3, 'retry #3 expected');
  const r4 = advanceFallbackRetry(state, url, true, 3);
  assert(!r4.shouldRetry && r4.warned && r4.next === 0, 'retry cap should stop and warn on stale pending deletes');

  const r5 = advanceFallbackRetry(state, url, false, 3);
  assert(r5.shouldRetry && r5.next === 1, 'counter should restart after cap reset');

  console.log('PASS tonies_stale_fallback_guard_check');
}

try { run(); } catch (e) { console.error('FAIL tonies_stale_fallback_guard_check:', e.message || e); process.exit(1); }
