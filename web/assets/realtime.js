/** Live updates: server events (SSE) with polling as the fallback, plus health checks. */
async function lockApp() {
  stopLocalPlayback();
  try {
    await api('/api/setup/lock', { method: 'POST' });
  } catch {}
  window.location.href = '/login?locked=1';
}

async function checkHealth() {
  const wasOnline = isBackendOnline;
  try {
    // Reachability only drives retrying a search queued while offline; it isn't shown.
    await api('/api/health', {}, 5000);
    isBackendOnline = true;
  } catch {
    isBackendOnline = false;
  }

  if (!wasOnline && isBackendOnline && queuedSearchMessage) {
    if (queuedSearchRetryTimer) clearTimeout(queuedSearchRetryTimer);
    queuedSearchRetryTimer = setTimeout(async () => {
      queuedSearchRetryTimer = null;
      await flushQueuedSearchRetry();
    }, 250);
  }
}

function hasActiveJobFlow() {
  if (searchUiActive || selectingCandidateJobIds.size > 0) return true;
  return !!pendingToniesUpload || activeUploadJobIds.size > 0 || jobsCache.some(isJobActive);
}

const REALTIME_MODE = 'sse'; // switch to 'poll' for immediate fallback

const RT_DEBUG = new URLSearchParams(window.location.search).get('rtDebug') === '1';

let sseConnected = false;

let sseBackoffMs = 1000;

let sseLastEventId = 0;

let sseRefreshTimer = null;

let lastSseRefreshAt = 0;

let lastJobsReconcileAt = Date.now();

function renderRealtimeStatus() {
  if (!realtimeStatusEl || !RT_DEBUG) return;
  realtimeStatusEl.style.display = 'inline-flex';
  const mode = REALTIME_MODE === 'sse' ? (sseConnected ? 'sse-connected' : 'sse-reconnecting') : 'poll-only';
  realtimeStatusEl.textContent = `realtime: ${mode}`;
}

function scheduleSseRefresh(delayMs = 450) {
  if (sseRefreshTimer) return;
  sseRefreshTimer = setTimeout(async () => {
    sseRefreshTimer = null;
    const now = Date.now();
    const minIntervalMs = hasActiveJobFlow() ? 250 : 3500;
    if ((now - lastSseRefreshAt) < minIntervalMs) return;
    lastSseRefreshAt = now;
    try {
      await refreshJobs({ background: true });
      lastJobsReconcileAt = Date.now();
    } catch (e) {
      console.warn('sse refresh failed', e);
    }
  }, delayMs);
}

function connectSse() {
  const url = sseLastEventId > 0 ? `/api/events?lastEventId=${sseLastEventId}` : '/api/events';
  const es = new EventSource(url);

  es.onopen = () => {
    sseConnected = true;
    sseBackoffMs = 1000;
    renderRealtimeStatus();
  };

  es.onmessage = (ev) => {
    const idNum = Number(ev.lastEventId || 0);
    if (Number.isFinite(idNum) && idNum > 0) sseLastEventId = idNum;

    let kind = '';
    let payload = null;
    try {
      payload = JSON.parse(ev.data || '{}');
      kind = String(payload?.kind || '');
    } catch {}

    if (kind === 'tonies_actions') {
      try { handleToniesActionsEvent(payload); } catch (e) { console.warn('tonies action event failed', e); }
      return;
    }

    // Only refresh jobs for state-changing events.
    if (!['job_created', 'job_updated', 'job_selected'].includes(kind)) return;

    const delayMs = hasActiveJobFlow() ? 120 : 500;
    scheduleSseRefresh(delayMs);
  };

  es.onerror = () => {
    try { es.close(); } catch {}
    sseConnected = false;
    renderRealtimeStatus();
    const wait = sseBackoffMs;
    sseBackoffMs = Math.min(10000, Math.floor(sseBackoffMs * 1.7));
    setTimeout(connectSse, wait);
  };
}

let pollInFlight = false;

let lastHealthCheckAt = 0;

async function pollLoop() {
  try {
    if (!pollInFlight) {
      pollInFlight = true;
      try {
        if (sseConnected) {
          // SSE mode: sparse health checks + infrequent reconciliation.
          const now = Date.now();
          if ((now - lastHealthCheckAt) > 15000) {
            await checkHealth();
            lastHealthCheckAt = now;
          }

          const reconcileMs = hasActiveJobFlow() ? 3000 : 20000;
          if ((now - lastJobsReconcileAt) > reconcileMs) {
            await refreshJobs({ background: true });
            lastJobsReconcileAt = Date.now();
          }
        } else {
          await Promise.all([refreshJobs({ background: true }), checkHealth()]);
        }
      } finally {
        pollInFlight = false;
      }
    }
  } catch (e) {
    console.warn('pollLoop error', e);
  } finally {
    const nextMs = sseConnected ? 4000 : (hasActiveJobFlow() ? 800 : 3500);
    setTimeout(pollLoop, nextMs);
  }
}
