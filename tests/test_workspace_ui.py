"""Audio workspace behavior through the actual page controller (no live cloud)."""
from html.parser import HTMLParser

from test_frontend_contracts import HTML, run_js


def test_workspace_navigation_preserves_sources_and_native_player():
    run_js("""
const audio = document.getElementById('local-audio');
filesCache = [{name:'story.mp3'}]; await toggleLocalPlayback('story.mp3');
audio.currentTime = 19; queryInputEl.value = 'bedtime'; fileFilterEl.value = 'story';
setWorkspaceView('add'); setWorkspaceSource('local');
assert.equal(workspaceView, 'add'); assert.equal(workspaceSource, 'local');
assert.equal(document.getElementById('source-local').hidden, false);
assert.equal(document.getElementById('source-youtube').hidden, true);
setWorkspaceView('activity'); setWorkspaceView('tracks'); setWorkspaceView('add');
assert.equal(workspaceSource, 'local');
assert.equal(queryInputEl.value, 'bedtime'); assert.equal(fileFilterEl.value, 'story');
assert.equal(document.getElementById('local-audio'), audio);
assert.equal(audio.currentTime, 19); assert.equal(audio.paused, false);
assert.equal(document.getElementById('nav-add').getAttribute('aria-current'), 'page');
setWorkspaceView('bogus'); assert.equal(workspaceView, 'add');
""")


def test_workspace_theme_defaults_to_system_and_remembers_explicit_choice():
    run_js("""
const saved = new Map();
localStorage.getItem = k => saved.get(k) ?? null;
localStorage.setItem = (k,v) => saved.set(k,v);
loadWorkspaceTheme();
assert.equal(document.documentElement.getAttribute('data-theme'), 'system');
setWorkspaceTheme('dark');
assert.equal(document.documentElement.getAttribute('data-theme'), 'dark');
assert.equal(saved.get('tonies-auto.workspace-theme.v1'), 'dark');
loadWorkspaceTheme(); assert.equal(document.documentElement.getAttribute('data-theme'), 'dark');
setWorkspaceTheme('invalid'); assert.equal(document.documentElement.getAttribute('data-theme'), 'dark');
""")


def test_selected_header_and_add_destination_use_exact_target():
    run_js("""
toniesCache = [{edit_url:'tonie-a',name:'Fox & friends',image:'/fox.webp'},
 {edit_url:'tonie-b',name:'Bear',image:'/bear.webp'}];
renderWorkspaceTarget();
assert.equal(document.getElementById('selected-tonie-name').textContent, 'Fox & friends');
assert.match(document.getElementById('add-destination').textContent, /Fox & friends/);
selectedToniesUrl = 'tonie-b'; renderWorkspaceTarget();
assert.equal(document.getElementById('selected-tonie-name').textContent, 'Bear');
assert.match(document.getElementById('add-destination').textContent, /Bear/);
selectedToniesUrl = ''; toniesCache = []; renderWorkspaceTarget();
assert.equal(document.getElementById('selected-tonie-name').textContent, 'Choose a Tonie');
""")


def test_workspace_markup_has_named_views_sources_and_player_outside_views():
    class Tree(HTMLParser):
        def __init__(self):
            super().__init__(); self.stack = []; self.ids = {}; self.audio_ancestors = []
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if 'id' in attrs: self.ids[attrs['id']] = attrs
            if tag == 'audio': self.audio_ancestors = list(self.stack)
            if tag not in {'img','input','meta','link','br','hr','source'}:
                self.stack.append(attrs.get('id', ''))
        def handle_endtag(self, tag):
            if self.stack: self.stack.pop()
    tree = Tree(); tree.feed(HTML.read_text())
    for view in ('tracks','add','activity'):
        assert f'view-{view}' in tree.ids
        assert f'nav-{view}' in tree.ids
    assert 'source-youtube' in tree.ids and 'source-local' in tree.ids
    assert not any(x.startswith('view-') or x.startswith('source-') for x in tree.audio_ancestors)
    assert tree.ids['query-input'].get('aria-label') == 'Search YouTube'
    assert tree.ids['file-filter'].get('aria-label') == 'Filter local files'


