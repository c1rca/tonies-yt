/** The chosen Tonie's track list: loading it, status, edit mode and processing polls. */
let toniesApplying = false;
let toniesReverseView = true;
let toniesEditMode = false;
let toniesNameDrafts = {};
let toniesContentRequestSeq = 0;
const toniesContentVersionByUrl = {};
const toniesContentReadSeqByUrl = {};  // latest-started library read per Tonie
let toniesProcessingTimer = null;
let toniesProcessingPolls = 0;
let loadingToniesContent = false;
let loadingToniesContentUrl = '';
const TONIES_CONTENT_CACHE_KEY = 'tonies-auto.tonies-content-by-url.v1';
const TONIES_REVERSE_VIEW_KEY = 'tonies-auto.tonies-reverse-view.v1';

function initTonieLibrary() {
  for (const event of ['dragend', 'drop']) {
    document.addEventListener(event, () => {
      toniesContentEl?.classList.remove('external-drop');
      try { toniesContentEl?.querySelectorAll('.tonies-item.drop-target').forEach(n => n.classList.remove('drop-target')); } catch {}
    });
  }
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
}

function blockIfActionInProgress() {
  if (toniesApplying) {
    showToast('Please wait for the Tonies library to load.', 'warn', 3000);
    return true;
  }
  return false;
}

// Only a blocking library load stops edits; uploads and queued edits never do.
function isToniesActionBusy() {
  return !!toniesApplying;
}
function isToniesShowingNewestFirst() {
  return !!toniesReverseView;
}

function renderToniesOrderToggle() {
  if (!toggleToniesOrderEl) return;
  const showingNewestFirst = isToniesShowingNewestFirst();
  toggleToniesOrderEl.textContent = showingNewestFirst ? 'Show playback order' : 'Reverse display';
  toggleToniesOrderEl.setAttribute('aria-pressed', String(showingNewestFirst));
  toggleToniesOrderEl.title = showingNewestFirst ? 'Currently reversed. Show playback order without changing the Tonie.' : 'Reverse the display only. Playback order will not change.';
}

function stripToniesAppToken(s) {
  const raw = String(s || '').trim();
  return raw.replace(/\s\[(?:oc:)?[a-f0-9]{4,12}\]$/i, '').trim() || raw;
}

function toniesDisplayTitle(s) {
  return stripToniesAppToken(s);
}

function setToniesStatus(text, loading = false) {
  toniesApplying = loading;
  const safeText = esc(text || '');
  const isSynced = !loading && String(text || '').trim() === 'Synced with my.tonies.com';
  toniesStatusEl.innerHTML = loading
    ? `<span class="spinner"></span>${safeText}`
    : (isSynced ? `<span class="synced-mark">✓</span>${safeText}` : safeText);
  // Keep header controls (like Refresh) visually in sync with status transitions.
  if (typeof renderTonies === 'function') {
    try { renderTonies(); } catch {}
  }
}

// A spinner status that, unlike setToniesStatus(text, true), never blocks edits.
function setToniesProgress(text) {
  toniesApplying = false;
  toniesStatusEl.innerHTML = `<span class="spinner"></span>${esc(text || '')}`;
  if (typeof renderTonies === 'function') {
    try { renderTonies(); } catch {}
  }
}

function pulseToniesRow(sourceIndex) {
  if (sourceIndex == null) return;
  const row = toniesContentEl?.querySelector(`.tonies-item[data-source-index="${sourceIndex}"]`);
  if (!row) return;
  row.classList.add('just-moved');
  setTimeout(() => row.classList.remove('just-moved'), 500);
}

