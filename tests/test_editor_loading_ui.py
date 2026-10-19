"""Deferred editor loading: honest stages, immediate cancel, stale work and fast readiness."""
from test_audio_editor_ui import SETUP
from test_frontend_contracts import run_js


def test_loading_is_indeterminate_and_changes_stage_without_waiting_for_library_refresh():
    run_js(SETUP + """
let fetched, outlined, refreshed;
const realApi = api;
api = async (url, options) => {
  if (url.includes('/fetch')) return await new Promise(r => { fetched = r; });
  if (url.includes('/waveform')) return await new Promise(r => { outlined = r; });
  return realApi(url, options);
};
refreshFiles = () => new Promise(r => { refreshed = r; });
const opening = openAudioEditorForCandidate('job', 0);
const wave = document.getElementById('editor-wave');
assert.equal(wave.getAttribute('role'), 'progressbar');
assert.equal(wave.getAttribute('aria-valuenow'), null, 'no invented percentage');
assert.match(wave.getAttribute('aria-label'), /YouTube/);
assert.equal(document.getElementById('editor-cancel').disabled, false);
fetched({filename:'Twinkle.mp3'});
for (let i=0; i<8; i++) await Promise.resolve();
assert.equal(typeof outlined, 'function', 'the library listing must not block decoding');
assert.match(wave.getAttribute('aria-label'), /waveform/);
outlined({seconds:120, start:0, end:120, peaks:[0.4], levels:[0.2]});
await opening;
assert.equal(editorState.ready, true);
assert.equal(wave.getAttribute('role'), null);
assert.equal(wave.getAttribute('aria-busy'), 'false');
refreshed?.();
""")


def test_old_fetch_failure_does_not_replace_reopened_editor_status():
    run_js(SETUP + """
let rejectOld;
const realApi = api;
api = async (url, options) => url.includes('/fetch') ? await new Promise((_, reject) => { rejectOld=reject; }) : realApi(url, options);
const old = openAudioEditorForCandidate('job', 0);
editor.close();
await openAudioEditorForFile('Other.mp3');
rejectOld(Error('old download failed'));
await old;
assert.equal(editorState.filename, 'Other.mp3');
assert.equal(document.getElementById('editor-error').textContent, '');
assert.equal(document.getElementById('editor-status').textContent, '');
""")


def test_cancel_and_reopen_drops_old_waveform_errors_and_resets_playback_and_loading():
    run_js(SETUP + """
let rejectOld;
api = () => new Promise((_, reject) => { rejectOld=reject; });
const old = openAudioEditorForFile('Old.mp3');
editor.close();
const next = showEditor('Next');
setEditorStatus('New session');
rejectOld(Error('old waveform failed'));
await old;
assert.equal(editorState.session, next);
assert.equal(document.getElementById('editor-error').textContent, '');
assert.equal(document.getElementById('editor-status').textContent, 'New session');
assert.equal(document.getElementById('editor-audio').src, '');
assert.equal(editorState.seconds, 0);
assert.deepEqual(editorState.selection, {start:0, end:0});
assert.equal(document.getElementById('editor-wave').getAttribute('role'), null);
""")


def test_failed_waveform_clears_progress_and_keeps_cancel_available():
    run_js(SETUP + """
api = async () => { throw Error('Cannot decode'); };
await openAudioEditorForFile('Bad.mp3');
assert.match(document.getElementById('editor-error').textContent, /Cannot decode/);
assert.equal(document.getElementById('editor-wave').getAttribute('role'), null);
assert.equal(document.getElementById('editor-cancel').disabled, false);
assert.equal(editorState.ready, false);
""")


def test_library_refresh_failure_does_not_break_ready_editor():
    run_js(SETUP + """
refreshFiles = async () => { throw Error('listing unavailable'); };
await openAudioEditorForCandidate('job', 0);
assert.equal(editorState.ready, true);
assert.equal(document.getElementById('editor-error').textContent, '');
""")


def test_a_late_success_cannot_retarget_reopened_editor_or_replace_its_levels():
    run_js(SETUP + """
let releaseOld;
const realApi=api;
api=async (url, options) => url.includes('/Old.mp3/waveform')
  ? await new Promise(r => { releaseOld=r; }) : realApi(url,options);
const old=openAudioEditorForFile('Old.mp3');
editor.close();
await openAudioEditorForFile('Other.mp3');
releaseOld({seconds:1,start:0,end:1,peaks:[1],levels:[1]});
await old;
assert.equal(editorState.filename,'Other.mp3');
assert.equal(editorState.seconds,120);
assert.equal(editorState.levels,undefined,'legacy replacement data still owns the editor');
assert.equal(document.getElementById('editor-audio').src,'/api/files/Other.mp3?inline=true');
assert.equal(document.getElementById('editor-wave').getAttribute('aria-busy'),'false');
assert.equal(editor.open,true);
""")
