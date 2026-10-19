/** Search results view model: which jobs and candidates show, and in what state. */
let highlightedJobId = '';
let highlightJobTimer = null;
let searchResultsCutoffMs = null;
let activeSearchJobId = '';
let resultsCleared = false;
const START_WITH_CLEARED_RESULTS = true;
let searchLimit = 5;
let searchUiActive = false;
let selectingCandidateJobIds = new Set();
let selectedCandidateIndexByJob = {};
const ACTIVE_JOB_STATUSES = new Set(['queued', 'parsing', 'searching', 'awaiting_selection', 'queued_download', 'downloading', 'queued_prepare', 'preparing', 'waiting_upload', 'uploading']);
const SEARCH_READY_STATUSES = new Set(['awaiting_selection', 'failed']);
const SEARCH_PENDING_STATUSES = new Set(['queued', 'parsing', 'searching']);
function getSelectedCandidateIndex(jobId) {
  return Number(selectedCandidateIndexByJob[jobId]);
}

function setSelectedCandidateIndex(jobId, index) {
  selectedCandidateIndexByJob[jobId] = Number(index);
}

function clearSelectedCandidateIndex(jobId) {
  delete selectedCandidateIndexByJob[jobId];
}

function beginCandidateSelection(jobId) {
  selectingCandidateJobIds.add(jobId);
}

function endCandidateSelection(jobId) {
  selectingCandidateJobIds.delete(jobId);
}

function isCandidateSelectionPending(jobId, index = null) {
  const pending = selectingCandidateJobIds.has(jobId);
  if (!pending) return false;
  if (index == null) return true;
  return Number.isFinite(getSelectedCandidateIndex(jobId)) && getSelectedCandidateIndex(jobId) === Number(index);
}

function isHighlightedJob(jobId) {
  return !!(highlightedJobId && highlightedJobId === jobId);
}

function isJobActive(jobOrStatus) {
  return ACTIVE_JOB_STATUSES.has(getJobStatus(jobOrStatus));
}

function hasCandidateList(job) {
  return Array.isArray(job?.candidates) && job.candidates.length > 0;
}

function isProcessingJobStatus(status) {
  const s = getJobStatus(status);
  return ['queued', 'parsing', 'searching', 'awaiting_selection', 'queued_download', 'downloading', 'queued_prepare', 'preparing', 'waiting_upload', 'uploading'].includes(s);
}

function hasActiveRelatedJobs(rootJob, sourceJobs = jobsCache) {
  return getRelatedSearchJobs(rootJob, sourceJobs)
    .some((j) => j.id !== rootJob?.id && isProcessingJobStatus(j?.status));
}

function shouldPinJobInResultsWindow(job) {
  // Pin only true processing jobs (not awaiting_selection/searching roots from prior queries).
  if (isProcessingJobStatus(job?.status)) return true;
  if (hasCandidateList(job) && hasActiveRelatedJobs(job, jobsCache)) return true;
  return false;
}

function isJobInCurrentResultsWindow(job) {
  if (!searchResultsCutoffMs) return true;
  if (shouldPinJobInResultsWindow(job)) return true;
  const t = Date.parse(job?.created_at || '');
  // If timestamp is missing/invalid, treat as out-of-window to avoid stale flashes.
  return Number.isFinite(t) ? t >= searchResultsCutoffMs : false;
}

function isSearchRootJob(job) {
  // A root search card is the candidate list itself, not a selected/derived job.
  return hasCandidateList(job) && !job?.selected_candidate;
}

function getJobById(jobId, sourceJobs = jobsCache) {
  if (!jobId) return null;
  return sourceJobs.find((j) => j.id === jobId) || null;
}

function getRelatedSearchJobs(rootJob, sourceJobs = jobsCache) {
  const rootId = rootJob?.id;
  return sourceJobs.filter((j) => {
    if (j.id === rootId) return true;
    if ((j.user_message || '') !== (rootJob?.user_message || '')) return false;
    // Treat non-root derived jobs from the same search as related siblings,
    // including transient states right after selection where selected_candidate
    // may not be populated yet.
    if (!hasCandidateList(j)) return true;
    if (j.selected_candidate) return true;
    if (isProcessingJobStatus(j.status) || isCandidateSelectionPending(j.id)) return true;
    return false;
  });
}

function shouldSuppressSearchResults() {
  // Global guard: once results are cleared and no active search is running,
  // never rehydrate old lists from background refreshes/reconnects.
  return resultsCleared && !activeSearchJobId && !searchUiActive;
}

function shouldAutoClearAgedResults() {
  if (resultsCleared || searchUiActive || activeSearchJobId) return false;
  if (!Number.isFinite(lastSearchStartedAtMs) || lastSearchStartedAtMs <= 0) return false;
  const ageMs = Date.now() - lastSearchStartedAtMs;
  return ageMs > 10 * 60 * 1000;
}

