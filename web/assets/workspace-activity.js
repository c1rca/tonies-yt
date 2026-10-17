/** In-session activity ledger. Server jobs/actions remain authoritative.
 * Accepted attachment, canonical refresh and cloud processing are distinct outcomes.
 * No client replay, localStorage history, speculative recovery or automatic retries.
 */
const workspaceActionHistory = new Map();
const workspaceJobHistory = new Map();
const workspaceNotices = new Map();
let workspaceNoticeSequence = 0;
const dismissedWorkspaceActivities = new Set();

/** Final statuses shared by jobs and queued library actions. */
const TERMINAL_STATUSES = Object.freeze(['done', 'failed', 'canceled', 'undone']);
/** Reversed work: no later snapshot or event may bring back its earlier state. */
const REVERSED_STATUSES = Object.freeze(['canceled', 'undone']);

function isTerminalStatus(status) {
  return TERMINAL_STATUSES.includes(status);
}

function isReversedStatus(status) {
  return REVERSED_STATUSES.includes(status);
}

function dismissWorkspaceActivity(id) {
  dismissedWorkspaceActivities.add(id);
  renderWorkspaceActivity();
}

function recordWorkspaceAction(targetUrl, action) {
  if (!action?.id) return;
  const previous = workspaceActionHistory.get(action.id);
  // A late original POST/read cannot resurrect a submission already reversed.
  if (previous && isReversedStatus(previous.status) && previous.status !== action.status) return;
  // An event can beat its POST response. Never regress a terminal result to queued.
  if (previous && isTerminalStatus(previous.status) && !isTerminalStatus(action.status)) return;
  if (previous?.status !== action.status) dismissedWorkspaceActivities.delete(`action:${action.id}`);
  workspaceActionHistory.set(action.id, {...previous, ...action, target_url:targetUrl});
  workspaceActionRevision += 1;
  trimCompletedActivity(workspaceActionHistory);
}

function trimCompletedActivity(history) {
  if (history.size <= 150) return;
  for (const [id, item] of history) {
    if (isTerminalStatus(item.status) && item.status !== 'failed' && !item.error && !item.needs_refresh && !item.undo_available) history.delete(id);
    if (history.size <= 150) break;
  }
}

function recordWorkspaceNotice({title, stage, error = '', note = '', targetUrl = '', id = ''}) {
  const key = id || `notice-${++workspaceNoticeSequence}`;
  dismissedWorkspaceActivities.delete(key);
  workspaceNotices.set(key, {id:key, title, stage, error, note, targetUrl, active:false});
  renderWorkspaceActivity();
  return key;
}

// Local uploads carry their file path; searches carry the chosen result or the request text.
function workspaceJobTitle(job) {
  const localFile = String(job.output_file || '').split('/').pop();
  return job.filename || job.selected_candidate?.title || localFile || job.user_message || 'Audio task';
}

function workspaceJobActivity(job) {
  const receipt = hasUploadAttachmentReceipt(job);
  const canonical = getCachedToniesContent(job.target_url);
  const receiptChapter = job.tonies_content?.chapters?.find(c => c.chapter_id === job.uploaded_chapter_id);
  const canonicalChapter = canonical?.chapters?.find(c => c.chapter_id === job.uploaded_chapter_id);
  const processing = (canonicalChapter || receiptChapter)?.processing === true;
  const stages = {
    queued:'Queued', queued_prepare:'Queued for preparation', preparing:'Preparing audio',
    queued_upload:'Waiting to send', waiting_upload:'Waiting to send', uploading:'Sending audio',
    downloading:'Downloading audio', searching:'Searching YouTube', parsing:'Reading source',
    awaiting_selection:'Choose a result in Add audio', canceled:'Canceled', undone:'Undone', failed:'Failed'
  };
  let stage = stages[job.status] || String(job.status || 'Queued');
  if (receipt) {
    stage = job.error || job.needs_refresh ? 'Saved; refresh needed' : processing ? 'Saved; cloud processing' : 'Saved to Tonie';
  } else if (job.status === 'done') {
    stage = job.target_url ? 'Completed; refresh to verify attachment' : 'Ready';
  }
  return {
    id:`job:${job.id}`, kind:'job', title:workspaceJobTitle(job),
    stage, targetUrl:job.target_url || '', error:job.error || '',
    note:receipt && (job.error || job.needs_refresh) ? 'Do not add again. The attachment was accepted; refresh the Tonie to check its current state.' : '',
    active:!isTerminalStatus(job.status) && job.status !== 'awaiting_selection'
  };
}