def test_local_multiselect_add_captures_target_and_blocks_repeat_taps():
    run_js("""
filesCache = [{name:"Kid's <song>.mp3"},{name:'b.mp3'}];
toggleLocalSelection("Kid's <song>.mp3", true); toggleLocalSelection('b.mp3', true);
let finish; const calls = [];
uploadExisting = async (name, options) => { calls.push({name,...options}); if (calls.length === 1) await new Promise(r => finish = r); };
const first = addSelectedLocalFiles(); await Promise.resolve();
selectedToniesUrl = 'tonie-b'; await addSelectedLocalFiles();
finish(); await first;
assert.equal(calls.length, 2); assert.ok(calls.every(c => c.targetUrl === 'tonie-a'));
assert.deepEqual(calls.map(c => c.name), ["Kid's <song>.mp3",'b.mp3']);
assert.equal(localSelectedFiles.size, 0);
""")


def test_bulk_cloud_remove_confirms_count_and_target_and_uses_exact_ids():
    run_js("""
toniesCache = [{edit_url:'tonie-a',name:'Fox'}];
toniesContentCache = {chapters:[{chapter_id:'cloud-a',title:'Same'}, {chapter_id:'cloud-b',title:'Same'}]};
toggleTrackSelection(); toggleTrackSelected('cloud-a',true); toggleTrackSelected('cloud-b',true);
let confirmation; askConfirm = async options => { confirmation = options; return true; };
api = async (path, options) => queueResponse(options);
await deleteSelectedTracks();
assert.match(confirmation.message, /2 tracks/); assert.match(confirmation.message, /Fox/);
assert.equal(actionPosts.length,1); assert.equal(actionPosts[0].target_url,'tonie-a');
assert.deepEqual(actionPosts[0].actions,[{type:'delete',chapter_id:'cloud-a'},{type:'delete',chapter_id:'cloud-b'}]);
await deleteSelectedTracks(); assert.equal(actionPosts.length,1);
""")


def test_bulk_confirmation_does_not_retarget_if_user_changes_tonie():
    run_js("""
toniesContentCache = {chapters:[{chapter_id:'a',title:'A'}]};
toggleTrackSelection(); toggleTrackSelected('a',true);
let confirm; askConfirm = () => new Promise(r => confirm = r);
api = async () => { throw Error('Must not write'); };
const pending = deleteSelectedTracks(); selectedToniesUrl = 'tonie-b'; confirm(true); await pending;
assert.equal(toniesQueuedActions.size,0);
""")


def test_inline_rename_keeps_identity_suffix_and_draft_across_refresh():
    run_js("""
toniesContentCache = {chapters:[{chapter_id:'a',title:'Same'},{chapter_id:'b',title:'Same [ty:abc123]'}]};
beginInlineRename('b'); const edit = getInlineRename(); edit.draft = 'A new story';
toniesContentCache.chapters.reverse(); actualRenderToniesContent();
assert.equal(getInlineRename(),edit); assert.equal(edit.draft,'A new story');
assert.match(toniesContentEl.innerHTML,/tonies-title-inline/);
api = async (path, options) => queueResponse(options);
await saveInlineRename();
assert.equal(actionPosts.length,1); assert.deepEqual(actionPosts[0].actions,[{type:'rename',chapter_id:'b',title:'A new story'}]);
assert.equal(getInlineRename(),null);
""")


def test_inline_rename_escape_cancels_and_failed_save_preserves_draft_without_blind_retry():
    run_js("""
toniesContentCache = {chapters:[{chapter_id:'a',title:'Story'}]};
beginInlineRename('a'); getInlineRename().draft = 'Draft'; cancelInlineRename();
assert.equal(getInlineRename(),null);
beginInlineRename('a'); const state = getInlineRename(); state.draft = 'Keep this';
let calls=0; api = async () => {calls++; throw Error('Connection lost');};
await saveInlineRename();
assert.equal(getInlineRename(),state); assert.equal(state.draft,'Keep this');
assert.match(state.error,/confirm|unknown|refresh/i);
await saveInlineRename(); assert.equal(calls,1);
""")


def test_inline_rename_late_completion_does_not_close_replacement_editor():
    run_js("""
toniesContentCache = {chapters:[{chapter_id:'a',title:'A'},{chapter_id:'b',title:'B'}]};
beginInlineRename('a'); getInlineRename().draft='First';
let resolve; api = async (path, options) => {await new Promise(r => resolve=r); return queueResponse(options);};
const pending=saveInlineRename(); cancelInlineRename(); beginInlineRename('b');
const replacement=getInlineRename(); replacement.draft='Second';
resolve(); await pending; assert.equal(getInlineRename(),replacement); assert.equal(replacement.draft,'Second');
""")


