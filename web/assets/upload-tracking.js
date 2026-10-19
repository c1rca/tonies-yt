/** Following an upload from the app to the Tonie until the track is confirmed there. */
let recentUploadedChapterIds = {};
const completedUploadJobIds = new Set();
const reconciledUploadReceiptJobIds = new Set();
const toniesUploadReconciliations = [];
let toniesUploadReconciliationRunning = false;
const uploadIntentsByJobId = new Map();
const localUploadToastShownJobIds = new Set();
let toniesNewClearTimer = null;
const attachedUploadJobIds = new Set();  // upload jobs whose chapter is already in the list
const TONIES_NEW_HIGHLIGHT_MS = 5000;
let toniesUploadSyncing = false;
let pendingDropUploadIntent = null;
let pendingToniesUpload = null;
let activeUploadJobIds = new Set();
let syncingResultJobIds = new Set();
function markUploadJobActive(jobId) {
  if (jobId) activeUploadJobIds.add(jobId);
}

function getEffectiveSelectedToniesUrl() {
  if (selectedToniesUrl) return selectedToniesUrl;
  if (Array.isArray(toniesCache) && toniesCache.length) return String(toniesCache[0]?.edit_url || '');
  return '';
}

async function ensureUploadTargetUrl() {
  let targetUrl = getEffectiveSelectedToniesUrl();
  if (targetUrl) return targetUrl;
  try {
    await refreshTonies({ force: true });
  } catch {}
  targetUrl = getEffectiveSelectedToniesUrl();
  if (targetUrl && !selectedToniesUrl) {
    selectedToniesUrl = targetUrl;
    saveToniesCache();
    renderTonies();
  }
  return targetUrl;
}

function startPendingToniesUpload(filename, targetUrl) {
  pendingToniesUpload = { filename, targetUrl, jobId: '', stage: 'preparing' };
  if (targetUrl === selectedToniesUrl) {
    toniesUploadSyncing = true;
    setToniesProgress('Preparing local file for Tonies…');
  }
  renderToniesContent();
}

function clearPendingToniesUpload() {
  pendingToniesUpload = null;
}

async function drainToniesUploadReconciliations() {
  if (toniesUploadReconciliationRunning) return;
  // Receipts reconcile one at a time: a receipt's read + optional positioning stay together.
  toniesUploadReconciliationRunning = true;
  try {
    while (toniesUploadReconciliations.length) {
      await toniesUploadReconciliations.shift().run();
    }
  } finally {
    toniesUploadReconciliationRunning = false;
  }
}

function hasUploadAttachmentReceipt(job) {
  return !isReversedStatus(job?.status) && !!job?.uploaded_chapter_id
    && (Array.isArray(job?.tonies_content?.chapters) || !!job?.attachment_receipt);
}

// Tonies accepted the attachment but its read-back failed: a receipt, not inventory.
// The source stays blocked so it can't be added twice; nothing is positioned or replayed.
function isAcceptedUnconfirmedAttachment(job) {
  return hasUploadAttachmentReceipt(job) && !!job.attachment_receipt && !Array.isArray(job.tonies_content?.chapters);
}

/** Mark a just-added track as new for a few seconds. */
function highlightNewTrack(chapterId) {
  recentUploadedChapterIds[chapterId] = Date.now();
  if (toniesNewClearTimer) clearTimeout(toniesNewClearTimer);
  toniesNewClearTimer = setTimeout(() => {
    recentUploadedChapterIds = {};
    renderToniesContent();
  }, TONIES_NEW_HIGHLIGHT_MS);
}

/** Move an added track to where the person dropped or asked for it; returns the new content. */
async function moveUploadToIntent(url, content, chapterId, intent) {
  const from = content.chapters.findIndex(c => c.chapter_id === chapterId);
  if (!chapterId || from < 0) throw new Error('Upload saved, but its cloud ID is missing. Refresh before positioning it.');
  const to = intent.targetEdge === 'end' ? content.chapters.length - 1
    : intent.targetEdge === 'start' ? 0
    : Math.max(0, Math.min(content.chapters.length - 1, intent.targetIndex));
  if (from === to) return content;
  const moved = await api('/api/tonies-content/reorder', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ target_url: url, from_index: from, to_index: to, chapter_id: chapterId })
  }, 60000);
  applyToniesContent(url, moved);
  return moved;
}

