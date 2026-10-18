/** Waveform editor: listen, see and clip audio before adding it. The server checks every clip. */
const CLOCK = /^(?:(\d+):)?(?:(\d+):)?(\d+(?:[.,]\d+)?)$/;
const EDITOR_MIN_CLIP = 0.5;
const EDITOR_POINTS = 1000;
const EDITOR_WAVEFORM_TIMEOUT_MS = 180000;
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
  const step = shift ? 10 : 1, at = selection[which];
  const value = {ArrowLeft: at - step, ArrowDown: at - step, ArrowRight: at + step, ArrowUp: at + step,
    PageDown: at - 10, PageUp: at + 10, Home: 0, End: seconds}[key];
  return value === undefined ? null : editorClamp({...selection, [which]: value}, seconds, which);
}

function readClipWindow(startText, endText, seconds) {
  const start = parseClock(startText) ?? 0, end = parseClock(endText) ?? seconds;
  if (Number.isNaN(start) || Number.isNaN(end)) return {start: 0, end: seconds, error: 'Use minutes:seconds, for example 1:30.'};
  if (start >= seconds) return {start: 0, end: seconds, error: 'Start must be before the end of the track.'};
  if (end <= start) return {start: 0, end: seconds, error: 'End must be after the start.'};
  return {start, end: Math.min(end, seconds), error: ''};
}

/** Which handle a press on `pressed` moves: when both sit on the same spot, the side pressed decides. */
function editorPickHandle(pressed, time, selection, closeSeconds) {
  if (selection.end - selection.start > closeSeconds) return pressed;
  return time < (selection.start + selection.end) / 2 ? 'start' : 'end';
}

/** "HTTP 422: {"detail":"…"}" -> "…": the message a person should read. */
function editorErrorText(error) {
  const text = String(error?.message || error);
  const body = /^HTTP \d+: (.*)$/s.exec(text)?.[1];
  if (!body) return text;
  try {
    const detail = JSON.parse(body)?.detail;
    return typeof detail === 'string' ? detail : text;
  } catch {
    return body;
  }
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
    // Each handle can move up to the other one.
    handle.setAttribute('aria-valuemin', String(Math.round(which === 'start' ? 0 : selection.start)));
    handle.setAttribute('aria-valuemax', String(Math.round(which === 'start' ? selection.end : editorState.seconds)));
    handle.setAttribute('aria-valuenow', String(Math.round(selection[which])));
    handle.setAttribute('aria-valuetext', formatClock(selection[which]));
  }
  drawEditorWaveform();
}

function drawEditorWaveform() {
  const canvas = editorEl('editor-canvas');
  const context = canvas.getContext?.('2d');
  if (!context) return;
  const ratio = window.devicePixelRatio || 1;
  const width = Math.round(canvas.clientWidth * ratio), height = Math.round(canvas.clientHeight * ratio);
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

/** Load the outline for `view`; false when a newer request or a close made this one stale. */
async function loadEditorWaveform(view) {
  const request = ++editorState.request;
  const range = view.start > 0 || view.end < editorState.seconds ? `&start=${view.start}&end=${view.end}` : '';
  const outline = await api(`/api/files/${encodeURIComponent(editorState.filename)}/waveform?points=${EDITOR_POINTS}${range}`,
    {}, EDITOR_WAVEFORM_TIMEOUT_MS);
  if (request !== editorState.request) return false;
  editorState.seconds = outline.seconds;
  editorState.view = {start: outline.start, end: outline.end};
  editorState.peaks = outline.peaks;
  return true;
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
    if (!(error instanceof EditorClosed)) setEditorStatus('', `Could not open this file: ${editorErrorText(error)}`);
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
    setEditorStatus('', editorErrorText(error));
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
  const session = editorState.session;
  editorState.busy = true;
  renderEditorControls();
  try {
    await action();
    if (session === editorState.session) editorEl('audio-editor').close();
  } catch (error) {
    if (session === editorState.session) editorEl('editor-error').textContent = editorErrorText(error);
  } finally {
    if (session === editorState.session) {
      editorState.busy = false;
      renderEditorControls();
    }
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
    const fresh = await loadEditorWaveform(zoomed ? {start: 0, end: editorState.seconds}
      : {start: Math.max(0, start - pad), end: Math.min(editorState.seconds, end + pad)});
    if (!fresh) return;
  } catch (error) {
    editorEl('editor-error').textContent = editorErrorText(error);
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

/** Apply the typed Start and End (on change, or Enter in either field). */
function applyTypedClip(moved) {
  const typed = readClipWindow(editorEl('editor-start').value, editorEl('editor-end').value, editorState.seconds);
  if (typed.error) editorEl('editor-error').textContent = typed.error;
  else setEditorSelection(typed, moved);
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
  editorEl('editor-add').onclick = () => void editorAddToTonie();
  // Enter in a time field only applies the times; adding always takes a deliberate tap.
  dialog.onsubmit = event => {
    event.preventDefault();
    if (editorState.ready) applyTypedClip(document.activeElement === editorEl('editor-start') ? 'start' : 'end');
  };
  // Escape can't close the editor while a clip is being saved or added.
  dialog.addEventListener('cancel', event => { if (editorState.busy) event.preventDefault(); });
  dialog.onclose = () => {
    editorState.request += 1;
    editorState.session += 1;
    editorState.ready = false;
    audio.pause();
    audio.removeAttribute('src');
    audio.load();
  };
  const stopAtClipEnd = () => {
    if (editorState.clipPlayback && audio.currentTime >= editorState.selection.end) audio.pause();
  };
  const frame = () => {
    stopAtClipEnd();
    drawEditorWaveform();
    if (!audio.paused) requestAnimationFrame(frame);
  };
  // Animation frames pause in background tabs; timeupdate still stops the clip there.
  audio.addEventListener('timeupdate', stopAtClipEnd);
  audio.addEventListener('play', () => { renderEditorControls(); requestAnimationFrame(frame); });
  audio.addEventListener('pause', () => { editorState.clipPlayback = false; renderEditorControls(); });
  for (const which of ['start', 'end']) {
    const handle = editorEl(`editor-handle-${which}`);
    handle.addEventListener('pointerdown', event => {
      event.stopPropagation();
      const pixelSeconds = (editorState.view.end - editorState.view.start) / wave.getBoundingClientRect().width;
      editorState.dragging = editorPickHandle(which, editorPointerTime(event), editorState.selection, 24 * pixelSeconds);
      handle.setPointerCapture(event.pointerId);
    });
    handle.addEventListener('pointermove', event => {
      const moving = editorState.dragging;
      if (moving) setEditorSelection({...editorState.selection, [moving]: editorPointerTime(event)}, moving);
    });
    for (const end of ['pointerup', 'pointercancel', 'lostpointercapture']) {
      handle.addEventListener(end, () => { editorState.dragging = null; });
    }
    handle.addEventListener('keydown', event => {
      const next = editorNudge(editorState.selection, which, event.key, event.shiftKey, editorState.seconds);
      if (next) { event.preventDefault(); setEditorSelection(next, which); }
    });
    const field = editorEl(`editor-${which}`);
    field.addEventListener('change', () => applyTypedClip(which));
    // Not every phone keyboard fires change on Return; apply it directly.
    field.addEventListener('keydown', event => {
      if (event.key === 'Enter') { event.preventDefault(); applyTypedClip(which); }
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