def test_visible_track_actions_and_reverse_display_do_not_mutate_playback():
    run_js("""
toniesContentCache={chapters:[{chapter_id:'a',title:'A'},{chapter_id:'b',title:'B'}]};
actualRenderToniesContent(); assert.match(toniesContentEl.innerHTML,/track-overflow/);
assert.doesNotMatch(toniesContentEl.innerHTML,/onclick="enqueueToniesDelete/);
api = async (path, options) => queueResponse(options);
toniesReverseView=false; renderToniesOrderToggle();
assert.match(toggleToniesOrderEl.textContent,/Reverse display/);
toggleToniesOrderEl.onclick(); assert.equal(actionPosts.length,0);
assert.match(toggleToniesOrderEl.textContent,/playback order/i);
openToniesMenu(0,100,100); await moveToniesFromMenu('up');
assert.equal(actionPosts[0].actions[0].chapter_id,'a');
assert.equal(actionPosts[0].actions[0].type,'move');
""")


def test_activity_retains_partial_failures_and_names_each_target():
    run_js("""
toniesCache=[{edit_url:'tonie-a',name:'Fox'},{edit_url:'tonie-b',name:'Bear'}];
api=async (path,options)=>queueResponse(options);
await submitToniesActions('tonie-a',[{type:'rename',chapter_id:'a',title:'A'},{type:'delete',chapter_id:'b'}]);
finishActions('tonie-a',['act-1'],{chapters:[]});
finishActions('tonie-a',['act-2'],{chapters:[]},'failed','Refused <script>unsafe</script>');
recordWorkspaceAction('tonie-b',{id:'other',type:'move',status:'queued'});
renderWorkspaceActivity();
const html=document.getElementById('workspace-activity').innerHTML;
assert.match(html,/Fox/); assert.match(html,/Bear/); assert.match(html,/Saved/); assert.match(html,/Failed/);
assert.match(html,/&lt;script&gt;/); assert.doesNotMatch(html,/<script>/);
renderWorkspaceActivity(); assert.match(document.getElementById('workspace-activity').innerHTML,/Refused/);
""")


def test_activity_does_not_call_storage_acceptance_finished_processing():
    run_js("""
jobsCache=[{id:'accepted',target_url:'tonie-a',status:'failed',error:'Refresh offline',
  uploaded_chapter_id:'cloud-new',tonies_content:{chapters:[{chapter_id:'cloud-new',processing:true}]},filename:'Story.mp3'},
 {id:'sending',target_url:'tonie-b',status:'uploading',filename:'Song.mp3'}];
renderWorkspaceActivity();
const html=document.getElementById('workspace-activity').innerHTML;
assert.match(html,/Saved.*refresh|Saved.*processing/i); assert.match(html,/Sending/);
assert.match(html,/Do not add again/); assert.doesNotMatch(html,/Retry upload/);
assert.match(document.getElementById('activity-summary').textContent,/1/);
""")


def test_candidate_links_and_tonie_images_reject_active_content_and_escape_attributes():
    run_js(r"""
assert.equal(safeImageSrc('javascript:alert(1)'), '');
assert.equal(safeImageSrc('data:image/svg+xml,<svg onload=alert(1)>'), '');
assert.equal(safeSourceLink('javascript:alert(1)'), '');
const card=renderCandidateCard({id:'root',status:'awaiting_selection'},
 {title:'<script>bad</script>',url:'javascript:alert(1)',uploader:'" onclick="bad',duration:30},0);
assert.doesNotMatch(card,/href="javascript:/); assert.doesNotMatch(card,/<script>/);
const good=renderCandidateCard({id:'root',status:'awaiting_selection'},
 {title:'Story',url:'https://www.youtube.com/watch?v=abc',duration:30},0);
assert.match(good,/href="https:\/\/www.youtube.com/);
""")


