/** HTML builders for Tonie capacity, empty/loading states, tracks and upload placeholders. */
const toniesMinutesEl = document.getElementById('tonies-minutes');
const toniesProgressEl = document.getElementById('tonies-progress');
function renderToniesCapacity() {
  const freeRaw = toniesContentCache?.free_minutes;
  const freeNum = Number(freeRaw);
  const hasFree = freeRaw != null && Number.isFinite(freeNum);
  const total = Number(toniesContentCache?.total_minutes ?? 90);
  const used = hasFree
    ? Math.max(0, total - freeNum)
    : (toniesContentCache?.used_minutes == null ? null : Number(toniesContentCache.used_minutes));

  const minutesText = hasFree
    ? `${Math.round(freeNum)} min left • ${Math.round(used)} min used`
    : `Space syncing…`;
  toniesMinutesEl.textContent = minutesText;
  if (toniesProgressEl) {
    const pct = used == null || !total ? 0 : Math.max(0, Math.min(100, (used / total) * 100));
    toniesProgressEl.style.width = `${pct.toFixed(1)}%`;
  }
}

function toniesEmptyContentHtml() {
  const syncing = !!selectedToniesUrl && (
    toniesApplying
    || toniesUploadSyncing
    || toniesStatsSyncing.has(selectedToniesUrl)
    || (loadingToniesContent && (!loadingToniesContentUrl || loadingToniesContentUrl === selectedToniesUrl))
    || loadingTonies
  );
  return syncing
    ? '<div class="tonies-sync-skeleton" title="Syncing with my.tonies.com…"></div>'
    : '<div class="empty-tracks"><span class="empty-note" aria-hidden="true">♪</span><h3>Room for a new favourite.</h3><p>No tracks yet. Choose YouTube or Local files in Add audio to get started.</p><button class="ghost mobile-add" onclick="setWorkspaceView(\'add\', true)">Add audio</button></div>';
}

function toniesInlineRenameHtml(inline) {
  return `<input class="tonies-title-edit tonies-title-inline" aria-label="Track name" value="${esc(inline.draft)}" ${inline.busy || inline.submitted ? 'readonly' : ''} /><div class="row inline-rename-actions"><button class="inline-save" ${inline.busy || inline.submitted ? 'disabled' : ''}>${inline.busy ? 'Saving…' : 'Save'}</button><button class="inline-cancel ghost">Cancel</button></div>${inline.error ? `<p class="error" role="alert">${esc(inline.error)}</p>` : ''}`;
}

function toniesTrackRowHtml(c, sourceIndex, { inline, nowTs, busy, pendingDeleteIds }) {
  const rawTitle = uiText(c.title || `Chapter ${sourceIndex + 1}`);
  const title = toniesDisplayTitle(rawTitle);
  const displayNumber = sourceIndex + 1;
  const identityKey = getToniesIdentityKey(c);
  const draftTitle = String(toniesNameDrafts[identityKey] ?? title);
  const ts = recentUploadedChapterIds[c.chapter_id] || 0;
  const isNew = ts && (nowTs - ts) < TONIES_NEW_HIGHLIGHT_MS;
  const isDeleting = pendingDeleteIds.has(c.chapter_id);
  return `
    <div class="tonies-item ${isNew ? 'new-upload' : ''} ${isDeleting ? 'deleting' : ''} " draggable="${(toniesEditMode || busy) ? 'false' : 'true'}" data-source-index="${sourceIndex}" data-chapter-id="${esc(c.chapter_id || '')}">
      ${trackSelectionMode ? `<label class="track-select-label"><input type="checkbox" class="track-select" aria-label="Select ${esc(title)}" ${selectedTracks().has(c.chapter_id) ? 'checked' : ''} /></label>` : `<div class="drag-handle" style="opacity:${(toniesEditMode || busy) ? '0.35' : '1'};" aria-label="Drag to arrange">⋮⋮</div>`}
      <div>
        ${inline?.id === c.chapter_id ? toniesInlineRenameHtml(inline) : toniesEditMode
          ? `<input class="tonies-title-edit" data-source-index="${sourceIndex}" value="${esc(draftTitle)}" />`
          : `<div class="truncate"><strong>${esc(title)}</strong>${isNew ? ' <span class="tiny badge-new">NEW</span>' : ''}</div>`}
        <div class="tiny">${c.processing === true ? '<span class="loading-dot"></span>Processing audio…' : esc(fmtChapterDuration(c.duration || ''))}</div>
      </div>
      <div class="row track-actions">
        <div class="tiny mono" title="Playback position ${displayNumber}">#${displayNumber}</div>
<button type="button" class="ghost track-overflow" aria-label="Actions for ${esc(title)}" aria-haspopup="menu" ${(isDeleting || toniesEditMode) ? 'disabled' : ''}>⋯</button>
      </div>
    </div>
  `;
}

