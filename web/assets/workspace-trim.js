/** Trim a YouTube result before adding it. The server re-validates every window. */
const CLOCK = /^(?:(\d+):)?(?:(\d+):)?(\d+(?:[.,]\d+)?)$/;

/** "90", "1:30", "1:02:03" or "0:07.5" -> seconds; '' -> null; anything else -> NaN. */
function parseClock(text) {
  const value = String(text ?? '').trim();
  if (!value) return null;
  const match = CLOCK.exec(value);
  if (!match) return NaN;
  const [hours, minutes] = match[2] === undefined ? [0, match[1]] : [match[1], match[2]];
  const seconds = Number(match[3].replace(',', '.'));
  if ((minutes !== undefined && seconds >= 60) || (match[2] !== undefined && Number(minutes) >= 60)) return NaN;
  return Number(hours || 0) * 3600 + Number(minutes || 0) * 60 + seconds;
}

/** Validate the two fields against the video length; a window covering everything is no trim. */
function readTrimWindow(startText, endText, duration) {
  const start = parseClock(startText) ?? 0;
  let end = parseClock(endText);
  if (Number.isNaN(start) || Number.isNaN(end)) return {trim:null, error:'Use minutes:seconds, for example 1:30.'};
  const length = Number(duration) > 0 ? Number(duration) : null;
  if (length !== null && start >= length) return {trim:null, error:'Start must be before the end of the video.'};
  if (end !== null && end <= start) return {trim:null, error:'End must be after the start.'};
  if (end !== null && length !== null && end >= length) end = null;
  if (start === 0 && end === null) return {trim:null, error:''};
  return {trim:{start, end}, error:''};
}

function bindCandidateTrim(root) {
  for (const button of root.querySelectorAll('.candidate-trim')) button.addEventListener('click', () => {
    openTrimDialog(button.getAttribute('data-job-id'), Number(button.getAttribute('data-candidate-index')));
  });
}

function focusAfterTrim(jobId, index) {
  // Results re-render while the dialog is open; focus the current button, not a detached one.
  const selector = `.candidate-trim[data-job-id="${CSS.escape(jobId)}"][data-candidate-index="${index}"]`;
  (document.querySelector(selector) || document.getElementById('query-input'))?.focus();
}

function openTrimDialog(jobId, index) {
  const candidate = getJobById(jobId)?.candidates?.[index];
  const dialog = document.getElementById('trim-dialog');
  if (!candidate || !dialog || dialog.open) return;
  const start = document.getElementById('trim-start');
  const end = document.getElementById('trim-end');
  const error = document.getElementById('trim-error');
  document.getElementById('trim-title').textContent = `Trim “${uiText(candidate.title)}”`;
  document.getElementById('trim-length').textContent = candidate.duration
    ? `The video is ${fmtDuration(candidate.duration)} long. Leave End empty to keep the rest.`
    : 'Leave End empty to keep the rest.';
  start.value = '';
  end.value = '';
  end.placeholder = candidate.duration ? fmtDuration(candidate.duration) : 'end';
  error.textContent = '';
  document.getElementById('trim-cancel').onclick = () => dialog.close();
  dialog.onclose = () => focusAfterTrim(jobId, index);
  dialog.onsubmit = event => {
    const chosen = readTrimWindow(start.value, end.value, candidate.duration);
    if (chosen.error) {
      event.preventDefault();
      error.textContent = chosen.error;
      return;
    }
    void pickCandidate(jobId, index, chosen.trim);
  };
  dialog.showModal();
  start.focus();
}
