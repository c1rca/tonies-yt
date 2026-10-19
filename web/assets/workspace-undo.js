/** Server-owned, one-level Undo. No client snapshots, inverse recipes or mutation retries.
 * The server's last Undo offers are kept for this tab only (sessionStorage), so a page that
 * opens again draws the Undo bar at once; the next server read confirms or withdraws them. */
const workspaceUndoBusy = new Set();
const workspaceUndoConsumed = new Set();
const WORKSPACE_ACTIONS_TAB_KEY = 'tonies-auto.workspace-undo-offers.v1';
const restoredWorkspaceActionIds = new Set();
let workspaceUndoExpiryTimer = null;
let workspaceActionRevision = 0;

function liveUndoOffers() {
  return [...workspaceActionHistory.values()].filter(action => action.undo_available === true &&
    !workspaceUndoConsumed.has(action.id) && Date.parse(action.undo_expires_at) > Date.now());
}

function saveWorkspaceActionsForTab() {
  const offers = liveUndoOffers().map(({id, target_url, type, status, undo_available, undo_expires_at, undo_pending}) =>
    ({id, target_url, type, status, undo_available, undo_expires_at, undo_pending: Boolean(undo_pending)}));
  try { sessionStorage.setItem(WORKSPACE_ACTIONS_TAB_KEY, JSON.stringify(offers)); } catch {}
}

function restoreWorkspaceActionsForTab() {
  let offers = [];
  try { offers = JSON.parse(sessionStorage.getItem(WORKSPACE_ACTIONS_TAB_KEY) || '[]'); } catch {}
  for (const action of Array.isArray(offers) ? offers : []) {
    if (!action?.id || workspaceActionHistory.has(action.id)) continue;
    recordWorkspaceAction(action.target_url, action);
    restoredWorkspaceActionIds.add(action.id);
  }
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
  saveWorkspaceActionsForTab();
}

async function refreshWorkspaceActions() {
  const revision = workspaceActionRevision;
  try {
    const result = await api('/api/tonies-actions');
    // An offer restored from this tab that the server no longer lists is withdrawn. Those
    // offers predate this read, so this holds even when newer receipts arrived meanwhile.
    const listed = new Set((result?.actions || []).map(action => action.id));
    for (const id of restoredWorkspaceActionIds) {
      const action = workspaceActionHistory.get(id);
      if (action && !listed.has(id)) action.undo_available = false;
    }
    restoredWorkspaceActionIds.clear();
    // A queued POST/SSE receipt that arrived during this read is newer than this snapshot.
    if (revision !== workspaceActionRevision) { renderWorkspaceUndo(); return; }
    for (const action of result?.actions || []) recordWorkspaceAction(action.target_url, action);
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
