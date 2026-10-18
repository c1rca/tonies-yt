/** Drawing the chosen Tonie's track list. */
function renderToniesContent() {
  const focused = document.activeElement;
  const restoreInline = focused?.classList?.contains('tonies-title-inline');
  const caret = restoreInline ? [focused.selectionStart, focused.selectionEnd] : null;
  renderTrackSelection();
  const inline = getInlineRename();
  const chapters = toniesContentCache?.chapters || [];
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

  if (!chapters.length) {
    const syncing = !!selectedToniesUrl && (
      toniesApplying
      || toniesUploadSyncing
      || toniesStatsSyncing.has(selectedToniesUrl)
      || (loadingToniesContent && (!loadingToniesContentUrl || loadingToniesContentUrl === selectedToniesUrl))
      || loadingTonies
    );

    const showInlineSync = syncing;
    toniesContentEl.innerHTML = showInlineSync
      ? '<div class="tonies-sync-skeleton" title="Syncing with my.tonies.com…"></div>'
      : '<div class="empty-tracks"><span class="empty-note" aria-hidden="true">♪</span><h3>Room for a new favourite.</h3><p>No tracks yet. Choose YouTube or Local files in Add audio to get started.</p><button class="ghost mobile-add" onclick="setWorkspaceView(\'add\', true)">Add audio</button></div>';
    return;
  }

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
  const placeholdersAt = new Map();
  for (const intent of uploadPlaceholders) {
    let at = intent.position ? resolveToniesPosition(chapters, intent.position) : -1;
    if (at < 0) at = Math.max(0, Math.min(chapters.length, Number(intent.targetIndex) || 0));
    placeholdersAt.set(at, [...(placeholdersAt.get(at) || []), intent]);
  }
  const inlineDropSkeleton = (intent) => `<div class="tonies-sync-skeleton" title="${esc(intent.filename ? `Uploading ${intent.filename}…` : 'Uploading to this position…')}"></div>`;

  const rowHtmlByIndex = new Map(viewItems.map(({ c, sourceIndex }) => {
    const rawTitle = uiText(c.title || `Chapter ${sourceIndex + 1}`);
    const title = toniesDisplayTitle(rawTitle);
    const displayNumber = sourceIndex + 1;
    const identityKey = getToniesIdentityKey(c);
    const draftTitle = String(toniesNameDrafts[identityKey] ?? title);
    const ts = recentUploadedChapterIds[c.chapter_id] || 0;
    const isNew = ts && (nowTs - ts) < TONIES_NEW_HIGHLIGHT_MS;
    const isDeleting = pendingDeleteIds.has(c.chapter_id);
    const html = `
    <div class="tonies-item ${isNew ? 'new-upload' : ''} ${isDeleting ? 'deleting' : ''} " draggable="${(toniesEditMode || busy) ? 'false' : 'true'}" data-source-index="${sourceIndex}" data-chapter-id="${esc(c.chapter_id || '')}">
      ${trackSelectionMode ? `<label class="track-select-label"><input type="checkbox" class="track-select" aria-label="Select ${esc(title)}" ${selectedTracks().has(c.chapter_id) ? 'checked' : ''} /></label>` : `<div class="drag-handle" style="opacity:${(toniesEditMode || busy) ? '0.35' : '1'};" aria-label="Drag to arrange">⋮⋮</div>`}
      <div>
        ${inline?.id === c.chapter_id ? `<input class="tonies-title-edit tonies-title-inline" aria-label="Track name" value="${esc(inline.draft)}" ${inline.busy || inline.submitted ? 'readonly' : ''} /><div class="row inline-rename-actions"><button class="inline-save" ${inline.busy || inline.submitted ? 'disabled' : ''}>${inline.busy ? 'Saving…' : 'Save'}</button><button class="inline-cancel ghost">Cancel</button></div>${inline.error ? `<p class="error" role="alert">${esc(inline.error)}</p>` : ''}` : toniesEditMode
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
    return [sourceIndex, html];
  }));

  // Build in native order (a placeholder sits before the chapter at its index), then
  // flip for the newest-first view so placeholders keep their place between rows.
  const combined = [];
  for (let k = 0; k <= chapters.length; k++) {
    for (const intent of placeholdersAt.get(k) || []) combined.push(inlineDropSkeleton(intent));
    if (k < chapters.length) combined.push(rowHtmlByIndex.get(k) || '');
  }
  if (toniesReverseView) combined.reverse();
  const rowsHtml = combined.join('');

  const unplacedUpload = activeUploadJobIds.size === 0 || [...activeUploadJobIds].some(id => !attachedUploadJobIds.has(id));
  const syncSkeleton = (toniesUploadSyncing && !uploadPlaceholders.length && unplacedUpload) ? '<div class="tonies-sync-skeleton" title="Syncing with my.tonies.com…"></div>' : '';
  const endDropZone = toniesEditMode ? '' : '<div class="tonies-end-drop-zone" data-end-drop-zone="1">Drop here to upload to bottom</div>';
  toniesContentEl.innerHTML = syncSkeleton + rowsHtml + endDropZone;
  bindWorkspaceTrackRows();
  if (restoreInline) {
    const input = toniesContentEl.querySelector('.tonies-title-inline');
    input?.focus(); if (input?.setSelectionRange && caret) input.setSelectionRange(...caret);
  }

  toniesContentEl.querySelectorAll('.tonies-title-edit').forEach((inp) => {
    inp.addEventListener('input', () => {
      const idx = Number(inp.getAttribute('data-source-index'));
      if (!Number.isNaN(idx)) toniesNameDrafts[getToniesIdentityKey(chapters[idx])] = inp.value;
    });
  });

  let dragIndex = null;
  let touchDragIndex = null;
  let touchTargetIndex = null;

  const clearDropTargets = () => {
    toniesContentEl.querySelectorAll('.tonies-item.drop-target').forEach(n => n.classList.remove('drop-target'));
  };

  toniesContentEl.ondragover = (e) => {
    if (toniesEditMode) return;
    if (!e.dataTransfer) return;
    const isExternalLocal = Array.from(e.dataTransfer.types || []).includes('text/local-file');
    if (!isExternalLocal) return;
    e.preventDefault();
    toniesContentEl.classList.add('external-drop');
    e.dataTransfer.dropEffect = 'copy';
  };
  toniesContentEl.ondragleave = (e) => {
    // Ignore leave events while still inside the container.
    const toEl = e?.relatedTarget;
    if (toEl && toniesContentEl.contains(toEl)) return;
    toniesContentEl.classList.remove('external-drop');
    clearDropTargets();
  };
  toniesContentEl.ondrop = async (e) => {
    if (toniesEditMode) return;
    if (e.defaultPrevented) return;
    const localFile = e.dataTransfer?.getData('text/local-file');
    toniesContentEl.classList.remove('external-drop');
    clearDropTargets();
    if (!localFile) return;
    e.preventDefault();

    // If container-level drop fires (e.g., drop lands on gaps/children), resolve
    // nearest row under cursor and insert ABOVE that row in the CURRENT visual order.
    const x = (e.clientX ?? 0);
    const y = (e.clientY ?? 0);
    const under = document.elementFromPoint(x, y);
    const dropRow = under && under.closest ? under.closest('.tonies-item') : null;
    if (dropRow) {
      const rowIdx = Number(dropRow.getAttribute('data-source-index'));
      if (!Number.isNaN(rowIdx)) {
        const targetIndex = toniesReverseView ? (rowIdx + 1) : rowIdx;
        await uploadExistingToIndex(localFile, targetIndex);
        return;
      }
    }

    // Fallback: append to visual bottom.
    const targetIndex = toniesReverseView ? 0 : (toniesContentCache?.chapters || []).length;
    await uploadExistingToIndex(localFile, targetIndex);
  };

  const endDropZoneEl = toniesContentEl.querySelector('[data-end-drop-zone="1"]');
  if (endDropZoneEl) {
    endDropZoneEl.addEventListener('dragover', (e) => {
      if (toniesEditMode) return;
      const localFile = e.dataTransfer?.getData('text/local-file');
      const hasType = Array.from(e.dataTransfer?.types || []).includes('text/local-file');
      if (!localFile && !hasType) return;
      e.preventDefault();
      endDropZoneEl.classList.add('active');
      if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
    });
    endDropZoneEl.addEventListener('dragleave', () => {
      endDropZoneEl.classList.remove('active');
    });
    endDropZoneEl.addEventListener('drop', async (e) => {
      if (toniesEditMode) return;
      const localFile = e.dataTransfer?.getData('text/local-file');
      endDropZoneEl.classList.remove('active');
      if (!localFile) return;
      e.preventDefault();
      const targetIndex = toniesReverseView ? 0 : (toniesContentCache?.chapters || []).length;
      await uploadExistingToIndex(localFile, targetIndex);
    });
  }

  toniesContentEl.querySelectorAll('.tonies-item').forEach((el) => {
    // Hold / right-click menu for quick actions.
    let holdTimer = null;
    let holdStart = null;

    const clearHold = () => {
      if (holdTimer) clearTimeout(holdTimer);
      holdTimer = null;
      holdStart = null;
    };

    el.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      if (toniesApplying || toniesEditMode) return;
      const idx = Number(el.dataset.sourceIndex);
      if (Number.isNaN(idx)) return;
      openToniesMenu(idx, e.clientX, e.clientY);
    });

    let swipeStart = null;

    el.addEventListener('touchstart', (e) => {
      if (toniesApplying || toniesEditMode) return;
      const target = e.target;
      if (target.closest?.('button') || target.closest?.('.drag-handle')) return;
      const idx = Number(el.dataset.sourceIndex);
      if (Number.isNaN(idx)) return;
      const t = e.touches && e.touches[0];
      holdStart = t ? { x: t.clientX, y: t.clientY } : null;
      swipeStart = holdStart ? { x: holdStart.x, y: holdStart.y } : null;
      holdTimer = setTimeout(() => {
        const pos = holdStart || { x: window.innerWidth / 2, y: window.innerHeight / 2 };
        openToniesMenu(idx, pos.x + 8, pos.y + 8);
        clearHold();
      }, 520);
    }, { passive: true });

    el.addEventListener('touchmove', (e) => {
      if (!holdStart) return;
      const t = e.touches && e.touches[0];
      if (!t) return;
      if (Math.abs(t.clientX - holdStart.x) > 8 || Math.abs(t.clientY - holdStart.y) > 8) clearHold();
    }, { passive: true });

    el.addEventListener('touchend', (e) => {
      const idx = Number(el.dataset.sourceIndex);
      const target = e.target;
      const isRowTap = !(target.closest?.('button') || target.closest?.('.drag-handle'));
      const hadHoldTimer = !!holdTimer;

      let swipedLeft = false;
      let swipedRight = false;
      if (swipeStart && e.changedTouches && e.changedTouches[0]) {
        const dx = e.changedTouches[0].clientX - swipeStart.x;
        const dy = e.changedTouches[0].clientY - swipeStart.y;
        if (Math.abs(dx) > 52 && Math.abs(dx) > Math.abs(dy) * 1.2) {
          swipedLeft = dx < 0;
          swipedRight = dx > 0;
        }
      }

      clearHold();
      swipeStart = null;

      if (!isRowTap || Number.isNaN(idx) || toniesApplying || toniesEditMode) return;

      if (swipedLeft) {
        const rect = el.getBoundingClientRect();
        openToniesMenu(idx, rect.right - 8, rect.top + rect.height / 2);
        return;
      }
      if (swipedRight) {
        closeToniesMenu();
        return;
      }

      const now = Date.now();
      const isDoubleTap = (lastToniesTapIndex === idx) && (now - lastToniesTapAt <= 320);
      lastToniesTapIndex = idx;
      lastToniesTapAt = now;

      // Open menu on double-tap (only if this wasn't already consumed by long-press).
      if (isDoubleTap && hadHoldTimer) {
        const x = (e.changedTouches && e.changedTouches[0]?.clientX) || window.innerWidth / 2;
        const y = (e.changedTouches && e.changedTouches[0]?.clientY) || window.innerHeight / 2;
        openToniesMenu(idx, x + 8, y + 8);
      }
    }, { passive: true });
    el.addEventListener('touchcancel', clearHold, { passive: true });

    // Desktop drag/drop
    el.addEventListener('dragstart', (e) => {
      if (toniesApplying || toniesEditMode) { e.preventDefault(); return; }
      dragIndex = Number(el.dataset.sourceIndex);
      el.classList.add('dragging');
      e.dataTransfer.effectAllowed = 'move';
    });
    el.addEventListener('dragend', () => {
      el.classList.remove('dragging');
      clearDropTargets();
    });
    el.addEventListener('dragover', (e) => {
      if (toniesEditMode || !e.dataTransfer) return;
      const isExternalLocal = Array.from(e.dataTransfer.types || []).includes('text/local-file');
      e.preventDefault();
      e.dataTransfer.dropEffect = isExternalLocal ? 'copy' : 'move';
      clearDropTargets();
      el.classList.add('drop-target');
    });
    el.addEventListener('drop', async (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (toniesEditMode) return;
      const toIndex = Number(el.dataset.sourceIndex);
      clearDropTargets();

      const localFile = e.dataTransfer?.getData('text/local-file');
      if (localFile) {
        // Local-file drop semantics: dropping ON an item means insert above that row
        // in the CURRENT visual order.
        const targetIndex = toniesReverseView ? (toIndex + 1) : toIndex;
        await uploadExistingToIndex(localFile, targetIndex);
        toniesContentEl.classList.remove('external-drop');
        return;
      }

      if (dragIndex == null || Number.isNaN(toIndex) || dragIndex === toIndex) return;
      await reorderToniesChapter(dragIndex, toIndex);
    });

    // Android/touch drag via handle
    const handle = el.querySelector('.drag-handle');
    if (!handle) return;

    handle.addEventListener('touchstart', (e) => {
      if (toniesApplying || toniesEditMode) return;
      touchDragIndex = Number(el.dataset.sourceIndex);
      touchTargetIndex = touchDragIndex;
      el.classList.add('dragging');
    }, { passive: true });

    handle.addEventListener('touchmove', (e) => {
      if (touchDragIndex == null) return;
      const t = e.touches && e.touches[0];
      if (!t) return;
      e.preventDefault();
      const under = document.elementFromPoint(t.clientX, t.clientY);
      const dropEl = under && under.closest ? under.closest('.tonies-item') : null;
      clearDropTargets();
      if (!dropEl) return;
      dropEl.classList.add('drop-target');
      const idx = Number(dropEl.dataset.sourceIndex);
      if (!Number.isNaN(idx)) touchTargetIndex = idx;
    }, { passive: false });

    const finishTouchDrag = async () => {
      if (touchDragIndex == null) return;
      clearDropTargets();
      el.classList.remove('dragging');
      const from = touchDragIndex;
      const to = touchTargetIndex;
      touchDragIndex = null;
      touchTargetIndex = null;
      if (from == null || to == null || Number.isNaN(to) || from === to) return;
      await reorderToniesChapter(from, to);
    };

    handle.addEventListener('touchend', () => { finishTouchDrag(); }, { passive: true });
    handle.addEventListener('touchcancel', () => { finishTouchDrag(); }, { passive: true });
  });
}