function renderToniesEditUI() {
  const busy = isToniesActionBusy();
  if (toggleToniesEditEl) {
    toggleToniesEditEl.textContent = toniesEditMode ? 'Done editing' : 'Edit names';
    // Keep exit control available even while busy to avoid trapping edit mode.
    toggleToniesEditEl.disabled = false;
  }
  if (saveToniesNamesEl) saveToniesNamesEl.disabled = busy;
  if (cancelToniesNamesEl) cancelToniesNamesEl.disabled = false;
  if (deleteAllToniesContentEl) deleteAllToniesContentEl.disabled = busy;
  if (toniesEditActionsEl) toniesEditActionsEl.style.display = toniesEditMode ? 'flex' : 'none';
}

function beginToniesEditMode() {
  const chapters = toniesContentCache?.chapters || [];
  if (!chapters.length) {
    showToast('No content to edit. Upload tracks first.', 'err', 2600);
    toniesEditMode = false;
    renderToniesEditUI();
    return;
  }
  toniesNameDrafts = {};
  chapters.forEach((c, i) => {
    toniesNameDrafts[getToniesIdentityKey(c)] = toniesDisplayTitle(uiText(c?.title || `Chapter ${i + 1}`));
  });
  toniesEditMode = true;
  closeToniesMenu();
  renderToniesEditUI();
  renderToniesContent();
}

function cancelToniesEditMode() {
  toniesEditMode = false;
  toniesNameDrafts = {};
  renderToniesEditUI();
  renderToniesContent();
}

async function deleteAllToniesContent() {
  if (!selectedToniesUrl || isToniesActionBusy()) return;
  const url = selectedToniesUrl;
  const ok = await askConfirm({
    title: 'Delete all content?',
    message: `Permanently remove all ${(toniesContentCache?.chapters || []).length} tracks from ${workspaceTargetName(url)}?\n\nThis cannot be undone.`,
    confirmText: 'Delete all content'
  });
  if (!ok || url !== selectedToniesUrl) return;
  if (await queueToniesEdits(url, [{ type: 'clear' }], 'Delete all') && url === selectedToniesUrl) {
    toniesEditMode = false;
    toniesNameDrafts = {};
  }
  renderToniesEditUI();
  renderToniesContent();
}

async function saveToniesNameEdits() {
  if (!toniesEditMode || !selectedToniesUrl || isToniesActionBusy()) return;
  const url = selectedToniesUrl;
  const drafts = toniesNameDrafts;
  const submittedDrafts = { ...drafts };
  const changes = (toniesContentCache?.chapters || []).flatMap((c, i) => {
    const key = getToniesIdentityKey(c);
    const current = toniesDisplayTitle(uiText(c.title || `Chapter ${i + 1}`));
    const title = String(drafts[key] ?? current).trim();
    return title && title !== current && c.chapter_id ? [{ type: 'rename', chapter_id: c.chapter_id, title }] : [];
  });
  if (!changes.length) { cancelToniesEditMode(); return; }
  // All renames go in one request and one batch. Drafts survive a rejected request,
  // and edits typed while the request was in flight stay open for another save.
  if (await queueToniesEdits(url, changes, 'Rename') && url === selectedToniesUrl && drafts === toniesNameDrafts) {
    const hasNewerEdits = Object.keys(drafts).some(key => drafts[key] !== submittedDrafts[key]);
    if (!hasNewerEdits) {
      toniesEditMode = false;
      toniesNameDrafts = {};
    }
  }
  renderToniesEditUI();
  renderToniesContent();
}

function toniesIsProcessing(data) {
  return data?.processing === true || (data?.chapters || []).some(c => c.processing === true);
}

function toniesProcessingStatus(data) {
  return toniesIsProcessing(data)
    ? (toniesProcessingPolls >= 30 ? 'Audio is still processing. Tap Refresh to check again.' : 'Saved to Tonies. Processing audio…')
    : 'Synced with my.tonies.com';
}

function invalidateToniesContentRequests(url) {
  toniesContentVersionByUrl[url] = (toniesContentVersionByUrl[url] || 0) + 1;
  if (url === selectedToniesUrl) {
    toniesContentRequestSeq += 1;
    loadingToniesContent = false;
    loadingToniesContentUrl = '';
    if (toniesProcessingTimer) clearTimeout(toniesProcessingTimer);
    toniesProcessingTimer = null;
  }
}

