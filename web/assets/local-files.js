/** The Local files list: rendering, refreshing, downloading and deleting files. */
const fileFilterEl = document.getElementById('file-filter');
const stCount = document.getElementById('st-count');
const stSize = document.getElementById('st-size');
const stLength = document.getElementById('st-length');
let loadingFiles = false;

function initLocalFiles() {
  document.getElementById('refresh-files').onclick = () => refreshFiles().catch(error => {
    showToast(`Could not refresh local files: ${error?.message || error}`, 'err');
  });
  document.getElementById('clear-filter').onclick = () => { fileFilterEl.value = ''; renderFiles(); };
  fileFilterEl.oninput = renderFiles;
}

function downloadLocalFile(filename) {
  const safe = String(filename || '').trim();
  if (!safe) return;
  const a = document.createElement('a');
  a.href = `/api/files/${encodeURIComponent(safe)}`;
  a.download = safe;
  document.body.appendChild(a);
  a.click();
  a.remove();
}
const LOCAL_DELETE_UNDO_MS = 2400;

function deleteConfirmation(names) {
  if (names.length === 1) {
    return {title: 'Delete local file?', message: `${names[0]}\n\nThis cannot be undone.`, confirmText: 'Delete'};
  }
  const shown = names.slice(0, 6).join('\n') + (names.length > 6 ? `\n…and ${names.length - 6} more` : '');
  return {title: `Delete ${names.length} local files?`, message: `${shown}\n\nThis cannot be undone.`, confirmText: 'Delete files'};
}

/** Confirm, offer a short Undo, then delete. Only acknowledged deletes leave selection. */
async function deleteLocalFiles(names) {
  names = [...new Set(names)].filter(Boolean);
  const result = {deleted: [], failed: []};
  if (!names.length || !(await askConfirm(deleteConfirmation(names)))) return result;
  const many = names.length > 1;
  let canceled = false;
  let closeToast = null;
  closeToast = showToast(`Deleting ${many ? `${names.length} local files` : `local file “${localFileTitle(names[0])}”`}…`, 'warn', LOCAL_DELETE_UNDO_MS, {
    label: 'Undo',
    onClick: () => { canceled = true; closeToast?.(); }
  });
  await new Promise(r => setTimeout(r, LOCAL_DELETE_UNDO_MS));
  if (canceled) {
    showToast(many ? `${names.length} local files kept` : 'Local file kept', 'ok');
    return result;
  }
  closeToast?.();
  for (const name of names) {
    const response = await fetch(`/api/files/${encodeURIComponent(name)}`, { method: 'DELETE' }).catch(() => null);
    if (!response?.ok) { result.failed.push(name); continue; }
    result.deleted.push(name);
    if (name === localPlaybackFilename) stopLocalPlayback();
  }
  let refreshFailed = false;
  if (result.deleted.length) {
    // Do not revive acknowledged deletions when the follow-up read fails.
    filesCache = filesCache.filter(f => !result.deleted.includes(f.name));
    saveFilesCache();
    try { await refreshFiles(); } catch { refreshFailed = true; }
    renderFiles();
  }
  const failed = result.failed.length;
  if (failed) {
    showToast(many ? `Could not delete ${failed} of ${names.length} local files. Refresh and try again.`
      : 'Could not delete local file. Refresh and try again.', 'err');
  } else {
    const message = many ? `${names.length} local files deleted` : 'Local file deleted';
    showToast(message + (refreshFailed ? '; refresh the library to check the latest files.' : ''), refreshFailed ? 'warn' : 'ok');
  }
  return result;
}

async function deleteFile(filename) {
  const rows = [...filesEl.querySelectorAll('.file')];
  const index = rows.findIndex(row => row.getAttribute('data-file-name') === filename);
  const trigger = document.activeElement;
  const result = await deleteLocalFiles([filename]);
  const lostRowFocus = trigger?.isConnected === false && document.activeElement === document.body;
  if (!result.deleted.length || (document.activeElement !== trigger && !lostRowFocus)) return result;
  const remaining = [...filesEl.querySelectorAll('.file')];
  const next = remaining[Math.min(Math.max(0, index), remaining.length - 1)];
  (next?.querySelector('.local-more-btn, .local-play-btn') || fileFilterEl).focus();
  return result;
}

/** The files the filter currently shows, in list order. */
function filteredLocalFiles() {
  const q = (fileFilterEl.value || '').toLowerCase().trim();
  return !q ? filesCache : filesCache.filter(f => String(f.name).toLowerCase().includes(q));
}

function renderFiles() {
  renderLocalSelection();
  // The one native player lives outside filesEl, so routine list updates never reset it.
  if (localPlaybackFilename && !filesCache.some(f => f.name === localPlaybackFilename)) stopLocalPlayback();
  const q = (fileFilterEl.value || '').trim();
  const filtered = filteredLocalFiles();

  const totalSize = filesCache.reduce((acc, f) => acc + (f.size_bytes || 0), 0);
  const totalDuration = filesCache.reduce((acc, f) => acc + (Number(f.duration_sec) || 0), 0);
  stCount.textContent = String(filesCache.length);
  stSize.textContent = `${(totalSize / (1024 * 1024)).toFixed(1)} MB`;
  if (stLength) stLength.textContent = fmtDuration(totalDuration);

  if (loadingFiles && !filesCache.length) {
    filesEl.innerHTML = '<div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div>';
    return;
  }

  if (!filtered.length) {
    filesEl.innerHTML = `<div class="muted results-empty">${q ? 'No files match your filter.' : 'Your local library is empty. Import an audio file to get started.'}</div>`;
    return;
  }

  filesEl.innerHTML = filtered.map(localFileRowHtml).join('');
  filesEl.querySelectorAll('.file[draggable="true"]').forEach(bindLocalFileRow);
  updateLocalPlaybackButtons();
}

async function refreshFiles() {
  loadingFiles = filesCache.length === 0;
  renderFiles();
  try {
    filesCache = await api('/api/files');
    saveFilesCache();
  } finally {
    loadingFiles = false;
    renderFiles();
  }
}
