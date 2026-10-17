"""Workspace SPEC regressions; real page controller, synthetic offline boundaries only."""
import pytest

from test_frontend_contracts import run_js


def test_local_lost_acknowledgment_keeps_source_and_captured_target_protected():
    run_js("""
let posts=0;
api=async(path,options)=>{assert.equal(path,'/api/upload-existing');posts++;throw Error('Connection lost after request was sent');};
await uploadExisting('Story.mp3');
selectedToniesUrl='tonie-b';
await uploadExisting('Story.mp3');
assert.equal(posts,1,'Ambiguous acknowledgment must not authorize a second Add');
assert.equal(localUploadOutstandingByName['Story.mp3'],1);
assert.equal(pendingDropUploadIntent.targetUrl,'tonie-a');
assert.match(document.getElementById('workspace-activity').innerHTML,/Do not add again/);
// A different source retains its own identity and destination.
await uploadExisting('Other.mp3');
assert.equal(posts,2);assert.equal(pendingDropUploadIntent.targetUrl,'tonie-b');
""")


@pytest.mark.parametrize('status', [400, 401, 403, 404, 409, 413, 422])
def test_explicit_local_refusal_releases_source_for_deliberate_retry(status):
    run_js("""
let posts=0;api=async()=>{posts++;throw Error('HTTP STATUS: Synthetic refusal');};
await uploadExisting('Story.mp3');
assert.equal(localUploadOutstandingByName['Story.mp3']||0,0);
assert.equal(pendingDropUploadIntent,null);
await uploadExisting('Story.mp3');assert.equal(posts,2);
assert.match(document.getElementById('workspace-activity').innerHTML,/Synthetic refusal/);
""".replace('STATUS', str(status)))


@pytest.mark.parametrize('response', ['null', '{}'])
def test_missing_job_acknowledgment_is_ambiguous_not_retryable(response):
    run_js("""
let posts=0;api=async()=>{posts++;return RESPONSE;};refreshJobs=async()=>{};
await uploadExisting('Story.mp3');await uploadExisting('Story.mp3');
assert.equal(posts,1);assert.equal(localUploadOutstandingByName['Story.mp3'],1);
assert.match(document.getElementById('workspace-activity').innerHTML,/unconfirmed|confirm upload/i);
""".replace('RESPONSE', response))


def test_accepted_local_job_failed_refresh_remains_blocked_control():
    run_js("""
let posts=0;api=async()=>({job_id:'accepted-'+(++posts)});
refreshJobs=async()=>{throw Error('Read offline');};
await uploadExisting('Story.mp3');await uploadExisting('Story.mp3');
assert.equal(posts,1);assert.equal(localUploadOutstandingByName['Story.mp3'],1);
assert.match(document.getElementById('workspace-activity').innerHTML,/Accepted.*refresh/i);
""")


@pytest.mark.parametrize('new_first', [False, True])
def test_offscreen_latest_started_refresh_wins_in_either_completion_order(new_first):
    run_js("""
const pending=[];api=()=>new Promise(resolve=>pending.push(resolve));
const old=refreshToniesContent({targetUrl:'tonie-b',force:true});
const newer=refreshToniesContent({targetUrl:'tonie-b',force:true});
const responses=[{chapters:[{chapter_id:'old'}]},{chapters:[{chapter_id:'new'}]}];
ORDER
assert.equal(getCachedToniesContent('tonie-b').chapters[0].chapter_id,'new');
assert.equal(selectedToniesUrl,'tonie-a');
""".replace('ORDER', "pending[1](responses[1]);await newer;pending[0](responses[0]);await old;" if new_first else "pending[0](responses[0]);await old;pending[1](responses[1]);await newer;"))


def test_independent_target_reads_do_not_supersede_each_other_or_newer_events():
    run_js("""
const pending=[];api=()=>new Promise(resolve=>pending.push(resolve));
const a=refreshToniesContent({targetUrl:'tonie-a',force:true});
const b=refreshToniesContent({targetUrl:'tonie-b',force:true});
pending[1]({chapters:[{chapter_id:'b'}]});await b;
pending[0]({chapters:[{chapter_id:'a'}]});await a;
assert.equal(getCachedToniesContent('tonie-a').chapters[0].chapter_id,'a');
assert.equal(getCachedToniesContent('tonie-b').chapters[0].chapter_id,'b');
const stale=refreshToniesContent({targetUrl:'tonie-b',force:true});
applyToniesContent('tonie-b',{chapters:[{chapter_id:'event'}]});
pending[2]({chapters:[{chapter_id:'stale'}]});await stale;
assert.equal(getCachedToniesContent('tonie-b').chapters[0].chapter_id,'event');
""")


def test_first_target_discovery_enables_already_rendered_local_add_controls():
    run_js("""
selectedToniesUrl='';toniesCache=[];filesCache=[{name:'Story.mp3'}];
renderFiles=actualRenderFiles;renderTonies=actualRenderTonies;
renderFiles();assert.match(filesEl.innerHTML,/local-upload-btn[^>]+disabled/);
api=async()=>[{edit_url:'tonie-a',name:'Fox'}];syncCreativeToniesStats=async()=>{};
await refreshTonies({force:true});
assert.equal(selectedToniesUrl,'tonie-a');
assert.doesNotMatch(filesEl.innerHTML,/local-upload-btn[^>]+disabled/);
assert.match(filesEl.innerHTML,/Add Story.mp3 to Fox/);
""")


