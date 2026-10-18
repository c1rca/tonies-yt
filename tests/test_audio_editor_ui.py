"""Waveform audio editor: selection maths, keyboard, fetching YouTube audio, clip and add."""
from test_frontend_contracts import HTML, run_js

SETUP = """
const posts = [], uploads = [];
const editor = document.getElementById('audio-editor');
editor.showModal = function () { this.open = true; };
editor.close = function () { this.open = false; this.onclose?.(); };
api = async (url, options = {}) => {
  posts.push({url, method: options.method || 'GET', body: options.body ? JSON.parse(options.body) : null});
  if (url.includes('/fetch')) return {filename: 'Twinkle.mp3'};
  if (url.includes('/waveform')) return {seconds: 120, start: 0, end: 120, peaks: Array(200).fill(0.5)};
  if (url.includes('/clip')) return {ok: true, filename: 'Twinkle_0-05-0-15.mp3'};
  throw Error(`unexpected ${url}`);
};
uploadExisting = async (name, options) => { uploads.push({name, targetUrl: options?.targetUrl}); };
refreshFiles = async () => {};
toniesCache = [{edit_url: 'tonie-a', name: 'Blue'}];
jobsCache = [{id: 'job', status: 'awaiting_selection', candidates: [{title: 'Twinkle', url: 'u', duration: 120}]}];
initAudioEditor();
"""


def test_clock_text_parses_to_seconds():
    run_js("""
assert.equal(parseClock('90'), 90);
assert.equal(parseClock('1:30'), 90);
assert.equal(parseClock(' 1:02:03 '), 3723);
assert.equal(parseClock('0:07,5'), 7.5);
assert.equal(parseClock(''), null);
for (const bad of ['1:', ':30', '1:60', 'abc', '-5', '1:2:3:4', '1e3']) assert.ok(Number.isNaN(parseClock(bad)), bad);
""")


def test_selection_is_kept_inside_the_track_with_a_minimum_length():
    run_js("""
assert.deepEqual(editorClamp({start: -5, end: 500}, 120), {start: 0, end: 120});
assert.deepEqual(editorClamp({start: 50, end: 50.1}, 120, 'end'), {start: 50, end: 50.5});
assert.deepEqual(editorClamp({start: 119.9, end: 120}, 120, 'start'), {start: 119.5, end: 120});
assert.deepEqual(editorClamp({start: 0, end: 153.887}, 153.887), {start: 0, end: 153.887}, 'never past the end');
""")


def test_positions_map_between_pixels_and_time_in_the_visible_window():
    run_js("""
const view = {start: 30, end: 90};
assert.equal(editorTimeAt(0.5, view), 60);
assert.equal(editorFraction(45, view), 0.25);
assert.equal(editorTimeAt(1.4, view), 90, 'clamped to the window');
""")


def test_arrow_keys_nudge_a_handle():
    run_js("""
assert.deepEqual(editorNudge({start: 10, end: 60}, 'start', 'ArrowRight', false, 120), {start: 11, end: 60});
assert.deepEqual(editorNudge({start: 10, end: 60}, 'end', 'ArrowLeft', true, 120), {start: 10, end: 50});
assert.deepEqual(editorNudge({start: 10, end: 60}, 'start', 'Home', false, 120), {start: 0, end: 60});
assert.deepEqual(editorNudge({start: 10, end: 60}, 'end', 'End', false, 120), {start: 10, end: 120});
assert.equal(editorNudge({start: 10, end: 60}, 'end', 'a', false, 120), null);
""")


def test_typed_times_are_checked_against_the_track():
    run_js("""
assert.deepEqual(readClipWindow('0:05', '0:15', 120), {start: 5, end: 15, error: ''});
assert.deepEqual(readClipWindow('', '', 120), {start: 0, end: 120, error: ''});
assert.match(readClipWindow('1:00', '0:30', 120).error, /End must be after the start/);
assert.match(readClipWindow('3:00', '', 120).error, /Start must be before the end/);
assert.match(readClipWindow('x', '', 120).error, /minutes:seconds/);
assert.deepEqual(readClipWindow('0:10', '9:00', 120), {start: 10, end: 120, error: ''});
""")


def test_editing_a_youtube_result_fetches_it_then_loads_the_waveform():
    run_js(SETUP + """
await openAudioEditorForCandidate('job', 0);
assert.equal(editor.open, true);
assert.equal(posts[0].url, '/api/jobs/job/fetch');
assert.deepEqual(posts[0].body, {index: 0});
assert.match(posts[1].url, /^\\/api\\/files\\/Twinkle\\.mp3\\/waveform\\?points=\\d+$/);
assert.equal(document.getElementById('editor-audio').src, '/api/files/Twinkle.mp3?inline=true');
assert.deepEqual(editorState.selection, {start: 0, end: 120});
assert.equal(document.getElementById('editor-add').textContent, 'Add to Blue');
""")


