/** The Local files list: rendering, refreshing and deleting files. */
async function deleteFile(filename) {
  const ok = await askConfirm({
    title: 'Delete local file?',
    message: `${filename}\n\nThis cannot be undone.`,
    confirmText: 'Delete'
  });
  if (!ok) return;

  let canceled = false;
  showToast(`Deleting local file “${filename}”…`, 'warn', 2400, {
    label: 'Undo',
    onClick: () => { canceled = true; }
  });

  await new Promise(r => setTimeout(r, 2400));
  if (canceled) return;

  const response = await fetch(`/api/files/${encodeURIComponent(filename)}`, { method: 'DELETE' });
  if (!response.ok) {
    showToast('Could not delete local file. Refresh and try again.', 'err');
    return;
  }
  if (filename === localPlaybackFilename) stopLocalPlayback();
  await refreshFiles();
  showToast('Local file deleted', 'ok');
}

function renderFiles() {
  renderLocalSelection();
  // The one native player lives outside filesEl, so routine list updates never reset it.
  if (localPlaybackFilename && !filesCache.some(f => f.name === localPlaybackFilename)) stopLocalPlayback();
  const q = (fileFilterEl.value || '').toLowerCase().trim();
  const filtered = !q ? filesCache : filesCache.filter(f => String(f.name).toLowerCase().includes(q));

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

  filesEl.innerHTML = filtered.map(f => {
    const fileKey = encodeURIComponent(f.name);
    const isQueuedLocalUpload = (Number(localUploadOutstandingByName[String(f.name || '')]) || 0) > 0;
    return `
      <article class="file ${isQueuedLocalUpload ? 'queued-upload' : ''}" draggable="true" data-file-key="${fileKey}" data-file-name="${esc(f.name)}">
        <label class="file-select-label"><input type="checkbox" class="local-file-select" aria-label="Select ${esc(f.name)}" ${localSelectedFiles.has(f.name) ? 'checked' : ''} ${localSelectionSubmitting ? 'disabled' : ''} /></label>
        <div class="file-main">
          <div class="file-title" title="${esc(uiText(f.name))}"><strong>${esc(uiText(f.name))}</strong></div>
          <div class="tiny">${fmtDuration(f.duration_sec)} • ${Math.round((f.size_bytes || 0) / 1024)} KB</div>
        </div>
        <div class="row">
          <button type="button" class="ghost local-play-btn" aria-label="Play ${esc(f.name)}">Play</button>
          <button type="button" class="ghost local-edit-btn" aria-label="Edit and clip ${esc(f.name)}">Edit</button>
          <button class="good local-upload-btn" aria-label="Add ${esc(f.name)} to ${esc(workspaceTargetName())}" ${isQueuedLocalUpload || !selectedToniesUrl ? 'disabled' : ''}>${isQueuedLocalUpload ? 'Adding…' : 'Add'}</button>
          <button class="trash-btn" title="Delete local file" aria-label="Delete local file"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M6 6l1 14h10l1-14"/><path d="M10 10v7"/><path d="M14 10v7"/></svg></button>
        </div>
      </article>
    `;
  }).join('');

  filesEl.querySelectorAll('.file[draggable="true"]').forEach((el) => {
    el.addEventListener('dragstart', (e) => {
      const name = el.getAttribute('data-file-name') || '';
      if (!name || !e.dataTransfer) return;
      e.dataTransfer.setData('text/local-file', name);
      e.dataTransfer.effectAllowed = 'copy';
    });

    el.addEventListener('contextmenu', (e) => {
      // Desktop: right-click menu for upload actions.
      e.preventDefault();
      const name = el.getAttribute('data-file-name') || '';
      if (!name) return;
      openFileMenu(name, e.clientX, e.clientY);
    });

    el.querySelector('.local-file-select')?.addEventListener('change', e => toggleLocalSelection(el.getAttribute('data-file-name') || '', e.target.checked));
    el.querySelector('.local-play-btn')?.addEventListener('click', () => {
      const name = el.getAttribute('data-file-name') || '';
      if (name) toggleLocalPlayback(name);
    });
    el.querySelector('.local-edit-btn')?.addEventListener('click', () => {
      const name = el.getAttribute('data-file-name') || '';
      if (name) openAudioEditorForFile(name);
    });
    el.querySelector('.local-upload-btn')?.addEventListener('click', () => {
      const name = el.getAttribute('data-file-name') || '';
      if (name) uploadExisting(name);
    });
    el.querySelector('.trash-btn')?.addEventListener('click', () => {
      const name = el.getAttribute('data-file-name') || '';
      if (name) deleteFile(name);
    });
  });
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
