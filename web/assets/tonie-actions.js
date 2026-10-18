/** Library edits queued on the server: delete, move, rename and upload placement. */
// ---- Library action queue -------------------------------------------------------
// Deletes, moves, renames and delete-all are sent to the server queue
// (POST /api/tonies-actions), which applies everything queued for a Tonie in one
// batch. The list shows each edit right away by layering still-pending actions on
// the server's last content; `tonies_actions` events bring the real result.

function pendingToniesActions(url) {
  return [...toniesQueuedActions.values()].filter(action => action.url === url);
}

function toniesTitleWithToken(current, title) {
  const token = String(current || '').match(/\s\[(?:oc:)?[a-f0-9]{4,12}\]$/i);
  return `${String(title || '').trim()}${token ? token[0] : ''}`;
}

// Mirrors resolve rules in app/tonies_batch.py: -1 means the anchor is gone.
function resolveToniesPosition(chapters, position) {
  if (position?.edge === 'start') return 0;
  if (position?.edge === 'end') return chapters.length;
  for (const [key, offset] of [['before', 0], ['after', 1]]) {
    if (!position?.[key]) continue;
    const at = chapters.findIndex(c => c.chapter_id === position[key]);
    if (at >= 0) return at + offset;
    if (position.index == null) return -1;
  }
  return Math.max(0, Math.min(chapters.length, Number(position?.index) || 0));
}

// Position for something placed at native index `index` of `chapters`.
function toniesPositionAt(chapters, index) {
  if (index <= 0) return { edge: 'start' };
  if (index >= chapters.length) return { edge: 'end' };
  return { before: chapters[index].chapter_id, index };
}

function toniesUploadPosition(chapters, edge, index) {
  if (edge === 'start' || edge === 'end') return { edge };
  return toniesPositionAt(chapters, Math.max(0, Number(index) || 0));
}

function overlayToniesContent(url, data) {
  const pending = pendingToniesActions(url);
  if (!pending.length || !Array.isArray(data?.chapters)) return data;
  let chapters = data.chapters.map(c => ({ ...c }));
  for (const action of pending) {
    const params = action.params || {};
    const at = chapters.findIndex(c => c.chapter_id === params.chapter_id);
    if (action.type === 'clear') chapters = [];
    else if (action.type === 'delete' && at >= 0) chapters.splice(at, 1);
    else if (action.type === 'rename' && at >= 0) chapters[at].title = toniesTitleWithToken(chapters[at].title, params.title);
    else if (action.type === 'move' && at >= 0) {
      const [moved] = chapters.splice(at, 1);
      const to = resolveToniesPosition(chapters, params.position);
      chapters.splice(to < 0 ? at : to, 0, moved);
    }
  }
  return { ...data, chapters: chapters.map((c, index) => ({ ...c, index })) };
}

function activeUploadPlaceholders(url) {
  const intents = new Set([...uploadIntentsByJobId.values(), pendingDropUploadIntent]);
  const finished = (jobId) => attachedUploadJobIds.has(jobId)
    || isTerminalStatus(getJobById(jobId)?.status);
  return [...intents].filter(intent => intent && intent.targetUrl === url
    && (intent.jobId ? activeUploadJobIds.has(intent.jobId) && !finished(intent.jobId)
      : intent === pendingDropUploadIntent && !intent.unconfirmed));
}

function showToniesSettledStatus(url, data) {
  const count = pendingToniesActions(url).length;
  if (count) setToniesProgress(`Saving ${count} change${count === 1 ? '' : 's'}…`);
  else setToniesStatus(toniesProcessingStatus(data), false);
}

function showQueuedToniesContent(url) {
  if (url !== selectedToniesUrl) return;
  const base = getCachedToniesContent(url) || toniesContentCache;
  toniesContentCache = overlayToniesContent(url, base);
  showToniesSettledStatus(url, base);
  renderToniesContent();
}

function rememberFinishedToniesAction(id) {
  finishedToniesActionIds.add(id);
  if (finishedToniesActionIds.size > 500) finishedToniesActionIds.delete(finishedToniesActionIds.values().next().value);
}

async function submitToniesActions(url, specs) {
  const res = await api('/api/tonies-actions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ target_url: url, actions: specs })
  }, 20000);
  if (res?.error) throw new Error(res.error);
  for (const action of res?.actions || []) {
    recordWorkspaceAction(url, action);
    // The result event can beat this response; never resurrect a finished action.
    if (isTerminalStatus(action.status) || finishedToniesActionIds.has(action.id)) continue;
    toniesQueuedActions.set(action.id, { id: action.id, url, type: action.type, params: action.params || {} });
  }
  showQueuedToniesContent(url);
  renderWorkspaceActivity();
  scheduleToniesActionPoll();
  return res?.actions || [];
}

async function queueToniesEdits(url, specs, label) {
  try {
    await submitToniesActions(url, specs);
    return true;
  } catch (e) {
    recordWorkspaceNotice({title:label, targetUrl:url, stage:'Could not confirm change', error:String(e?.message || e)});
    if (url === selectedToniesUrl) setToniesStatus(`${label} failed: ${e?.message || e}`, false);
    showToast(`${label} failed: ${e?.message || e}`, 'err', 5200);
    return false;
  }
}