def test_stale_jobs_poll_cannot_restore_pre_undo_saved_receipt():
    run_js("""
const before={id:'job',target_url:'tonie-a',status:'done',tonies_action_id:'action',uploaded_chapter_id:'uploaded',tonies_content:{chapters:[{chapter_id:'uploaded'}]}};
const after={...before,status:'undone',uploaded_chapter_id:null,tonies_content:null,cancel_requested:true};
const reads=[];api=async path=>{assert.equal(path,'/api/jobs');return new Promise(resolve=>reads.push(resolve));};
renderJobs=()=>renderWorkspaceActivity();
const stale=refreshJobs({background:true});const fresh=refreshJobs({background:true});
reads[1]([after]);await fresh;reads[0]([before]);await stale;
assert.equal(jobsCache[0].status,'undone');assert.equal(jobsCache[0].uploaded_chapter_id,null);
const entry=workspaceActivityEntries().find(e=>e.id==='job:job');
assert.equal(entry.active,false);assert.doesNotMatch(entry.stage,/Saved/);
""")


@pytest.mark.parametrize('status', ['undone', 'canceled'])
def test_reversed_job_activity_never_regresses_on_a_later_stale_snapshot(status):
    run_js("""
const reversed={id:'job',status:'STATUS',uploaded_chapter_id:null,tonies_content:null};
jobsCache=[reversed];renderWorkspaceActivity();
api=async()=>[{...reversed,status:'done',uploaded_chapter_id:'old',tonies_content:{chapters:[{chapter_id:'old'}]}}];
await refreshJobs({background:true});renderWorkspaceActivity();
assert.equal(jobsCache[0].status,'STATUS');
assert.equal(workspaceJobHistory.get('job').status,'STATUS');
""".replace('STATUS', status))


@pytest.mark.parametrize('readback', ['offline', 'missing', 'confirmed'])
def test_structured_accepted_unconfirmed_receipt_is_not_inventory_or_retry_permission(readback):
    run_js("""
const receipt={chapter_id:'permanent',accepted:true};
const job={id:'upload-job',target_url:'tonie-a',filename:'Story.mp3',status:'failed',
  tonies_action_id:'upload-action',uploaded_chapter_id:'permanent',attachment_receipt:receipt,
  tonies_content:null,needs_refresh:true,error:'Tonies accepted the upload attachment. Do not upload again.'};
jobsCache=[job];
recordWorkspaceAction('tonie-a',{id:'upload-action',type:'upload',status:'failed',attachment_receipt:receipt,needs_refresh:true});
markLocalUploadPlanned('Story.mp3');activeUploadJobIds.add(job.id);
uploadIntentsByJobId.set(job.id,{jobId:job.id,filename:'Story.mp3',targetUrl:'tonie-a'});
setCachedToniesContent('tonie-a',{chapters:[{chapter_id:'existing'}]});
let posts=0;
api=async(path,options)=>{
 if(path==='/api/upload-existing'){posts++;return {job_id:'duplicate'};}
 assert.equal(path,'/api/tonies-content');
 READBACK
};
processTrackedJobTransitions(jobsCache);
await Promise.resolve();await Promise.resolve();await Promise.resolve();await Promise.resolve();
await uploadExisting('Story.mp3');
assert.equal(posts,0,'A historical accepted receipt must not invite another Add');
const entries=workspaceActivityEntries();assert.equal(entries.length>=1,true);
const visible=entries.find(e=>e.id==='job:upload-job');
assert.match(visible.stage,/Saved.*refresh|accepted.*refresh/i);assert.match(visible.note,/Do not add again/);
assert.equal(entries.some(e=>e.id==='action:upload-action'),false);
assert.equal(getCachedToniesContent('tonie-a').chapters.some(c=>c.chapter_id==='fabricated'),false);
""".replace('READBACK', {
        'offline': "throw Error('Read offline');",
        'missing': "return {chapters:[{chapter_id:'existing'}]};",
        'confirmed': "return {chapters:[{chapter_id:'existing'},{chapter_id:'permanent'}]};",
    }[readback]))


def test_reversed_receipt_is_not_saved_and_explicit_failed_job_remains_retryable_control():
    run_js("""
for(const status of ['undone','canceled']){
 const job={id:status,status,attachment_receipt:{chapter_id:'old'},uploaded_chapter_id:'old',tonies_content:{chapters:[]}};
 assert.equal(hasUploadAttachmentReceipt(job),false);assert.doesNotMatch(workspaceJobActivity(job).stage,/Saved/);
}
const job={id:'refused',status:'failed',target_url:'tonie-a',error:'Explicit server refusal'};
jobsCache=[job];markLocalUploadPlanned('Story.mp3');activeUploadJobIds.add(job.id);
uploadIntentsByJobId.set(job.id,{filename:'Story.mp3',targetUrl:'tonie-a'});
processTrackedJobTransitions(jobsCache);await Promise.resolve();await Promise.resolve();
let posts=0;api=async()=>({job_id:'retry-'+(++posts)});refreshJobs=async()=>{};
await uploadExisting('Story.mp3');assert.equal(posts,1);
""")
