/** Server-owned Undo. Capabilities stay in authenticated page memory, never browser storage.
 * CSS reserves bar space across page visits; only a fresh server receipt enables Undo. */
const workspaceUndoBusy = new Set();
const workspaceUndoConsumed = new Set();
const WORKSPACE_ACTIONS_TAB_KEY = 'tonies-auto.workspace-undo-offers.v1';

let workspaceUndoExpiryTimer = null;
let workspaceActionRevision = 0;

function restoreWorkspaceActionsForTab() {
  // Migrate away from old unscoped tab capabilities (including other logged-in accounts).
  forgetWorkspaceActionsForTab();
  renderWorkspaceUndo();
}

function forgetWorkspaceActionsForTab() {
  try { sessionStorage.removeItem(WORKSPACE_ACTIONS_TAB_KEY); } catch {}
}

function workspaceUndoAction(url) {
  // A submission can contain several actions. Offer just one capability for the target.
  return [...workspaceActionHistory.values()].reverse().find(action =>
    action.target_url === url && action.undo_available === true &&
    !workspaceUndoConsumed.has(action.id) &&
    Date.parse(action.undo_expires_at) > Date.now()) || null;
}

function renderWorkspaceUndo() {
  clearTimeout(workspaceUndoExpiryTimer);
  workspaceUndoExpiryTimer = null;
  const action = workspaceUndoAction(selectedToniesUrl);
  const bar = document.getElementById('workspace-undo');
  bar.hidden = !action && !workspaceUndoBusy.has(selectedToniesUrl);
  const button = document.getElementById('workspace-undo-button');
  button.disabled = !action || workspaceUndoBusy.has(selectedToniesUrl);
  button.textContent = workspaceUndoBusy.has(selectedToniesUrl) ? 'Undoing…' : 'Undo';
  const operation = action?.undo_pending ? 'Removal waiting to send' : 'Last change';
  document.getElementById('workspace-undo-label').textContent = `${operation} · ${workspaceTargetName()}`;
  button.setAttribute('aria-label', `Undo last change to ${workspaceTargetName()}`);
  if (action) {
    workspaceUndoExpiryTimer = setTimeout(renderWorkspaceUndo,
      Math.max(1, Date.parse(action.undo_expires_at) - Date.now()));
  }

}

async function refreshWorkspaceActions() {
  const revision = workspaceActionRevision;
  try {
    const result = await api('/api/tonies-actions');
    // A queued POST/SSE receipt that arrived during this read is newer than this snapshot.
    if (revision !== workspaceActionRevision) { renderWorkspaceUndo(); return; }
    if (!Array.isArray(result?.actions)) throw new Error('Invalid action history');
    for (const action of workspaceActionHistory.values()) action.undo_available = false;
    for (const action of result.actions) recordWorkspaceAction(action.target_url, action);
    renderWorkspaceActivity();
  } catch {
    if (revision !== workspaceActionRevision) return;
    // Do not advertise an old capability after an unsuccessful availability check.
    for (const action of workspaceActionHistory.values()) action.undo_available = false;
    renderWorkspaceUndo();
  }
}

async function undoWorkspaceAction() {
  const url = selectedToniesUrl;
  const action = workspaceUndoAction(url);
  if (!action || workspaceUndoBusy.has(url)) return;
  workspaceUndoBusy.add(url);
  // Consume every member of this target's advertised group before sending. Lost
  // acknowledgments must not expose a duplicate Undo or fall back to an older action.
  for (const item of workspaceActionHistory.values()) {
    if (item.target_url === url && item.undo_available) workspaceUndoConsumed.add(item.id);
  }
  renderWorkspaceUndo();
  let accepted = false;
  try {
    const result = await api(`/api/tonies-actions/${encodeURIComponent(action.id)}/undo`, {method:'POST'}, 20000);
    if (!Array.isArray(result?.actions) || !result.actions.length) throw new Error('Invalid Undo response.');
    accepted = true;
    handleToniesActionsEvent({target_url:url, actions:result.actions});
    // The endpoint acknowledges the inverse, not a client-side content restoration.
    await refreshToniesContent({targetUrl:url, force:true});
    recordWorkspaceNotice({title:'Undo', targetUrl:url, stage:'Undo accepted; check Tracks for the current library'});
  } catch (error) {
    recordWorkspaceNotice({title:'Undo', targetUrl:url,
      stage:accepted ? 'Undo accepted; refresh needed' : 'Could not confirm Undo',
      error:`${error?.message || error} Refresh the Tonie to check its current state. Do not repeat this Undo.`});
  } finally {
    workspaceUndoBusy.delete(url);
    renderWorkspaceUndo();
  }
}
