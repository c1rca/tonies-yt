/** Waveform editor: listen, see and clip audio before adding it. The server checks every clip. */
const CLOCK = /^(?:(\d+):)?(?:(\d+):)?(\d+(?:[.,]\d+)?)$/;
const EDITOR_MIN_CLIP = 0.5;
const EDITOR_POINTS = 1000;
const editorState = {filename: '', seconds: 0, selection: {start: 0, end: 0}, view: {start: 0, end: 0},
  peaks: [], dragging: null, clipPlayback: false, busy: false, ready: false, request: 0, session: 0};

/** "90", "1:30", "1:02:03" or "0:07,5" -> seconds; '' -> null; anything else -> NaN. */
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

function formatClock(seconds) {
  const tenths = Math.round(Math.max(0, seconds) * 10) / 10;
  const whole = Math.floor(tenths), fraction = Math.round((tenths - whole) * 10);
  return `${fmtDuration(whole)}${fraction ? `.${fraction}` : ''}`;
}

/** Keep the selection inside the track and at least EDITOR_MIN_CLIP long, moving `moved` if needed. */
function editorClamp({start, end}, seconds, moved = 'end') {
  let s = Math.min(Math.max(0, start), seconds), e = Math.min(Math.max(0, end), seconds);
  if (e - s < EDITOR_MIN_CLIP) {
    if (moved === 'start') s = Math.max(0, e - EDITOR_MIN_CLIP); else e = Math.min(seconds, s + EDITOR_MIN_CLIP);
    if (e - s < EDITOR_MIN_CLIP) s = Math.max(0, e - EDITOR_MIN_CLIP);
  }
  const round = value => Math.min(seconds, Math.round(value * 10) / 10);
  return {start: round(s), end: round(e)};
}

function editorTimeAt(fraction, view) {
  return view.start + Math.min(1, Math.max(0, fraction)) * (view.end - view.start);
}

function editorFraction(time, view) {
  return (time - view.start) / (view.end - view.start);
}

/** New selection for a key on a handle, or null when the key isn't handled. */
function editorNudge(selection, which, key, shift, seconds) {
  const step = shift ? 10 : 1;
  const value = {ArrowLeft: selection[which] - step, ArrowDown: selection[which] - step,
    ArrowRight: selection[which] + step, ArrowUp: selection[which] + step, Home: 0, End: seconds}[key];
  return value === undefined ? null : editorClamp({...selection, [which]: value}, seconds, which);
}

function readClipWindow(startText, endText, seconds) {
  const start = parseClock(startText) ?? 0, end = parseClock(endText) ?? seconds;
  if (Number.isNaN(start) || Number.isNaN(end)) return {start: 0, end: seconds, error: 'Use minutes:seconds, for example 1:30.'};
  if (start >= seconds) return {start: 0, end: seconds, error: 'Start must be before the end of the track.'};
  if (end <= start) return {start: 0, end: seconds, error: 'End must be after the start.'};
  return {start, end: Math.min(end, seconds), error: ''};
}

function isWholeTrack({start, end}, seconds) {
  return start < 0.05 && end > seconds - 0.05;
}

const editorEl = id => document.getElementById(id);

function setEditorStatus(text, error = '') {
  editorEl('editor-status').textContent = text;
  editorEl('editor-error').textContent = error;
}

function renderEditorControls() {
  const {selection, ready, busy} = editorState;
  for (const id of ['editor-play', 'editor-play-clip', 'editor-zoom', 'editor-start', 'editor-end']) editorEl(id).disabled = !ready;
  editorEl('editor-save').disabled = !ready || busy || isWholeTrack(selection, editorState.seconds);
  const add = editorEl('editor-add');
  add.textContent = `Add to ${workspaceTargetName()}`;
  add.disabled = !ready || busy || !selectedToniesUrl;
  if (document.activeElement !== editorEl('editor-start')) editorEl('editor-start').value = formatClock(selection.start);
  if (document.activeElement !== editorEl('editor-end')) editorEl('editor-end').value = formatClock(selection.end);
  editorEl('editor-length').textContent = ready ? `Keeps ${formatClock(selection.end - selection.start)} of ${formatClock(editorState.seconds)}` : '';
  editorEl('editor-play').textContent = editorEl('editor-audio').paused ? 'Play' : 'Pause';
  for (const which of ['start', 'end']) {
    const handle = editorEl(`editor-handle-${which}`);
    handle.style.left = `${Math.min(100, Math.max(0, editorFraction(selection[which], editorState.view) * 100))}%`;
    handle.hidden = !ready || selection[which] < editorState.view.start || selection[which] > editorState.view.end;
    handle.setAttribute('aria-valuemin', '0');
    handle.setAttribute('aria-valuemax', String(Math.round(editorState.seconds)));
    handle.setAttribute('aria-valuenow', String(Math.round(selection[which])));
    handle.setAttribute('aria-valuetext', formatClock(selection[which]));
  }
  drawEditorWaveform();
}

