"""Pick several YouTube results (or a playlist) and add them in one go."""
from test_frontend_contracts import HTML, run_js

SETUP = """
const posts = [];
api = async (url, options) => { posts.push({url, body:JSON.parse(options.body)}); return {job_ids:['a','b']}; };
refreshJobs = async () => {};
toniesCache = [{edit_url:'tonie-a', name:'Blue'}];
const job = {id:'job', status:'awaiting_selection', playlist:true,
  candidates:[0,1,2,3].map(n => ({title:`Song ${n}`, url:`https://www.youtube.com/watch?v=Video00000${n}`, duration:60}))};
jobsCache = [job];
"""


def test_each_addable_result_has_a_selection_checkbox():
    run_js(SETUP + """
const html = renderCandidateCard(job, job.candidates[1], 1);
assert.match(html, /<input type="checkbox" class="candidate-pick" data-job-id="job" data-candidate-index="1"[^>]*aria-label="Select Song 1"/);
""")


def test_selection_bar_counts_and_names_the_destination():
    run_js(SETUP + """
toggleYoutubeSelection('job', 2, true);
toggleYoutubeSelection('job', 0, true);
assert.equal(document.getElementById('youtube-selection-bar').hidden, false);
assert.equal(document.getElementById('youtube-selection-count').textContent, '2 selected');
assert.match(document.getElementById('add-selected-youtube').getAttribute('aria-label'), /Add 2 videos to/);
toggleYoutubeSelection('job', 2, false);
toggleYoutubeSelection('job', 0, false);
assert.equal(document.getElementById('youtube-selection-bar').hidden, true);
""")


def test_adding_the_selection_sends_one_request_in_list_order():
    run_js(SETUP + """
toggleYoutubeSelection('job', 3, true);
toggleYoutubeSelection('job', 1, true);
const first = addSelectedYoutube();
await addSelectedYoutube();  // a second tap while the first is in flight does nothing
await first;
assert.equal(posts.length, 1);
assert.equal(posts[0].url, '/api/jobs/job/select-many');
assert.deepEqual(posts[0].body.indexes, [1, 3]);
assert.equal(posts[0].body.target_url, 'tonie-a');
assert.deepEqual(posts[0].body.position, getCandidateSelectionPayload(0).position, 'same placement as a single Add');
assert.equal(youtubeSelectionSize(), 0, 'the selection is consumed once');
""")


def test_a_refused_batch_keeps_nothing_selected_and_says_why():
    run_js(SETUP + """
const notices = [];
recordWorkspaceNotice = notice => { notices.push(notice); return 'n'; };
api = async () => { throw Error('HTTP 422 A chosen video is no longer in the results'); };
toggleYoutubeSelection('job', 0, true);
await addSelectedYoutube();
assert.equal(youtubeSelectionSize(), 0);
assert.match(notices.at(-1).error, /no longer in the results/);
""")


def test_select_all_picks_every_result_still_addable():
    run_js(SETUP + """
selectAllYoutube('job');
assert.equal(youtubeSelectionSize(), 4);
""")


def test_playlist_results_are_labelled_as_a_playlist():
    run_js(SETUP + """
assert.equal(youtubeResultsLabel(job), 'Playlist · 4 videos');
assert.equal(youtubeResultsLabel({...job, candidates:Array(50).fill(job.candidates[0])}), 'Playlist · first 50 videos');
assert.equal(youtubeResultsLabel({...job, playlist:false}), null);
""")


def test_selection_bar_markup_sits_with_the_youtube_results():
    html = HTML.read_text(encoding="utf-8")
    youtube = html[html.index('id="source-youtube"'):html.index('id="source-local"')]
    for part in ('id="youtube-selection-bar"', 'id="youtube-selection-count"', 'id="add-selected-youtube"',
                 'id="select-all-youtube"', 'id="clear-youtube-selection"'):
        assert part in youtube
