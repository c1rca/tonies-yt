/** Drawing the chosen Tonie's track list. */
let lastToniesTapIndex = null;
let lastToniesTapAt = 0;

function renderToniesContent() {
  const focused = document.activeElement;
  const restoreInline = focused?.classList?.contains('tonies-title-inline');
  const caret = restoreInline ? [focused.selectionStart, focused.selectionEnd] : null;
  renderTrackSelection();
  const inline = getInlineRename();
  const chapters = toniesContentCache?.chapters || [];
  renderToniesCapacity();

  if (!chapters.length) {
    toniesContentEl.innerHTML = toniesEmptyContentHtml();
    return;
  }

  toniesContentEl.innerHTML = toniesTrackListHtml(chapters, inline);
  bindWorkspaceTrackRows();
  if (restoreInline) {
    const input = toniesContentEl.querySelector('.tonies-title-inline');
    input?.focus(); if (input?.setSelectionRange && caret) input.setSelectionRange(...caret);
  }

  bindToniesNameDrafts(chapters);
  bindToniesTrackInteractions();
}

function bindToniesNameDrafts(chapters) {
  toniesContentEl.querySelectorAll('.tonies-title-edit').forEach((inp) => {
    inp.addEventListener('input', () => {
      const idx = Number(inp.getAttribute('data-source-index'));
      if (!Number.isNaN(idx)) toniesNameDrafts[getToniesIdentityKey(chapters[idx])] = inp.value;
    });
  });
}

function bindToniesTrackInteractions() {
  // Shared by this render's rows only; a re-render starts a fresh drag session.
  const dragState = { dragIndex: null, touchDragIndex: null, touchTargetIndex: null };
  bindToniesContainerDrop();
  bindToniesEndDropZone();
  toniesContentEl.querySelectorAll('.tonies-item').forEach((el) => {
    bindToniesRowMenu(el);
    bindToniesRowDrag(el, dragState);
    bindToniesTouchDrag(el, dragState);
  });
}

function clearToniesDropTargets() {
  toniesContentEl.querySelectorAll('.tonies-item.drop-target').forEach(n => n.classList.remove('drop-target'));
}

function bindToniesContainerDrop() {
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
    clearToniesDropTargets();
  };
  toniesContentEl.ondrop = async (e) => {
    if (toniesEditMode) return;
    if (e.defaultPrevented) return;
    const localFile = e.dataTransfer?.getData('text/local-file');
    toniesContentEl.classList.remove('external-drop');
    clearToniesDropTargets();
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
}

function bindToniesEndDropZone() {
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
}

function bindToniesRowMenu(el) {
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
}

function bindToniesRowDrag(el, dragState) {
  // Desktop drag/drop
  el.addEventListener('dragstart', (e) => {
    if (toniesApplying || toniesEditMode) { e.preventDefault(); return; }
    dragState.dragIndex = Number(el.dataset.sourceIndex);
    el.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
  });
  el.addEventListener('dragend', () => {
    el.classList.remove('dragging');
    clearToniesDropTargets();
  });
  el.addEventListener('dragover', (e) => {
    if (toniesEditMode || !e.dataTransfer) return;
    const isExternalLocal = Array.from(e.dataTransfer.types || []).includes('text/local-file');
    e.preventDefault();
    e.dataTransfer.dropEffect = isExternalLocal ? 'copy' : 'move';
    clearToniesDropTargets();
    el.classList.add('drop-target');
  });
  el.addEventListener('drop', async (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (toniesEditMode) return;
    const toIndex = Number(el.dataset.sourceIndex);
    clearToniesDropTargets();

    const localFile = e.dataTransfer?.getData('text/local-file');
    if (localFile) {
      // Local-file drop semantics: dropping ON an item means insert above that row
      // in the CURRENT visual order.
      const targetIndex = toniesReverseView ? (toIndex + 1) : toIndex;
      await uploadExistingToIndex(localFile, targetIndex);
      toniesContentEl.classList.remove('external-drop');
      return;
    }

    if (dragState.dragIndex == null || Number.isNaN(toIndex) || dragState.dragIndex === toIndex) return;
    await reorderToniesChapter(dragState.dragIndex, toIndex);
  });
}

function bindToniesTouchDrag(el, dragState) {
  // Android/touch drag via handle
  const handle = el.querySelector('.drag-handle');
  if (!handle) return;

  handle.addEventListener('touchstart', (e) => {
    if (toniesApplying || toniesEditMode) return;
    dragState.touchDragIndex = Number(el.dataset.sourceIndex);
    dragState.touchTargetIndex = dragState.touchDragIndex;
    el.classList.add('dragging');
  }, { passive: true });

  handle.addEventListener('touchmove', (e) => {
    if (dragState.touchDragIndex == null) return;
    const t = e.touches && e.touches[0];
    if (!t) return;
    e.preventDefault();
    const under = document.elementFromPoint(t.clientX, t.clientY);
    const dropEl = under && under.closest ? under.closest('.tonies-item') : null;
    clearToniesDropTargets();
    if (!dropEl) return;
    dropEl.classList.add('drop-target');
    const idx = Number(dropEl.dataset.sourceIndex);
    if (!Number.isNaN(idx)) dragState.touchTargetIndex = idx;
  }, { passive: false });

  const finishTouchDrag = async () => {
    if (dragState.touchDragIndex == null) return;
    clearToniesDropTargets();
    el.classList.remove('dragging');
    const from = dragState.touchDragIndex;
    const to = dragState.touchTargetIndex;
    dragState.touchDragIndex = null;
    dragState.touchTargetIndex = null;
    if (from == null || to == null || Number.isNaN(to) || from === to) return;
    await reorderToniesChapter(from, to);
  };

  handle.addEventListener('touchend', () => { finishTouchDrag(); }, { passive: true });
  handle.addEventListener('touchcancel', () => { finishTouchDrag(); }, { passive: true });
}