function getVisibleSearchJobs(sourceJobs = jobsCache) {
  if (shouldSuppressSearchResults()) return [];
  return sourceJobs.filter((j) => (isJobActive(j) || hasCandidateList(j)) && isJobInCurrentResultsWindow(j));
}

function isSearchResultReadyStatus(status) {
  return SEARCH_READY_STATUSES.has(getJobStatus(status));
}

function isSearchPendingStatus(status) {
  return SEARCH_PENDING_STATUSES.has(getJobStatus(status));
}

function getLinkedJobsForCandidate(relatedJobs, parentId, candidate, targetUrl = null, parentCreatedAtMs = 0) {
  return relatedJobs.filter((j) => {
    if (j.id === parentId) return false;
    if (targetUrl && j.target_url && j.target_url !== targetUrl) return false;
    // Ignore stale jobs from older searches of the same query.
    if (parentCreatedAtMs > 0 && getJobCreatedAtMs(j) > 0 && getJobCreatedAtMs(j) < parentCreatedAtMs) return false;
    return !!(j.selected_candidate && candidate && j.selected_candidate.url === candidate.url);
  });
}

function getCandidateContext(parentJob, candidate, index) {
  const parentId = parentJob?.id;
  const targetUrl = selectedToniesUrl || null;
  const selectedIdx = getSelectedCandidateIndex(parentId);
  const selectingSame = isCandidateSelectionPending(parentId, index);
  const parentMatches = !!(parentJob?.selected_candidate && candidate && parentJob.selected_candidate.url === candidate.url);
  const relatedJobs = getRelatedSearchJobs(parentJob);
  const parentCreatedAtMs = getJobCreatedAtMs(parentJob);
  const linkedJobs = getLinkedJobsForCandidate(relatedJobs, parentId, candidate, targetUrl, parentCreatedAtMs);
  return { parentId, selectedIdx, selectingSame, parentMatches, relatedJobs, linkedJobs };
}

function getCandidateState(parentJob, candidate, index) {
  const ctx = getCandidateContext(parentJob, candidate, index);
  if (ctx.selectingSame) return { state: 'downloading', source: 'optimistic' };

  if (ctx.parentMatches) {
    const s = getJobStatus(parentJob);
    if (isJobActive(s)) return { state: s || 'active', source: 'parent', context: ctx };
    if (s === 'done') return { state: 'done', source: 'parent', context: ctx };
  }

  const active = ctx.linkedJobs.find((j) => isJobActive(j.status));
  if (active) return { state: getJobStatus(active) || 'active', source: 'linked', job: active, context: ctx };
  const done = ctx.linkedJobs.find((j) => getJobStatus(j) === 'done');
  if (done) return { state: 'done', source: 'linked', job: done, context: ctx };
  return { state: '', source: '', context: ctx };
}

function getCandidateDisplayState(info) {
  const state = String(info?.state || '');
  if (!state) return '';
  if (state === 'done') return 'done';
  if (state === 'downloading' || state === 'queued_download') return 'downloading';
  if (state === 'queued_prepare' || state === 'preparing') return 'preparing';
  if (state === 'waiting_upload') return 'waiting_upload';
  if (state === 'uploading') return 'uploading';
  return state;
}

function getCandidateCardView(parentJob, candidate, index) {
  const stateInfo = getCandidateState(parentJob, candidate, index);
  return {
    parentJob,
    candidate,
    index,
    stateInfo,
    isSelected: !!stateInfo.state,
    progressHtml: renderCandidateProgress(parentJob, candidate, index),
    selectLabel: 'Select',
  };
}

function getSearchCardCandidates(job) {
  return job.candidates || (job.selected_candidate ? [job.selected_candidate] : []);
}

function getSearchSessionCardView(job, session = null) {
  return {
    job,
    jobId: job.id,
    title: uiText(job.user_message),
    isHighlighted: isHighlightedJob(job.id),
    showHeaderProgress: !hasCandidateList(job),
    canCancel: canCancelJob(job.status),
    candidates: getSearchCardCandidates(job),
    error: job.error || '',
    session,
  };
}