def test_local_import_accepted_refresh_failure_is_not_reported_as_rejected():
    run_js("""
fetch=async()=>({ok:true,json:async()=>({filename:'Story.mp3'})});
refreshFiles=async()=>{throw Error('Offline');};
await importWorkspaceFile(Object.assign(new Blob(['audio']), {name:'Story.mp3'}));
const html=document.getElementById('workspace-activity').innerHTML;
assert.match(html,/Imported.*refresh/i); assert.doesNotMatch(html,/Import failed/);
""")


def test_tonie_rail_uses_data_identity_not_executable_url_attributes():
    run_js("""
toniesCache=[{edit_url:`bad'\" onclick=\"alert(1)`,name:'<Fox>',image:'javascript:alert(1)'}];
actualRenderTonies();
const html=document.getElementById('tonies').innerHTML;
assert.doesNotMatch(html,/onclick=/); assert.doesNotMatch(html,/src="javascript:/);
assert.match(html,/&lt;Fox&gt;/);
""")


def test_confirm_dialog_focus_cancel_and_focus_return_are_real():
    run_js("""
const launcher=element(); launcher.focus();
const result=actualAskConfirm({title:'Remove 2 tracks?',message:'From Fox',confirmText:'Remove'});
assert.equal(document.activeElement,confirmCancelEl);
let prevented=false;
document.onkeydown({key:'Tab',shiftKey:true,preventDefault(){prevented=true;}});
assert.equal(document.activeElement,confirmOkEl); assert.equal(prevented,true);
document.onkeydown({key:'Escape'}); assert.equal(await result,false);
assert.equal(document.activeElement,launcher);
""")


def test_inline_rename_refused_write_can_retry_but_acceptance_cannot_duplicate():
    run_js("""
toniesContentCache={chapters:[{chapter_id:'a',title:'Old'}]};
beginInlineRename('a'); getInlineRename().draft='New';
api=async()=>{throw Error('HTTP 422: Title refused');}; await saveInlineRename();
assert.equal(getInlineRename().submitted,false); assert.match(getInlineRename().error,/Title refused/);
api=async(path,options)=>queueResponse(options); await saveInlineRename();
assert.equal(actionPosts.length,1); assert.equal(getInlineRename(),null);
await saveInlineRename(); assert.equal(actionPosts.length,1);
""")


def test_tonie_selector_returns_focus_when_dismissed():
    run_js("""
const target=element(); target.focus();
toggleTonieSelector(true); toggleTonieSelector(false);
assert.equal(document.activeElement, document.getElementById('change-tonie'));
assert.equal(document.getElementById('change-tonie').getAttribute('aria-expanded'),'false');
""")


def test_target_change_refreshes_source_action_labels_without_clearing_search():
    run_js("""
queryInputEl.value='Story'; const rendered=[];
renderFiles=()=>rendered.push(['local',selectedToniesUrl]);
renderJobs=()=>rendered.push(['youtube',selectedToniesUrl]);
refreshToniesContent=async()=>{};
selectTonies('tonie-b');
assert.ok(rendered.some(([kind,url])=>kind==='local' && url==='tonie-b'));
assert.ok(rendered.some(([kind,url])=>kind==='youtube' && url==='tonie-b'));
assert.equal(queryInputEl.value,'Story');
""")


def test_late_rename_completion_does_not_steal_focus_from_replacement():
    run_js("""
toniesContentCache={chapters:[{chapter_id:'a',title:'A'},{chapter_id:'b',title:'B'}]};
beginInlineRename('a'); getInlineRename().draft='First';
let resolve; api=async(path,options)=>{await new Promise(r=>resolve=r);return queueResponse(options);};
const pending=saveInlineRename();cancelInlineRename();beginInlineRename('b');
let focusCalls=0;focusTrackAction=()=>focusCalls++;
resolve();await pending;assert.equal(focusCalls,0);
""")


def test_activity_error_can_be_dismissed_without_replaying_any_action():
    run_js("""
recordWorkspaceAction('tonie-a',{id:'failed',type:'delete',status:'failed',error:'Refused'});
renderWorkspaceActivity();assert.match(document.getElementById('workspace-activity').innerHTML,/Refused/);
api=async()=>{throw Error('Dismiss must not call API');};
dismissWorkspaceActivity('action:failed');
assert.doesNotMatch(document.getElementById('workspace-activity').innerHTML,/Refused/);
assert.equal(workspaceActionHistory.get('failed').status,'failed');
""")