function drawEditorWaveform() {
  const canvas = editorEl('editor-canvas');
  const context = canvas.getContext?.('2d');
  if (!context) return;
  const ratio = window.devicePixelRatio || 1, width = canvas.clientWidth * ratio, height = canvas.clientHeight * ratio;
  if (canvas.width !== width || canvas.height !== height) Object.assign(canvas, {width, height});
  const style = getComputedStyle(canvas);
  const {peaks, view, selection} = editorState;
  context.clearRect(0, 0, width, height);
  const bar = width / Math.max(1, peaks.length);
  peaks.forEach((peak, index) => {
    const time = editorTimeAt((index + 0.5) / peaks.length, view);
    context.fillStyle = time >= selection.start && time <= selection.end ? style.getPropertyValue('--accent') : style.getPropertyValue('--border');
    const h = Math.max(ratio, peak * height * 0.95);
    context.fillRect(index * bar, (height - h) / 2, Math.max(ratio, bar - ratio), h);
  });
  const now = editorEl('editor-audio').currentTime || 0;
  if (now >= view.start && now <= view.end) {
    context.fillStyle = style.getPropertyValue('--text');
    context.fillRect(editorFraction(now, view) * width - ratio, 0, 2 * ratio, height);
  }
  editorEl('editor-time').textContent = `${formatClock(now)} / ${formatClock(editorState.seconds)}`;
}

function setEditorSelection(selection, moved = 'end') {
  editorState.selection = editorClamp(selection, editorState.seconds, moved);
  editorEl('editor-error').textContent = '';
  renderEditorControls();
}

/** Thrown when the editor was closed or reopened while a request was in flight. */
class EditorClosed extends Error {}

function ensureEditorSession(session) {
  if (session !== editorState.session) throw new EditorClosed();
}

async function loadEditorWaveform(view) {
  const request = ++editorState.request;
  const range = view.start > 0 || view.end < editorState.seconds ? `&start=${view.start}&end=${view.end}` : '';
  const outline = await api(`/api/files/${encodeURIComponent(editorState.filename)}/waveform?points=${EDITOR_POINTS}${range}`);
  if (request !== editorState.request) return;
  editorState.seconds = outline.seconds;
  editorState.view = {start: outline.start, end: outline.end};
  editorState.peaks = outline.peaks;
}

async function loadEditorFile(filename, session) {
  editorState.filename = filename;
  editorState.seconds = 1;
  setEditorStatus('Drawing the waveform…');
  await loadEditorWaveform({start: 0, end: Infinity});
  ensureEditorSession(session);
  const audio = editorEl('editor-audio');
  audio.src = `/api/files/${encodeURIComponent(filename)}?inline=true`;
  audio.load();
  editorState.selection = {start: 0, end: editorState.seconds};
  editorState.ready = true;
  editorEl('editor-zoom').setAttribute('aria-pressed', 'false');
  editorEl('editor-zoom').textContent = 'Zoom to clip';
  setEditorStatus('');
  renderEditorControls();
  editorEl('editor-play').focus({preventScroll: true});
}

/** Open the dialog; returns the session that later async steps must still match. */
function showEditor(title) {
  const dialog = editorEl('audio-editor');
  editorState.session += 1;
  Object.assign(editorState, {ready: false, busy: false, peaks: [], clipPlayback: false, dragging: null});
  if (typeof stopLocalPlayback === 'function') stopLocalPlayback();
  editorEl('editor-title').textContent = `Edit “${uiText(title)}”`;
  setEditorStatus('');
  renderEditorControls();
  dialog.showModal();
  return editorState.session;
}

async function openAudioEditorForFile(filename) {
  const session = showEditor(filename.replace(/\.mp3$/i, ''));
  try {
    await loadEditorFile(filename, session);
  } catch (error) {
    if (!(error instanceof EditorClosed)) setEditorStatus('', `Could not open this file: ${error?.message || error}`);
  }
}

async function openAudioEditorForCandidate(jobId, index) {
  const candidate = getJobById(jobId)?.candidates?.[index];
  if (!candidate) return;
  const session = showEditor(candidate.title);
  setEditorStatus('Getting the audio from YouTube… (it is saved to Local files)');
  try {
    const {filename} = await api(`/api/jobs/${encodeURIComponent(jobId)}/fetch`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({index})
    }, 900000);
    await refreshFiles();
    ensureEditorSession(session);
    await loadEditorFile(filename, session);
    setEditorStatus(`Saved to Local files as ${filename}`);
  } catch (error) {
    if (error instanceof EditorClosed) return;
    setEditorStatus('', String(error?.message || error));
    renderEditorControls();
  }
}

/** The file to add or keep: the original for the whole track, otherwise a new clip file. */
async function editorClipFile() {
  const {filename, selection, seconds} = editorState;
  if (isWholeTrack(selection, seconds)) return filename;
  const result = await api(`/api/files/${encodeURIComponent(filename)}/clip`, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(selection)
  }, 300000);
  await refreshFiles();
  return result.filename;
}

