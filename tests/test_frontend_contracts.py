"""Frontend invariants, executed without a browser or any live API calls."""
from pathlib import Path
import subprocess

import pytest


HTML = Path(__file__).parents[1] / "web" / "index.html"


def run_js(body):
    source = HTML.read_text(encoding="utf-8").split("<script>", 1)[1].split("</script>", 1)[0]
    source = source.split("\n      loadResultsClearedState();", 1)[0]
    harness = r"""
const assert = require('node:assert/strict');
const timers = new Map(); let timerId = 0;
global.setTimeout = (fn, ms) => { timers.set(++timerId, {fn, ms}); return timerId; };
global.clearTimeout = (id) => timers.delete(id);
global.setInterval = () => { throw Error('Unexpected extra interval'); };
global.clearInterval = () => {};
const elements = new Map();
function element() { return {value:'', textContent:'', innerHTML:'', style:{}, dataset:{},
  classList:{add(){}, remove(){}, toggle(){}, contains(){return false;}},
  addEventListener(){}, setAttribute(){}, querySelector(){return null;},
  querySelectorAll(){return [];}, getBoundingClientRect(){return {width:100};}}; }
global.document = { getElementById(id) { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); },
  documentElement:element(), addEventListener(){}, querySelector(){return null;} };
global.window = {innerWidth:1200, innerHeight:900};
global.localStorage = {getItem(){return null;}, setItem(){}};
global.fetch = () => {throw Error('Live network forbidden');};
"""
    stubs = """
const actualRenderToniesContent = renderToniesContent;
renderTonies = renderToniesContent = renderJobs = renderFiles = renderToniesEditUI = () => {};
showToast = () => {};
refreshFiles = async () => {};
askConfirm = async () => true;
selectedToniesUrl = 'tonie-a';
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
assert.deepEqual(calls[0].payload, {target_url:'tonie-a',index:1,chapter_id:'cloud-b',title:'New name'});
assert.ok(calls[0].timeout >= 15000);
assert.equal(toniesEditMode, true);
assert.equal(toniesNameDrafts['id:cloud-b'], 'New name');
assert.equal(toniesContentCache.chapters[1].title, 'Same');
assert.match(toniesStatusEl.innerHTML, /Refused by API/);
""")


def test_rename_applies_each_canonical_result_without_refresh_or_losing_other_drafts():
    run_js("""
toniesContentCache = {chapters:[{chapter_id:'a',title:'Old A'}, {chapter_id:'b',title:'Old B'}]};
beginToniesEditMode();
toniesNameDrafts['id:a'] = 'New A'; toniesNameDrafts['id:b'] = 'New B';
const calls=[];
api = async (path, options) => {
  const payload=JSON.parse(options.body); calls.push(payload);
  assert.equal(path, '/api/tonies-content/rename');
  if (calls.length === 1) return {chapters:[{chapter_id:'b',title:'Old B'},{chapter_id:'a',title:'New A'}],free_minutes:80};
  return {chapters:[{chapter_id:'b',title:'New B'},{chapter_id:'a',title:'New A'}],free_minutes:79};
};
await saveToniesNameEdits();
assert.deepEqual(calls.map(x => [x.index,x.chapter_id]), [[0,'a'],[0,'b']]);
assert.equal(toniesContentCache.free_minutes,79);
assert.equal(toniesEditMode,false);
""")


