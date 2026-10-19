/** The Local files list: rendering, refreshing, downloading and deleting files. */
let loadingFiles = false;
const FILES_CACHE_KEY = 'tonies-auto.files-cache.v1';

function initLocalFiles() {
  document.getElementById('refresh-files').onclick = refreshFiles;
  document.getElementById('clear-filter').onclick = () => { fileFilterEl.value = ''; renderFiles(); };
  fileFilterEl.oninput = renderFiles;
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
const LOCAL_DELETE_UNDO_MS = 2400;

function deleteConfirmation(names) {
  if (names.length === 1) {
    return {title: 'Delete local file?', message: `${names[0]}\n\nThis cannot be undone.`, confirmText: 'Delete'};
  }
  const shown = names.slice(0, 6).join('\n') + (names.length > 6 ? `\n…and ${names.length - 6} more` : '');
  return {title: `Delete ${names.length} local files?`, message: `${shown}\n\nThis cannot be undone.`, confirmText: 'Delete files'};
}

/** Confirm, offer a short Undo, then delete. Returns true once the deletes were sent. */
async function deleteLocalFiles(names) {
  names = [...new Set(names)].filter(Boolean);
  if (!names.length || !(await askConfirm(deleteConfirmation(names)))) return false;
  const many = names.length > 1;
  let canceled = false;
  let closeToast = null;
  closeToast = showToast(`Deleting ${many ? `${names.length} local files` : `local file “${names[0]}”`}…`, 'warn', LOCAL_DELETE_UNDO_MS, {
    label: 'Undo',
    onClick: () => { canceled = true; closeToast?.(); }
  });
  await new Promise(r => setTimeout(r, LOCAL_DELETE_UNDO_MS));
  if (canceled) return false;
  closeToast?.();

  let failed = 0;
  for (const name of names) {
    const response = await fetch(`/api/files/${encodeURIComponent(name)}`, { method: 'DELETE' }).catch(() => null);
    if (!response?.ok) { failed += 1; continue; }
    if (name === localPlaybackFilename) stopLocalPlayback();
  }
  // Nothing changed when every delete was refused, so the list stays as it is.
  if (failed < names.length) await refreshFiles();
  if (failed) {
    showToast(many ? `Could not delete ${failed} of ${names.length} local files. Refresh and try again.`
      : 'Could not delete local file. Refresh and try again.', 'err');
  } else {
    showToast(many ? `${names.length} local files deleted` : 'Local file deleted', 'ok');
  }
  return true;
}

function deleteFile(filename) {
  return deleteLocalFiles([filename]);
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
  filesCache = await api('/api/files');
  saveFilesCache();
  loadingFiles = false;
  renderFiles();
}
