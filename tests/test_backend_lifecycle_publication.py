"""Offline queue publication ordering and linked-job attachment evidence."""
import copy
import socket
import threading
from pathlib import Path

import httpx
import pytest

from app import events, job_store as jobs, job_upload
from app.actions import Action, ActionQueue
from app.models import JobState
from tonies_fake import URL


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Lifecycle regressions forbid real sockets")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(jobs, "_jobs", {})
    monkeypatch.setattr(jobs, "persist_state", lambda: None)
    monkeypatch.setattr(jobs, "emit_job_event", lambda *args, **kwargs: None)


@pytest.fixture
def queue(cloud_api, monkeypatch):
    _, api = cloud_api
    q = ActionQueue(lambda: api, batch_window=0)
    monkeypatch.setattr(q, "_ensure_worker", lambda _: None)
    monkeypatch.setattr(q, "_transfer", lambda _: None)
    yield q
    q._transfers.shutdown(wait=True)


def apply_ready(q):
    with q._cond:
        batch = q._ready_batch(URL)
        for action in batch:
            q._set(action, "applying")
    q._apply(URL, batch)


@pytest.mark.parametrize("inverse", ["confirmed", "unconfirmed", "newer_forward"])
def test_delayed_forward_notification_never_publishes_superseded_inventory(queue, cloud_api, monkeypatch, inverse):
    cloud, _ = cloud_api
    action = queue.submit(URL, [{"type": "rename", "chapter_id": "one", "title": "Changed"}])[0]
    packets, errors = [], []
    monkeypatch.setattr(events, "publish", lambda kind, payload: packets.append(copy.deepcopy(payload)))
    entered, release = threading.Event(), threading.Event()
    notify = queue._notify

    def delayed(batch, content=None):
        if threading.current_thread().name == "delayed-forward":
            entered.set()
            assert release.wait(3)
        notify(batch, content)

    monkeypatch.setattr(queue, "_notify", delayed)

    def forward():
        try:
            apply_ready(queue)
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(name="delayed-forward", target=forward)
    thread.start()
    try:
        assert entered.wait(3)
        assert action.status == "done" and action.public()["undo_available"]
        if inverse == "unconfirmed":
            cloud.patch_timeout = True
            with pytest.raises(RuntimeError, match="unconfirmed"):
                queue.undo(action.id)
        else:
            queue.undo(action.id)
            assert cloud.chapters[0]["title"] == "Same"
            if inverse == "newer_forward":
                queue.submit(URL, [{"type": "rename", "chapter_id": "one", "title": "Newest"}])
                apply_ready(queue)
    finally:
        release.set()
        thread.join(3)
    assert not thread.is_alive() and not errors
    # Real cloud responses and real queue packets, not fabricated event envelopes.
    assert packets
    assert "content" not in packets[-1], "Late forward content must be suppressed after inverse ownership changes"
    if inverse == "unconfirmed":
        assert packets[-1]["actions"][0]["needs_refresh"] is True
        assert all("content" not in packet for packet in packets)
    else:
        assert packets[-1]["actions"][0]["status"] == "undone"
        inventories = [packet["content"] for packet in packets if "content" in packet]
        assert inventories[-1]["chapters"][0]["title"] == ("Newest" if inverse == "newer_forward" else "Same")


def test_delayed_inverse_notification_cannot_overwrite_newer_forward(queue, cloud_api, monkeypatch):
    cloud, _ = cloud_api
    action = queue.submit(URL, [{"type": "rename", "chapter_id": "one", "title": "Changed"}])[0]
    apply_ready(queue)
    packets, errors = [], []
    monkeypatch.setattr(events, "publish", lambda kind, payload: packets.append(copy.deepcopy(payload)))
    entered, release = threading.Event(), threading.Event()
    notify = queue._notify

    def delayed(batch, content=None):
        if threading.current_thread().name == "delayed-inverse":
            entered.set()
            assert release.wait(3)
        notify(batch, content)

    monkeypatch.setattr(queue, "_notify", delayed)

    def inverse():
        try:
            queue.undo(action.id)
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(name="delayed-inverse", target=inverse)
    thread.start()
    try:
        assert entered.wait(3)
        queue.submit(URL, [{"type": "rename", "chapter_id": "one", "title": "Newest"}])
        apply_ready(queue)
    finally:
        release.set()
        thread.join(3)
    assert not thread.is_alive() and not errors
    assert cloud.chapters[0]["title"] == "Newest"
    assert "content" not in packets[-1]
    assert [p["content"]["chapters"][0]["title"] for p in packets if "content" in p] == ["Newest"]