function workspaceActionActivity(action) {
  const operations = {rename:'Rename track', move:'Move track', delete:'Remove track', clear:'Clear Tonie', upload:'Add audio'};
  const stages = {queued:'Queued', transferring:'Sending audio', ready:'Waiting to attach', applying:'Saving changes', done:'Saved', failed:'Failed', canceled:'Canceled', undone:'Undone'};
  const accepted = !!action.attachment_receipt;
  return {
    id:`action:${action.id}`, kind:'action', actionId:action.id,
    title:operations[action.type] || 'Library change', targetUrl:action.target_url,
    stage:action.needs_refresh ? (accepted ? 'Saved; refresh needed' : 'Outcome unconfirmed; refresh needed') : stages[action.status] || action.status,
    error:action.error || '', note:accepted && action.needs_refresh ? 'Do not add again. Refresh the Tonie to verify the accepted attachment.' : '',
    active:!isTerminalStatus(action.status)
  };
}

function workspaceActivityEntries() {
  for (const job of jobsCache) {
    const previous = workspaceJobHistory.get(job.id);
    if (previous?.status !== job.status) dismissedWorkspaceActivities.delete(`job:${job.id}`);
    workspaceJobHistory.set(job.id, {...previous, ...job});
  }
  trimCompletedActivity(workspaceJobHistory);
  const linkedActions = new Set([...workspaceJobHistory.values()].map(job => job.tonies_action_id).filter(Boolean));
  return [
    ...[...workspaceNotices.values()],
    ...[...workspaceActionHistory.values()].filter(action => !linkedActions.has(action.id)).map(workspaceActionActivity),
    ...[...workspaceJobHistory.values()].map(workspaceJobActivity)
  ];
}

function renderWorkspaceActivity() {
  renderWorkspaceUndo();
  const entries = workspaceActivityEntries().filter(entry => !dismissedWorkspaceActivities.has(entry.id));
  const activeCount = entries.filter(entry => entry.active).length;
  const errors = entries.filter(entry => entry.error || /unconfirmed|refresh needed/i.test(entry.stage)).length;
  document.getElementById('activity-summary').textContent = activeCount ? `Activity (${activeCount})` : errors ? `Activity (${errors} to check)` : 'Activity';
  document.getElementById('workspace-activity').innerHTML = entries.length ? [...entries].reverse().map(entry => `
    <article class="activity-row ${entry.error ? 'failed' : ''}">
      <header><strong>${esc(entry.title)}</strong><span class="stage">${esc(entry.stage)}</span></header>
      <p>${esc(entry.targetUrl ? workspaceTargetName(entry.targetUrl) : 'Local library')}</p>
      ${entry.error ? `<p class="activity-error">${esc(entry.error)}</p>` : ''}
      ${entry.note ? `<p>${esc(entry.note)}</p>` : ''}
      ${!entry.active ? `<button class="ghost activity-dismiss" data-activity-id="${esc(entry.id)}" aria-label="Dismiss ${esc(entry.title)}">Dismiss</button>` : ''}
    </article>`).join('') : '<div class="activity-empty">Nothing running right now.<br />Add audio or arrange tracks. Your progress will appear here.</div>';
  for (const button of document.getElementById('workspace-activity').querySelectorAll('.activity-dismiss')) {
    button.addEventListener('click', () => dismissWorkspaceActivity(button.getAttribute('data-activity-id')));
  }
}