def test_accepted_local_upload_with_failed_refresh_stays_pending_and_cannot_duplicate():
    run_js("""
let posts=0;api=async(path,options)=>{assert.equal(path,'/api/upload-existing');posts++;return {job_id:'accepted-job'};};
refreshJobs=async()=>{throw Error('Refresh offline');};
await uploadExisting('Story.mp3');
assert.equal(localUploadOutstandingByName['Story.mp3'],1);
assert.match(document.getElementById('workspace-activity').innerHTML,/Accepted.*refresh/i);
await uploadExisting('Story.mp3');assert.equal(posts,1);
""")


def test_local_upload_repeated_taps_are_deduplicated_until_terminal_receipt():
    run_js("""
let posts=0;api=async()=>({job_id:'job-'+(++posts)});refreshJobs=async()=>{};
await uploadExisting('Story.mp3');await uploadExisting('Story.mp3');
assert.equal(posts,1);
resolveLocalUploadPlanned('Story.mp3');
uploadIntentsByJobId.clear();
await uploadExisting('Story.mp3');assert.equal(posts,2);
""")


def test_selected_local_destination_keeps_accessible_full_name_when_visual_label_is_clipped():
    run_js("""
const name='The woodland adventures and bedtime stories collection for the whole family';
toniesCache=[{edit_url:'tonie-a',name}];filesCache=[{name:'Story.mp3'}];
toggleLocalSelection('Story.mp3',true);
const button=document.getElementById('add-selected-local');
assert.equal(button.getAttribute('title'),'Add to '+name);
assert.equal(button.getAttribute('aria-label'),'Add 1 file to '+name);
""")


def test_undo_is_one_server_owned_capability_per_target_and_expires():
    run_js("""
toniesCache=[{edit_url:'tonie-a',name:'Fox'}];
const expiry=new Date(Date.now()+60000).toISOString();
recordWorkspaceAction('tonie-a',{id:'a',type:'rename',status:'done',undo_available:true,undo_expires_at:expiry});
recordWorkspaceAction('tonie-a',{id:'b',type:'move',status:'done',undo_available:true,undo_expires_at:expiry});
renderWorkspaceUndo();
assert.equal(workspaceUndoAction('tonie-a').id,'b');
assert.equal(document.getElementById('workspace-undo').hidden,false);
assert.match(document.getElementById('workspace-undo-label').textContent,/Fox/);
selectedToniesUrl='tonie-b';renderWorkspaceUndo();
assert.equal(document.getElementById('workspace-undo').hidden,true);
selectedToniesUrl='tonie-a';
recordWorkspaceAction('tonie-a',{id:'b',status:'done',undo_available:false,undo_expires_at:null});
recordWorkspaceAction('tonie-a',{id:'a',status:'done',undo_available:true,undo_expires_at:new Date(Date.now()-1000).toISOString()});
assert.equal(workspaceUndoAction('tonie-a'),null);
""")


def test_real_undo_posts_only_action_id_once_and_clears_canceled_overlays():
    run_js("""
toniesContentCache={chapters:[{chapter_id:'keep',title:'Keep'}]};
setCachedToniesContent('tonie-a',toniesContentCache);
const action={id:'remove/exact',type:'delete',status:'queued',params:{chapter_id:'keep'},undo_available:true,undo_pending:true,undo_expires_at:new Date(Date.now()+5000).toISOString()};
recordWorkspaceAction('tonie-a',action);
toniesQueuedActions.set(action.id,{id:action.id,url:'tonie-a',type:'delete',params:action.params});
let finish,posts=0; api=async(path,options)=>{
assert.equal(path,'/api/tonies-actions/remove%2Fexact/undo');assert.equal(options.method,'POST');
assert.equal(options.body,undefined);posts++;await new Promise(r=>finish=r);
return {actions:[{...action,status:'canceled',undo_available:false,undo_pending:false}]};};
refreshToniesContent=async()=>{};
const pending=undoWorkspaceAction();await undoWorkspaceAction();assert.equal(posts,1);
finish();await pending;
assert.equal(toniesQueuedActions.size,0);
assert.equal(workspaceUndoAction('tonie-a'),null);
assert.equal(workspaceActionActivity(workspaceActionHistory.get(action.id)).stage,'Canceled');
""")


