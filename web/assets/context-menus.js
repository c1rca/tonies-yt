/** The right-click / long-press menus for tracks and local files. */
let menuReturnFocusId = null;

let toniesMenuTargetUrl = '';

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

function downloadLocalFile(filename) {
  const safe = String(filename || '').trim();
  if (!safe) return;
  closeFileMenu();
  const a = document.createElement('a');
  a.href = `/api/files/${encodeURIComponent(safe)}`;
  a.download = safe;
  document.body.appendChild(a);
  a.click();
  a.remove();
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
