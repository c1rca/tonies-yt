/** The right-click / long-press menus for tracks and local files. */
let menuReturnFocusId = null;
let toniesMenuTargetUrl = '';
let toniesMenuChapterId = null;
let fileMenuFilename = '';

function initContextMenus() {
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
}

function closeToniesMenu() {
  const returnId = menuReturnFocusId; menuReturnFocusId = null;
  toniesMenuChapterId = null;
  if (returnId) focusTrackAction(returnId);
  if (!toniesMenuEl) return;
  toniesMenuEl.classList.remove('open');
  toniesMenuEl.setAttribute('aria-hidden', 'true');
}

function openToniesMenu(index, x, y) {
  if (!toniesMenuEl) return;
  toniesMenuChapterId = toniesContentCache?.chapters?.[index]?.chapter_id || null;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  toniesMenuTargetUrl = selectedToniesUrl;
  if (!toniesMenuChapterId) return;
  const width = 208;
  const height = 370;
  const left = Math.max(8, Math.min(x, vw - width - 8));
  const top = Math.max(8, Math.min(y, vh - height - 8));
  toniesMenuEl.style.left = `${left}px`;
  toniesMenuEl.style.top = `${top}px`;
  toniesMenuEl.classList.add('open');
  toniesMenuEl.setAttribute('aria-hidden', 'false');
  document.getElementById('tonies-menu-rename').focus();
}

function closeFileMenu() {
  fileMenuFilename = '';
  if (!fileMenuEl) return;
  fileMenuEl.classList.remove('open');
  fileMenuEl.setAttribute('aria-hidden', 'true');
}

function openFileMenu(filename, x, y) {
  if (!fileMenuEl || !filename) return;
  fileMenuFilename = String(filename || '');
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const width = 190;
  const height = 156;
  const left = Math.max(8, Math.min(x, vw - width - 8));
  const top = Math.max(8, Math.min(y, vh - height - 8));
  fileMenuEl.style.left = `${left}px`;
  fileMenuEl.style.top = `${top}px`;
  fileMenuEl.classList.add('open');
  fileMenuEl.setAttribute('aria-hidden', 'false');
}

async function uploadFromFileMenu(kind = 'default') {
  const filename = String(fileMenuFilename || '');
  closeFileMenu();
  if (!filename) return;

  if (kind === 'top') {
    const targetIndex = toniesReverseView ? (toniesContentCache?.chapters || []).length : 0;
    await uploadExistingToIndex(filename, targetIndex);
    return;
  }
  if (kind === 'bottom') {
    const targetIndex = toniesReverseView ? 0 : (toniesContentCache?.chapters || []).length;
    await uploadExistingToIndex(filename, targetIndex);
    return;
  }
  const targetIndex = toniesReverseView ? (toniesContentCache?.chapters || []).length : 0;
  await uploadExistingToIndex(filename, targetIndex);
}

async function moveToniesFromMenu(kind) {
  if (!toniesMenuChapterId || toniesMenuTargetUrl !== selectedToniesUrl) { closeToniesMenu(); return; }
  const chapters = toniesContentCache?.chapters || [];
  const count = chapters.length;
  const from = chapters.findIndex(c => c.chapter_id === toniesMenuChapterId);
  if (from < 0) { closeToniesMenu(); return; }
  let to = from;

  // Actions should follow the CURRENT VIEW order.
  // Source indices are in Tonies native order; reverse-mode flips visual direction.
  if (!toniesReverseView) {
    if (kind === 'top') to = 0;
    if (kind === 'bottom') to = Math.max(0, count - 1);
    if (kind === 'up') to = Math.max(0, from - 1);
    if (kind === 'down') to = Math.min(Math.max(0, count - 1), from + 1);
  } else {
    if (kind === 'top') to = Math.max(0, count - 1);
    if (kind === 'bottom') to = 0;
    if (kind === 'up') to = Math.min(Math.max(0, count - 1), from + 1);
    if (kind === 'down') to = Math.max(0, from - 1);
  }

  closeToniesMenu();
  if (to === from) return;
  await reorderToniesChapter(from, to);
}