def test_undo_lost_acknowledgment_is_not_retried_and_retains_attention_notice():
    run_js("""
recordWorkspaceAction('tonie-a',{id:'rename',type:'rename',status:'done',undo_available:true,undo_expires_at:new Date(Date.now()+60000).toISOString()});
let posts=0;api=async()=>{posts++;throw Error('Connection lost');};
await undoWorkspaceAction();await undoWorkspaceAction();assert.equal(posts,1);
assert.match(document.getElementById('workspace-activity').innerHTML,/Undo.*unconfirmed|Could not confirm Undo/i);
assert.match(document.getElementById('workspace-activity').innerHTML,/Refresh/);
""")


def test_undone_terminal_receipt_cannot_regress_to_queued_or_show_saved():
    run_js("""
recordWorkspaceAction('tonie-a',{id:'a',type:'rename',status:'undone',undo_available:false});
recordWorkspaceAction('tonie-a',{id:'a',type:'rename',status:'queued',undo_available:true});
assert.equal(workspaceActionHistory.get('a').status,'undone');
assert.equal(workspaceActionActivity(workspaceActionHistory.get('a')).stage,'Undone');
assert.equal(workspaceActionActivity(workspaceActionHistory.get('a')).active,false);
""")


def test_source_selection_success_captures_target_and_blocks_only_the_same_candidate():
    run_js("""
toniesCache=[{edit_url:'tonie-a',name:'Fox'},{edit_url:'tonie-b',name:'Bear'}];
jobsCache=[{id:'root/exact',status:'awaiting_selection',candidates:[{title:'First'},{title:'Second'}]}];
let finish;const requests=[];
api=async(path,options)=>{requests.push({path,payload:JSON.parse(options.body)});await new Promise(r=>finish=r);return {id:'child',status:'queued',target_url:'tonie-a'};};
refreshJobs=async()=>{};
const pending=pickCandidate('root/exact',0);
assert.match(renderCandidateCard(jobsCache[0],jobsCache[0].candidates[0],0),/Sending selection/);
selectedToniesUrl='tonie-b';finish();await pending;
assert.equal(requests[0].path,'/api/jobs/root%2Fexact/select');
assert.equal(requests[0].payload.target_url,'tonie-a');
assert.equal(uploadIntentsByJobId.get('child').targetUrl,'tonie-a');
await pickCandidate('root/exact',0);assert.equal(requests.length,1);
assert.equal(workspaceCandidateAttempt('root/exact',1),undefined);
""")


def test_source_tabs_keep_44px_touch_targets_on_tablet_and_desktop():
    import re
    css=(HTML.parent/'assets'/'workspace.css').read_text()
    match=re.search(r'\.source-tabs button\s*\{([^}]+)\}',css)
    assert match is not None
    assert 'min-height:44px' in match.group(1)


def test_source_selection_refusal_restores_results_and_retains_attention():
    run_js("""
jobsCache=[{id:'root',status:'awaiting_selection',candidates:[{title:'Story',url:'https://youtube.com/watch?v=synthetic'}]}];
let calls=0;api=async()=>{calls++;throw Error('HTTP 422: Synthetic refusal');};
await pickCandidate('root',0);
assert.equal(jobsCache[0].status,'awaiting_selection');
assert.match(document.getElementById('workspace-activity').innerHTML,/Selection refused/);
await pickCandidate('root',0);assert.equal(calls,2);
""")


def test_source_selection_lost_acknowledgment_never_offers_duplicate_add():
    run_js("""
jobsCache=[{id:'root',status:'awaiting_selection',candidates:[{title:'Story',url:'https://youtube.com/watch?v=synthetic'}]}];
let calls=0;api=async()=>{calls++;throw Error('Connection lost');};
await pickCandidate('root',0);await pickCandidate('root',0);assert.equal(calls,1);
assert.equal(jobsCache[0].status,'awaiting_selection');
assert.match(renderCandidateCard(jobsCache[0],jobsCache[0].candidates[0],0),/Check Activity/);
assert.match(document.getElementById('workspace-activity').innerHTML,/unconfirmed/i);
""")


