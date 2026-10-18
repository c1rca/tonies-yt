"""Trim before adding a YouTube pick: time parsing, validation and the selection payload."""
from test_frontend_contracts import HTML, run_js


def test_clock_text_parses_to_seconds():
    run_js("""
assert.equal(parseClock('90'), 90);
assert.equal(parseClock('1:30'), 90);
assert.equal(parseClock(' 1:02:03 '), 3723);
assert.equal(parseClock('0:07.5'), 7.5);
assert.equal(parseClock('0:07,5'), 7.5, 'comma decimals, as typed in many locales');
assert.equal(parseClock(''), null);
for (const bad of ['1:', ':30', '1:60', 'abc', '-5', '1:2:3:4', '1e3']) assert.ok(Number.isNaN(parseClock(bad)), bad);
""")


def test_trim_window_is_checked_against_the_video_length():
    run_js("""
assert.deepEqual(readTrimWindow('0:05', '2:00', 150), {trim:{start:5, end:120}, error:''});
assert.deepEqual(readTrimWindow('', '', 150), {trim:null, error:''});
assert.deepEqual(readTrimWindow('0:10', '', 150), {trim:{start:10, end:null}, error:''});
assert.match(readTrimWindow('2:00', '1:00', 150).error, /End must be after the start/);
assert.match(readTrimWindow('3:00', '', 150).error, /Start must be before the end of the video/);
assert.match(readTrimWindow('x', '', 150).error, /Use minutes:seconds/);
assert.deepEqual(readTrimWindow('0:10', '9:00', 150), {trim:{start:10, end:null}, error:''},
  'an end past the video just keeps the rest');
assert.deepEqual(readTrimWindow('0:00', '2:30', 150), {trim:null, error:''}, 'the full video is no trim');
""")


def test_trimmed_pick_sends_the_trim_with_the_selection():
    run_js("""
const posts = [];
api = async (url, options) => { posts.push({url, body:JSON.parse(options.body)}); return {id:'job'}; };
refreshJobs = async () => {};
jobsCache = [{id:'job', status:'awaiting_selection', candidates:[{title:'Song', url:'u', duration:150}]}];
await pickCandidate('job', 0, {start:5, end:120});
assert.deepEqual(posts[0].body.trim, {start:5, end:120});
""")


def test_untrimmed_pick_sends_no_trim():
    run_js("""
const posts = [];
api = async (url, options) => { posts.push(JSON.parse(options.body)); return {id:'job'}; };
refreshJobs = async () => {};
jobsCache = [{id:'job', status:'awaiting_selection', candidates:[{title:'Song', url:'u', duration:150}]}];
await pickCandidate('job', 0);
assert.equal('trim' in posts[0], false);
""")


def test_each_result_offers_trim_next_to_add():
    run_js("""
const html = renderCandidateCard({id:'job', status:'awaiting_selection', candidates:[]},
  {title:'Song', url:'https://www.youtube.com/watch?v=AbCdEf_12-3', duration:150}, 0);
assert.match(html, /class="ghost candidate-trim"[^>]*data-job-id="job"[^>]*data-candidate-index="0"/);
assert.match(html, /aria-label="Trim Song before adding"/);
""")


def test_trim_dialog_is_a_native_modal_with_labelled_fields():
    html = HTML.read_text(encoding="utf-8")
    dialog = html[html.index('<dialog id="trim-dialog"'):html.index("</dialog>")]
    assert 'aria-labelledby="trim-title"' in dialog
    for field in ("trim-start", "trim-end"):
        assert f'<label for="{field}"' in dialog and f'id="{field}"' in dialog
    assert 'id="trim-error"' in dialog and 'role="alert"' in dialog
    assert 'method="dialog"' in dialog
    assert 'aria-describedby="trim-length"' in html[html.index('<dialog id="trim-dialog"'):]
    # Enter in a field must submit "Add trimmed": Cancel is not a submit button.
    assert '<button type="button" id="trim-cancel"' in dialog
    assert 'inputmode="decimal"' not in dialog, "phone decimal keypads have no colon"
