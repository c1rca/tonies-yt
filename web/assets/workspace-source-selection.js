/** Source selection acknowledgments are distinct from job-list refreshes.
 * A refusal is retryable; a lost/accepted response must never offer a second Add.
 */
const workspaceCandidateAttempts = new Map();
function workspaceCandidateKey(jobId, index) {
  return JSON.stringify([jobId, Number(index)]);
}
function workspaceCandidateAttempt(jobId, index) {
  return workspaceCandidateAttempts.get(workspaceCandidateKey(jobId, index));
}

function prepareWorkspaceCandidateIntent(jobId, targetUrl) {
  if (!targetUrl) return;
  pendingDropUploadIntent = {
    jobId, targetUrl,
    targetIndex: toniesReverseView ? (toniesContentCache?.chapters || []).length : 0,
    targetEdge: toniesReverseView ? 'end' : 'start',
    position: {edge:toniesReverseView ? 'end' : 'start'},
    createdAt:Date.now()
  };
  uploadIntentsByJobId.set(jobId, pendingDropUploadIntent);
}

async function pickCandidate(jobId, index, trim = null) {
  if (isCandidateSelectionPending(jobId) || workspaceCandidateAttempt(jobId, index)?.blocked) return;
  const targetUrl = selectedToniesUrl;
  const payload = getCandidateSelectionPayload(index, trim);
  const parent = getJobById(jobId);
  const previous = {status:parent?.status, selected:parent?.selected_candidate};
  const picked = applyOptimisticCandidateSelection(jobId, index);
  const key = workspaceCandidateKey(jobId, index);
  const attempt = {blocked:true, accepted:false, stage:'Sending selection'};
  workspaceCandidateAttempts.set(key, attempt);
  prepareWorkspaceCandidateIntent(jobId, targetUrl);
  searchUiActive = false;
  renderSearchUX();
  renderJobs();
  try {
    const result = await api(`/api/jobs/${encodeURIComponent(jobId)}/select`, {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)
    }, 180000);
    attempt.accepted = true;
    attempt.stage = 'Accepted';
    const returnedJobId = result?.id || jobId;
    const intent = uploadIntentsByJobId.get(jobId);
    if (intent && returnedJobId !== jobId) {
      uploadIntentsByJobId.delete(jobId);
      intent.jobId = returnedJobId;
      uploadIntentsByJobId.set(returnedJobId, intent);
    }
    applySelectionResponse(jobId, index, result);
    await refreshJobs();
  } catch (error) {
    const refused = !attempt.accepted && /^HTTP (400|401|403|404|409|413|422)\b/.test(String(error?.message || error));
    attempt.stage = attempt.accepted ? 'Selection accepted; refresh needed' : refused ? 'Selection refused' : 'Selection outcome unconfirmed';
    if (!attempt.accepted) {
      uploadIntentsByJobId.delete(jobId);
      if (pendingDropUploadIntent?.jobId === jobId) pendingDropUploadIntent = null;
      activeUploadJobIds.delete(jobId);
      rollbackOptimisticCandidateSelection(jobId);
      // Restore only our own speculative state, never a newer canonical job object.
      if (parent && getJobById(jobId) === parent && parent.status === 'downloading') {
        parent.status = previous.status;
        parent.selected_candidate = previous.selected;
      }
    }
    if (refused) workspaceCandidateAttempts.delete(key);
    recordWorkspaceNotice({id:`selection:${key}`, title:picked?.title || 'Source selection', targetUrl,
      stage:attempt.stage, error:`${error?.message || error}${refused ? '' : ' Do not add again. Refresh Activity and the Tonie to check the accepted or unconfirmed request.'}`});
    showToast(attempt.stage, 'err', 3000);
  } finally {
    endCandidateSelection(jobId);
    renderJobs();
  }
}