def test_adding_a_clip_saves_it_then_adds_that_file_to_the_tonie():
    run_js(SETUP + """
await openAudioEditorForFile('Twinkle.mp3');
setEditorSelection({start: 5, end: 15});
await editorAddToTonie();
const clip = posts.find(p => p.url.endsWith('/clip'));
assert.equal(clip.url, '/api/files/Twinkle.mp3/clip');
assert.deepEqual(clip.body, {start: 5, end: 15});
assert.deepEqual(uploads, [{name: 'Twinkle_0-05-0-15.mp3', targetUrl: 'tonie-a'}]);
assert.equal(editor.open, false);
""")


def test_adding_the_whole_track_skips_the_clip():
    run_js(SETUP + """
await openAudioEditorForFile('Twinkle.mp3');
await editorAddToTonie();
assert.equal(posts.some(p => p.url.endsWith('/clip')), false);
assert.deepEqual(uploads, [{name: 'Twinkle.mp3', targetUrl: 'tonie-a'}]);
""")


def test_saving_a_clip_keeps_the_original_and_adds_nothing():
    run_js(SETUP + """
await openAudioEditorForFile('Twinkle.mp3');
setEditorSelection({start: 5, end: 15});
await editorSaveClip();
assert.equal(posts.filter(p => p.url.endsWith('/clip')).length, 1);
assert.deepEqual(uploads, []);
""")


def test_a_second_tap_while_saving_does_nothing():
    run_js(SETUP + """
await openAudioEditorForFile('Twinkle.mp3');
setEditorSelection({start: 5, end: 15});
const first = editorAddToTonie();
await editorAddToTonie();
await first;
assert.equal(posts.filter(p => p.url.endsWith('/clip')).length, 1);
assert.equal(uploads.length, 1);
""")


def test_closing_the_editor_while_fetching_leaves_it_closed():
    run_js(SETUP + """
let release;
const fetched = new Promise(resolve => { release = resolve; });
const realApi = api;
api = async (url, options) => url.includes('/fetch') ? (await fetched, {filename: 'Twinkle.mp3'}) : realApi(url, options);
const opening = openAudioEditorForCandidate('job', 0);
editor.close();
release();
await opening;
assert.equal(editor.open, false);
assert.equal(editorState.ready, false);
assert.equal(document.getElementById('editor-audio').getAttribute('src'), null);
assert.equal(posts.some(p => p.url.includes('/waveform')), false);
""")


def test_a_failed_fetch_says_so_and_offers_no_actions():
    run_js(SETUP + """
api = async () => { throw Error('HTTP 502 Could not download this video; try again'); };
await openAudioEditorForCandidate('job', 0);
assert.match(document.getElementById('editor-error').textContent, /Could not download/);
assert.equal(document.getElementById('editor-add').disabled, true);
assert.equal(document.getElementById('editor-save').disabled, true);
""")


def test_results_and_local_files_offer_edit():
    run_js(SETUP + """
const card = renderCandidateCard(jobsCache[0], jobsCache[0].candidates[0], 0);
assert.match(card, /class="ghost candidate-edit"[^>]*data-job-id="job"[^>]*data-candidate-index="0"/);
assert.match(card, /aria-label="Edit and clip Twinkle"/);
""")
    html = HTML.read_text(encoding="utf-8")
    render = html[html.index("function renderFiles()"):html.index("function getResultsRenderState")]
    assert 'class="ghost local-edit-btn"' in render and "openAudioEditorForFile(name)" in render


def test_editor_markup_is_an_accessible_native_dialog():
    html = HTML.read_text(encoding="utf-8")
    dialog = html[html.index('<dialog id="audio-editor"'):html.index("</dialog>", html.index('<dialog id="audio-editor"'))]
    assert 'aria-labelledby="editor-title"' in dialog and 'aria-describedby="editor-help"' in dialog
    for handle in ("editor-handle-start", "editor-handle-end"):
        assert f'id="{handle}"' in dialog
    assert dialog.count('role="slider"') == 2 and dialog.count('tabindex="0"') >= 2
    for field in ("editor-start", "editor-end"):
        assert f'<label for="{field}"' in dialog and f'id="{field}"' in dialog
    assert 'id="editor-error"' in dialog and 'role="alert"' in dialog
    assert '<button type="button" id="editor-cancel"' in dialog, "Enter must not cancel"
    assert 'inputmode="decimal"' not in dialog
    assert 'id="trim-dialog"' not in html and "workspace-trim.js" not in html