def test_reorder_uses_exact_cloud_id_and_canonical_response_once():
    run_js("""
toniesContentCache = {chapters:[{chapter_id:' CLOUD/A ',title:'Same'}, {chapter_id:'b',title:'Same'}]};
let calls=0;
api=async (path, options) => {
  calls++;
  assert.equal(path,'/api/tonies-content/reorder');
  assert.deepEqual(JSON.parse(options.body),{target_url:'tonie-a',from_index:0,to_index:1,chapter_id:' CLOUD/A '});
  return {chapters:[{chapter_id:'b',title:'Same'}, {chapter_id:' CLOUD/A ',title:'Canonical'}],free_minutes:73};
};
await reorderToniesChapter(0,1);
assert.equal(calls,1);
assert.equal(toniesContentCache.chapters[1].title,'Canonical');
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


def test_delete_last_chapter_accepts_empty_result_without_refresh():
    run_js("""
toniesContentCache={chapters:[{chapter_id:'last',title:'Last'}]};
let calls=0;
api=async (path,options) => {
  calls++; assert.equal(path,'/api/tonies-content/delete');
  assert.equal(JSON.parse(options.body).chapter_id,'last');
  return {chapters:[],free_minutes:90,processing:false};
};
await enqueueToniesDelete(0);
await new Promise(resolve => setImmediate(resolve));
assert.equal(calls,1);
assert.deepEqual(toniesContentCache.chapters,[]);
assert.deepEqual(getCachedToniesContent('tonie-a').chapters,[]);
assert.equal(pendingDeleteKeys.size,0);
assert.equal(toniesApplying,false);
""")


def test_queued_deletes_follow_ids_after_indices_change_not_duplicate_titles():
    run_js("""
toniesContentCache={chapters:[{chapter_id:'a',title:'Same'},{chapter_id:'b',title:'Same'},{chapter_id:'c',title:'Same'}]};
let resolveFirst; const calls=[];
api=async (path,options) => {
  const payload=JSON.parse(options.body); calls.push(payload);
  if (calls.length===1) return await new Promise(resolve => {resolveFirst=resolve;});
  return {chapters:[{chapter_id:'b',title:'Same'}],free_minutes:89};
};
await enqueueToniesDelete(0);
await enqueueToniesDelete(2);
resolveFirst({chapters:[{chapter_id:'c',title:'Same'},{chapter_id:'b',title:'Same'}],free_minutes:88});
await new Promise(resolve => setImmediate(resolve));
assert.deepEqual(calls.map(c => [c.index,c.chapter_id]),[[0,'a'],[0,'c']]);
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


def test_stale_refresh_cannot_override_newer_mutation_or_selection():
    run_js("""
toniesContentCache={chapters:[{chapter_id:'a',title:'Old'}]};
let resolveRead;
api=async path => {
  if (path==='/api/tonies-content') return await new Promise(resolve => {resolveRead=resolve;});
  return {chapters:[],free_minutes:90,processing:false};
};
const reading=refreshToniesContent({force:true});
await deleteToniesChapter(0,true,true);
resolveRead({chapters:[{chapter_id:'a',title:'Stale'}],free_minutes:89});
await reading;
assert.deepEqual(toniesContentCache.chapters,[]);
assert.equal(loadingToniesContent,false);
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


def test_completion_keeps_upload_queue_blocked_until_positioning_finishes():
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
assert.equal(hasToniesUploadBusy(),true);
await new Promise(resolve=>setImmediate(resolve));
resolveReorder({chapters:[{chapter_id:'new'},{chapter_id:'old'}]});
await finishing;
assert.equal(hasToniesUploadBusy(),false);
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
activeUploadJobIds.add('busy');
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
beginToniesEditMode(); toniesNameDrafts['id:a']='Submitted';
let resolveSave;
api=async () => await new Promise(resolve => {resolveSave=resolve;});
const saving=saveToniesNameEdits();
toniesNameDrafts['id:a']='Newer draft';
resolveSave({chapters:[{chapter_id:'a',title:'Submitted'}]});
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


def test_mutation_refusals_are_not_retried_and_keep_authoritative_content():
    run_js("""
toniesContentCache={chapters:[{chapter_id:'a',title:'Old'},{chapter_id:'b',title:'Other'}]};
let calls=0; api=async () => {calls++; return {error:'Refused'};};
await reorderToniesChapter(0,1);
assert.equal(calls,1); assert.equal(toniesContentCache.chapters[0].chapter_id,'a');
await deleteToniesChapter(0,true,true);
assert.equal(calls,2); assert.equal(toniesContentCache.chapters.length,2);
assert.equal(pendingDeleteKeys.size,0); assert.equal(toniesApplying,false);
assert.equal(timers.size,0);
""")


def test_selection_change_cannot_unlock_a_pending_library_mutation():
    run_js("""
toniesContentCache={chapters:[{chapter_id:'a',title:'Old'}]};
setCachedToniesContent('tonie-b',{chapters:[{chapter_id:'b'},{chapter_id:'c'}]});
beginToniesEditMode(); toniesNameDrafts['id:a']='Submitted';
let resolveSave; let calls=0;
api=async () => {
  calls++;
  if(calls===1) return await new Promise(resolve => {resolveSave=resolve;});
  return {chapters:[{chapter_id:'c'},{chapter_id:'b'}]};
};
const saving=saveToniesNameEdits();
selectTonies('tonie-b');
await reorderToniesChapter(0,1);
assert.equal(calls,1);
resolveSave({chapters:[{chapter_id:'a',title:'Submitted'}]});
await saving;
assert.equal(toniesContentCache.chapters[0].chapter_id,'b');
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


def test_delete_all_confirmation_and_empty_canonical_response_are_preserved():
    run_js("""
toniesContentCache={chapters:[{chapter_id:'a',title:'Old'}]};
beginToniesEditMode();
let calls=0;
api=async (path,options) => {
  calls++; assert.equal(path,'/api/tonies-content/delete-all');
  assert.deepEqual(JSON.parse(options.body),{target_url:'tonie-a'});
  return {chapters:[],free_minutes:90,processing:false};
};
askConfirm=async()=>false; await deleteAllToniesContent();
assert.equal(calls,0); assert.equal(toniesEditMode,true);
askConfirm=async()=>true; await deleteAllToniesContent();
assert.equal(calls,1); assert.equal(toniesEditMode,false);
assert.deepEqual(toniesContentCache.chapters,[]);
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
assert.equal(calls.length,4); assert.equal(hasToniesUploadBusy(),false);
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
assert.equal(pendingToniesUpload,null); assert.equal(hasToniesUploadBusy(),false);
""".replace("STATUS", repr(status)))


@pytest.mark.parametrize("mutation", ["reorder", "rename", "delete", "delete-all"])
def test_late_receipt_waits_for_existing_library_mutation(mutation):
    run_js("""
const failed={id:'late',status:'failed',target_url:'tonie-a'};
activeUploadJobIds.add('late'); processTrackedJobTransitions([failed]);
await new Promise(resolve=>setImmediate(resolve));
toniesContentCache={chapters:[{chapter_id:'a',title:'A'},{chapter_id:'b',title:'B'}]};
let finishMutation; const calls=[];
const current={chapters:[{chapter_id:'accepted',title:'Current'}],processing:false};
api=async (path,options)=>{
  calls.push(path);
  if(path==='/api/tonies-content') return current;
  assert.equal(path,'/api/tonies-content/'+MUTATION);
  return await new Promise(resolve=>{finishMutation=resolve;});
};
let changing;
if(MUTATION==='rename') {
  beginToniesEditMode(); toniesNameDrafts['id:a']='Submitted'; changing=saveToniesNameEdits();
  toniesNameDrafts['id:a']='Newer draft';
} else if(MUTATION==='delete-all') {beginToniesEditMode(); changing=deleteAllToniesContent();}
else if(MUTATION==='delete') changing=deleteToniesChapter(0,true,true);
else changing=reorderToniesChapter(0,1);
await new Promise(resolve=>setImmediate(resolve));
const receipt={...failed,uploaded_chapter_id:'accepted',tonies_content:{chapters:[{chapter_id:'accepted',title:'Historical'}]}};
processTrackedJobTransitions([receipt]);
await new Promise(resolve=>setImmediate(resolve));
assert.equal(calls.length,1,'Receipt must not read or position while an earlier mutation is pending');
assert.equal(hasToniesUploadBusy(),true);
finishMutation({chapters:[],processing:false}); await changing;
await new Promise(resolve=>setImmediate(resolve));
assert.deepEqual(calls,['/api/tonies-content/'+MUTATION,'/api/tonies-content']);
assert.equal(toniesContentCache.chapters[0].title,'Current');
assert.equal(hasToniesUploadBusy(),false); assert.equal(toniesMutationInFlight,false);
if(MUTATION==='rename') {assert.equal(toniesEditMode,true); assert.equal(toniesNameDrafts['id:a'],'Newer draft');}
""".replace("MUTATION", repr(mutation)))


def test_concurrent_upload_completions_serialize_authoritative_read_and_positioning():
    run_js("""
const first={id:'first',target_url:'tonie-a',uploaded_chapter_id:'new-1',
  tonies_content:{chapters:[{chapter_id:'old'},{chapter_id:'new-1'}]}};
const second={id:'second',target_url:'tonie-a',uploaded_chapter_id:'new-2',
  tonies_content:{chapters:[{chapter_id:'old'},{chapter_id:'new-1'},{chapter_id:'new-2'}]}};
uploadIntentsByJobId.set('first',{targetUrl:'tonie-a',targetEdge:'start'});
uploadIntentsByJobId.set('second',{targetUrl:'tonie-a',targetEdge:'start'});
let releaseRead,releaseReorder; const calls=[];
api=async (path,options)=>{
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
await reorderToniesChapter(0,1); // Human mutation cannot overlap completion positioning.
assert.equal(calls.length,2);
releaseReorder({chapters:[{chapter_id:'new-1'},{chapter_id:'old'},{chapter_id:'new-2'}]});
await Promise.all([finishingFirst,finishingSecond]);
assert.deepEqual(calls.map(c=>c[0]),['/api/tonies-content','/api/tonies-content/reorder',
  '/api/tonies-content','/api/tonies-content/reorder']);
assert.deepEqual(toniesContentCache.chapters.map(c=>c.chapter_id),['new-2','new-1','old']);
assert.equal(hasToniesUploadBusy(),false);
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
assert.equal(uploadIntentsByJobId.has('late'),false); assert.equal(hasToniesUploadBusy(),false);
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
assert.equal(hasToniesUploadBusy(),false);
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
assert.equal(hasToniesUploadBusy(),false);
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
assert.equal(calls,2); assert.equal(hasToniesUploadBusy(),false);
""")


def test_receipt_arriving_during_initial_finalization_keeps_mutation_lane_busy():
    run_js("""
uploadIntentsByJobId.set('job',{targetUrl:'tonie-a',targetEdge:'start'});
let firstRead,secondRead; const calls=[];
api=async (path,options)=>{
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
assert.equal(calls.length,2); assert.equal(hasToniesUploadBusy(),true);
await reorderToniesChapter(0,1);
assert.equal(calls.length,2,'A second receipt for the same job must keep the lane locked after lifecycle cleanup');
secondRead({chapters:[{chapter_id:'old'},{chapter_id:'new'}]});
await Promise.all([finishing,reconciling]);
assert.equal(calls.length,3); assert.equal(toniesContentCache.chapters[0].chapter_id,'new');
assert.equal(hasToniesUploadBusy(),false);
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
assert.equal(hasToniesUploadBusy(),false);
""".replace("POSITION", str(position).lower()))
