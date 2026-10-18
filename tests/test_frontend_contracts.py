"""Frontend invariants, executed without a browser or any live API calls."""
from pathlib import Path
import subprocess

import pytest


HTML = Path(__file__).parents[1] / "web" / "index.html"


def run_js(body):
    # Match the real page load order. Session/auth and runtime boot are network boundaries;
    # every local feature module is evaluated, never replaced with test-only behavior.
    from html.parser import HTMLParser

    class Scripts(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts = []
            self.inline = False

        def handle_starttag(self, tag, attrs):
            if tag != 'script':
                return
            src = dict(attrs).get('src')
            if src:
                if not src.startswith('/static/assets/'):
                    raise AssertionError(f'Unexpected script origin: {src}')
                asset = src.split('?', 1)[0].removeprefix('/static/')
                if asset != 'assets/session.js':
                    self.parts.append((HTML.parent / asset).read_text(encoding='utf-8'))
            else:
                self.inline = True

        def handle_data(self, data):
            if self.inline:
                self.parts.append(data.split('\n      loadResultsClearedState();', 1)[0])

        def handle_endtag(self, tag):
            if tag == 'script':
                self.inline = False

    scripts = Scripts()
    scripts.feed(HTML.read_text(encoding='utf-8'))
    source = '\n'.join(scripts.parts)
    harness = r"""
const assert = require('node:assert/strict');
const timers = new Map(); let timerId = 0;
global.setTimeout = (fn, ms) => { timers.set(++timerId, {fn, ms}); return timerId; };
global.clearTimeout = (id) => timers.delete(id);
global.setInterval = () => { throw Error('Unexpected extra interval'); };
global.clearInterval = () => {};
const elements = new Map();
function element() { const listeners = new Map(), attrs = new Map(); return {
  value:'', textContent:'', innerHTML:'', style:{}, dataset:{}, hidden:true,
  paused:true, currentTime:0, src:'', loads:0, plays:0,
  classList:{add(){}, remove(){}, toggle(){}, contains(){return false;}},
  addEventListener(type, fn){ if (!listeners.has(type)) listeners.set(type, []); listeners.get(type).push(fn); },
  dispatchEvent(event){ for (const fn of listeners.get(event.type) || []) fn(event); },
  setAttribute(k,v){ attrs.set(k,String(v)); }, getAttribute(k){return attrs.get(k) ?? null;},
  removeAttribute(k){ attrs.delete(k); if (k === 'src') this.src = ''; },
  play(){ this.plays++; this.paused = false; this.dispatchEvent({type:'play'}); return Promise.resolve(); },
  pause(){ this.paused = true; this.dispatchEvent({type:'pause'}); },
  load(){ this.loads++; this.currentTime = 0; },
  focus(){document.activeElement = this;}, select(){},
  querySelector(){return null;}, querySelectorAll(){return [];},
  getBoundingClientRect(){return {width:100};}}; }
global.document = { getElementById(id) { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); },
  documentElement:element(), addEventListener(){}, querySelector(){return null;} };
global.window = {innerWidth:1200, innerHeight:900};
global.localStorage = {getItem(){return null;}, setItem(){}};
global.fetch = () => {throw Error('Live network forbidden');};
"""
    stubs = """
const actualRenderToniesContent = renderToniesContent;
const actualRenderTonies = renderTonies, actualAskConfirm = askConfirm;
const actualRenderFiles = renderFiles, actualRefreshFiles = refreshFiles;
renderTonies = renderToniesContent = renderJobs = renderFiles = renderToniesEditUI = () => {};
showToast = () => {};
refreshFiles = async () => {};
askConfirm = async () => true;
selectedToniesUrl = 'tonie-a';
const uploadLaneBusy = () => toniesUploadReconciliations.length > 0 || toniesUploadReconciliationRunning || syncingResultJobIds.size > 0;
// Fake server action queue: records POST /api/tonies-actions and answers "queued".
let queuedSeq = 0; const actionPosts = [];
function queueResponse(options) {
  const body = JSON.parse(options.body); actionPosts.push(body);
  return {actions: body.actions.map(a => ({id:`act-${++queuedSeq}`, status:'queued', type:a.type,
    params:Object.fromEntries(Object.entries(a).filter(([k]) => k !== 'type'))}))};
}
function finishActions(url, ids, content, status='done', error=null) {
  handleToniesActionsEvent({target_url:url, content, actions:ids.map(id => ({id, status, error}))});
}
"""
    script = harness + source + stubs + "\n(async () => {\n" + body + "\n})().then(() => { console.log('__frontend_contract_completed__'); }).catch(e => { console.error(e); process.exitCode = 1; });"
    result = subprocess.run(["node"], input=script, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr or result.stdout
    assert "__frontend_contract_completed__" in result.stdout, "Contract did not reach its final assertion (unsettled promise)"
    return result.stdout


def test_second_candidate_click_is_ignored_while_selection_is_pending():
    run_js("""
selectingCandidateJobIds.add('job');
api = async () => {throw Error('Must not submit again');};
await pickCandidate('job', 1);
""")


def test_local_file_actions_do_not_embed_filename_in_inline_javascript():
    html = HTML.read_text(encoding="utf-8")
    render = html[html.index("function renderFiles()"):html.index("function getResultsRenderState")]
    assert "onclick=\"uploadExisting('" not in render
    assert "onclick=\"deleteFile('" not in render


def test_local_player_is_one_persistent_native_control_without_autoplay():
    from html.parser import HTMLParser

    class PlayerParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.audio = []

        def handle_starttag(self, tag, attrs):
            if tag == 'audio':
                self.audio.append(dict(attrs))

    html = HTML.read_text(encoding='utf-8')
    parser = PlayerParser()
    parser.feed(html)
    # One persistent dock player, plus the waveform editor's own player inside its dialog.
    assert [a['id'] for a in parser.audio] == ['local-audio', 'editor-audio']
    assert all('autoplay' not in a and 'src' not in a for a in parser.audio)
    audio = parser.audio[0]
    assert 'controls' in audio and audio['preload'] == 'metadata'
    assert 'autoplay' not in audio and 'src' not in audio
    # The dock is outside all view/source panes, rather than above only Local files.
    assert 'aria-label="Local preview"' in html
    assert 'id="local-player"' not in html[html.index('function renderFiles()'):html.index('function getResultsRenderState')]


def test_local_playback_toggles_switches_and_survives_file_renders_and_filtering():
    run_js(r"""
const audio = document.getElementById('local-audio');
const panel = document.getElementById('local-player');
const filename = `Kid's "song" & <b> 雪 #?.mp3`;
filesCache = [{name:filename}, {name:'other.mp3'}];
const rows = filesCache.map(f => {
  const row = element(), play = element(), upload = element(), trash = element();
  row.setAttribute('data-file-name', f.name);
  row.querySelector = s => ({'.local-play-btn':play, '.local-upload-btn':upload, '.trash-btn':trash}[s]);
  return {row, play, upload, trash};
});
filesEl.querySelectorAll = () => rows.map(r => r.row);
renderFiles = actualRenderFiles;
actualRenderFiles();
assert.match(filesEl.innerHTML, /local-play-btn/);
assert.doesNotMatch(filesEl.innerHTML, /onclick=/);
assert.match(filesEl.innerHTML, /aria-label="Play /);
assert.equal(audio.plays, 0);
let uploads = 0, deletes = 0;
uploadExisting = () => {uploads++;}; deleteFile = () => {deletes++;};
rows[0].play.dispatchEvent({type:'click'});
await Promise.resolve();
assert.equal(audio.src, '/api/files/' + encodeURIComponent(filename) + '?inline=true');
assert.equal(document.getElementById('local-player-name').textContent, filename);
assert.equal(panel.hidden, false);
assert.equal(audio.paused, false);
assert.equal(rows[0].play.textContent, 'Pause');
assert.equal(rows[0].play.getAttribute('aria-label'), 'Pause ' + filename);
assert.equal(uploads, 0); assert.equal(deletes, 0);
audio.currentTime = 37;
const loads = audio.loads, plays = audio.plays;
actualRenderFiles(); // Same routine rendering used by upload/queue updates.
fileFilterEl.value = 'no matches'; actualRenderFiles();
assert.equal(audio.currentTime, 37);
assert.equal(audio.loads, loads); assert.equal(audio.plays, plays);
assert.equal(audio.paused, false);
await toggleLocalPlayback(filename);
assert.equal(audio.paused, true);
assert.equal(rows[0].play.textContent, 'Play');
await toggleLocalPlayback(filename);
assert.equal(audio.currentTime, 37);
assert.equal(audio.paused, false);
await toggleLocalPlayback('other.mp3');
assert.equal(audio.src, '/api/files/other.mp3?inline=true');
assert.equal(audio.currentTime, 0);
assert.equal(rows[0].play.textContent, 'Play');
assert.equal(rows[1].play.textContent, 'Pause');
audio.pause(); // Native controls must update row labels too.
assert.equal(rows[1].play.textContent, 'Play');
await audio.play();
assert.equal(rows[1].play.textContent, 'Pause');
audio.paused = true; audio.dispatchEvent({type:'ended'});
assert.equal(rows[1].play.textContent, 'Play');
""")


def test_local_playback_closes_resets_on_removal_and_stops_before_lock():
    run_js("""
renderFiles = actualRenderFiles; refreshFiles = actualRefreshFiles;
const audio = document.getElementById('local-audio');
const panel = document.getElementById('local-player');
filesCache = [{name:'a.mp3'}, {name:'b.mp3'}];
await toggleLocalPlayback('a.mp3'); audio.currentTime = 12;
api = async () => [{name:'a.mp3'}]; // Removing another file does not stop this one.
await refreshFiles(); assert.equal(audio.currentTime, 12); assert.equal(audio.paused, false);
api = async () => []; await refreshFiles(); // Delete/clear/missing-file refresh.
assert.equal(audio.paused, true); assert.equal(audio.src, ''); assert.equal(audio.currentTime, 0);
assert.equal(panel.hidden, true);
filesCache = [{name:'a.mp3'}];
await toggleLocalPlayback('a.mp3');
document.getElementById('local-player-close').dispatchEvent({type:'click'});
assert.equal(audio.paused, true); assert.equal(audio.src, ''); assert.equal(panel.hidden, true);
await toggleLocalPlayback('a.mp3');
window.location = {};
api = async path => {
  assert.equal(path, '/api/setup/lock');
  assert.equal(audio.paused, true); assert.equal(audio.src, '');
};
await lockApp(); assert.equal(window.location.href, '/login?locked=1');
const plays = audio.plays;
await toggleLocalPlayback('not-in-library.mp3');
assert.equal(audio.plays, plays);
""")


def test_local_file_delete_stops_current_audio_before_refresh_but_preserves_undo_and_other_files():
    run_js("""
const audio = document.getElementById('local-audio');
filesCache = [{name:'a.mp3'}, {name:'b.mp3'}];
await toggleLocalPlayback('a.mp3');
const remove = deleteFile; // Keep production deletion, including its Undo window.
let undo;
showToast = (message, kind, duration, action) => { if (action) undo = action.onClick; };
fetch = async (path, options) => {
  assert.equal(options.method, 'DELETE');
  return {ok:true};
};
refreshFiles = async () => {};
const other = remove('b.mp3'); await Promise.resolve();
let timer = [...timers.values()].find(t => t.ms === 2400);
timer.fn(); await other;
assert.equal(audio.paused, false);
const canceled = remove('a.mp3'); await Promise.resolve();
undo();
timer = [...timers.values()].filter(t => t.ms === 2400).at(-1);
timer.fn(); await canceled;
assert.equal(audio.paused, false);
refreshFiles = async () => { throw Error('Must not refresh after a refused delete'); };
fetch = async () => ({ok:false});
const refused = remove('a.mp3'); await Promise.resolve();
timer = [...timers.values()].filter(t => t.ms === 2400).at(-1);
timer.fn(); await refused;
assert.equal(audio.paused, false);
fetch = async () => ({ok:true});
refreshFiles = async () => { assert.equal(audio.paused, true); assert.equal(audio.src, ''); };
const current = remove('a.mp3'); await Promise.resolve();
timer = [...timers.values()].filter(t => t.ms === 2400).at(-1);
timer.fn(); await current;
assert.equal(document.getElementById('local-player').hidden, true);
""")


def test_local_player_handles_play_refusal_without_stale_switch_errors():
    run_js("""
filesCache = [{name:'a.mp3'}, {name:'b.mp3'}];
const audio = document.getElementById('local-audio');
const status = document.getElementById('local-player-status');
let rejectFirst;
audio.play = () => new Promise((resolve, reject) => {rejectFirst = reject;});
const first = toggleLocalPlayback('a.mp3');
audio.play = () => Promise.resolve();
await toggleLocalPlayback('b.mp3');
rejectFirst(Error('Aborted by source change')); await first;
assert.equal(status.textContent, '');
audio.play = () => Promise.reject(Error('Not allowed'));
await toggleLocalPlayback('b.mp3');
assert.match(status.textContent, /Could not play/);
stopLocalPlayback();
assert.equal(status.textContent, '');
""")


def test_rename_uses_cloud_identity_and_preserves_drafts_on_rejection():
    run_js("""
toniesContentCache = {chapters:[{chapter_id:'cloud-a',title:'Same'}, {chapter_id:'cloud-b',title:'Same'}]};
beginToniesEditMode();
toniesNameDrafts['id:cloud-b'] = 'New name';
const calls = [];
api = async (path, options, timeout) => {
  calls.push({path, payload:JSON.parse(options.body), timeout});
  throw Error('Refused by API');
};
await saveToniesNameEdits();
assert.equal(calls.length, 1);
assert.equal(calls[0].path, '/api/tonies-actions');
assert.deepEqual(calls[0].payload, {target_url:'tonie-a',actions:[{type:'rename',chapter_id:'cloud-b',title:'New name'}]});
assert.ok(calls[0].timeout >= 15000);
assert.equal(toniesEditMode, true);
assert.equal(toniesNameDrafts['id:cloud-b'], 'New name');
assert.equal(toniesContentCache.chapters[1].title, 'Same');
assert.equal(toniesQueuedActions.size, 0);
assert.match(toniesStatusEl.innerHTML, /Refused by API/);
""")


def test_renames_go_in_one_request_show_at_once_and_settle_on_the_server_result():
    run_js("""
toniesContentCache = {chapters:[{chapter_id:'a',title:'Old A [oc:aaaaaa]'}, {chapter_id:'b',title:'Old B'}]};
setCachedToniesContent('tonie-a', toniesContentCache);
beginToniesEditMode();
toniesNameDrafts['id:a'] = 'New A'; toniesNameDrafts['id:b'] = 'New B';
const paths=[];
api = async (path, options) => { paths.push(path); return queueResponse(options); };
await saveToniesNameEdits();
assert.deepEqual(paths, ['/api/tonies-actions']);
assert.deepEqual(actionPosts[0].actions.map(a => [a.chapter_id, a.title]), [['a','New A'],['b','New B']]);
assert.equal(toniesEditMode,false);
// Shown immediately, keeping the hidden track token.
assert.deepEqual(toniesContentCache.chapters.map(c => c.title), ['New A [oc:aaaaaa]','New B']);
assert.match(toniesStatusEl.innerHTML, /Saving 2 changes/);
finishActions('tonie-a', ['act-1','act-2'], {chapters:[{chapter_id:'a',title:'New A [oc:aaaaaa]'},{chapter_id:'b',title:'New B'}],free_minutes:79});
assert.equal(toniesContentCache.free_minutes,79);
assert.equal(toniesQueuedActions.size,0);
assert.doesNotMatch(toniesStatusEl.innerHTML, /Saving/);
""")


def test_reorder_queues_a_move_by_cloud_id_and_anchor_and_shows_it_at_once():
    run_js("""
toniesContentCache = {chapters:[{chapter_id:' CLOUD/A ',title:'Same'}, {chapter_id:'b',title:'Same'}, {chapter_id:'c',title:'Same'}]};
setCachedToniesContent('tonie-a', toniesContentCache);
const paths=[];
api=async (path, options) => { paths.push(path); return queueResponse(options); };
await reorderToniesChapter(0,1);
await reorderToniesChapter(2,0);
assert.deepEqual(paths,['/api/tonies-actions','/api/tonies-actions']);
assert.deepEqual(actionPosts[0].actions,[{type:'move',chapter_id:' CLOUD/A ',position:{before:'c',index:1}}]);
assert.deepEqual(actionPosts[1].actions,[{type:'move',chapter_id:'c',position:{edge:'start'}}]);
assert.deepEqual(toniesContentCache.chapters.map(c => c.chapter_id),['c','b',' CLOUD/A ']);
finishActions('tonie-a',['act-1','act-2'],{chapters:[{chapter_id:'c'},{chapter_id:'b'},{chapter_id:' CLOUD/A ',title:'Canonical'}],free_minutes:73});
assert.equal(toniesContentCache.chapters[2].title,'Canonical');
assert.equal(toniesContentCache.free_minutes,73);
""")


def test_empty_canonical_refresh_replaces_and_persists_old_content_without_retries():
    run_js("""
toniesContentCache = {chapters:[{chapter_id:'old',title:'Old'}]};
setCachedToniesContent('tonie-a',toniesContentCache);
let calls=0;
api=async () => {calls++; return {chapters:[], free_minutes:90, processing:false};};
await refreshToniesContent({force:true});
assert.equal(calls,1);
assert.deepEqual(toniesContentCache.chapters,[]);
assert.deepEqual(getCachedToniesContent('tonie-a').chapters,[]);
assert.equal(timers.size,0);
assert.equal(toniesApplying,false);
""")


def test_delete_last_chapter_disappears_at_once_and_accepts_the_empty_result():
    run_js("""
toniesContentCache={chapters:[{chapter_id:'last',title:'Last'}]};
setCachedToniesContent('tonie-a', toniesContentCache);
let calls=0;
api=async (path,options) => {
  calls++; assert.equal(path,'/api/tonies-actions');
  assert.deepEqual(JSON.parse(options.body).actions,[{type:'delete',chapter_id:'last'}]);
  return queueResponse(options);
};
await enqueueToniesDelete(0);
assert.equal(calls,1);
assert.deepEqual(toniesContentCache.chapters,[]);
finishActions('tonie-a',['act-1'],{chapters:[],free_minutes:90,processing:false});
assert.deepEqual(toniesContentCache.chapters,[]);
assert.deepEqual(getCachedToniesContent('tonie-a').chapters,[]);
assert.equal(toniesQueuedActions.size,0);
assert.equal(toniesApplying,false);
""")


def test_rapid_deletes_queue_without_waiting_and_follow_cloud_ids():
    run_js("""
toniesContentCache={chapters:[{chapter_id:'a',title:'Same'},{chapter_id:'b',title:'Same'},{chapter_id:'c',title:'Same'}]};
setCachedToniesContent('tonie-a', toniesContentCache);
api=async (path,options) => queueResponse(options);
await enqueueToniesDelete(0);   // 'a'; the list re-renders without it straight away
await enqueueToniesDelete(1);   // the second row is now 'c'
assert.deepEqual(actionPosts.map(p => p.actions[0].chapter_id),['a','c']);
assert.deepEqual(toniesContentCache.chapters.map(c => c.chapter_id),['b']);
finishActions('tonie-a',['act-1','act-2'],{chapters:[{chapter_id:'b',title:'Same'}],free_minutes:89});
assert.deepEqual(toniesContentCache.chapters.map(c => c.chapter_id),['b']);
""")


def test_upload_finalization_uses_returned_id_once_and_never_guesses_by_name():
    run_js("""
startPendingToniesUpload('Same.mp3','tonie-a'); pendingToniesUpload.jobId='job';
activeUploadJobIds.add('job');
pendingDropUploadIntent={jobId:'job',targetUrl:'tonie-a',targetIndex:0};
const job={id:'job',status:'done',target_url:'tonie-a',uploaded_chapter_id:'uploaded',
  tonies_content:{chapters:[{chapter_id:'old',title:'Same'},{chapter_id:'uploaded',title:'Same'}],free_minutes:82}};
let calls=0, reads=0;
api=async (path,options) => {
  if(path==='/api/tonies-content') {
    reads++; assert.deepEqual(JSON.parse(options.body),{target_url:'tonie-a'});
    return {chapters:[{chapter_id:'old',title:'Same'},{chapter_id:'uploaded',title:'Same'}],free_minutes:82};
  }
  calls++; assert.equal(path,'/api/tonies-content/reorder');
  assert.deepEqual(JSON.parse(options.body),{target_url:'tonie-a',from_index:1,to_index:0,chapter_id:'uploaded'});
  return {chapters:[{chapter_id:'uploaded',title:'Same'},{chapter_id:'old',title:'Same'}],free_minutes:82};
};
await Promise.all([finalizePendingToniesUpload(job,true),finalizePendingToniesUpload(job,true)]);
handleTrackedJobTransitions(job,'uploading');
await new Promise(resolve => setImmediate(resolve));
assert.equal(calls,1);
assert.equal(reads,1);
assert.equal(toniesContentCache.chapters[0].chapter_id,'uploaded');
assert.equal(pendingToniesUpload,null);
assert.equal(activeUploadJobIds.size,0);
assert.equal(timers.size,1); // NEW highlight only, no confirmation/refetch timers
""")


def test_upload_missing_returned_id_never_reorders_a_same_title_record():
    run_js("""
startPendingToniesUpload('Same.mp3','tonie-a'); pendingToniesUpload.jobId='job';
pendingDropUploadIntent={jobId:'job',targetUrl:'tonie-a',targetIndex:0};
let reads=0;
api=async path => {
  assert.equal(path,'/api/tonies-content','Must not guess an uploaded identity'); reads++;
  return {chapters:[{chapter_id:'other',title:'Same'}],processing:false};
};
await finalizePendingToniesUpload({id:'job',target_url:'tonie-a',status:'done',
  tonies_content:{chapters:[{chapter_id:'other',title:'Same'}],processing:false}},true);
assert.equal(toniesContentCache.chapters[0].chapter_id,'other');
assert.equal(reads,1);
assert.equal(toniesUploadSyncing,false);
assert.equal(pendingDropUploadIntent,null);
""")


def test_processing_polls_only_true_api_processing_and_stops_at_bound():
    run_js("""
api=async () => ({chapters:[{chapter_id:'a',title:'Track',processing:true}],processing:true,free_minutes:89});
await refreshToniesContent({force:true});
let polls=0;
while (timers.size && polls<100) {
  const [id,timer]=timers.entries().next().value; timers.delete(id);
  await timer.fn(); polls++;
}
assert.ok(polls>0 && polls<=30, `unbounded processing polling: ${polls}`);
assert.equal(timers.size,0);
api=async () => ({chapters:[],processing:false,free_minutes:90});
await refreshToniesContent({force:true});
assert.equal(timers.size,0);
assert.equal(toniesApplying,false);
""")


def test_stale_refresh_cannot_bring_back_a_queued_delete():
    run_js("""
toniesContentCache={chapters:[{chapter_id:'a',title:'Old'}]};
setCachedToniesContent('tonie-a', toniesContentCache);
let resolveRead;
api=async (path,options) => {
  if (path==='/api/tonies-content') return await new Promise(resolve => {resolveRead=resolve;});
  return queueResponse(options);
};
const reading=refreshToniesContent({force:true});
await enqueueToniesDelete(0);
resolveRead({chapters:[{chapter_id:'a',title:'Stale'}],free_minutes:89});
await reading;
assert.deepEqual(toniesContentCache.chapters,[]);
assert.equal(loadingToniesContent,false);
finishActions('tonie-a',['act-1'],{chapters:[],free_minutes:90});
assert.deepEqual(toniesContentCache.chapters,[]);
""")


def test_view_menu_preserves_reversed_native_index_conversion():
    run_js("""
toniesContentCache={chapters:[{chapter_id:'a'},{chapter_id:'b'},{chapter_id:'c'}]};
const calls=[]; reorderToniesChapter=async (from,to) => calls.push([from,to]);
toniesReverseView=true; openToniesMenu(1,100,100); await moveToniesFromMenu('top');
openToniesMenu(1,100,100); await moveToniesFromMenu('down');
toniesReverseView=false; openToniesMenu(1,100,100); await moveToniesFromMenu('top');
assert.deepEqual(calls,[[1,2],[1,0],[1,0]]);
""")


def test_startup_does_not_duplicate_selected_content_fetch_through_helpers():
    run_js("""
toniesCache=[]; const calls=[];
api=async (path,options) => {
  calls.push([path, options?.body ? JSON.parse(options.body).target_url : null]);
  if(path==='/api/creative-tonies') return [{edit_url:'tonie-a'},{edit_url:'tonie-b'}];
  return {chapters:[],free_minutes:90,processing:false};
};
await refreshTonies({force:true});
assert.deepEqual(calls.filter(c=>c[0]==='/api/tonies-content').map(c=>c[1]).sort(),['tonie-a','tonie-b']);
""")


def test_legacy_positioning_keeps_the_receipt_lane_busy_until_it_finishes():
    run_js("""
activeUploadJobIds.add('job');
jobsCache=[{id:'job',status:'done'}];
uploadIntentsByJobId.set('job',{targetUrl:'tonie-a',targetIndex:0});
let resolveReorder;
api=async path => {
  if(path==='/api/tonies-content') return {chapters:[{chapter_id:'old'},{chapter_id:'new'}]};
  assert.equal(path,'/api/tonies-content/reorder');
  return await new Promise(resolve => {resolveReorder=resolve;});
};
const finishing=finalizePendingToniesUpload({id:'job',target_url:'tonie-a',uploaded_chapter_id:'new',
  tonies_content:{chapters:[{chapter_id:'old'},{chapter_id:'new'}]}},true);
assert.equal(uploadLaneBusy(),true);
await new Promise(resolve=>setImmediate(resolve));
resolveReorder({chapters:[{chapter_id:'new'},{chapter_id:'old'}]});
await finishing;
assert.equal(uploadLaneBusy(),false);
""")


def test_upload_completion_after_selection_change_updates_only_original_target():
    run_js("""
selectedToniesUrl='tonie-b';
toniesContentCache={chapters:[{chapter_id:'untouched-b'}]};
uploadIntentsByJobId.set('job',{targetUrl:'tonie-a',targetIndex:0});
let calls=0;
api=async (path,options) => {
  calls++; assert.equal(JSON.parse(options.body).target_url,'tonie-a');
  if(path==='/api/tonies-content') return {chapters:[{chapter_id:'old-a'},{chapter_id:'new-a'}]};
  assert.equal(path,'/api/tonies-content/reorder');
  return {chapters:[{chapter_id:'new-a'},{chapter_id:'old-a'}],processing:false};
};
await finalizePendingToniesUpload({id:'job',target_url:'tonie-a',uploaded_chapter_id:'new-a',
  tonies_content:{chapters:[{chapter_id:'old-a'},{chapter_id:'new-a'}]}},true);
assert.equal(calls,2);
assert.equal(toniesContentCache.chapters[0].chapter_id,'untouched-b');
assert.equal(getCachedToniesContent('tonie-a').chapters[0].chapter_id,'new-a');
""")


def test_manual_refresh_failure_releases_busy_state_and_displays_api_error():
    run_js("""
api=async () => {throw Error('Account needs sign-in');};
await document.getElementById('refresh-tonies').onclick();
assert.equal(toniesApplying,false);
assert.match(toniesStatusEl.innerHTML,/Account needs sign-in/);
""")


def test_drafts_survive_canonical_refresh_and_render_under_their_cloud_ids():
    run_js("""
toniesContentCache={chapters:[{chapter_id:'a',title:'Same'},{chapter_id:'b',title:'Same'}]};
beginToniesEditMode(); toniesNameDrafts['id:b']='Draft "quoted"';
api=async () => ({chapters:[{chapter_id:'b',title:'Same'},{chapter_id:'a',title:'Same'}],free_minutes:88});
await refreshToniesContent({force:true});
assert.equal(toniesNameDrafts['id:b'],'Draft "quoted"');
assert.equal(toniesEditMode,true);
actualRenderToniesContent();
assert.match(toniesContentEl.innerHTML,/data-source-index="0" value="Draft &quot;quoted&quot;"/);
""")


def test_queued_upload_keeps_original_target_and_native_edge_intent():
    run_js("""
toniesContentCache={chapters:[{chapter_id:'old-a'},{chapter_id:'old-b'}]};
localUploadQueuePumping=true;  // another submission is in flight
await uploadExisting('Track.mp3',{targetIndex:2});
selectedToniesUrl='tonie-b';
assert.equal(queuedLocalUploads[0].options.targetUrl,'tonie-a');
assert.equal(queuedLocalUploads[0].options.targetEdge,'end');
""")


def test_upload_to_native_end_uses_final_inventory_length_for_position_not_identity():
    run_js("""
uploadIntentsByJobId.set('job',{targetUrl:'tonie-a',targetIndex:2,targetEdge:'end'});
let calls=0,reads=0;
api=async (path,options) => {
  if(path==='/api/tonies-content') {
    reads++; return {chapters:[{chapter_id:'new'},{chapter_id:'a'},{chapter_id:'b'},{chapter_id:'c'}]};
  }
  calls++; assert.equal(path,'/api/tonies-content/reorder'); const payload=JSON.parse(options.body);
  assert.equal(payload.chapter_id,'new'); assert.equal(payload.from_index,0); assert.equal(payload.to_index,3);
  return {chapters:[{chapter_id:'a'},{chapter_id:'b'},{chapter_id:'c'},{chapter_id:'new'}]};
};
await finalizePendingToniesUpload({id:'job',target_url:'tonie-a',uploaded_chapter_id:'new',
  tonies_content:{chapters:[{chapter_id:'new'},{chapter_id:'a'},{chapter_id:'b'}]}},true);
assert.equal(calls,1);
assert.equal(reads,1);
assert.equal(toniesContentCache.chapters[3].chapter_id,'new');
""")


def test_pending_rename_preserves_newer_human_edits_and_replacement_edit_session():
    run_js("""
toniesContentCache={chapters:[{chapter_id:'a',title:'Old'}]};
setCachedToniesContent('tonie-a', toniesContentCache);
beginToniesEditMode(); toniesNameDrafts['id:a']='Submitted';
let resolveSave, saveOptions;
api=async (path, options) => { saveOptions=options; return await new Promise(resolve => {resolveSave=resolve;}); };
const saving=saveToniesNameEdits();
toniesNameDrafts['id:a']='Newer draft';
resolveSave(queueResponse(saveOptions));
await saving;
assert.equal(toniesEditMode,true);
assert.equal(toniesNameDrafts['id:a'],'Newer draft');
assert.equal(toniesContentCache.chapters[0].title,'Submitted');
""")


def test_context_menu_move_keeps_clicked_cloud_identity_across_refresh():
    run_js("""
toniesContentCache={chapters:[{chapter_id:'a'},{chapter_id:'b'},{chapter_id:'c'}]};
openToniesMenu(0,100,100);
toniesContentCache={chapters:[{chapter_id:'b'},{chapter_id:'a'},{chapter_id:'c'}]};
const calls=[]; reorderToniesChapter=async (from,to) => calls.push([from,to]);
toniesReverseView=false;
await moveToniesFromMenu('bottom');
assert.deepEqual(calls,[[1,2]]);
""")


def test_selected_refresh_failure_preserves_content_and_is_not_retried():
    run_js("""
toniesContentCache={chapters:[{chapter_id:'a'}]};
let calls=0; api=async () => {calls++; throw Error('Unavailable');};
await refreshToniesContent({force:true});
assert.equal(calls,1); assert.equal(timers.size,0);
assert.equal(toniesContentCache.chapters[0].chapter_id,'a');
assert.equal(loadingToniesContent,false);
assert.match(toniesStatusEl.innerHTML,/Unavailable/);
""")


def test_cloud_processing_poll_stops_immediately_when_api_finishes():
    run_js("""
let calls=0;
api=async () => ({chapters:[{chapter_id:'a',title:'Track'}],processing:++calls===1,free_minutes:89});
await refreshToniesContent({force:true});
assert.equal(timers.size,1);
const [id,timer]=timers.entries().next().value; timers.delete(id); await timer.fn();
assert.equal(calls,2); assert.equal(timers.size,0);
assert.equal(toniesApplying,false);
""")


def test_refused_requests_are_not_retried_and_keep_the_library():
    run_js("""
toniesContentCache={chapters:[{chapter_id:'a',title:'Old'},{chapter_id:'b',title:'Other'}]};
setCachedToniesContent('tonie-a', toniesContentCache);
let calls=0; api=async () => {calls++; return {error:'Refused'};};
await reorderToniesChapter(0,1);
assert.equal(calls,1); assert.equal(toniesContentCache.chapters[0].chapter_id,'a');
await enqueueToniesDelete(0);
assert.equal(calls,2); assert.equal(toniesContentCache.chapters.length,2);
assert.equal(toniesQueuedActions.size,0); assert.equal(toniesApplying,false);
assert.equal(timers.size,0);
assert.match(toniesStatusEl.innerHTML,/Refused/);
""")


def test_a_failed_queued_action_is_reported_and_dropped_from_the_view():
    run_js("""
toniesContentCache={chapters:[{chapter_id:'a',title:'A'},{chapter_id:'b',title:'B'}]};
setCachedToniesContent('tonie-a', toniesContentCache);
const toasts=[]; showToast=(m)=>toasts.push(m);
api=async (path,options) => queueResponse(options);
await enqueueToniesDelete(0);
assert.deepEqual(toniesContentCache.chapters.map(c=>c.chapter_id),['b']);
finishActions('tonie-a',['act-1'],{chapters:[{chapter_id:'a',title:'A'},{chapter_id:'b',title:'B'}]},'failed','Track is no longer on this Tonie');
assert.deepEqual(toniesContentCache.chapters.map(c=>c.chapter_id),['a','b']);
assert.match(toasts.at(-1),/Change failed: Track is no longer on this Tonie/);
""")


def test_edits_queued_for_one_tonie_never_change_another_tonies_view():
    run_js("""
toniesContentCache={chapters:[{chapter_id:'a',title:'Old'}]};
setCachedToniesContent('tonie-a', toniesContentCache);
setCachedToniesContent('tonie-b',{chapters:[{chapter_id:'b'},{chapter_id:'c'}]});
beginToniesEditMode(); toniesNameDrafts['id:a']='Submitted';
api=async (path,options) => queueResponse(options);
await saveToniesNameEdits();
selectTonies('tonie-b');
await reorderToniesChapter(0,1);
assert.deepEqual(actionPosts.map(p => p.target_url),['tonie-a','tonie-b']);
finishActions('tonie-a',['act-1'],{chapters:[{chapter_id:'a',title:'Submitted'}]});
assert.deepEqual(toniesContentCache.chapters.map(c=>c.chapter_id),['c','b']);
assert.equal(getCachedToniesContent('tonie-a').chapters[0].title,'Submitted');
""")


def test_queued_local_uploads_finish_on_original_target_after_selection_changes():
    run_js("""
toniesContentCache={chapters:[]};
setCachedToniesContent('tonie-b',{chapters:[{chapter_id:'untouched-b'}]});
const uploads=[]; let currentJob=null; let reads=0;
api=async (path,options) => {
  if(path==='/api/upload-existing') {
    const payload=JSON.parse(options.body); uploads.push(payload);
    currentJob={id:`job-${uploads.length}`,status:'uploading',target_url:payload.target_url};
    return {job_id:currentJob.id};
  }
  if(path==='/api/jobs') return [currentJob];
  if(path==='/api/tonies-content') {
    reads++; assert.equal(JSON.parse(options.body).target_url,'tonie-a');
    return {chapters:uploads.map((_,i)=>({chapter_id:`new-${i+1}`}))};
  }
  throw Error(`Unexpected call: ${path}`);
};
await uploadExisting('First.mp3');
await uploadExisting('Second.mp3');
selectTonies('tonie-b');
await finalizePendingToniesUpload({id:'job-1',target_url:'tonie-a',uploaded_chapter_id:'new-1',
  tonies_content:{chapters:[{chapter_id:'new-1'}]}},true);
await new Promise(resolve=>setImmediate(resolve));
assert.equal(uploads.length,2);
assert.deepEqual(uploads.map(x=>x.target_url),['tonie-a','tonie-a']);
await finalizePendingToniesUpload({id:'job-2',target_url:'tonie-a',uploaded_chapter_id:'new-2',
  tonies_content:{chapters:[{chapter_id:'new-1'},{chapter_id:'new-2'}]}},true);
assert.equal(pendingToniesUpload,null);
assert.deepEqual(localUploadOutstandingByName,{});
assert.equal(reads,2);
assert.equal(toniesContentCache.chapters[0].chapter_id,'untouched-b');
assert.equal(toniesApplying,false);
""")


def test_delete_all_needs_confirmation_and_queues_one_clear():
    run_js("""
toniesContentCache={chapters:[{chapter_id:'a',title:'Old'}]};
setCachedToniesContent('tonie-a', toniesContentCache);
beginToniesEditMode();
let calls=0;
api=async (path,options) => {
  calls++; assert.equal(path,'/api/tonies-actions');
  assert.deepEqual(JSON.parse(options.body),{target_url:'tonie-a',actions:[{type:'clear'}]});
  return queueResponse(options);
};
askConfirm=async()=>false; await deleteAllToniesContent();
assert.equal(calls,0); assert.equal(toniesEditMode,true);
askConfirm=async()=>true; await deleteAllToniesContent();
assert.equal(calls,1); assert.equal(toniesEditMode,false);
assert.deepEqual(toniesContentCache.chapters,[]);
finishActions('tonie-a',['act-1'],{chapters:[],free_minutes:90,processing:false});
assert.deepEqual(getCachedToniesContent('tonie-a').chapters,[]);
assert.equal(toniesApplying,false);
""")


def test_derived_selection_job_transfers_tracking_instead_of_leaving_parent_busy():
    run_js("""
activeUploadJobIds.add('parent');
applySelectionResponse('parent',0,{id:'child',status:'downloading',target_url:'tonie-a'});
assert.equal(activeUploadJobIds.has('parent'),false);
assert.equal(activeUploadJobIds.has('child'),true);
""")


def test_rendered_library_retains_processing_capacity_and_drag_drop_handlers():
    run_js("""
toniesContentCache={chapters:[{chapter_id:'a',title:'A',processing:true},{chapter_id:'b',title:'B',duration:'00:01:30'}],
  free_minutes:88,total_minutes:90,processing:true};
toniesReverseView=true; actualRenderToniesContent();
assert.match(toniesContentEl.innerHTML,/Processing audio/);
assert.match(toniesMinutesEl.textContent,/88 min left/);
assert.ok(toniesContentEl.innerHTML.indexOf('data-source-index="1"')<toniesContentEl.innerHTML.indexOf('data-source-index="0"'));
assert.equal(typeof toniesContentEl.ondrop,'function');
assert.equal(typeof toniesContentEl.ondragover,'function');
assert.match(toniesContentEl.innerHTML,/data-end-drop-zone/);
""")


def test_historical_upload_receipts_reconcile_current_inventory_oldest_first():
    run_js("""
const first={id:'first',status:'done',target_url:'tonie-a',uploaded_chapter_id:'new-1',
  tonies_content:{chapters:[{chapter_id:'old'},{chapter_id:'new-1'}],processing:false}};
const second={id:'second',status:'done',target_url:'tonie-a',uploaded_chapter_id:'new-2',
  tonies_content:{chapters:[{chapter_id:'old'},{chapter_id:'new-1'},{chapter_id:'new-2'}],processing:false}};
for (const job of [first,second]) {
  activeUploadJobIds.add(job.id);
  uploadIntentsByJobId.set(job.id,{targetUrl:'tonie-a',targetEdge:'end'});
}
jobsCache=[second,first]; // list_jobs returns newest-created first.
let current=['old','new-1','new-2']; const calls=[];
api=async (path,options) => {
  const payload=JSON.parse(options.body); calls.push([path,payload]);
  assert.equal(payload.target_url,'tonie-a');
  if(path==='/api/tonies-content/reorder') {
    assert.equal(current[payload.from_index],payload.chapter_id);
    current.splice(payload.to_index,0,...current.splice(payload.from_index,1));
  } else assert.equal(path,'/api/tonies-content');
  return {chapters:current.map(chapter_id=>({chapter_id})),processing:false};
};
processTrackedJobTransitions(jobsCache);
await new Promise(resolve=>setImmediate(resolve));
assert.deepEqual(toniesContentCache.chapters.map(c=>c.chapter_id),['old','new-1','new-2']);
assert.deepEqual(getCachedToniesContent('tonie-a').chapters.map(c=>c.chapter_id),current);
assert.deepEqual(calls.map(c=>c[0]),['/api/tonies-content','/api/tonies-content/reorder',
  '/api/tonies-content','/api/tonies-content/reorder']);
assert.deepEqual(calls.filter(c=>c[0].endsWith('/reorder')).map(c=>c[1].chapter_id),['new-1','new-2']);
processTrackedJobTransitions(jobsCache);
await new Promise(resolve=>setImmediate(resolve));
assert.equal(calls.length,4); assert.equal(uploadLaneBusy(),false);
""")


@pytest.mark.parametrize("status", ["failed", "uploading", "done"])
def test_late_attachment_receipt_reconciles_after_finalized_watchdog_failure(status):
    run_js("""
const failed={id:'late',status:'failed',target_url:'tonie-a',error:'Timed out in state uploading'};
startPendingToniesUpload('Track.mp3','tonie-a'); pendingToniesUpload.jobId='late';
activeUploadJobIds.add('late'); jobsCache=[failed];
let settlements=0; const settle=resolveLocalUploadPlanned;
resolveLocalUploadPlanned=name=>{settlements++; settle(name);};
let fileRefreshes=0; refreshFiles=async()=>{fileRefreshes++;};
processTrackedJobTransitions(jobsCache);
await new Promise(resolve=>setImmediate(resolve));
assert.equal(pendingToniesUpload,null); assert.equal(settlements,1);
selectedToniesUrl='tonie-b'; toniesContentCache={chapters:[{chapter_id:'untouched-b'}]};
let reads=0;
api=async (path,options)=>{
  reads++; assert.equal(path,'/api/tonies-content');
  assert.deepEqual(JSON.parse(options.body),{target_url:'tonie-a'});
  return {chapters:[{chapter_id:'accepted',title:'Track'},{chapter_id:'newer'}],processing:false};
};
const lateReceipt={...failed,status:STATUS,uploaded_chapter_id:'accepted',
  tonies_content:{chapters:[{chapter_id:'accepted',title:'Historical title'}],processing:false}};
jobsCache=[lateReceipt];
processTrackedJobTransitions(jobsCache); syncPendingToniesUploadState(jobsCache);
processTrackedJobTransitions(jobsCache);
await new Promise(resolve=>setImmediate(resolve));
assert.deepEqual(getCachedToniesContent('tonie-a')?.chapters.map(c=>c.chapter_id),['accepted','newer'],
  'Late confirmed attachment must reconcile the original target even after job failure was finalized');
assert.equal(toniesContentCache.chapters[0].chapter_id,'untouched-b');
assert.equal(reads,1); assert.equal(settlements,1); assert.equal(fileRefreshes,0);
assert.equal(pendingToniesUpload,null); assert.equal(uploadLaneBusy(),false);
""".replace("STATUS", repr(status)))


@pytest.mark.parametrize("mutation", ["reorder", "rename", "delete", "delete-all"])
def test_late_receipt_reconciles_under_still_queued_edits(mutation):
    run_js("""
const failed={id:'late',status:'failed',target_url:'tonie-a'};
activeUploadJobIds.add('late'); processTrackedJobTransitions([failed]);
await new Promise(resolve=>setImmediate(resolve));
toniesContentCache={chapters:[{chapter_id:'a',title:'A'},{chapter_id:'b',title:'B'}]};
setCachedToniesContent('tonie-a', toniesContentCache);
const calls=[];
const current={chapters:[{chapter_id:'accepted',title:'Current'},{chapter_id:'a',title:'A'},{chapter_id:'b',title:'B'}],processing:false};
api=async (path,options)=>{
  calls.push(path);
  if(path==='/api/tonies-content') return current;
  return queueResponse(options);
};
if(MUTATION==='rename') {
  beginToniesEditMode(); toniesNameDrafts['id:a']='Submitted'; await saveToniesNameEdits();
} else if(MUTATION==='delete-all') {beginToniesEditMode(); await deleteAllToniesContent();}
else if(MUTATION==='delete') await enqueueToniesDelete(0);
else await reorderToniesChapter(0,1);
const receipt={...failed,uploaded_chapter_id:'accepted',tonies_content:{chapters:[{chapter_id:'accepted',title:'Historical'}]}};
processTrackedJobTransitions([receipt]);
await new Promise(resolve=>setImmediate(resolve));
assert.deepEqual(calls,['/api/tonies-actions','/api/tonies-content']);
const ids=toniesContentCache.chapters.map(c=>c.chapter_id);
const expected={reorder:['accepted','b','a'], rename:['accepted','a','b'], delete:['accepted','b'], 'delete-all':[]}[MUTATION];
assert.deepEqual(ids,expected, 'the queued edit stays applied over the fresh read');
if(MUTATION==='rename') assert.equal(toniesContentCache.chapters[1].title,'Submitted');
assert.equal(uploadLaneBusy(),false);
""".replace("MUTATION", repr(mutation)))


def test_concurrent_upload_completions_serialize_read_and_positioning_while_edits_queue():
    run_js("""
const first={id:'first',target_url:'tonie-a',uploaded_chapter_id:'new-1',
  tonies_content:{chapters:[{chapter_id:'old'},{chapter_id:'new-1'}]}};
const second={id:'second',target_url:'tonie-a',uploaded_chapter_id:'new-2',
  tonies_content:{chapters:[{chapter_id:'old'},{chapter_id:'new-1'},{chapter_id:'new-2'}]}};
uploadIntentsByJobId.set('first',{targetUrl:'tonie-a',targetEdge:'start'});
uploadIntentsByJobId.set('second',{targetUrl:'tonie-a',targetEdge:'start'});
let releaseRead,releaseReorder; const calls=[];
api=async (path,options)=>{
  if(path==='/api/tonies-actions') return queueResponse(options);
  const payload=JSON.parse(options.body); calls.push([path,payload]);
  if(calls.length===1) return await new Promise(resolve=>{releaseRead=resolve;});
  if(calls.length===2) return await new Promise(resolve=>{releaseReorder=resolve;});
  if(calls.length===3) return {chapters:[{chapter_id:'new-1'},{chapter_id:'old'},{chapter_id:'new-2'}]};
  assert.equal(calls.length,4);
  assert.equal(payload.chapter_id,'new-2'); assert.equal(payload.from_index,2); assert.equal(payload.to_index,0);
  return {chapters:[{chapter_id:'new-2'},{chapter_id:'new-1'},{chapter_id:'old'}]};
};
const finishingFirst=finalizePendingToniesUpload(first,true);
const finishingSecond=finalizePendingToniesUpload(second,true);
await new Promise(resolve=>setImmediate(resolve));
assert.equal(calls.length,1);
releaseRead({chapters:[{chapter_id:'old'},{chapter_id:'new-1'},{chapter_id:'new-2'}]});
await new Promise(resolve=>setImmediate(resolve));
assert.equal(calls.length,2); assert.equal(calls[1][1].chapter_id,'new-1');
await reorderToniesChapter(0,1); // A human edit queues on the server instead of waiting.
assert.equal(actionPosts.length,1); assert.equal(calls.length,2);
releaseReorder({chapters:[{chapter_id:'new-1'},{chapter_id:'old'},{chapter_id:'new-2'}]});
await Promise.all([finishingFirst,finishingSecond]);
assert.deepEqual(calls.map(c=>c[0]),['/api/tonies-content','/api/tonies-content/reorder',
  '/api/tonies-content','/api/tonies-content/reorder']);
assert.equal(uploadLaneBusy(),false);
""")


@pytest.mark.parametrize("initial_status", ["failed", "done"])
def test_late_receipt_preserves_original_exact_id_edge_intent_without_reupload(initial_status):
    run_js("""
const failed={id:'late',status:INITIAL_STATUS,target_url:'tonie-a'};
activeUploadJobIds.add('late');
pendingDropUploadIntent={jobId:'late',targetUrl:'tonie-a',targetEdge:'end',targetIndex:1};
api=async path=>{assert.equal(path,'/api/tonies-content'); return {chapters:[]};};
processTrackedJobTransitions([failed]);
await new Promise(resolve=>setImmediate(resolve));
assert.equal(pendingDropUploadIntent,null);
toniesReverseView=false; // Receipt must retain the original native edge, not this new view preference.
const calls=[];
api=async (path,options)=>{
  const payload=JSON.parse(options.body); calls.push(path);
  if(path==='/api/tonies-content') return {chapters:[{chapter_id:' ID/A ',title:'Same'},{chapter_id:'old',title:'Same'},{chapter_id:'newer'}]};
  assert.equal(path,'/api/tonies-content/reorder');
  assert.deepEqual(payload,{target_url:'tonie-a',from_index:0,to_index:2,chapter_id:' ID/A '});
  return {chapters:[{chapter_id:'old',title:'Same'},{chapter_id:'newer'},{chapter_id:' ID/A ',title:'Same'}]};
};
const receipt={...failed,uploaded_chapter_id:' ID/A ',tonies_content:{chapters:[{chapter_id:' ID/A '}]}};
processTrackedJobTransitions([receipt]); syncPendingToniesUploadState([receipt]);
await new Promise(resolve=>setImmediate(resolve));
processTrackedJobTransitions([receipt]);
await new Promise(resolve=>setImmediate(resolve));
assert.deepEqual(calls,['/api/tonies-content','/api/tonies-content/reorder']);
assert.equal(toniesContentCache.chapters[2].chapter_id,' ID/A ');
assert.equal(uploadIntentsByJobId.has('late'),false); assert.equal(uploadLaneBusy(),false);
""".replace("INITIAL_STATUS", repr(initial_status)))


def test_receipt_does_not_resurrect_chapters_absent_from_current_inventory():
    run_js("""
let reads=0;
api=async path=>{
  reads++; assert.equal(path,'/api/tonies-content');
  return {chapters:[],free_minutes:90,processing:false};
};
uploadIntentsByJobId.set('job',{targetUrl:'tonie-a',targetEdge:'start'});
await finalizePendingToniesUpload({id:'job',target_url:'tonie-a',uploaded_chapter_id:'deleted',
  tonies_content:{chapters:[{chapter_id:'deleted',title:'Gone'}],processing:true}},true);
assert.equal(reads,1); assert.deepEqual(toniesContentCache.chapters,[]);
assert.deepEqual(getCachedToniesContent('tonie-a').chapters,[]);
assert.match(toniesStatusEl.innerHTML,/cloud ID is missing/);
assert.equal(uploadLaneBusy(),false);
assert.equal(toniesProcessingTimer,null); // Historical processing is not a reason to poll.
""")


def test_receipt_read_failure_preserves_inventory_and_draft_without_retry_or_reupload():
    run_js("""
toniesContentCache={chapters:[{chapter_id:'a',title:'Saved'}]};
beginToniesEditMode(); toniesNameDrafts['id:a']='Unsaved draft';
const job={id:'job',status:'done',target_url:'tonie-a',uploaded_chapter_id:'new',
  tonies_content:{chapters:[{chapter_id:'new',title:'Historical'}]}};
activeUploadJobIds.add('job'); let calls=0;
api=async path=>{calls++; assert.equal(path,'/api/tonies-content'); throw Error('Unavailable');};
processTrackedJobTransitions([job]);
await new Promise(resolve=>setImmediate(resolve));
processTrackedJobTransitions([job]);
await new Promise(resolve=>setImmediate(resolve));
assert.equal(calls,1); assert.equal(timers.size,0);
assert.equal(toniesContentCache.chapters[0].title,'Saved');
assert.equal(toniesEditMode,true); assert.equal(toniesNameDrafts['id:a'],'Unsaved draft');
assert.match(toniesStatusEl.innerHTML,/Upload saved; library update failed: Unavailable/);
assert.equal(uploadLaneBusy(),false);
""")


def test_receipt_polls_only_current_cloud_processing_without_repeating_positioning():
    run_js("""
let calls=0;
api=async path=>{
  calls++; assert.equal(path,'/api/tonies-content');
  return {chapters:[{chapter_id:'new',processing:calls===1}],processing:calls===1};
};
const receipt={id:'job',status:'failed',target_url:'tonie-a',uploaded_chapter_id:'new',
  tonies_content:{chapters:[{chapter_id:'new'}],processing:false}};
activeUploadJobIds.add('job'); processTrackedJobTransitions([receipt]);
await new Promise(resolve=>setImmediate(resolve));
assert.equal(calls,1); assert.notEqual(toniesProcessingTimer,null);
const id=toniesProcessingTimer; const poll=timers.get(id); timers.delete(id); await poll.fn();
assert.equal(calls,2); assert.equal(toniesProcessingTimer,null);
processTrackedJobTransitions([receipt]);
await new Promise(resolve=>setImmediate(resolve));
assert.equal(calls,2); assert.equal(uploadLaneBusy(),false);
""")


def test_receipt_arriving_during_initial_finalization_keeps_the_receipt_lane_busy():
    run_js("""
uploadIntentsByJobId.set('job',{targetUrl:'tonie-a',targetEdge:'start'});
let firstRead,secondRead; const calls=[];
api=async (path,options)=>{
  if(path==='/api/tonies-actions') return queueResponse(options);
  calls.push(path);
  if(calls.length===1) return await new Promise(resolve=>{firstRead=resolve;});
  if(calls.length===2) return await new Promise(resolve=>{secondRead=resolve;});
  assert.equal(path,'/api/tonies-content/reorder');
  assert.deepEqual(JSON.parse(options.body),{target_url:'tonie-a',from_index:1,to_index:0,chapter_id:'new'});
  return {chapters:[{chapter_id:'new'},{chapter_id:'old'}]};
};
const finishing=finalizePendingToniesUpload({id:'job',target_url:'tonie-a',status:'done'},true);
const receipt={id:'job',target_url:'tonie-a',status:'done',uploaded_chapter_id:'new',
  tonies_content:{chapters:[{chapter_id:'old'},{chapter_id:'new'}]}};
const reconciling=finalizePendingToniesUpload(receipt,true);
firstRead({chapters:[{chapter_id:'old'},{chapter_id:'new'}]});
await new Promise(resolve=>setImmediate(resolve));
assert.equal(calls.length,2); assert.equal(uploadLaneBusy(),true);
secondRead({chapters:[{chapter_id:'old'},{chapter_id:'new'}]});
await Promise.all([finishing,reconciling]);
assert.equal(calls.length,3); assert.equal(toniesContentCache.chapters[0].chapter_id,'new');
assert.equal(uploadLaneBusy(),false);
""")


def test_browser_resync_workarounds_are_removed():
    html=HTML.read_text(encoding="utf-8")
    for obsolete in ('transientPlaywright','toniesRefreshHardWatchdogByUrl',
                     'refreshToniesContentWithFollowups','mergeOptimisticUploadedChapters',
                     'confirmedDeletedKeys','chapterFingerprint','beforeNorm','retryPayload'):
        assert obsolete not in html


@pytest.mark.parametrize("position", [False, True])
def test_receipt_read_cannot_overwrite_later_stats_refresh_or_position_from_stale_indices(position):
    run_js("""
const receipt={id:'job',status:'done',target_url:'tonie-a',uploaded_chapter_id:'accepted',
 tonies_content:{chapters:[{chapter_id:'accepted',title:'Historical'}]}};
if(POSITION) uploadIntentsByJobId.set('job',{targetUrl:'tonie-a',targetEdge:'start'});
let releaseReceiptRead; let reads=0; let writes=0;
const newer={chapters:[{chapter_id:'newer',title:'Later upload',processing:true},
                      {chapter_id:'accepted',title:'Renamed externally'}],processing:true};
api=async (path,options)=>{
 if(path==='/api/tonies-content/reorder') {
   writes++;
   assert.deepEqual(JSON.parse(options.body),{target_url:'tonie-a',chapter_id:'accepted',from_index:1,to_index:0});
   return {...newer,chapters:[newer.chapters[1],newer.chapters[0]]};
 }
 assert.equal(path,'/api/tonies-content'); reads++;
 if(reads===1) return await new Promise(resolve=>{releaseReceiptRead=resolve;});
 assert.equal(reads,2); return newer;
};
const finalizing=finalizePendingToniesUpload(receipt,true);
assert.equal(reads,1);
toniesCache=[{edit_url:'tonie-a'}];
await syncCreativeToniesStats({force:true});
assert.equal(reads,2);
assert.deepEqual(toniesContentCache.chapters.map(c=>c.chapter_id),['newer','accepted']);
assert.notEqual(toniesProcessingTimer,null);
releaseReceiptRead({chapters:[{chapter_id:'accepted',title:'Old read'}],processing:false});
await finalizing;
const expected=POSITION?['accepted','newer']:['newer','accepted'];
assert.deepEqual(toniesContentCache.chapters.map(c=>c.chapter_id),expected);
assert.deepEqual(getCachedToniesContent('tonie-a').chapters.map(c=>c.chapter_id),expected);
assert.equal(toniesContentCache.chapters.find(c=>c.chapter_id==='accepted').title,'Renamed externally');
assert.notEqual(toniesProcessingTimer,null);
assert.equal(writes,POSITION?1:0);
assert.equal(uploadLaneBusy(),false);
""".replace("POSITION", str(position).lower()))



def test_a_result_event_that_beats_the_post_response_leaves_no_ghost_pending_edit():
    run_js("""
toniesContentCache={chapters:[{chapter_id:'a'},{chapter_id:'b'}]};
setCachedToniesContent('tonie-a', toniesContentCache);
api=async (path,options) => {
  const res=queueResponse(options);
  finishActions('tonie-a',[res.actions[0].id],{chapters:[{chapter_id:'b'}]});  // event first
  return res;
};
await enqueueToniesDelete(0);
assert.equal(toniesQueuedActions.size,0);
assert.deepEqual(toniesContentCache.chapters.map(c=>c.chapter_id),['b']);
""")


def test_without_the_event_stream_queued_edits_settle_through_polling():
    run_js("""
toniesContentCache={chapters:[{chapter_id:'a'},{chapter_id:'b'}]};
setCachedToniesContent('tonie-a', toniesContentCache);
const paths=[];
api=async (path,options) => {
  paths.push(path);
  if(path==='/api/tonies-actions') return queueResponse(options);
  if(path.startsWith('/api/tonies-actions?')) return {actions:[{id:'act-1',status:'done'}]};
  assert.equal(path,'/api/tonies-content'); return {chapters:[{chapter_id:'b'}]};
};
await enqueueToniesDelete(0);
assert.equal(toniesQueuedActions.size,1);
const [id,timer]=[...timers.entries()].find(([,t]) => t.ms===1200); timers.delete(id);
await timer.fn();
assert.deepEqual(paths,['/api/tonies-actions','/api/tonies-actions?target_url=tonie-a','/api/tonies-content']);
assert.equal(toniesQueuedActions.size,0);
assert.deepEqual(toniesContentCache.chapters.map(c=>c.chapter_id),['b']);
""")


def test_overlay_follows_the_server_planner_rules():
    run_js("""
const base={chapters:['a','b','c','d'].map(chapter_id=>({chapter_id,title:chapter_id.toUpperCase()}))};
const add=(id,type,params)=>toniesQueuedActions.set(id,{id,url:'tonie-a',type,params});
add('1','delete',{chapter_id:'b'});
add('2','move',{chapter_id:'d',position:{edge:'start'}});
add('3','move',{chapter_id:'a',position:{before:'b',index:2}});  // anchor deleted: index fallback
add('4','rename',{chapter_id:'c',title:'Renamed'});
add('5','delete',{chapter_id:'gone'});                           // no-op, as on the server
const shown=overlayToniesContent('tonie-a',base);
assert.deepEqual(shown.chapters.map(c=>c.chapter_id),['d','c','a']);
assert.equal(shown.chapters[1].title,'Renamed');
assert.deepEqual(shown.chapters.map(c=>c.index),[0,1,2]);
assert.deepEqual(base.chapters.map(c=>c.chapter_id),['a','b','c','d']);  // server copy untouched
assert.deepEqual(overlayToniesContent('tonie-b',base),base);
""")


def test_local_uploads_send_their_position_and_do_not_wait_for_each_other():
    run_js("""
toniesContentCache={chapters:[{chapter_id:'a'},{chapter_id:'b'},{chapter_id:'c'}]};
setCachedToniesContent('tonie-a', toniesContentCache);
toniesReverseView=false;
const posts=[]; let jobs=[];
api=async (path,options) => {
  if(path==='/api/upload-existing') {
    const body=JSON.parse(options.body); posts.push(body);
    const job={id:`job-${posts.length}`,status:'waiting_upload',target_url:body.target_url}; jobs=[job,...jobs];
    return {job_id:job.id};
  }
  if(path==='/api/jobs') return jobs;
  throw Error(`Unexpected ${path}`);
};
await uploadExisting('One.mp3',{targetIndex:1});
await uploadExisting('Two.mp3',{targetIndex:0});
await uploadExisting('Three.mp3',{targetIndex:3});
assert.deepEqual(posts.map(p=>p.position),[{before:'b',index:1},{edge:'start'},{edge:'end'}]);
assert.equal(queuedLocalUploads.length,0);
assert.equal(activeUploadJobIds.size,3);
""")


def test_server_positioned_upload_is_not_reordered_again():
    run_js("""
activeUploadJobIds.add('job');
uploadIntentsByJobId.set('job',{targetUrl:'tonie-a',targetEdge:'start',position:{edge:'start'},filename:'New.mp3'});
markLocalUploadPlanned('New.mp3');
const paths=[];
api=async path => { paths.push(path); return {chapters:[{chapter_id:'new'},{chapter_id:'old'}]}; };
await finalizePendingToniesUpload({id:'job',status:'done',target_url:'tonie-a',uploaded_chapter_id:'new',upload_positioned:true,
  tonies_content:{chapters:[{chapter_id:'new'},{chapter_id:'old'}]}},true);
assert.deepEqual(paths,['/api/tonies-content']);
assert.deepEqual(localUploadOutstandingByName,{});
""")


def test_each_upload_in_flight_shows_a_placeholder_where_it_will_land():
    run_js("""
toniesContentCache={chapters:[{chapter_id:'a',title:'A'},{chapter_id:'b',title:'B'},{chapter_id:'c',title:'C'}]};
for (const [jobId,position] of [['j1',{edge:'start'}],['j2',{before:'c',index:2}],['j3',{edge:'end'}]]) {
  activeUploadJobIds.add(jobId);
  uploadIntentsByJobId.set(jobId,{jobId,targetUrl:'tonie-a',position,filename:`${jobId}.mp3`});
}
uploadIntentsByJobId.set('done',{jobId:'done',targetUrl:'tonie-a',position:{edge:'start'}});  // finished: no placeholder
const order=()=>[...toniesContentEl.innerHTML.matchAll(/data-source-index="(\\d)"|Uploading (j\\d)/g)].map(m=>m[1]??m[2]);
toniesReverseView=false; actualRenderToniesContent();
assert.deepEqual(order(),['j1','0','1','j2','2','j3']);
toniesReverseView=true; actualRenderToniesContent();
assert.deepEqual(order(),['j3','2','j2','1','0','j1']);
""")