def test_source_selection_accepted_failed_refresh_is_not_reported_as_refused_or_replayed():
    run_js("""
jobsCache=[{id:'root',status:'awaiting_selection',candidates:[{title:'Story',url:'https://youtube.com/watch?v=synthetic'}]}];
let calls=0;api=async()=>{calls++;return {id:'child',status:'queued',target_url:'tonie-a'};};
refreshJobs=async()=>{throw Error('Refresh offline');};
await pickCandidate('root',0);await pickCandidate('root',0);assert.equal(calls,1);
assert.match(document.getElementById('workspace-activity').innerHTML,/Selection accepted.*refresh/i);
assert.doesNotMatch(document.getElementById('workspace-activity').innerHTML,/Selection refused/);
""")


def test_undo_terminal_receipt_and_newer_capability_survive_stale_responses():
    run_js("""
recordWorkspaceAction('tonie-a',{id:'old',type:'rename',status:'undone',undo_available:false});
recordWorkspaceAction('tonie-a',{id:'old',type:'rename',status:'done',undo_available:true});
assert.equal(workspaceActionHistory.get('old').status,'undone');
let reject;api=()=>new Promise((_,r)=>reject=r);
const pending=refreshWorkspaceActions();
recordWorkspaceAction('tonie-a',{id:'new',type:'move',status:'done',undo_available:true,undo_expires_at:new Date(Date.now()+60000).toISOString()});
reject(Error('Read offline'));await pending;
assert.equal(workspaceUndoAction('tonie-a').id,'new');
""")


def test_single_track_removal_names_the_destination_and_irreversible_boundary():
    run_js("""
toniesCache=[{edit_url:'tonie-a',name:'Fox'}];
toniesContentCache={chapters:[{chapter_id:'a',title:'A story'}]};
let confirmation;askConfirm=async options=>{confirmation=options;return false;};
await enqueueToniesDelete(0);
assert.match(confirmation.message,/Fox/);assert.match(confirmation.message,/Once sent.*cannot be undone/i);
assert.equal(actionPosts.length,0);
""")


def test_actual_canceled_receipt_is_terminal_and_clears_pending_overlay():
    run_js("""
toniesQueuedActions.set('pending',{id:'pending',url:'tonie-a',type:'delete',params:{chapter_id:'a'}});
handleToniesActionsEvent({target_url:'tonie-a',actions:[{id:'pending',type:'delete',status:'canceled',undo_available:false}]});
assert.equal(toniesQueuedActions.size,0);
const entry=workspaceActionActivity(workspaceActionHistory.get('pending'));
assert.equal(entry.stage,'Canceled');assert.equal(entry.active,false);
""")


def test_bulk_confirm_keeps_launcher_focusable_until_user_confirms():
    run_js("""
toniesContentCache={chapters:[{chapter_id:'a',title:'A'}]};
toggleTrackSelection();toggleTrackSelected('a',true);
askConfirm=async()=>{
assert.equal(document.getElementById('delete-selected-tracks').disabled,false);
return false;};
await deleteSelectedTracks();
assert.equal(document.getElementById('delete-selected-tracks').disabled,false);
""")


def test_source_thumbnail_is_retained_only_when_supplied_and_safe():
    run_js(r"""
const parent={id:'search',status:'awaiting_selection'};
const card=renderCandidateCard(parent,{title:'Story',thumbnail:'https://images.example.invalid/story.jpg',uploader:'Story channel',duration:83},0);
assert.match(card,/<img[^>]+src="https:\/\/images.example.invalid\/story.jpg"/);
assert.match(card,/Story channel/);assert.match(card,/1:23|01:23/);
assert.doesNotMatch(renderCandidateCard(parent,{title:'Story',thumbnail:'javascript:alert(1)'},0),/<img/);
assert.doesNotMatch(renderCandidateCard(parent,{title:'Story'},0),/<img/);
""")


def test_preview_reserves_space_after_the_shared_footer_not_only_before_it():
    css=(HTML.parent/'assets'/'workspace.css').read_text()
    assert 'body:has(.local-player:not([hidden]))' in css
    assert 'padding-bottom: calc(140px + env(safe-area-inset-bottom' in css


def test_primary_view_navigation_starts_at_top_without_native_focus_scroll():
    run_js("""
let scroll=null,focus=null;
window.scrollTo=options=>{scroll=options;};
document.getElementById('view-tracks').focus=options=>{focus=options;};
setWorkspaceView('tracks',true);
assert.deepEqual(focus,{preventScroll:true});assert.equal(scroll.top,0);
""")
