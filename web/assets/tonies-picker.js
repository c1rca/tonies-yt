/** The Creative Tonie picker: the list of Tonies, their artwork and choosing one. */
let toniesImagesLoading = false;
let loadingTonies = false;
let toniesLoadError = '';
let toniesStatsSyncing = new Set();
let toniesStatsSyncBusy = false;
let manualToniesRefreshInFlight = false;
const TONIES_CACHE_KEY = 'tonies-auto.creative-tonies.v1';
const TONIES_SELECTED_KEY = 'tonies-auto.selected-tonies-url.v1';
const TONIES_IMAGE_CACHE_KEY = 'tonies-auto.creative-tonies.image-cache.v1';
function initToniesPicker() {
  document.getElementById('refresh-tonies').onclick = async () => {
    if (blockIfActionInProgress() || manualToniesRefreshInFlight) return;
    manualToniesRefreshInFlight = true;
    setToniesStatus('Loading Tonies library…', true);
    try {
      await refreshTonies({ force: true });
    } catch (e) {
      setToniesStatus(`Error: ${e?.message || e}`, false);
      renderToniesContent();
    } finally {
      manualToniesRefreshInFlight = false;
      toniesApplying = false;
      renderTonies();
      renderToniesEditUI();
    }
  };
}

function renderTonies() {
  renderWorkspaceTarget();
  const el = document.getElementById('tonies');
  const refreshBtn = document.getElementById('refresh-tonies');
  const busy = isToniesActionBusy();
  if (!toniesCache.length) {
    if (refreshBtn) {
      refreshBtn.disabled = busy;
      refreshBtn.innerHTML = (toniesImagesLoading || loadingTonies || toniesStatsSyncBusy || toniesStatsSyncing.size) ? `<span class="loading-dot"></span>Loading…` : (busy ? 'Busy…' : 'Refresh');
    }
    if (loadingTonies) {
      setHtmlIfChanged(el, '<div class="tonies-row-skeleton"></div><div class="tonies-row-skeleton"></div>');
    } else if (toniesLoadError) {
      setHtmlIfChanged(el, `<div class="tiny status-err">${esc(toniesLoadError)}</div>`);
    } else {
      setHtmlIfChanged(el, '<div class="tiny muted">No creative tonies found.</div>');
    }
    return;
  }
  const selected = toniesCache.find(t => t.edit_url === selectedToniesUrl) || toniesCache[0];
  if (refreshBtn) {
    const selectedUrl = selected?.edit_url || selectedToniesUrl || '';
    const statusText = String(toniesStatusEl?.textContent || '').trim().toLowerCase();
    const statusLooksSyncing = toniesApplying && (statusText.includes('syncing tonies library') || statusText.includes('loading tonies library'));
    const selectedContentSyncing = !!selectedUrl && loadingToniesContent && selectedUrl === selectedToniesUrl && toniesApplying;
    refreshBtn.disabled = busy;

    if (toniesImagesLoading) refreshBtn.innerHTML = `<span class="loading-dot"></span>Loading images…`;
    else if (selectedContentSyncing || statusLooksSyncing) refreshBtn.innerHTML = `<span class="loading-dot"></span>Syncing…`;
    else refreshBtn.innerHTML = busy ? 'Busy…' : 'Refresh';
  }
  renderWorkspaceTonieRail(el);
}

function blobToDataUrl(blob) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onloadend = () => resolve(r.result);
    r.onerror = reject;
    r.readAsDataURL(blob);
  });
}

async function hydrateImageCacheForTonies() {
  const missing = toniesCache.filter(t => t?.image && !toniesImageCache[t.edit_url]);
  if (!missing.length) return;

  toniesImagesLoading = true;
  renderTonies();

  let changed = false;
  try {
    for (const t of missing) {
      try {
        const resp = await fetch(t.image, { cache: 'force-cache' });
        if (!resp.ok) continue;
        const blob = await resp.blob();
        const dataUrl = await blobToDataUrl(blob);
        toniesImageCache[t.edit_url] = dataUrl;
        changed = true;
      } catch {}
    }
  } finally {
    toniesImagesLoading = false;
    if (changed) saveImageCache();
    renderTonies();
  }
}