function getSearchSessions(visibleJobs) {
  const visibleIds = new Set(visibleJobs.map((j) => j.id));
  const rootCandidates = visibleJobs.filter((j) => isSearchRootJob(j));
  const rootCandidateIds = new Set(rootCandidates.map((j) => j.id));

  // De-duplicate roots by query text; keep the newest root for each query.
  const latestRootByQuery = new Map();
  for (const root of rootCandidates) {
    const key = String(root?.user_message || '').trim().toLowerCase();
    const prev = latestRootByQuery.get(key);
    const prevMs = Date.parse(prev?.created_at || '') || 0;
    const curMs = Date.parse(root?.created_at || '') || 0;
    if (!prev || curMs >= prevMs) latestRootByQuery.set(key, root);
  }
  const rootJobs = Array.from(latestRootByQuery.values());

  const sessions = rootJobs.map((rootJob) => {
    const relatedJobs = getRelatedSearchJobs(rootJob, visibleJobs)
      .filter((j) => j.id !== rootJob.id && visibleIds.has(j.id));
    return { rootJob, relatedJobs };
  });

  const rootedIds = new Set(rootJobs.map((j) => j.id));
  const relatedIds = new Set(sessions.flatMap((s) => s.relatedJobs.map((j) => j.id)));
  const rootedQueries = new Set(rootJobs.map((j) => String(j?.user_message || '').trim().toLowerCase()));

  // Critical: never render duplicate same-query cards outside their root session.
  const unsessionedJobs = visibleJobs.filter((j) => (
    !rootedIds.has(j.id) &&
    !relatedIds.has(j.id) &&
    !rootCandidateIds.has(j.id) &&
    !rootedQueries.has(String(j?.user_message || '').trim().toLowerCase())
  ));

  return { sessions, unsessionedJobs };
}

function getJobCreatedAtMs(job) {
  const t = Date.parse(job?.created_at || '');
  return Number.isFinite(t) ? t : 0;
}

function getLatestRootJob(sourceJobs = []) {
  const roots = sourceJobs.filter((j) => hasCandidateList(j));
  if (!roots.length) return null;
  return roots.slice().sort((a, b) => getJobCreatedAtMs(b) - getJobCreatedAtMs(a))[0] || null;
}

function filterToLatestSearchAndActive(sourceJobs = []) {
  if (!sourceJobs.length) return [];

  const latestRoot = getLatestRootJob(sourceJobs);
  const activeRoot = activeSearchJobId ? getJobById(activeSearchJobId, sourceJobs) : null;
  const focusRoot = activeRoot || latestRoot;
  if (!focusRoot) return [];

  const related = getRelatedSearchJobs(focusRoot, sourceJobs);
  const keepIds = new Set(related.map((j) => j.id));
  keepIds.add(focusRoot.id);

  return sourceJobs.filter((j) => keepIds.has(j.id));
}

function getRenderableVisibleJobs() {
  const visibleJobs = getVisibleSearchJobs();
  let scopedJobs = filterToLatestSearchAndActive(visibleJobs);

  // Always prefer the actively submitted search root (and its related jobs)
  // when present. This prevents the Results pane from staying stuck on the
  // loader if another root search wins the "latest" filter race.
  if (activeSearchJobId) {
    const activeRoot = getJobById(activeSearchJobId, visibleJobs);
    if (activeRoot) {
      const preferred = getRelatedSearchJobs(activeRoot, visibleJobs);
      if (preferred.length) scopedJobs = preferred;
    } else {
      // Active search submitted, but root not yet visible in filtered set:
      // avoid flashing prior query results.
      return [];
    }
  }

  // While a new search is initializing, don't flash prior results.
  if (searchUiActive && !activeSearchJobId) return [];
  if (!searchUiActive) return scopedJobs;
  return scopedJobs.filter((j) => isSearchResultReadyStatus(j.status) || isJobActive(j.status));
}

function getSessionCardViews(sessions) {
  return sessions.map((session) => getSearchSessionCardView(session.rootJob, session));
}

function getUnsessionedCardViews(unsessionedJobs) {
  return unsessionedJobs.map((job) => getSearchSessionCardView(job));
}

function getSearchViewSignature(view) {
  const job = view?.job || {};
  // Keep dedupe strictly job-id based so a fresh search root cannot be
  // collapsed into an older card with matching query/candidates.
  return `job|${String(view?.jobId || job?.id || '')}`;
}

function dedupeSearchViews(views = []) {
  const seen = new Set();
  const out = [];
  for (const view of views) {
    const key = getSearchViewSignature(view);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    out.push(view);
  }
  return out;
}

function getResultsViewModel() {
  const jobsToRender = getRenderableVisibleJobs();
  const { sessions, unsessionedJobs } = getSearchSessions(jobsToRender);
  const sessionViews = getSessionCardViews(sessions);
  const unsessionedViews = getUnsessionedCardViews(unsessionedJobs);
  const deduped = dedupeSearchViews([...sessionViews, ...unsessionedViews]);

  // Hard guard: Results pane should represent a single active/latest search card.
  if (!deduped.length) return deduped;
  if (activeSearchJobId) {
    const activeView = deduped.find((v) => String(v?.jobId || '') === String(activeSearchJobId));
    if (activeView) return [activeView];

    const activeJob = getJobById(activeSearchJobId, jobsCache);
    const activeQuery = String(activeJob?.user_message || '').trim().toLowerCase();
    if (activeQuery) {
      const queryView = deduped.find((v) => String(v?.job?.user_message || v?.title || '').trim().toLowerCase() === activeQuery);
      if (queryView) return [queryView];
    }
  }

  return [deduped[0]];
}
