/** Connects buttons, menus and the local player to their handlers. Runs once at load. */
try {
  if (localStorage.getItem('tonies-auto.results-cleared.v1') === '1') {
    document.documentElement.classList.add('hide-search-results');
  }
} catch {}

for (const event of ['play', 'pause', 'ended']) {
  localAudioEl.addEventListener(event, updateLocalPlaybackButtons);
}

localAudioEl.addEventListener('error', () => {
  if (localPlaybackFilename) {
    localPlayerStatusEl.textContent = 'Could not play this file. Try again or refresh the library.';
    updateLocalPlaybackButtons();
  }
});

document.getElementById('local-player-close').addEventListener('click', stopLocalPlayback);

// expose for inline handlers
window.uploadExisting = uploadExisting;

window.deleteFile = deleteFile;

window.pickCandidate = pickCandidate;

window.selectTonies = selectTonies;

window.enqueueToniesDelete = enqueueToniesDelete;

if (searchBtnEl) searchBtnEl.onclick = sendQuery;

if (clearResultsEl) clearResultsEl.onclick = clearResults;

if (lockAppEl) lockAppEl.onclick = lockApp;

if (queryInputEl) {
  queryInputEl.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') sendQuery();
  });
}

document.getElementById('refresh-files').onclick = refreshFiles;

let manualToniesRefreshInFlight = false;

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

document.getElementById('tonies-menu-rename').onclick = () => {
  const id = toniesMenuChapterId, url = toniesMenuTargetUrl; closeToniesMenu();
  if (url === selectedToniesUrl) beginInlineRename(id);
};

document.getElementById('tonies-menu-delete').onclick = () => {
  const id = toniesMenuChapterId, url = toniesMenuTargetUrl; closeToniesMenu();
  const index = (toniesContentCache?.chapters || []).findIndex(c => c.chapter_id === id);
  if (url === selectedToniesUrl && index >= 0) void enqueueToniesDelete(index);
};

toniesMenuEl.addEventListener('keydown', e => {
  const items = [...toniesMenuEl.querySelectorAll('button')];
  if (['ArrowUp','ArrowDown','Home','End','Tab'].includes(e.key)) {
    if (e.key === 'Tab') { closeToniesMenu(); return; }
    e.preventDefault(); const i = items.indexOf(document.activeElement);
    const next = e.key === 'Home' ? 0 : e.key === 'End' ? items.length-1 : (i + (e.key === 'ArrowUp' ? -1 : 1) + items.length) % items.length;
    items[next]?.focus();
  }
});

if (toniesMenuTopEl) toniesMenuTopEl.onclick = () => moveToniesFromMenu('top');

if (toniesMenuBottomEl) toniesMenuBottomEl.onclick = () => moveToniesFromMenu('bottom');

if (toniesMenuUpEl) toniesMenuUpEl.onclick = () => moveToniesFromMenu('up');

if (toniesMenuDownEl) toniesMenuDownEl.onclick = () => moveToniesFromMenu('down');

if (toniesMenuCancelEl) toniesMenuCancelEl.onclick = () => closeToniesMenu();

if (fileMenuUploadEl) fileMenuUploadEl.onclick = () => uploadFromFileMenu('default');

if (fileMenuUploadTopEl) fileMenuUploadTopEl.onclick = () => uploadFromFileMenu('top');

if (fileMenuUploadBottomEl) fileMenuUploadBottomEl.onclick = () => uploadFromFileMenu('bottom');

if (fileMenuDownloadEl) fileMenuDownloadEl.onclick = () => downloadLocalFile(fileMenuFilename);

if (fileMenuCancelEl) fileMenuCancelEl.onclick = () => closeFileMenu();

document.addEventListener('click', (e) => {
  if (toniesMenuEl?.classList.contains('open')) {
    if (!toniesMenuEl.contains(e.target)) closeToniesMenu();
  }
  if (fileMenuEl?.classList.contains('open')) {
    if (!fileMenuEl.contains(e.target)) closeFileMenu();
  }
});

document.addEventListener('dragend', () => {
  toniesContentEl?.classList.remove('external-drop');
  try { toniesContentEl?.querySelectorAll('.tonies-item.drop-target').forEach(n => n.classList.remove('drop-target')); } catch {}
});

document.addEventListener('drop', () => {
  toniesContentEl?.classList.remove('external-drop');
  try { toniesContentEl?.querySelectorAll('.tonies-item.drop-target').forEach(n => n.classList.remove('drop-target')); } catch {}
});

document.addEventListener('pointerdown', (e) => {
  const btn = e.target.closest?.('button');
  if (!btn) return;
  btn.classList.remove('tap-feedback');
  void btn.offsetWidth;
  btn.classList.add('tap-feedback');
  setTimeout(() => btn.classList.remove('tap-feedback'), 180);
});

if (toggleToniesOrderEl) {
  toggleToniesOrderEl.onclick = () => {
    toniesReverseView = !toniesReverseView;
    saveToniesViewPrefs();
    renderToniesOrderToggle();
    renderToniesContent();
  };
}

if (toggleToniesEditEl) {
  toggleToniesEditEl.onclick = () => {
    if (toniesEditMode) {
      cancelToniesEditMode();
      return;
    }
    if (isToniesActionBusy()) {
      showToast('Please wait for current Tonies sync to finish.', 'warn', 2200);
      return;
    }
    beginToniesEditMode();
  };
}

if (saveToniesNamesEl) saveToniesNamesEl.onclick = saveToniesNameEdits;

if (cancelToniesNamesEl) cancelToniesNamesEl.onclick = cancelToniesEditMode;

if (deleteAllToniesContentEl) deleteAllToniesContentEl.onclick = deleteAllToniesContent;

document.getElementById('clear-filter').onclick = () => { fileFilterEl.value = ''; renderFiles(); };

fileFilterEl.oninput = renderFiles;