async function runEditorAction(action) {
  if (editorState.busy || !editorState.ready) return;
  editorState.busy = true;
  renderEditorControls();
  try {
    await action();
    editorEl('audio-editor').close();
  } catch (error) {
    editorEl('editor-error').textContent = String(error?.message || error);
  } finally {
    editorState.busy = false;
    renderEditorControls();
  }
}

function editorAddToTonie() {
  const targetUrl = selectedToniesUrl;
  return runEditorAction(async () => {
    if (!targetUrl) throw Error('Choose a Tonie first.');
    const name = await editorClipFile();
    await uploadExisting(name, {targetUrl});
  });
}

function editorSaveClip() {
  return runEditorAction(async () => {
    const name = await editorClipFile();
    showToast(`Saved “${name}” to Local files`, 'ok');
  });
}

async function toggleEditorZoom() {
  const zoomed = editorEl('editor-zoom').getAttribute('aria-pressed') === 'true';
  const {start, end} = editorState.selection;
  const pad = Math.max(1, (end - start) * 0.1);
  try {
    await loadEditorWaveform(zoomed ? {start: 0, end: editorState.seconds}
      : {start: Math.max(0, start - pad), end: Math.min(editorState.seconds, end + pad)});
  } catch (error) {
    editorEl('editor-error').textContent = String(error?.message || error);
    return;
  }
  editorEl('editor-zoom').setAttribute('aria-pressed', String(!zoomed));
  editorEl('editor-zoom').textContent = zoomed ? 'Zoom to clip' : 'Show whole track';
  renderEditorControls();
}

function editorPlay(fromClipStart) {
  const audio = editorEl('editor-audio');
  editorState.clipPlayback = fromClipStart;
  if (fromClipStart) audio.currentTime = editorState.selection.start;
  else if (!audio.paused) { audio.pause(); return; }
  audio.play().catch(() => { editorEl('editor-error').textContent = 'Could not play this file.'; });
}

function editorPointerTime(event) {
  const box = editorEl('editor-wave').getBoundingClientRect();
  return editorTimeAt((event.clientX - box.left) / box.width, editorState.view);
}

function initAudioEditor() {
  const dialog = editorEl('audio-editor'), audio = editorEl('editor-audio'), wave = editorEl('editor-wave');
  editorEl('editor-cancel').onclick = () => dialog.close();
  editorEl('editor-save').onclick = () => void editorSaveClip();
  editorEl('editor-play').onclick = () => editorPlay(false);
  editorEl('editor-play-clip').onclick = () => editorPlay(true);
  editorEl('editor-zoom').onclick = () => void toggleEditorZoom();
  dialog.onsubmit = event => { event.preventDefault(); void editorAddToTonie(); };
  dialog.onclose = () => {
    editorState.request += 1;
    editorState.session += 1;
    editorState.ready = false;
    audio.pause();
    audio.removeAttribute('src');
    audio.load();
  };
  const frame = () => {
    if (editorState.clipPlayback && audio.currentTime >= editorState.selection.end) audio.pause();
    drawEditorWaveform();
    if (!audio.paused) requestAnimationFrame(frame);
  };
  audio.addEventListener('play', () => { renderEditorControls(); requestAnimationFrame(frame); });
  audio.addEventListener('pause', () => { editorState.clipPlayback = false; renderEditorControls(); });
  for (const which of ['start', 'end']) {
    const handle = editorEl(`editor-handle-${which}`);
    handle.addEventListener('pointerdown', event => {
      event.stopPropagation();
      editorState.dragging = which;
      handle.setPointerCapture(event.pointerId);
    });
    handle.addEventListener('pointermove', event => {
      if (editorState.dragging === which) setEditorSelection({...editorState.selection, [which]: editorPointerTime(event)}, which);
    });
    handle.addEventListener('pointerup', () => { editorState.dragging = null; });
    handle.addEventListener('keydown', event => {
      const next = editorNudge(editorState.selection, which, event.key, event.shiftKey, editorState.seconds);
      if (next) { event.preventDefault(); setEditorSelection(next, which); }
    });
    editorEl(`editor-${which}`).addEventListener('change', () => {
      const typed = readClipWindow(editorEl('editor-start').value, editorEl('editor-end').value, editorState.seconds);
      if (typed.error) editorEl('editor-error').textContent = typed.error;
      else setEditorSelection(typed, which);
    });
  }
  wave.addEventListener('pointerdown', event => {
    if (!editorState.ready) return;
    audio.currentTime = editorPointerTime(event);
    drawEditorWaveform();
  });
  // Redraw sharp when the dialog or phone rotates; the canvas is sized from its box.
  if (typeof ResizeObserver === 'function') new ResizeObserver(() => { if (dialog.open) drawEditorWaveform(); }).observe(wave);
}

function bindCandidateEdit(root) {
  for (const button of root.querySelectorAll('.candidate-edit')) button.addEventListener('click', () => {
    void openAudioEditorForCandidate(button.getAttribute('data-job-id'), Number(button.getAttribute('data-candidate-index')));
  });
}
