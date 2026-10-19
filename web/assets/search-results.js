/** YouTube search: sending a search, rendering results and picking a result. */
let queuedSearchMessage = '';
let queuedSearchRetryTimer = null;
let activeSearchRequestController = null;
let activeSearchRequestSeq = 0;
let lastSearchStartedAtMs = 0;
const RESULTS_CLEARED_KEY = 'tonies-auto.results-cleared.v1';
const SEARCH_LIMIT_KEY = 'tonies-auto.search-limit.v1';
function initSearch() {
  try {
    if (localStorage.getItem(RESULTS_CLEARED_KEY) === '1') {
      document.documentElement.classList.add('hide-search-results');
    }
  } catch {}
  if (searchBtnEl) searchBtnEl.onclick = sendQuery;
  if (clearResultsEl) clearResultsEl.onclick = clearResults;
  if (queryInputEl) {
    queryInputEl.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') sendQuery();
    });
  }
}

const JOB_PROGRESS_TEXT = {
  queued_download: 'Queued for download…', downloading: 'Downloading audio…',
  queued_prepare: 'Queued for preparation…', preparing: 'Preparing audio…',
  waiting_upload: 'Queued for upload…', uploading: 'Uploading to Tonies…',
};

function renderJobProgress(job, placement = '') {
  const s = String(job?.status || '');
  if (isCandidateSelectionPending(job?.id)) return statusLine('Downloading audio…', { busy: true, placement });
  if (s === 'awaiting_selection') return statusLine('Select a result to upload to creative Tonies', { placement });
  if (JOB_PROGRESS_TEXT[s]) return statusLine(JOB_PROGRESS_TEXT[s], { busy: true, placement });
  if (s === 'done' && syncingResultJobIds.has(job?.id)) return statusLine('Syncing with my.tonies.com…', { busy: true, placement });
  if (s === 'done') return statusLine('✓ Completed', { tone: 'ok', placement });
  if (s === 'failed') return statusLine('✕ Failed', { tone: 'err', placement });
  return '';
}

function renderCandidateProgress(parentJob, candidate, index) {
  const info = getCandidateState(parentJob, candidate, index);
  const displayState = getCandidateDisplayState(info);
  if (!displayState) return '';
  if (displayState === 'done') return statusLine('✓ Uploaded', { tone: 'ok', placement: 'above' });
  if (info.job) return renderJobProgress(info.job, 'above');
  const text = JOB_PROGRESS_TEXT[displayState];
  if (text) return statusLine(text, { busy: true, placement: 'above' });
  return '';
}

function renderSearchJobCard(view) {
  const { job, jobId, title, isHighlighted, showHeaderProgress, canCancel, candidates, error } = view;
  const cands = candidates.map((c, i) => renderCandidateCard(job, c, i)).join('');
  return `
    <article class="job ${isHighlighted ? 'focus-upload-job' : ''}">
      <div class="job-top">
        <div>
          <div class="job-title-row">
            <strong>${esc(title)}</strong>
          </div>
          ${showHeaderProgress ? renderJobProgress(job) : ''}
        </div>
        <div>${canCancel ? `<button class="ghost tiny" style="padding:4px 8px;" onclick="cancelJob('${jobId}')">Cancel</button>` : ''}</div>
      </div>

      ${error ? `<div class="error"><div><strong>Error:</strong> ${esc(error)}</div><div><strong>Reason:</strong> ${esc(classifyError(error))}</div></div>` : ''}
      ${cands}
    </article>
  `;
}

function renderSearchJobCardsHtml(views) {
  return views.map((view) => renderSearchJobCard(view)).join('');
}

function getSearchUxState() {
  const renderableJobs = getRenderableVisibleJobs();
  const hasReadyVisibleResults = renderableJobs.some((j) => isSearchResultReadyStatus(j.status));
  const activeSearchJob = getJobById(activeSearchJobId, jobsCache);
  const activeSearchPending = !!(activeSearchJob && isSearchPendingStatus(activeSearchJob.status));
  return { hasReadyVisibleResults, activeSearchPending };
}

function getSearchDisplayState() {
  const uxState = getSearchUxState();
  if (uxState.hasReadyVisibleResults) return 'ready';
  if (searchUiActive || uxState.activeSearchPending) return 'searching';
  return 'idle';
}

