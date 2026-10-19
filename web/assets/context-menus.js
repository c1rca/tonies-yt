/** The right-click / long-press menus for tracks and local files. */
let menuReturnFocusId = null;
let toniesMenuTargetUrl = '';
let toniesMenuChapterId = null;
let fileMenuFilename = '';
let fileMenuReturnFocus = null;
const FILE_MENU_WIDTH = 208;
const FILE_MENU_HEIGHT = 300;

/** Arrow keys, Home and End move through a menu's items; Tab leaves (and closes) it. */
function bindMenuKeys(menuEl, close) {
  menuEl.addEventListener('keydown', e => {
    const items = [...menuEl.querySelectorAll('button')];
    if (!['ArrowUp','ArrowDown','Home','End','Tab'].includes(e.key)) return;
    if (e.key === 'Tab') { close(); return; }
    e.preventDefault(); const i = items.indexOf(document.activeElement);
    const next = e.key === 'Home' ? 0 : e.key === 'End' ? items.length-1 : (i + (e.key === 'ArrowUp' ? -1 : 1) + items.length) % items.length;
    items[next]?.focus();
  });
}

/** Run a file-menu item on the file the menu was opened for. */
function fileMenuAction(action) {
  return () => {
    const filename = fileMenuFilename;
    closeFileMenu();
    if (filename) return action(filename);
  };
}

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
  bindMenuKeys(toniesMenuEl, closeToniesMenu);
  bindMenuKeys(fileMenuEl, closeFileMenu);
  if (toniesMenuTopEl) toniesMenuTopEl.onclick = () => moveToniesFromMenu('top');
  if (toniesMenuBottomEl) toniesMenuBottomEl.onclick = () => moveToniesFromMenu('bottom');
  if (toniesMenuUpEl) toniesMenuUpEl.onclick = () => moveToniesFromMenu('up');
  if (toniesMenuDownEl) toniesMenuDownEl.onclick = () => moveToniesFromMenu('down');
  if (toniesMenuCancelEl) toniesMenuCancelEl.onclick = () => closeToniesMenu();
  document.getElementById('file-menu-edit').onclick = fileMenuAction(name => openAudioEditorForFile(name));
  document.getElementById('file-menu-upload-top').onclick = () => uploadFromFileMenu('top');
  document.getElementById('file-menu-upload-bottom').onclick = () => uploadFromFileMenu('bottom');
  document.getElementById('file-menu-download').onclick = fileMenuAction(name => downloadLocalFile(name));
  document.getElementById('file-menu-delete').onclick = fileMenuAction(name => deleteFile(name));
  document.getElementById('file-menu-cancel').onclick = () => closeFileMenu();
  document.addEventListener('click', (e) => {
    if (toniesMenuEl?.classList.contains('open')) {
      if (!toniesMenuEl.contains(e.target)) closeToniesMenu();
    }
    if (fileMenuEl?.classList.contains('open')) {
      // Clicking elsewhere keeps focus where the user clicked.
      if (!fileMenuEl.contains(e.target)) closeFileMenu(false);
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

function closeFileMenu(restoreFocus = true) {
  const returnTo = fileMenuReturnFocus;
  fileMenuReturnFocus = null;
  fileMenuFilename = '';
  if (!fileMenuEl) return;
  const wasOpen = fileMenuEl.getAttribute('aria-hidden') === 'false';
  fileMenuEl.classList.remove('open');
  fileMenuEl.setAttribute('aria-hidden', 'true');
  if (restoreFocus && wasOpen) returnTo?.focus();
}

function openFileMenu(filename, x, y, returnFocus = null) {
  if (!fileMenuEl || !filename) return;
  fileMenuFilename = String(filename || '');
  fileMenuReturnFocus = returnFocus;
  const left = Math.max(8, Math.min(x, window.innerWidth - FILE_MENU_WIDTH - 8));
  const top = Math.max(8, Math.min(y, window.innerHeight - FILE_MENU_HEIGHT - 8));
  fileMenuEl.style.left = `${left}px`;
  fileMenuEl.style.top = `${top}px`;
  fileMenuEl.classList.add('open');
  fileMenuEl.setAttribute('aria-hidden', 'false');
  document.getElementById('file-menu-edit').focus();
}

/** Open below the row's ⋯ button, or above it when there is no room below. */
function openFileMenuFrom(button, filename) {
  const r = button.getBoundingClientRect();
  const below = r.bottom + 4 + FILE_MENU_HEIGHT <= window.innerHeight - 8;
  openFileMenu(filename, r.right - FILE_MENU_WIDTH, below ? r.bottom + 4 : r.top - FILE_MENU_HEIGHT - 4, button);
}

async function uploadFromFileMenu(kind) {
  const filename = String(fileMenuFilename || '');
  closeFileMenu();
  if (!filename) return;
  const count = (toniesContentCache?.chapters || []).length;
  // "Top" and "bottom" follow the list as shown; the reversed display flips them.
  const atEnd = (kind === 'bottom') !== toniesReverseView;
  await uploadExistingToIndex(filename, atEnd ? count : 0);
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