function toniesUploadPlaceholdersByIndex(chapters, uploadPlaceholders) {
  const placeholdersAt = new Map();
  for (const intent of uploadPlaceholders) {
    let at = intent.position ? resolveToniesPosition(chapters, intent.position) : -1;
    if (at < 0) at = Math.max(0, Math.min(chapters.length, Number(intent.targetIndex) || 0));
    placeholdersAt.set(at, [...(placeholdersAt.get(at) || []), intent]);
  }
  return placeholdersAt;
}

function toniesUploadPlaceholderHtml(intent) {
  return `<div class="tonies-sync-skeleton" title="${esc(intent.filename ? `Uploading ${intent.filename}…` : 'Uploading to this position…')}"></div>`;
}

function toniesTrackListHtml(chapters, inline) {
  const nowTs = Date.now();
  const busy = isToniesActionBusy();
  const pendingDeleteIds = new Set(pendingToniesActions(selectedToniesUrl)
    .filter(a => a.type === 'delete').map(a => a.params?.chapter_id));
  // Drop stale NEW markers aggressively so highlights always clear quickly.
  recentUploadedChapterIds = Object.fromEntries(
    Object.entries(recentUploadedChapterIds).filter(([, ts]) => (nowTs - Number(ts || 0)) < TONIES_NEW_HIGHLIGHT_MS)
  );

  const viewItems = chapters.map((c, i) => ({ c, sourceIndex: i }));
  if (toniesReverseView) viewItems.reverse();

  // One placeholder per upload still in flight to this Tonie, where it will land.
  const uploadPlaceholders = activeUploadPlaceholders(selectedToniesUrl);
  const placeholdersAt = toniesUploadPlaceholdersByIndex(chapters, uploadPlaceholders);
  const rowHtmlByIndex = new Map(viewItems.map(({ c, sourceIndex }) =>
    [sourceIndex, toniesTrackRowHtml(c, sourceIndex, { inline, nowTs, busy, pendingDeleteIds })]));

  // Build in native order (a placeholder sits before the chapter at its index), then
  // flip for the newest-first view so placeholders keep their place between rows.
  const combined = [];
  for (let k = 0; k <= chapters.length; k++) {
    for (const intent of placeholdersAt.get(k) || []) combined.push(toniesUploadPlaceholderHtml(intent));
    if (k < chapters.length) combined.push(rowHtmlByIndex.get(k) || '');
  }
  if (toniesReverseView) combined.reverse();
  const rowsHtml = combined.join('');

  const unplacedUpload = activeUploadJobIds.size === 0 || [...activeUploadJobIds].some(id => !attachedUploadJobIds.has(id));
  const syncSkeleton = (toniesUploadSyncing && !uploadPlaceholders.length && unplacedUpload) ? '<div class="tonies-sync-skeleton" title="Syncing with my.tonies.com…"></div>' : '';
  const endDropZone = toniesEditMode ? '' : '<div class="tonies-end-drop-zone" data-end-drop-zone="1">Drop here to upload to bottom</div>';
  return syncSkeleton + rowsHtml + endDropZone;
}