function renderSearchUX() {
  if (!searchBtnEl || !resultsLoaderEl) return;

  searchBtnEl.disabled = false;
  searchBtnEl.textContent = 'Search';

  const displayState = getSearchDisplayState();

  if (displayState === 'ready') {
    resultsLoaderEl.textContent = '';
  } else if (displayState === 'searching') {
    resultsLoaderEl.innerHTML = `<span class="loading-dot"></span>Searching YouTube…`;
  } else if (queuedSearchMessage) {
    resultsLoaderEl.innerHTML = `<span class="loading-dot"></span>Connection lost. Retrying…`;
  } else {
    resultsLoaderEl.textContent = '';
  }
}

function queueSearchRetry(message) {
  queuedSearchMessage = String(message || '').trim();
  renderSearchUX();
}

async function flushQueuedSearchRetry() {
  if (!queuedSearchMessage || searchUiActive || !isBackendOnline) return;
  const msg = queuedSearchMessage;
  queuedSearchMessage = '';
  renderSearchUX();
  if (queryInputEl) queryInputEl.value = msg;
  await sendQuery();
}

async function sendQuery() {
  const message = (queryInputEl?.value || '').trim();
  if (!message) return;

  if (activeSearchRequestController) {
    try { activeSearchRequestController.abort(); } catch {}
    activeSearchRequestController = null;
  }
  const requestSeq = ++activeSearchRequestSeq;
  const controller = new AbortController();
  activeSearchRequestController = controller;

  // Start a fresh Results view for this search.
  resultsCleared = false;
  saveResultsClearedState();
  document.documentElement.classList.remove('hide-search-results');
  lastSearchStartedAtMs = Date.now();
  searchResultsCutoffMs = Date.now() - 2000;
  jobsCache = [];
  searchUiActive = true;
  renderSearchUX();
  renderJobs();

  // On narrow/mobile layouts, bring Results into view immediately after submit.
  if (window.matchMedia && window.matchMedia('(max-width: 1060px)').matches && jobsEl) {
    try { jobsEl.scrollIntoView({ behavior: 'smooth', block: 'start' }); } catch {}
  }

  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, search_limit: searchLimit }),
      signal: controller.signal,
    });
    if (!res.ok) {
      const txt = await res.text().catch(() => '');
      throw new Error(`HTTP ${res.status}${txt ? `: ${txt.slice(0, 160)}` : ''}`);
    }
    const data = await res.json();
    if (requestSeq !== activeSearchRequestSeq) return;
    activeSearchJobId = data?.job_id || '';
    if (queryInputEl) queryInputEl.value = '';
    await refreshJobs();
  } catch (e) {
    if (requestSeq !== activeSearchRequestSeq) return;
    const errText = String(e?.message || e || '').toLowerCase();
    const aborted = errText.includes('abort') || String(e?.name || '') === 'AbortError';
    activeSearchJobId = '';
    if (queryInputEl) queryInputEl.value = message;
    if (!aborted && (!isBackendOnline || errText.includes('failed to fetch') || errText.includes('network'))) {
      queueSearchRetry(message);
      showToast('Connection lost. Will retry when back online.', 'warn', 2200);
    } else if (!aborted) {
      showToast(`Search failed: ${e?.message || e}`, 'err', 2500);
    }
  } finally {
    if (activeSearchRequestController === controller) activeSearchRequestController = null;
    if (requestSeq === activeSearchRequestSeq) {
      searchUiActive = false;
      renderSearchUX();
    }
  }
}

function clearResults() {
  activeSearchJobId = '';
  searchResultsCutoffMs = null;
  resultsCleared = true;
  saveResultsClearedState();
  searchUiActive = false;
  queuedSearchMessage = '';
  selectingCandidateJobIds.clear();
  selectedCandidateIndexByJob = {};
  renderSearchUX();
  jobsCache = [];
  renderJobs();
}

function applyOptimisticCandidateSelection(jobId, index) {
  beginCandidateSelection(jobId);
  setSelectedCandidateIndex(jobId, index);
  markUploadJobActive(jobId);

  const j = getJobById(jobId);
  const i = Number(index);
  const pickedCandidate = (j && Array.isArray(j.candidates) && Number.isFinite(i) && j.candidates[i]) ? j.candidates[i] : null;
  if (j && pickedCandidate && getJobStatus(j) === 'awaiting_selection') {
    j.selected_candidate = pickedCandidate;
    j.status = 'downloading';
  }
  return pickedCandidate;
}

