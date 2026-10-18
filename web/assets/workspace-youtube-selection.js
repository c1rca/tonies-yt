/** Several YouTube results (or a playlist) added in one request; the server keeps their order. */
const youtubeSelection = {jobId:null, indexes:new Set()};
let youtubeSelectionSubmitting = false;

function youtubeSelectionSize() { return youtubeSelection.indexes.size; }

function isCandidateAddable(job, index) {
  const candidate = job?.candidates?.[index];
  if (!candidate || workspaceCandidateAttempt(job.id, index)?.blocked) return false;
  return !getCandidateCardView(job, candidate, index).isSelected;
}

function youtubeResultsLabel(job) {
  if (!job?.playlist) return null;
  const count = job.candidates?.length || 0;
  return count >= 50 ? 'Playlist · first 50 videos' : `Playlist · ${count} ${count === 1 ? 'video' : 'videos'}`;
}

function renderYoutubeSelection() {
  const job = getJobById(youtubeSelection.jobId);
  for (const index of youtubeSelection.indexes) if (!isCandidateAddable(job, index)) youtubeSelection.indexes.delete(index);
  const count = youtubeSelection.indexes.size;
  document.getElementById('youtube-selection-bar').hidden = !count;
  document.getElementById('youtube-selection-count').textContent = `${count} selected`;
  const add = document.getElementById('add-selected-youtube');
  add.textContent = `Add ${count} to ${workspaceTargetName()}`;
  add.setAttribute('aria-label', `Add ${count} to ${workspaceTargetName()} (${count === 1 ? 'video' : 'videos'})`);
  add.disabled = youtubeSelectionSubmitting || !selectedToniesUrl;
}

/** Called on every results render: a selection belongs to the results on screen. */
function syncYoutubeSelection(visibleJobId) {
  if (!youtubeSelectionSubmitting && youtubeSelection.jobId !== visibleJobId) {
    youtubeSelection.jobId = visibleJobId;
    youtubeSelection.indexes.clear();
  }
  renderYoutubeSelection();
}

function bindCandidatePicks(root) {
  for (const box of root.querySelectorAll('.candidate-pick')) box.addEventListener('change', () => {
    toggleYoutubeSelection(box.getAttribute('data-job-id'), Number(box.getAttribute('data-candidate-index')), box.checked);
  });
}

function toggleYoutubeSelection(jobId, index, checked) {
  if (youtubeSelectionSubmitting) return;
  if (youtubeSelection.jobId !== jobId) youtubeSelection.indexes.clear();
  youtubeSelection.jobId = jobId;
  if (checked && isCandidateAddable(getJobById(jobId), index)) youtubeSelection.indexes.add(index);
  else youtubeSelection.indexes.delete(index);
  renderYoutubeSelection();
}

function selectAllYoutube(jobId = youtubeSelection.jobId) {
  const job = getJobById(jobId);
  if (!job || youtubeSelectionSubmitting) return;
  youtubeSelection.jobId = jobId;
  job.candidates.forEach((_candidate, index) => { if (isCandidateAddable(job, index)) youtubeSelection.indexes.add(index); });
  renderJobs();
  renderYoutubeSelection();
}

function clearYoutubeSelection() {
  if (youtubeSelectionSubmitting) return;
  youtubeSelection.indexes.clear();
  renderJobs();
  renderYoutubeSelection();
}

async function addSelectedYoutube() {
  const {jobId} = youtubeSelection;
  if (youtubeSelectionSubmitting || !selectedToniesUrl || !youtubeSelection.indexes.size) return;
  const indexes = [...youtubeSelection.indexes].sort((a, b) => a - b);
  const {target_url: targetUrl, position} = getCandidateSelectionPayload(indexes[0]);
  const title = `${indexes.length} ${indexes.length === 1 ? 'video' : 'videos'}`;
  // Consumed once: a lost response must never offer a one-tap duplicate batch.
  youtubeSelection.indexes.clear();
  youtubeSelectionSubmitting = true;
  for (const index of indexes) workspaceCandidateAttempts.set(workspaceCandidateKey(jobId, index), {blocked:true, accepted:false, stage:'Sending selection'});
  renderJobs();
  renderYoutubeSelection();
  try {
    await api(`/api/jobs/${encodeURIComponent(jobId)}/select-many`, {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({indexes, target_url:targetUrl, position})
    }, 60000);
    for (const index of indexes) workspaceCandidateAttempt(jobId, index).accepted = true;
    recordWorkspaceNotice({title, targetUrl, stage:'Added to the queue'});
    await refreshJobs();
  } catch (error) {
    const refused = /^HTTP (400|401|403|404|409|413|422)\b/.test(String(error?.message || error));
    for (const index of indexes) {
      if (refused) workspaceCandidateAttempts.delete(workspaceCandidateKey(jobId, index));
      else workspaceCandidateAttempt(jobId, index).stage = 'Selection outcome unconfirmed';
    }
    recordWorkspaceNotice({title, targetUrl, stage:refused ? 'Selection refused' : 'Selection outcome unconfirmed',
      error:`${error?.message || error}${refused ? '' : ' Do not add again. Refresh Activity and the Tonie to check.'}`});
    showToast(refused ? 'Could not add those videos' : 'Check Activity before adding again', 'err', 3000);
  } finally {
    youtubeSelectionSubmitting = false;
    renderJobs();
    renderYoutubeSelection();
  }
}

function renderCandidatePick(job, index, title) {
  const checked = youtubeSelection.jobId === job.id && youtubeSelection.indexes.has(index) ? 'checked' : '';
  return `<label class="candidate-select"><input type="checkbox" class="candidate-pick" data-job-id="${esc(job.id)}" data-candidate-index="${index}" ${checked}
    aria-label="Select ${title}" ${youtubeSelectionSubmitting ? 'disabled' : ''} /><span aria-hidden="true">Select</span></label>`;
}

function initYoutubeSelection() {
  document.getElementById('add-selected-youtube').onclick = () => void addSelectedYoutube();
  document.getElementById('select-all-youtube').onclick = () => selectAllYoutube();
  document.getElementById('clear-youtube-selection').onclick = clearYoutubeSelection;
}
