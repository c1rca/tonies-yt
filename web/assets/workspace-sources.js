/** Local selection consumes a captured target once; the existing upload queue owns delivery. */
const localSelectedFiles = new Set();
let localSelectionSubmitting = false;
function toggleLocalSelection(name, checked) {
  if (localSelectionSubmitting || !filesCache.some(f => f.name === name)) return;
  if (checked) localSelectedFiles.add(name); else localSelectedFiles.delete(name);
  renderLocalSelection();
}
function renderLocalSelection() {
  for (const name of localSelectedFiles) if (!filesCache.some(f => f.name === name)) localSelectedFiles.delete(name);
  document.getElementById('local-selection-bar').hidden = !localSelectedFiles.size;
  document.getElementById('local-selection-count').textContent = `${localSelectedFiles.size} selected`;
  const add = document.getElementById('add-selected-local');
  add.textContent = `Add to ${workspaceTargetName()}`;
  add.setAttribute('title', add.textContent);
  add.setAttribute('aria-label', `Add ${localSelectedFiles.size} ${localSelectedFiles.size === 1 ? 'file' : 'files'} to ${workspaceTargetName()}`);
  add.disabled = localSelectionSubmitting || !selectedToniesUrl;
}
function clearLocalSelection() { if (!localSelectionSubmitting) { localSelectedFiles.clear(); renderFiles(); renderLocalSelection(); } }
async function addSelectedLocalFiles() {
  if (localSelectionSubmitting || !selectedToniesUrl || !localSelectedFiles.size) return;
  const targetUrl = selectedToniesUrl;
  const names = [...localSelectedFiles].filter(name => filesCache.some(f => f.name === name));
  localSelectionSubmitting = true;
  // Selection is consumed once. A lost response never creates a one-tap duplicate batch.
  localSelectedFiles.clear();
  renderLocalSelection();
  renderFiles();
  try {
    for (const name of names) await uploadExisting(name, {targetUrl});
  } finally {
    localSelectionSubmitting = false;
    renderLocalSelection();
    renderFiles();
  }
}

function safeSourceLink(value) {
  try {
    const url = new URL(String(value || ''));
    return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password ? url.href : '';
  } catch {
    return '';
  }
}

function renderCandidateCard(parentJob, candidate, index) {
  const view = getCandidateCardView(parentJob, candidate, index);
  const href = safeSourceLink(candidate.url);
  const thumbnail = safeImageSrc(candidate.thumbnail);
  const title = esc(uiText(candidate.title));
  const attempt = workspaceCandidateAttempt(parentJob.id, index);
  const awaitingReceipt = attempt?.blocked && !attempt.accepted;
  const receiptLabel = attempt?.stage === 'Sending selection' ? 'Sending selection…' : 'Check Activity before adding again';
  const metadata = esc(uiText(candidate.uploader || 'YouTube')) +
    (candidate.duration ? ` · ${fmtDuration(candidate.duration)}` : '');
  const disabled = !selectedToniesUrl ? 'disabled' : '';
  const addable = !awaitingReceipt && !view.isSelected;
  const add = awaitingReceipt ? `<span class="tiny">${receiptLabel}</span>` : view.isSelected ? '<span class="tiny">Added to queue</span>' : `
    <button class="ghost candidate-edit" data-job-id="${esc(parentJob.id)}" data-candidate-index="${index}"
      aria-label="Edit and clip ${title}">Edit</button>
    <button class="good candidate-add" data-job-id="${esc(parentJob.id)}"
      data-candidate-index="${index}" aria-label="Add ${title} to ${esc(workspaceTargetName())}"
      ${disabled}>Add</button>`;
  return `<article class="candidate-card ${view.isSelected ? 'selected' : ''}">
    ${view.progressHtml}
    <div class="candidate-heading">
      ${thumbnail ? `<img class="candidate-thumbnail" src="${esc(thumbnail)}" alt="" loading="lazy" />` :
        `<span class="candidate-number">${view.isSelected ? '✓' : index + 1}</span>`}
      <strong>${title}</strong>
    </div>
    <p class="tiny">${metadata}</p>
    <div class="candidate-actions">
      ${addable ? renderCandidatePick(parentJob, index, title) : ''}
      ${href ? `<a href="${esc(href)}" target="_blank" rel="noopener noreferrer">View source ↗</a>` : ''}
      ${add}
    </div>
  </article>`;
}

let localImportInFlight = false;
async function importWorkspaceFile(file) {
  if (!file || localImportInFlight) return;
  localImportInFlight = true;
  const button = document.getElementById('browse-files');
  button.disabled = true;
  const id = recordWorkspaceNotice({title:file.name || 'Audio file', stage:'Importing file'});
  let accepted = false;
  try {
    const form = new FormData();
    form.append('file', file, file.name || 'upload');
    const response = await fetch('/api/files/upload', {method:'POST', body:form});
    if (!response.ok) throw new Error(`Import refused (HTTP ${response.status}).`);
    accepted = true;
    const result = await response.json();
    await refreshFiles();
    focusUploadInLibrary(result?.filename || '');
    recordWorkspaceNotice({id, title:file.name, stage:'Imported to local library'});
  } catch (error) {
    recordWorkspaceNotice({id, title:file.name, stage:accepted ? 'Imported; refresh local library' : 'Import failed', error:String(error?.message || error)});
    showToast(accepted ? 'File imported. Refresh the local library to see it.' : `Import failed: ${error?.message || error}`, 'err');
  } finally {
    localImportInFlight = false;
    button.disabled = false;
  }
}

function initWorkspaceSources() {
  const picker = document.getElementById('upload-file-input');
  document.getElementById('browse-files').onclick = () => picker.click();
  picker.onchange = async () => {
    try { await importWorkspaceFile(picker.files?.[0]); }
    finally { picker.value = ''; }
  };
  const zone = document.getElementById('local-import-zone');
  zone.addEventListener('dragover', event => {
    if (!Array.from(event.dataTransfer?.types || []).includes('Files')) return;
    event.preventDefault();
    zone.classList.add('drag-over');
    event.dataTransfer.dropEffect = 'copy';
  });
  zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
  zone.addEventListener('drop', event => {
    event.preventDefault();
    zone.classList.remove('drag-over');
    void importWorkspaceFile(event.dataTransfer?.files?.[0]);
  });
}