function mergeReturnedSelectionJob(parentJobId, returnedJobId, res) {
  if (returnedJobId === parentJobId || !res) return;
  jobsCache = [res, ...jobsCache.filter((x) => x.id !== returnedJobId)];
}

function applySelectionResponse(parentJobId, index, res) {
  const returnedJobId = res?.id || parentJobId;
  if (returnedJobId !== parentJobId) activeUploadJobIds.delete(parentJobId);
  markUploadJobActive(returnedJobId);
  setSelectedCandidateIndex(returnedJobId, index);
  mergeReturnedSelectionJob(parentJobId, returnedJobId, res);
}

function rollbackOptimisticCandidateSelection(jobId) {
  endCandidateSelection(jobId);
  clearSelectedCandidateIndex(jobId);
}

function getCandidateSelectionPayload(index) {
  // YouTube picks land at the visual top; the server places them there directly.
  const position = selectedToniesUrl ? { edge: toniesReverseView ? 'end' : 'start' } : null;
  return { index, target_url: selectedToniesUrl || null, position };
}

function hasSelectionAdvanced(job) {
  return isCandidateSelectionPending(job?.id) && getJobStatus(job) !== 'awaiting_selection';
}

function getResultsRenderState() {
  const views = getResultsViewModel();
  return {
    views,
    hasReadyResults: views.some((v) => isSearchResultReadyStatus(v?.job?.status)),
    isEmpty: views.length === 0,
  };
}

function getResultsEmptyStateHtml(renderState) {
  if (!renderState.isEmpty) return null;
  if (loadingJobs) return '<div class="skeleton"></div>';
  if (getSearchDisplayState() === 'searching') return '<div class="skeleton"></div>';
  return '<div class="muted results-empty">No results yet. Search YouTube above and pick a track to begin.</div>';
}

function renderJobs() {
  renderWorkspaceActivity();
  const hideResults = !!resultsCleared && getSearchDisplayState() === 'idle';
  document.documentElement.classList.toggle('hide-search-results', hideResults);
  const renderState = getResultsRenderState();

  // If selectable results are rendering, force-clear the Results loader line.
  if (resultsLoaderEl && renderState.hasReadyResults) {
    resultsLoaderEl.textContent = '';
  }

  const emptyStateHtml = getResultsEmptyStateHtml(renderState);
  if (emptyStateHtml !== null) {
    jobsEl.innerHTML = emptyStateHtml;
    syncYoutubeSelection(null);
    return;
  }

  const singleCardViews = (renderState.views && renderState.views.length > 1)
    ? [renderState.views[0]]
    : renderState.views;
  if (searchLimitLabelEl) searchLimitLabelEl.textContent = youtubeResultsLabel(singleCardViews[0]?.job) || `Top ${searchLimit} results`;
  syncYoutubeSelection(singleCardViews[0]?.job?.id || null);
  jobsEl.innerHTML = renderSearchJobCardsHtml(singleCardViews);
  for (const button of jobsEl.querySelectorAll('.candidate-add')) button.addEventListener('click', () => {
    void pickCandidate(button.getAttribute('data-job-id'), Number(button.getAttribute('data-candidate-index')));
  });
  bindCandidateEdit(jobsEl);
  bindCandidatePicks(jobsEl);
}

function reconcileSelectionStateWithBackend(sourceJobs = jobsCache) {
  // Clear optimistic selection state once backend status advances.
  for (const j of sourceJobs) {
    if (hasSelectionAdvanced(j)) {
      endCandidateSelection(j.id);
      clearSelectedCandidateIndex(j.id);
    }
  }
}

function reconcileActiveSearchJob(sourceJobs = jobsCache) {
  if (!activeSearchJobId) return;
  const stillExists = sourceJobs.some((j) => j.id === activeSearchJobId);
  if (!stillExists) activeSearchJobId = '';
}

function reconcileSearchUiReadiness(sourceJobs = jobsCache) {
  if (!searchUiActive) return;
  const visibleSearchJobs = getVisibleSearchJobs(sourceJobs);
  const ready = visibleSearchJobs.some((j) => isSearchResultReadyStatus(j.status));
  if (!ready) return;
  searchUiActive = false;
  renderSearchUX();
}
