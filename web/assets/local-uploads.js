/** Adding Local files to a Tonie: the upload queue, one file at a time. */
let queuedLocalUploads = [];
let queuedLocalUploadNames = new Set();
let localUploadQueuePumping = false;
let localUploadOutstandingByName = {};
function focusUploadInLibrary(filename) {
  renderFiles();
  const key = encodeURIComponent(filename || '');
  const el = document.querySelector(`.file[data-file-key="${key}"]`);
  if (el) {
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
}

function markLocalUploadPlanned(filename) {
  const key = String(filename || '');
  if (!key) return;
  localUploadOutstandingByName[key] = (Number(localUploadOutstandingByName[key]) || 0) + 1;
  renderFiles();
}

function resolveLocalUploadPlanned(filename) {
  const key = String(filename || '');
  if (!key) return;
  const next = (Number(localUploadOutstandingByName[key]) || 0) - 1;
  if (next > 0) localUploadOutstandingByName[key] = next;
  else delete localUploadOutstandingByName[key];
  renderFiles();
}

// Returns false only when the file may be submitted again (nothing was sent, or the
// server refused it); otherwise the file stays blocked until its job settles.
async function startSingleLocalUpload(filename, options = {}) {
  const { scrollToResults = false, skipQueuedToast = true, suppressInitialToast = false, targetIndex = null } = options;
  const targetUrl = options.targetUrl || await ensureUploadTargetUrl();
  if (!targetUrl) {
    showToast('Select a Creative Tonie before uploading.', 'err', 3600);
    return false;
  }

  const targetContent = targetUrl === selectedToniesUrl ? toniesContentCache : getCachedToniesContent(targetUrl);
  const chaptersLen = (targetContent?.chapters || []).length;
  const maxFinalIndexAfterInsert = Math.max(0, chaptersLen);
  const effectiveTargetIndex = (targetIndex == null)
    ? (toniesReverseView ? maxFinalIndexAfterInsert : 0)
    : Math.max(0, Math.min(maxFinalIndexAfterInsert, Number(targetIndex) || 0));

  // The server places the upload, so no follow-up reorder is needed.
  const position = toniesUploadPosition(targetContent?.chapters || [], options.targetEdge, effectiveTargetIndex);
  const intent = {
    filename,
    targetIndex: effectiveTargetIndex,
    targetEdge: options.targetEdge,
    targetUrl,
    position,
    createdAt: Date.now(),
  };
  pendingDropUploadIntent = intent;

  startPendingToniesUpload(filename, targetUrl);
  let acknowledged = false;
  try {
    const res = await api('/api/upload-existing', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename, target_url: targetUrl, position })
    });

    if (!res?.job_id) {
      holdUnconfirmedLocalUpload(intent, filename, 'No job was returned for this upload.');
      return true;
    }
    acknowledged = true;
    highlightedJobId = res.job_id;
    activeUploadJobIds.add(res.job_id);
    localUploadToastShownJobIds.add(res.job_id);
    if (pendingToniesUpload?.filename === filename && !pendingToniesUpload.jobId) pendingToniesUpload.jobId = res.job_id;
    intent.jobId = res.job_id;
    uploadIntentsByJobId.set(res.job_id, intent);
    if (!skipQueuedToast) showToast('Upload queued', 'warn', 3000);
    if (skipQueuedToast && !suppressInitialToast) showToast('Preparing local file…', 'warn', 3000);
    if (highlightJobTimer) clearTimeout(highlightJobTimer);
    highlightJobTimer = setTimeout(() => {
      highlightedJobId = '';
      renderJobs();
    }, 8000);

    try {
      await refreshJobs();
    } catch (error) {
      // The POST already returned a job. A failed read must not release the source
      // or invite another upload. SSE/polling will reconcile the accepted job.
      recordWorkspaceNotice({id:`accepted-upload:${res?.job_id || filename}`, title:filename,
        targetUrl, stage:'Accepted; refresh needed', error:String(error?.message || error)});
      setToniesStatus('Upload accepted. Activity will update when the connection returns. Do not add again.', false);
    }

    const jobsCard = document.getElementById('jobs');
    if (scrollToResults && jobsCard) jobsCard.scrollIntoView({ behavior: 'smooth', block: 'start' });
    return true;
  } catch (e) {
    if (acknowledged) {
      // The job exists and its completion releases the file; never invite a second Add.
      recordWorkspaceNotice({id:`accepted-upload:${intent.jobId}`, title:filename, targetUrl,
        stage:'Accepted; refresh needed', error:String(e?.message || e)});
      return true;
    }
    if (!isDefiniteRefusal(e)) {
      holdUnconfirmedLocalUpload(intent, filename, String(e?.message || e));
      return true;
    }
    if (pendingDropUploadIntent === intent) pendingDropUploadIntent = null;
    if (pendingToniesUpload?.filename === filename && !pendingToniesUpload.jobId) clearPendingToniesUpload();
    toniesUploadSyncing = hasInFlightTrackedUploadForSelectedTonies();
    setToniesStatus(`Upload failed: ${e?.message || e}`, false);
    renderToniesContent();
    recordWorkspaceNotice({title:filename, targetUrl, stage:'Upload refused', error:String(e?.message || e)});
    showToast(`Upload failed: ${e?.message || e}`, 'err', 3200);
    return false;
  }
}

