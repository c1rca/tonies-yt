/** Browser storage: cached files, Tonies, library content and view preferences. */
function saveImageCache() {
  try {
    localStorage.setItem(TONIES_IMAGE_CACHE_KEY, JSON.stringify(toniesImageCache || {}));
  } catch {}
}

function loadImageCache() {
  try {
    const raw = localStorage.getItem(TONIES_IMAGE_CACHE_KEY);
    toniesImageCache = raw ? (JSON.parse(raw) || {}) : {};
  } catch {
    toniesImageCache = {};
  }
}

function loadToniesViewPrefs() {
  try {
    const raw = localStorage.getItem(TONIES_REVERSE_VIEW_KEY);
    toniesReverseView = raw == null ? true : raw === '1';
  } catch {
    toniesReverseView = true;
  }
  renderToniesOrderToggle();
}

function saveToniesViewPrefs() {
  try { localStorage.setItem(TONIES_REVERSE_VIEW_KEY, toniesReverseView ? '1' : '0'); } catch {}
}

function loadResultsClearedState() {
  // UX policy: always start with a clean Results pane on page load.
  resultsCleared = START_WITH_CLEARED_RESULTS;
  // Ensure stale jobs from prior sessions are outside the current window if pane is reopened.
  searchResultsCutoffMs = Date.now();
  activeSearchJobId = '';
  searchUiActive = false;
  jobsCache = [];
}

function saveResultsClearedState() {
  try { localStorage.setItem(RESULTS_CLEARED_KEY, resultsCleared ? '1' : '0'); } catch {}
}

function loadSearchLimit() {
  try {
    const raw = Number(localStorage.getItem(SEARCH_LIMIT_KEY));
    searchLimit = [5, 10, 15, 20].includes(raw) ? raw : 5;
  } catch {
    searchLimit = 5;
  }
  if (searchLimitLabelEl) searchLimitLabelEl.textContent = `Top ${searchLimit} results`;
}

function loadFilesCache() {
  try {
    const raw = localStorage.getItem(FILES_CACHE_KEY);
    if (!raw) return;
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed?.files)) filesCache = parsed.files;
  } catch {}
}

function saveFilesCache() {
  try {
    localStorage.setItem(FILES_CACHE_KEY, JSON.stringify({ files: filesCache || [], ts: Date.now() }));
  } catch {}
}

function saveToniesCache() {
  try {
    localStorage.setItem(TONIES_CACHE_KEY, JSON.stringify({ tonies: toniesCache, selected: selectedToniesUrl, ts: Date.now() }));
    if (selectedToniesUrl) localStorage.setItem(TONIES_SELECTED_KEY, selectedToniesUrl);
  } catch {}
}

function loadToniesCache() {
  try {
    const selected = localStorage.getItem(TONIES_SELECTED_KEY);
    if (selected) selectedToniesUrl = selected;
    const raw = localStorage.getItem(TONIES_CACHE_KEY);
    if (!raw) return false;
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed?.tonies) || !parsed.tonies.length) return false;
    toniesCache = parsed.tonies;
    if (!selectedToniesUrl && parsed.selected) selectedToniesUrl = parsed.selected;
    return true;
  } catch {
    return false;
  }
}

function loadToniesContentByUrlCache() {
  try {
    const raw = localStorage.getItem(TONIES_CONTENT_CACHE_KEY);
    toniesContentByUrlCache = raw ? (JSON.parse(raw) || {}) : {};
  } catch {
    toniesContentByUrlCache = {};
  }
}

function saveToniesContentByUrlCache() {
  try {
    localStorage.setItem(TONIES_CONTENT_CACHE_KEY, JSON.stringify(toniesContentByUrlCache || {}));
  } catch {}
}

function getCachedToniesContent(url) {
  if (!url) return null;
  const item = toniesContentByUrlCache?.[url];
  if (!item || !item.data) return null;
  return item.data;
}

function setCachedToniesContent(url, data) {
  if (!url || !data || !Array.isArray(data.chapters)) return;
  // An empty canonical list is valid, including deletion of the last track.
  toniesContentByUrlCache[url] = { ts: Date.now(), data };
  saveToniesContentByUrlCache();
}
