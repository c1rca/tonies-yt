/** Keeping the page's job list in step with the server, and reacting to job changes. */
function canCancelJob(status) {
  return CANCELLABLE_JOB_STATUSES.has(getJobStatus(status));
}

async function cancelJob(jobId) {
  if (!jobId) return;
  try {
    const res = await api(`/api/jobs/${jobId}/cancel`, { method: 'POST' });
    if (res?.error) throw new Error(res.error);
    showToast('Job canceled', 'ok', 1500);
    await refreshJobs();
  } catch (e) {
    showToast(`Cancel failed: ${e?.message || e}`, 'err', 2500);
  }
}

function getJobTransitionFlags(prevStatus, nextStatus) {
  return {
    justDownloading: prevStatus !== 'downloading' && nextStatus === 'downloading',
    justUploading: prevStatus !== 'uploading' && nextStatus === 'uploading',
    justDone: prevStatus !== 'done' && nextStatus === 'done',
    justFailed: prevStatus !== 'failed' && nextStatus === 'failed',
  };
}

function isTrackedJobForSelectedTonie(job, selectedUrl = selectedToniesUrl) {
  if (!selectedUrl || !job?.target_url) return false;
  return job.target_url === selectedUrl;
}

function handleTrackedInFlightTransitions(job, transitions, isTracked) {
  if (transitions.justDownloading && isTracked) {
    showToast('Downloading from YouTube…', 'warn', 3000);
  }

  if (transitions.justUploading && isTracked) {
    toniesUploadSyncing = true;
    setToniesProgress('Uploading to my.tonies.com…');
    renderToniesContent();
    if (job.selected_candidate) {
      showToast('Download complete. Uploading to Tonies…', 'warn', 3200);
    } else if (!localUploadToastShownJobIds.has(job.id)) {
      showToast('Uploading to Tonies…', 'warn', 3000);
    }
  }
}

function handleTrackedDoneTransition(job, transitions, isTracked) {
  if (!transitions.justDone || !isTracked) return;
  void finalizePendingToniesUpload(job, true);
}

function handleTrackedFailedTransition(job, transitions, isTracked) {
  if (!transitions.justFailed || !isTracked) return;
  void finalizePendingToniesUpload(job, false);
}

function handleTrackedJobTransitions(job, prevStatus) {
  jobStatusById[job.id] = job.status;
  const isTracked = activeUploadJobIds.has(job.id);
  const transitions = getJobTransitionFlags(prevStatus, job.status);

  handleTrackedInFlightTransitions(job, transitions, isTracked && isTrackedJobForSelectedTonie(job));
  handleTrackedDoneTransition(job, transitions, isTracked);
  handleTrackedFailedTransition(job, transitions, isTracked);
  if (isTracked && isReversedStatus(job.status)) {
    void finalizePendingToniesUpload(job, false);
  }
  if (hasUploadAttachmentReceipt(job) && (isTracked || completedUploadJobIds.has(job.id))) {
    void finalizePendingToniesUpload(job, job.status === 'done');
  }
}

function reconcileJobsSnapshot(sourceJobs = jobsCache) {
  reconcileActiveSearchJob(sourceJobs);
  reconcileSelectionStateWithBackend(sourceJobs);
  reconcileSearchUiReadiness(sourceJobs);
}

function processTrackedJobTransitions(sourceJobs = jobsCache) {
  // Backend snapshots are newest-created first; apply pending positioning intents oldest first.
  for (const job of [...sourceJobs].reverse()) {
    const prev = jobStatusById[job.id];
    handleTrackedJobTransitions(job, prev);
  }
}

function syncPendingToniesUploadState(sourceJobs = jobsCache) {
  if (!pendingToniesUpload) return;
  const pendingJobId = String(pendingToniesUpload.jobId || '');
  if (!pendingJobId) return;
  const job = sourceJobs.find(job => job.id === pendingJobId);
  if (job) syncPendingToniesUploadFromJob(job);
}

let jobsReadSeq = 0;

let jobsAppliedSeq = 0;

const reversedJobsById = new Map();

// Undone/canceled is final: no snapshot may bring back the job's earlier status or receipt.
function keepReversedJobs(snapshot) {
  for (const job of jobsCache) {
    if (isReversedStatus(job?.status)) reversedJobsById.set(job.id, job);
  }
  return snapshot.map(job => {
    if (isReversedStatus(job?.status)) {
      reversedJobsById.set(job.id, job);
      return job;
    }
    return reversedJobsById.get(job?.id) || job;
  });
}

async function refreshJobs(options = {}) {
  const { background = false } = options;
  const showLoading = !background && !hasLoadedJobsOnce;
  if (showLoading) {
    loadingJobs = true;
    renderJobs();
  }
  const readSeq = ++jobsReadSeq;
  const snapshot = await api('/api/jobs');
  if (readSeq < jobsAppliedSeq) return;  // an older poll answered after a newer one
  jobsAppliedSeq = readSeq;
  jobsCache = keepReversedJobs(Array.isArray(snapshot) ? snapshot : []);

  if (shouldAutoClearAgedResults()) {
    resultsCleared = true;
    saveResultsClearedState();
    searchResultsCutoffMs = Date.now();
  }

  // Keep full job state for background transition handling (uploads/sync),
  // even when search results are visually suppressed.
  reconcileJobsSnapshot(jobsCache);

  processTrackedJobTransitions(jobsCache);
  syncPendingToniesUploadState(jobsCache);

  loadingJobs = false;
  hasLoadedJobsOnce = true;
  renderJobs();
  renderToniesContent();  // upload placeholders follow job status
  pumpLocalUploadQueue();
}
