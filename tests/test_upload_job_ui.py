"""Upload cancellation/Undo receipts settle the real workspace controller."""
import pytest

from test_frontend_contracts import run_js


@pytest.mark.parametrize("status,label", [("canceled", "Canceled"), ("undone", "Undone")])
def test_reversed_upload_job_is_terminal_and_settles_without_cloud_replay(status, label):
    run_js("""
const job={id:'reversed',status:STATUS,target_url:'tonie-a',error:null,
  uploaded_chapter_id:'historical',tonies_content:{chapters:[{chapter_id:'historical'}]}};
assert.equal(hasUploadAttachmentReceipt(job),false);
const view=workspaceJobActivity(job);
assert.equal(view.stage,LABEL); assert.equal(view.active,false);
startPendingToniesUpload('Track.mp3','tonie-a'); pendingToniesUpload.jobId=job.id;
activeUploadJobIds.add(job.id);
uploadIntentsByJobId.set(job.id,{filename:'Track.mp3',targetUrl:'tonie-a',targetEdge:'end'});
let cloudCalls=0; api=async()=>{cloudCalls++; throw new Error('No cloud calls expected');};
jobsCache=[job];
processTrackedJobTransitions(jobsCache);
await new Promise(resolve=>setImmediate(resolve));
assert.equal(activeUploadJobIds.has(job.id),false);
assert.equal(uploadIntentsByJobId.has(job.id),false);
assert.equal(pendingToniesUpload,null);
assert.equal(cloudCalls,0);
assert.equal([...workspaceNotices.values()].some(n=>n.error),false);
""".replace("STATUS", repr(status)).replace("LABEL", repr(label)))