function hasRecentToniesSync(url, maxAgeMs = 7 * 60 * 1000) {
  if (!url) return false;
  const ts = Number(toniesContentByUrlCache?.[url]?.ts || 0);
  if (!ts) return false;
  return (Date.now() - ts) <= Math.max(1000, Number(maxAgeMs) || 0);
}

async function syncCreativeToniesStats(options = {}) {
  const { force = false } = options;
  if (toniesStatsSyncBusy) return;
  toniesStatsSyncBusy = true;
  try {
    for (const t of toniesCache) {
      const url = t.edit_url;
      if (!url || (!force && hasRecentToniesSync(url, 120000))) continue;
      toniesStatsSyncing.add(url);
      renderTonies();
      await refreshToniesContent({ targetUrl: url, force: true });
      toniesStatsSyncing.delete(url);
    }
  } finally {
    toniesStatsSyncBusy = false;
    toniesStatsSyncing.clear();
    renderTonies();
  }
}

async function refreshTonies(options = {}) {
  const { force = false } = options;
  if (!force && toniesCache.length) {
    renderTonies();
    return;
  }

  loadingTonies = true;
  toniesLoadError = '';
  renderTonies();

  try {
    const data = await api('/api/creative-tonies', {}, 30000);
    toniesCache = Array.isArray(data) ? data : [];
    if (!Array.isArray(data) && data?.error) {
      toniesLoadError = String(data.error || 'Failed to load Creative Tonies');
      setToniesStatus(toniesLoadError, false);
      return;
    }
    const previousSelection = selectedToniesUrl;
    if (!selectedToniesUrl && toniesCache.length) selectedToniesUrl = toniesCache[0].edit_url;
    // If previous selected Tonies disappeared, fall back to first available.
    if (selectedToniesUrl && toniesCache.length && !toniesCache.some(t => t.edit_url === selectedToniesUrl)) {
      selectedToniesUrl = toniesCache[0].edit_url;
    }
    if (selectedToniesUrl !== previousSelection) {
      // Source Add controls rendered before discovery name and enable the new destination.
      renderWorkspaceTarget();
      renderLocalSelection();
      renderFiles();
    }

    renderTonies();
    saveToniesCache();
    hydrateImageCacheForTonies();

    // One content response per Tonie supplies both card statistics and the library.
    await syncCreativeToniesStats({ force: true });
  } catch (e) {
    toniesLoadError = `Failed to load Creative Tonies: ${e?.message || e}`;
    setToniesStatus(toniesLoadError, false);
  } finally {
    loadingTonies = false;
    renderTonies();
  }
}

function selectTonies(url) {
  closeToniesMenu();
  workspaceTargetVisit += 1;
  trackSelectionMode = false;
  toggleTonieSelector(false);
  selectedToniesUrl = url;
  renderWorkspaceTarget();
  renderLocalSelection();
  void refreshWorkspaceActions();
  renderFiles();
  renderJobs();
  toniesEditMode = false;
  toniesNameDrafts = {};
  renderToniesEditUI();
  recentUploadedChapterIds = {};
  toniesContentRequestSeq += 1;
  loadingToniesContent = false;
  loadingToniesContentUrl = '';
  if (toniesProcessingTimer) clearTimeout(toniesProcessingTimer);
  toniesProcessingTimer = null;
  toniesProcessingPolls = 0;
  toniesUploadSyncing = hasInFlightTrackedUploadForSelectedTonies();
  renderTonies();
  saveToniesCache();
  const recentSync = hasRecentToniesSync(selectedToniesUrl, 7 * 60 * 1000);
  refreshToniesContent({ clearList: true, preferCache: true, force: !recentSync });
}
