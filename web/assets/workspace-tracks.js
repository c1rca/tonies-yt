/** Exact-ID selection and inline editors. Async completion belongs to its editor instance. */
const trackSelections = new Map();
let trackSelectionMode = false;
let bulkDeleteSubmitting = false;
let workspaceTargetVisit = 0;
const inlineRenames = new Map();
function selectedTracks() {
  if (!trackSelections.has(selectedToniesUrl)) trackSelections.set(selectedToniesUrl, new Set());
  return trackSelections.get(selectedToniesUrl);
}
function toggleTrackSelection() {
  trackSelectionMode = !trackSelectionMode;
  if (!trackSelectionMode) selectedTracks().clear();
  cancelInlineRename(false); renderTrackSelection(); renderToniesContent();
}
function toggleTrackSelected(id, checked) {
  if (!(toniesContentCache?.chapters || []).some(c => c.chapter_id === id)) return;
  if (checked) selectedTracks().add(id); else selectedTracks().delete(id);
  renderTrackSelection();
}
function renderTrackSelection() {
  const ids = new Set((toniesContentCache?.chapters || []).map(c => c.chapter_id));
  for (const id of selectedTracks()) if (!ids.has(id)) selectedTracks().delete(id);
  document.getElementById('track-selection-bar').hidden = !trackSelectionMode;
  document.getElementById('track-selection-count').textContent = `${selectedTracks().size} selected`;
  document.getElementById('select-tracks').textContent = trackSelectionMode ? 'Cancel selection' : 'Select';
  document.getElementById('delete-selected-tracks').disabled = !selectedTracks().size || bulkDeleteSubmitting;
}
async function deleteSelectedTracks() {
  if (bulkDeleteSubmitting || !selectedToniesUrl || !selectedTracks().size) return;
  const url = selectedToniesUrl, visit = workspaceTargetVisit, selection = selectedTracks();
  const ids = [...selection];
  // Keep the dialog launcher focusable for cancel/Escape focus return. The guard
  // still blocks repeat submissions while the confirmation is open.
  bulkDeleteSubmitting = true;
  try {
    const ok = await askConfirm({title:`Remove ${ids.length} ${ids.length === 1 ? 'track' : 'tracks'}?`,
      message:`Remove ${ids.length} ${ids.length === 1 ? 'track' : 'tracks'} from ${workspaceTargetName(url)}?\n\nOnce sent, removal cannot be undone.`,confirmText:'Remove tracks'});
    if (!ok || url !== selectedToniesUrl || visit !== workspaceTargetVisit) return;
    // Capture IDs before the dialog. Never derive a destructive action from refreshed indices.
    await queueToniesEdits(url, ids.map(chapter_id => ({type:'delete',chapter_id})), 'Remove tracks');
    ids.forEach(id => selection.delete(id));
  } finally { bulkDeleteSubmitting = false; renderTrackSelection(); renderToniesContent(); }
}
function getInlineRename() { return inlineRenames.get(selectedToniesUrl) || null; }
function beginInlineRename(id) {
  const chapter = (toniesContentCache?.chapters || []).find(c => c.chapter_id === id);
  if (!chapter || toniesEditMode) return;
  const current = getInlineRename();
  if (current && current.id === id) return;
  inlineRenames.set(selectedToniesUrl, {id, draft:toniesDisplayTitle(uiText(chapter.title)), busy:false, submitted:false, error:''});
  renderToniesContent(); focusInlineRename();
}
function focusInlineRename() {
  const input = toniesContentEl.querySelector('.tonies-title-inline');
  if (input) { input.focus(); input.select(); }
}
function cancelInlineRename(focus = true) {
  const edit = getInlineRename(); inlineRenames.delete(selectedToniesUrl); renderToniesContent();
  if (focus && edit) focusTrackAction(edit.id);
}
function focusTrackAction(id) {
  for (const row of toniesContentEl.querySelectorAll('.tonies-item')) {
    if (row.getAttribute('data-chapter-id') === id) row.querySelector('.track-overflow')?.focus();
  }
}
async function saveInlineRename() {
  const edit = getInlineRename(), url = selectedToniesUrl;
  if (!edit || edit.busy || edit.submitted || !edit.draft.trim()) return;
  const chapter = (toniesContentCache?.chapters || []).find(c => c.chapter_id === edit.id);
  if (!chapter) { edit.error = 'Track is no longer available. Refresh the Tonie.'; renderToniesContent(); return; }
  const title = edit.draft.trim();
  if (title === toniesDisplayTitle(uiText(chapter.title))) { cancelInlineRename(); return; }
  edit.busy = true; edit.submitted = true; edit.error = ''; renderToniesContent();
  try {
    await submitToniesActions(url, [{type:'rename', chapter_id:edit.id, title}]);
    if (inlineRenames.get(url) === edit) {
      inlineRenames.delete(url);
      if (url === selectedToniesUrl) {
        renderToniesContent();
        focusTrackAction(edit.id);
      }
    }
  } catch (e) {
    const refused = /^HTTP (400|401|403|404|409|413|422)\b/.test(String(e?.message || e));
    edit.submitted = !refused;
    edit.error = refused ? `Rename refused: ${e?.message || e}` : 'Could not confirm this rename. Refresh the Tonie before making another change.';
  } finally { edit.busy = false; if (inlineRenames.get(url) === edit && url === selectedToniesUrl) renderToniesContent(); }
}
function bindWorkspaceTrackRows() {
  for (const row of toniesContentEl.querySelectorAll('.tonies-item')) {
    const id = row.getAttribute('data-chapter-id');
    row.querySelector('.track-select')?.addEventListener('change', e => toggleTrackSelected(id, e.target.checked));
    row.querySelector('.track-overflow')?.addEventListener('click', e => {
      e.stopPropagation();
      const index = (toniesContentCache?.chapters || []).findIndex(c => c.chapter_id === id);
      const r = e.currentTarget.getBoundingClientRect();
      menuReturnFocusId = id;
      openToniesMenu(index, r.right - 208, r.bottom + 4);
    });
    const input = row.querySelector('.tonies-title-inline');
    input?.addEventListener('input', () => { const edit=getInlineRename(); if (edit?.id === id) edit.draft=input.value; });
    input?.addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); void saveInlineRename(); }
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); cancelInlineRename(); }
    });
    row.querySelector('.inline-save')?.addEventListener('click', saveInlineRename);
    row.querySelector('.inline-cancel')?.addEventListener('click', () => cancelInlineRename());
  }
}

