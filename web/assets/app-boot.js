/** Starts the page: wires each area, restores caches and loads live data. Loaded last. */
initLocalPreview();
initToniesPicker();
initTonieLibrary();
initAppCore();
initSearch();
initLocalFiles();
initContextMenus();
loadResultsClearedState();
initWorkspaceSources();
initYoutubeSelection();
initAudioEditor();
loadWorkspaceTheme();
restoreWorkspacePlace();
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
restoreWorkspaceActionsForTab();

const cachedContent = getCachedToniesContent(selectedToniesUrl);
if (cachedContent) {
  toniesContentCache = overlayToniesContent(selectedToniesUrl, cachedContent);
  renderToniesContent();
}
// Everything above draws from this browser's caches: show the workspace now, complete.
document.documentElement.classList.remove('booting');

void refreshTonies({ force: true });
void refreshWorkspaceActions();
Promise.all([refreshFiles(), refreshJobs()]);
renderRealtimeStatus();
if (REALTIME_MODE === 'sse') connectSse();
setTimeout(pollLoop, 3500);
