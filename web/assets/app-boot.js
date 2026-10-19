/** Starts the page: wires each area, restores caches and loads live data. Loaded last. */
initAppCore();
initSearch();
initLocalFiles();
initContextMenus();
loadResultsClearedState();
initWorkspaceSources();
initYoutubeSelection();
initAudioEditor();
loadWorkspaceTheme();
setWorkspaceView('tracks');
setWorkspaceSource('youtube');
renderWorkspaceTarget();
saveResultsClearedState();
loadSearchLimit();
loadFilesCache();
renderFiles();
checkHealth();
renderSearchUX();
renderToniesEditUI();
loadImageCache();
loadToniesContentByUrlCache();
loadToniesViewPrefs();
loadToniesCache();
renderTonies();

const cachedContent = getCachedToniesContent(selectedToniesUrl);
if (cachedContent) {
  toniesContentCache = overlayToniesContent(selectedToniesUrl, cachedContent);
  renderToniesContent();
}

void refreshTonies({ force: true });
void refreshWorkspaceActions();
Promise.all([refreshFiles(), refreshJobs()]);
renderRealtimeStatus();
if (REALTIME_MODE === 'sse') connectSse();
setTimeout(pollLoop, 3500);