function handleToniesActionsEvent(evt, options = {}) {
  const url = evt?.target_url;
  if (!url) return;
  const failures = [];
  let succeeded = 0;
  for (const action of evt.actions || []) {
    recordWorkspaceAction(url, action);
    if (!isTerminalStatus(action.status)) continue;
    rememberFinishedToniesAction(action.id);
    if (!toniesQueuedActions.delete(action.id)) continue;  // not queued from this page
    if (action.status === 'failed') failures.push(action);
    else if (action.status === 'done') succeeded += 1;
  }
  renderWorkspaceActivity();
  if (evt.content) applyToniesContent(url, evt.content);
  else showQueuedToniesContent(url);
  // An attached upload replaces its placeholder at once; its job catches up right after.
  const finishedUploads = (evt.actions || []).filter(a => a.type === 'upload' && ['done', 'failed'].includes(a.status));
  for (const action of finishedUploads) {
    const job = jobsCache.find(j => j.tonies_action_id === action.id);
    if (job) attachedUploadJobIds.add(job.id);
    // The job list may not know the action yet; local uploads also match by file name.
    for (const intent of uploadIntentsByJobId.values()) {
      if (intent?.jobId && intent.targetUrl === url && intent.filename
          && intent.filename === action.params?.filename) attachedUploadJobIds.add(intent.jobId);
    }
  }
  if (finishedUploads.length) {
    renderToniesContent();
    if (typeof refreshJobs === 'function') void refreshJobs({ background: true }).catch(() => {});
  }
  if (url === selectedToniesUrl && evt.content) showToniesSettledStatus(url, evt.content);
  if (failures.length) {
    const first = failures[0].error || 'Unknown error';
    const message = failures.length === 1 ? `Change failed: ${first}` : `${failures.length} changes failed: ${first}`;
    if (url === selectedToniesUrl) setToniesStatus(message, false);
    showToast(message, 'err', 5200);
    if (!evt.content && !options.refreshing) void refreshToniesContent({ targetUrl: url, force: true });
  } else if (succeeded && !pendingToniesActions(url).length) {
    showToast(succeeded === 1 ? 'Saved to Tonies' : `Saved ${succeeded} changes to Tonies`, 'ok', 1600);
  }
}

// Safety net when the event stream is down: ask the server how queued edits ended.
function scheduleToniesActionPoll() {
  if (toniesActionPollTimer || !toniesQueuedActions.size) return;
  const streaming = typeof sseConnected !== 'undefined' && sseConnected;
  toniesActionPollTimer = setTimeout(pollToniesActions, streaming ? 5000 : 1200);
}

async function pollToniesActions() {
  toniesActionPollTimer = null;
  for (const url of new Set([...toniesQueuedActions.values()].map(action => action.url))) {
    try {
      const res = await api(`/api/tonies-actions?target_url=${encodeURIComponent(url)}`);
      const known = new Map((res?.actions || []).map(action => [action.id, action]));
      for (const latest of known.values()) recordWorkspaceAction(url, latest);
      renderWorkspaceActivity();
      const finished = [];
      for (const action of pendingToniesActions(url)) {
        const latest = known.get(action.id);
        if (!latest) toniesQueuedActions.delete(action.id);  // server restarted; it's gone
        else if (isTerminalStatus(latest.status)) finished.push(latest);
      }
      if (finished.length) {
        handleToniesActionsEvent({ target_url: url, actions: finished }, { refreshing: true });
        await refreshToniesContent({ targetUrl: url, force: true });
      }
    } catch {}
  }
  scheduleToniesActionPoll();
}

async function enqueueToniesDelete(index) {
  if (!selectedToniesUrl) return;
  const url = selectedToniesUrl;
  const chapter = toniesContentCache?.chapters?.[index];
  if (!chapter?.chapter_id) { showToast('Track ID is missing. Refresh before deleting.', 'err'); return; }
  const chapterName = toniesDisplayTitle(uiText(chapter.title || `Chapter #${index + 1}`));
  const ok = await askConfirm({
    title: 'Remove from Tonies?',
    message: `Remove “${chapterName}” from ${workspaceTargetName(url)}?\n\nOnce sent, removal cannot be undone.`,
    confirmText: 'Remove'
  });
  if (!ok || url !== selectedToniesUrl) return;
  await queueToniesEdits(url, [{ type: 'delete', chapter_id: chapter.chapter_id }], 'Delete');
}

async function reorderToniesChapter(fromIndex, toIndex) {
  if (!selectedToniesUrl || blockIfActionInProgress()) return;
  const url = selectedToniesUrl;
  const chapters = toniesContentCache?.chapters || [];
  const chapterId = chapters[fromIndex]?.chapter_id;
  if (!chapterId || toIndex < 0 || toIndex >= chapters.length || fromIndex === toIndex) return;
  const rest = chapters.filter((_, i) => i !== fromIndex);
  const position = toniesPositionAt(rest, toIndex);
  if (await queueToniesEdits(url, [{ type: 'move', chapter_id: chapterId, position }], 'Reorder') && url === selectedToniesUrl) {
    pulseToniesRow(toIndex);
  }
}