async function finalizePendingToniesUpload(job, succeeded) {
  if (!job?.id) return;
  const reversed = isReversedStatus(job.status);
  const firstCompletion = !completedUploadJobIds.has(job.id);
  const newReceipt = hasUploadAttachmentReceipt(job) && !reconciledUploadReceiptJobIds.has(job.id);
  if (!firstCompletion && !newReceipt) return;
  // Lifecycle cleanup and attachment reconciliation have independent dedupe claims.
  completedUploadJobIds.add(job.id);
  if (newReceipt) reconciledUploadReceiptJobIds.add(job.id);
  const pending = firstCompletion && pendingToniesUpload?.jobId === job.id ? pendingToniesUpload : null;
  const intent = uploadIntentsByJobId.get(job.id)
    || (pendingDropUploadIntent?.jobId === job.id ? pendingDropUploadIntent : null);
  const url = String(job.target_url || intent?.targetUrl || pending?.targetUrl || '');
  const saved = succeeded || newReceipt;
  const unconfirmed = isAcceptedUnconfirmedAttachment(job);
  // A terminal status without an attachment receipt is not proof of rejection or identity.
  // Keep its positioning intent until the exact-ID receipt arrives.
  if (newReceipt || reversed) uploadIntentsByJobId.delete(job.id);
  else if (intent) uploadIntentsByJobId.set(job.id, intent);
  if (pendingDropUploadIntent?.jobId === job.id) pendingDropUploadIntent = null;
  const finish = async () => {
    try {
      if (reversed) {
        if (url === selectedToniesUrl) setToniesStatus(job.status === 'undone' ? 'Upload undone.' : 'Upload canceled.', false);
        return;
      }
      if (!saved) throw new Error(classifyError(job.error || ''));
      // Job content is a historical attachment receipt, never the current inventory.
      invalidateToniesContentRequests(url);
      const version = toniesContentVersionByUrl[url];
      let content = await api('/api/tonies-content', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_url: url })
      }, 45000);
      if (version === toniesContentVersionByUrl[url]) {
        applyToniesContent(url, content);
      } else {
        // A newer canonical response won while this read was pending.
        // Position only from that inventory, never from the superseded read.
        content = getCachedToniesContent(url);
        if (!Array.isArray(content?.chapters)) throw new Error('Library changed while loading. Refresh before positioning this upload.');
      }
      const chapterId = job.uploaded_chapter_id;
      if (chapterId) highlightNewTrack(chapterId);
      if (intent && !job.upload_positioned && !unconfirmed) content = await moveUploadToIntent(url, content, chapterId, intent);
      if (url === selectedToniesUrl) setToniesStatus(toniesProcessingStatus(content), false);
      showToast('Uploaded to Tonies', 'ok', 2800);
    } catch (e) {
      const message = saved ? `Upload saved; library update failed: ${e?.message || e}` : `Upload failed: ${e?.message || e}`;
      recordWorkspaceNotice({id:`upload-result:${job.id}`, title:job.filename || intent?.filename || 'Add audio', targetUrl:url, stage:saved ? 'Saved; refresh needed' : 'Upload failed', error:message});
      if (url === selectedToniesUrl) setToniesStatus(message, false);
      showToast(message, 'err', 5200);
    } finally {
      if (!toniesUploadReconciliations.some(entry => entry.jobId === job.id)) syncingResultJobIds.delete(job.id);
      if (firstCompletion) {
        activeUploadJobIds.delete(job.id);
        localUploadToastShownJobIds.delete(job.id);
        if (pending && pendingToniesUpload === pending) clearPendingToniesUpload();
        // Several local uploads can be in flight; each resolves its own file row.
        attachedUploadJobIds.delete(job.id);
        const uploadedFilename = pending?.filename || intent?.filename;
        if (uploadedFilename && !unconfirmed) resolveLocalUploadPlanned(uploadedFilename);
      }
      toniesUploadSyncing = hasInFlightTrackedUploadForSelectedTonies();
      renderTonies();
      renderToniesContent();
      renderJobs();
      // Local files and search downloads share this one completion path.
      if (firstCompletion && succeeded) void refreshFiles().catch(e => showToast(`Local library refresh failed: ${e?.message || e}`, 'err'));
      void pumpLocalUploadQueue();
    }
  };
  if (!saved) return finish();
  syncingResultJobIds.add(job.id);
  return new Promise(resolve => {
    toniesUploadReconciliations.push({ jobId: job.id, run: async () => { await finish(); resolve(); } });
    void drainToniesUploadReconciliations();
  });
}

function syncPendingToniesUploadFromJob(job) {
  if (!pendingToniesUpload?.jobId || pendingToniesUpload.jobId !== job?.id) return;
  const pendingTarget = String(pendingToniesUpload.targetUrl || '');
  const jobTarget = String(job.target_url || '');
  if (pendingToniesUpload.jobId && pendingToniesUpload.jobId !== job.id) return;
  if (pendingTarget && jobTarget && pendingTarget !== jobTarget) return;

  pendingToniesUpload.jobId = job.id || pendingToniesUpload.jobId;
  pendingToniesUpload.stage = String(job.status || pendingToniesUpload.stage || '');

  const status = String(job.status || '');
  const selectedMatchesPending = !!(pendingTarget && pendingTarget === getEffectiveSelectedToniesUrl());

  if (selectedMatchesPending) {
    if (status === 'queued_prepare' || status === 'preparing') setToniesProgress('Preparing local file for Tonies…');
    else if (status === 'queued_upload' || status === 'waiting_upload') setToniesProgress('Queued for Tonies…');
    else if (status === 'uploading') setToniesProgress('Uploading to my.tonies.com…');
    else if (status === 'done') setToniesStatus('Upload saved to Tonies.', false);
  }

  // Finalize regardless of current selection; otherwise pending upload state can get stuck
  // if the selected Tonie changes while job finishes in background.
  if (status === 'done' || hasUploadAttachmentReceipt(job)) { void finalizePendingToniesUpload(job, status === 'done'); }
  else if (isTerminalStatus(status)) { void finalizePendingToniesUpload(job, false); }
}

function hasInFlightTrackedUploadForSelectedTonies() {
  if (!selectedToniesUrl) return false;
  return jobsCache.some((j) => (
    activeUploadJobIds.has(j.id)
    && j.target_url === selectedToniesUrl
    && ['downloading', 'uploading'].includes(String(j.status || ''))
  ));
}
