/** One Local files row: its markup and its buttons (Play, Add, the actions menu, select). */
const LOCAL_ICON_PLAY = '<svg class="icon-play" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.6v12.8a1 1 0 0 0 1.5.86l10.4-6.4a1 1 0 0 0 0-1.72L9.5 4.74A1 1 0 0 0 8 5.6z"/></svg>';
const LOCAL_ICON_PAUSE = '<svg class="icon-pause" viewBox="0 0 24 24" aria-hidden="true"><rect x="6.5" y="5" width="4" height="14" rx="1.2"/><rect x="13.5" y="5" width="4" height="14" rx="1.2"/></svg>';
const LOCAL_ICON_ADD = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>';
const LOCAL_ICON_MORE = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="1.9"/><circle cx="12" cy="12" r="1.9"/><circle cx="19" cy="12" r="1.9"/></svg>';

/** The title a file gets on a Tonie: underscores as spaces, no audio extension. */
function localFileTitle(name) {
  return uiText(String(name || '').replace(/\.(mp3|m4a|aac|wav|flac|ogg|opus)$/i, ''));
}

function fmtFileSize(bytes) {
  const size = Math.max(0, Number(bytes) || 0);
  if (size >= 1024 * 1024) return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  return `${size ? Math.max(1, Math.round(size / 1024)) : 0} KB`;
}

function localFileActionsHtml(name, queued) {
  const target = esc(workspaceTargetName());
  return `<div class="file-actions">
      <button type="button" class="local-upload-btn" aria-label="Add ${esc(name)} to ${target}" title="Add to ${target}" ${queued || !selectedToniesUrl ? 'disabled' : ''}>${queued ? 'Adding…' : `${LOCAL_ICON_ADD}<span>Add</span>`}</button>
      <button type="button" class="ghost local-more-btn" aria-label="More actions for ${esc(name)}" aria-haspopup="menu" title="More actions">${LOCAL_ICON_MORE}</button>
    </div>`;
}

function localFileRowHtml(f) {
  const name = String(f.name || '');
  const queued = (Number(localUploadOutstandingByName[name]) || 0) > 0;
  const select = localSelectionMode
    ? `<label class="file-select-label"><input type="checkbox" class="local-file-select" aria-label="Select ${esc(name)}" ${localSelectedFiles.has(name) ? 'checked' : ''} ${localSelectionSubmitting ? 'disabled' : ''} /></label>`
    : '';
  return `
    <article class="file${queued ? ' queued-upload' : ''}${localSelectionMode ? ' selecting' : ''}" draggable="true" data-file-key="${encodeURIComponent(name)}" data-file-name="${esc(name)}">
      ${select}<button type="button" class="local-play-btn" data-state="paused" aria-label="Play ${esc(name)}">${LOCAL_ICON_PLAY}${LOCAL_ICON_PAUSE}</button>
      <div class="file-main" title="${esc(name)}">
        <div class="file-title"><strong>${esc(localFileTitle(name))}</strong></div>
        <div class="tiny">${fmtDuration(f.duration_sec)} · ${fmtFileSize(f.size_bytes)}</div>
      </div>
      ${localSelectionMode ? '' : localFileActionsHtml(name, queued)}
    </article>`;
}

function bindLocalFileRow(el) {
  const name = el.getAttribute('data-file-name') || '';
  if (!name) return;
  el.addEventListener('dragstart', (e) => {
    if (!e.dataTransfer) return;
    e.dataTransfer.setData('text/local-file', name);
    e.dataTransfer.effectAllowed = 'copy';
  });
  // Desktop right-click and phone long-press open the same menu as the ⋯ button.
  el.addEventListener('contextmenu', (e) => { e.preventDefault(); openFileMenu(name, e.clientX, e.clientY); });
  el.querySelector('.local-play-btn')?.addEventListener('click', () => toggleLocalPlayback(name));
  el.querySelector('.local-upload-btn')?.addEventListener('click', () => uploadExisting(name));
  el.querySelector('.local-more-btn')?.addEventListener('click', (e) => {
    e.stopPropagation();
    openFileMenuFrom(e.currentTarget, name);
  });
  const box = el.querySelector('.local-file-select');
  if (!box) return;
  box.addEventListener('change', e => toggleLocalSelection(name, e.target.checked));
  // While selecting, a tap anywhere on the row ticks it, not just on the small box.
  el.addEventListener('click', (e) => {
    if (e.target.closest('button, input, label') || box.disabled) return;
    box.checked = !box.checked;
    toggleLocalSelection(name, box.checked);
  });
}