// Only a 4xx answer proves the server refused the upload. A lost connection, timeout,
// 5xx or an answer without a job may still have queued it, so don't invite a repeat.
function isDefiniteRefusal(error) {
  const status = /^HTTP (\d{3})\b/.exec(String(error?.message || error || ''))?.[1] || '';
  return status.startsWith('4') && status !== '408';
}

// Keeps the file blocked (its outstanding count is not released) and the captured
// destination; Activity explains why. Reloading the page after checking the Tonie clears it.
function holdUnconfirmedLocalUpload(intent, filename, reason) {
  intent.unconfirmed = true;
  if (pendingToniesUpload?.filename === filename && !pendingToniesUpload.jobId) clearPendingToniesUpload();
  toniesUploadSyncing = hasInFlightTrackedUploadForSelectedTonies();
  renderToniesContent();
  recordWorkspaceNotice({id:`unconfirmed-upload:${intent.targetUrl}:${filename}`, title:filename, targetUrl:intent.targetUrl,
    stage:'Could not confirm upload', error:reason,
    note:'Do not add again. It may already be queued; refresh the Tonie and check Activity first.'});
  setToniesStatus('Could not confirm the upload. Do not add it again until you have refreshed the Tonie.', false);
}

async function pumpLocalUploadQueue() {
  if (localUploadQueuePumping) return;
  localUploadQueuePumping = true;
  try {
    // Submissions go one at a time (each is a quick POST); the server queue then
    // sends up to three files in parallel and attaches them in this order.
    while (queuedLocalUploads.length) {
      const next = queuedLocalUploads.shift();
      queuedLocalUploadNames.delete(String(next?.filename || ''));
      renderFiles();
      if (!next?.filename) continue;
      const ok = await startSingleLocalUpload(next.filename, next.options || {});
      if (!ok) resolveLocalUploadPlanned(next.filename);
    }
  } finally {
    localUploadQueuePumping = false;
  }
}

async function uploadExisting(filename, options = {}) {
  // Keep a single local source pending through its accepted job and reconciliation.
  if ((Number(localUploadOutstandingByName[String(filename || '')]) || 0) > 0) return;
  const { targetIndex = null } = options;
  const targetUrl = options.targetUrl || await ensureUploadTargetUrl();
  if (!targetUrl) { showToast('Select a Creative Tonie before uploading.', 'err'); return; }
  const content = targetUrl === selectedToniesUrl ? toniesContentCache : getCachedToniesContent(targetUrl);
  const count = (content?.chapters || []).length;
  const targetEdge = options.targetEdge ?? (targetIndex == null ? (toniesReverseView ? 'end' : 'start')
    : targetIndex === 0 ? 'start' : targetIndex >= count ? 'end' : null);
  options = { ...options, targetUrl, targetEdge };

  // Only one submission POST at a time; the upload itself never blocks the next.
  if (localUploadQueuePumping) {
    const key = String(filename || '');
    if (!queuedLocalUploadNames.has(key)) {
      queuedLocalUploadNames.add(key);
      queuedLocalUploads.push({ filename, options: { ...options, targetIndex } });
      markLocalUploadPlanned(filename);
      showToast(`Queued local upload: ${filename}`, 'warn', 2200);
      renderFiles();
    }
    return;
  }

  markLocalUploadPlanned(filename);
  localUploadQueuePumping = true;
  try {
    const ok = await startSingleLocalUpload(filename, options);
    if (!ok) resolveLocalUploadPlanned(filename);
  } finally {
    localUploadQueuePumping = false;
  }
  void pumpLocalUploadQueue();
}

async function uploadExistingToIndex(filename, targetIndex) {
  const desired = Math.max(0, Number(targetIndex) || 0);
  showToast(`Uploading ${filename} to selected Tonie…`, 'warn', 3200);
  await uploadExisting(filename, { scrollToResults: false, skipQueuedToast: true, suppressInitialToast: true, targetIndex: desired });
}