@pytest.mark.parametrize("starting_status", ["waiting_upload", "failed"])
@pytest.mark.parametrize("failure", ["readback", "rejected", "timeout", "confirmed"])
def test_real_attachment_receipt_mirrors_to_job_without_inventing_inventory(queue, cloud_api, monkeypatch, tmp_path, starting_status, failure):
    cloud, api = cloud_api
    original = cloud.__call__

    def handler(request):
        if failure == "readback" and cloud.patches and request.url.path == "/v2/graphql":
            raise httpx.ReadTimeout("synthetic unavailable readback", request=request)
        if failure == "rejected" and request.method == "PATCH":
            return httpx.Response(400)
        return original(request)

    cloud.patch_timeout = failure == "timeout"
    api._client.close()
    api._client = httpx.Client(transport=httpx.MockTransport(handler))
    job = JobState(id="linked-job", status=starting_status, created_at=events.now_iso(),
                   updated_at=events.now_iso(), user_message="upload existing: synthetic.mp3", target_url=URL)
    jobs.add_job(job)
    action = queue.submit_upload(URL, Path("synthetic.mp3"), "Synthetic", on_change=lambda a: job_upload._on_upload_action(job.id, a))
    queue._transfers.shutdown(wait=True)
    job.tonies_action_id = action.id
    action.params.update(file_id="staging", seconds=1)
    action.status = "ready"
    apply_ready(queue)
    if failure == "readback":
        assert cloud.chapters[-1]["id"] == "permanent"
        assert action.status == job.status == "failed"
        assert action.attachment_receipt and action.chapter_id == "permanent"
        assert job.uploaded_chapter_id == "permanent"
        assert job.attachment_receipt == action.attachment_receipt
        assert job.attachment_receipt is not action.attachment_receipt
        assert job.needs_refresh is True and job.tonies_content is None
        assert "Do not upload again" in job.error
        # Persist/reload uses the public JobState schema, without private scratch support.
        state_file = tmp_path / "jobs.json"
        monkeypatch.setattr(jobs, "_JOBS_STATE_FILE", state_file)
        persist = __import__("json").dumps([job.model_dump()])
        state_file.write_text(persist)
        jobs.load_state()
        restored = jobs.get_job(job.id)
        assert restored.attachment_receipt == action.attachment_receipt
        assert restored.uploaded_chapter_id == "permanent" and restored.needs_refresh
        assert restored.tonies_content is None
        action.attachment_receipt["chapters"].clear()
        assert job.attachment_receipt["chapters"]
    elif failure == "confirmed":
        assert job.uploaded_chapter_id == "permanent" and job.tonies_content
        assert not job.needs_refresh and job.attachment_receipt is None
        assert job.status == ("failed" if starting_status == "failed" else "done")
    else:
        assert job.status == "failed" and job.uploaded_chapter_id is None
        assert job.attachment_receipt is None and job.tonies_content is None


@pytest.mark.parametrize("terminal", ["undone", "canceled"])
def test_terminal_upload_mirror_clears_receipt_and_rejects_stale_acceptance(queue, monkeypatch, terminal):
    job = JobState(id="linked", status="uploading", created_at=events.now_iso(), updated_at=events.now_iso(), user_message="synthetic")
    jobs.add_job(job)
    action = Action("upload", 1, URL, "upload", {}, status="failed", chapter_id="permanent",
                    attachment_receipt={"chapters": [{"id": "permanent"}]}, needs_refresh=True)
    job_upload._on_upload_action(job.id, action)
    assert job.uploaded_chapter_id == "permanent" and job.attachment_receipt
    stale = copy.copy(action)
    action.status = terminal
    job_upload._on_upload_action(job.id, action)
    assert job.status == ("undone" if terminal == "undone" else "canceled")
    assert job.uploaded_chapter_id is None and job.attachment_receipt is None
    assert job.tonies_content is None and not job.needs_refresh
    job_upload._on_upload_action(job.id, stale)
    assert job.uploaded_chapter_id is None and job.attachment_receipt is None
    assert job.tonies_content is None and not job.needs_refresh