function scheduleToniesProcessingPoll(url, data, reset = false) {
  if (url !== selectedToniesUrl) return;
  if (toniesProcessingTimer) clearTimeout(toniesProcessingTimer);
  toniesProcessingTimer = null;
  if (reset) toniesProcessingPolls = 0;
  if (!toniesIsProcessing(data) || toniesProcessingPolls >= 30) return;
  toniesProcessingTimer = setTimeout(async () => {
    toniesProcessingTimer = null;
    if (url !== selectedToniesUrl || !toniesIsProcessing(toniesContentCache)) return;
    toniesProcessingPolls += 1;
    await refreshToniesContent({ targetUrl: url, force: true, processingPoll: true });
  }, 2000);
}

function applyToniesContent(url, data, options = {}) {
  if (data?.error) throw new Error(data.error);
  if (!Array.isArray(data?.chapters)) throw new Error('Invalid Tonies content response. Tap Refresh to retry.');
  invalidateToniesContentRequests(url);
  setCachedToniesContent(url, data);  // the server's content; queued edits are layered on top
  if (url === selectedToniesUrl) {
    toniesContentCache = overlayToniesContent(url, data);
    scheduleToniesProcessingPoll(url, data, !options.processingPoll);
    renderToniesContent();
  }
  renderTonies();
}

async function refreshToniesContent(options = {}) {
  const { clearList = false, preferCache = false, force = false, targetUrl = '', processingPoll = false } = options;
  const url = targetUrl || selectedToniesUrl;
  if (!url) return;
  const selected = url === selectedToniesUrl;
  const cached = getCachedToniesContent(url);
  if (selected && preferCache && cached) {
    toniesContentCache = overlayToniesContent(url, cached);
    renderToniesContent();
    if (!force) {
      scheduleToniesProcessingPoll(url, cached, true);
      setToniesStatus(toniesProcessingStatus(cached), false);
      return;
    }
  } else if (selected && clearList) {
    toniesContentCache = { chapters: [], free_minutes: null, total_minutes: 90 };
  }
  const reqId = selected ? ++toniesContentRequestSeq : null;
  const readSeq = toniesContentReadSeqByUrl[url] = (toniesContentReadSeqByUrl[url] || 0) + 1;
  const version = toniesContentVersionByUrl[url] || 0;
  if (selected) {
    loadingToniesContent = true;
    loadingToniesContentUrl = url;
    if (!processingPoll) setToniesStatus('Loading Tonies library…', false);
    renderToniesContent();
  }
  try {
    const data = await api('/api/tonies-content', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target_url: url })
    }, 45000);
    // A newer read of this Tonie (or newer content from an event) supersedes this one.
    if (readSeq !== toniesContentReadSeqByUrl[url]) return;
    if (version !== (toniesContentVersionByUrl[url] || 0)) return;
    if (selected && (reqId !== toniesContentRequestSeq || url !== selectedToniesUrl)) return;
    applyToniesContent(url, data, { processingPoll });
    if (url === selectedToniesUrl) showToniesSettledStatus(url, data);
  } catch (e) {
    if (version !== (toniesContentVersionByUrl[url] || 0)) return;
    if (selected && reqId === toniesContentRequestSeq && url === selectedToniesUrl) {
      setToniesStatus(`Error: ${e?.message || e}`, false);
    }
  } finally {
    if (selected && reqId === toniesContentRequestSeq) {
      loadingToniesContent = false;
      loadingToniesContentUrl = '';
    }
    renderTonies();
    renderToniesContent();
  }
}

function getToniesIdentityKey(chapter) {
  // Cloud chapter IDs, not DOM IDs, title tokens, names or row positions.
  return chapter?.chapter_id ? `id:${chapter.chapter_id}` : '';
}
