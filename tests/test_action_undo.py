"""Offline, server-owned one-level Undo; deleted cloud audio is never recovered."""
import copy
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest

from app.actions import ActionQueue
from tonies_fake import URL, chapter


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Undo tests must not open sockets")
    monkeypatch.setattr(socket.socket, "connect", denied)


@pytest.fixture
def queue(cloud_api, monkeypatch):
    _, api = cloud_api
    q = ActionQueue(lambda: api, batch_window=0)
    # Deterministic queue driving: ordinary production _apply, no sleeps/real deadlines.
    monkeypatch.setattr(q, "_ensure_worker", lambda target: None)
    yield q
    q._transfers.shutdown(wait=True)


def apply_ready(q):
    with q._cond:
        batch = q._ready_batch(URL)
        for action in batch:
            q._set(action, "applying")
    if batch:
        q._apply(URL, batch)
    return batch


def test_rename_move_submission_is_one_undo_not_the_network_batch(queue, cloud_api):
    cloud, _ = cloud_api
    cloud.chapters.append(chapter("three", "Third"))
    earlier = queue.submit(URL, [{"type": "rename", "chapter_id": "two", "title": "Keep"}])
    group = queue.submit(URL, [
        {"type": "rename", "chapter_id": "one", "title": "Changed"},
        {"type": "move", "chapter_id": "one", "position": {"edge": "end"}},
    ])
    apply_ready(queue)
    assert group[0].public().get("undo_available") is True
    assert earlier[0].public().get("undo_available") is False
    assert queue.undo(group[1].id) == group
    assert [c["id"] for c in cloud.chapters] == ["one", "two", "three"]
    assert [c["title"] for c in cloud.chapters] == ["Same", "Keep", "Third"]
    assert len(cloud.patches) == 2
    assert all(a.status == "undone" and not a.public()["undo_available"] for a in group)
    with pytest.raises((ValueError, RuntimeError), match="Undo"):
        queue.undo(group[0].id)
    assert len(cloud.patches) == 2


@pytest.mark.parametrize("change", ["title", "file", "seconds", "order", "extra", "missing"])
def test_stale_inventory_refuses_inverse_without_patch(queue, cloud_api, change):
    cloud, _ = cloud_api
    group = queue.submit(URL, [{"type": "rename", "chapter_id": "one", "title": "Changed"}])
    apply_ready(queue)
    assert group[0].public().get("undo_available") is True
    if change == "order":
        cloud.chapters.reverse()
    elif change == "extra":
        cloud.chapters.append(chapter("foreign"))
    elif change == "missing":
        cloud.chapters.pop()
    else:
        cloud.chapters[1][change] = 13 if change == "seconds" else "outside"
    snapshot = copy.deepcopy(cloud.chapters)
    with pytest.raises(RuntimeError, match="changed|stale"):
        queue.undo(group[0].id)
    assert cloud.chapters == snapshot and len(cloud.patches) == 1
    assert group[0].public()["undo_available"] is False


def test_exact_native_title_is_restored_not_renormalized(queue, cloud_api):
    cloud, _ = cloud_api
    cloud.chapters[0]["title"] = " Old title [AB12]"
    group = queue.submit(URL, [{"type": "rename", "chapter_id": "one", "title": "Changed"}])
    apply_ready(queue)
    assert group[0].public().get("undo_available") is True
    queue.undo(group[0].id)
    assert cloud.chapters[0]["title"] == " Old title [AB12]"


def test_pending_bulk_delete_cancels_whole_submission_without_cloud_call(queue, cloud_api):
    cloud, _ = cloud_api
    group = queue.submit(URL, [{"type": "delete", "chapter_id": c} for c in ("one", "two")])
    assert all(a.public().get("undo_pending") is True for a in group)
    assert apply_ready(queue) == []
    assert queue.undo(group[0].id) == group
    assert all(a.status == "canceled" and a.error is None and a.finished.is_set() for a in group)
    assert not cloud.calls
    assert apply_ready(queue) == []


def test_later_actions_never_wake_delete_early_or_skip_it(queue, cloud_api):
    cloud, _ = cloud_api
    deleted = queue.submit(URL, [{"type": "delete", "chapter_id": "one"}])[0]
    later = queue.submit(URL, [{"type": "rename", "chapter_id": "two", "title": "After"}])[0]
    assert deleted.public().get("undo_available") is False
    assert apply_ready(queue) == []
    # Advance the queue's monotonic seam, not a global clock shared with HTTP polling.
    queue._clock = lambda: deleted.ready_at + 1
    apply_ready(queue)
    assert [c["id"] for c in cloud.chapters] == ["two"]
    assert cloud.chapters[0]["title"] == "After"
    assert deleted.status == later.status == "done"
    assert deleted.public()["undo_available"] is False
    assert later.public()["undo_available"] is True


def test_committed_delete_and_clear_have_no_recovery(queue, cloud_api):
    cloud, _ = cloud_api
    deleted = queue.submit(URL, [{"type": "delete", "chapter_id": "one"}])[0]
    queue._clock = lambda: deleted.ready_at + 1
    apply_ready(queue)
    assert deleted.status == "done" and deleted.public().get("undo_available") is False
    with pytest.raises((ValueError, RuntimeError), match="Undo"):
        queue.undo(deleted.id)
    cleared = queue.submit(URL, [{"type": "clear"}])[0]
    apply_ready(queue)
    assert cleared.public()["undo_available"] is False
    assert cloud.chapters == [] and len(cloud.patches) == 2


def test_new_failed_or_pending_submission_does_not_skip_back_to_older_undo(queue):
    first = queue.submit(URL, [{"type": "rename", "chapter_id": "one", "title": "First"}])[0]
    apply_ready(queue)
    assert first.public().get("undo_available") is True
    second = queue.submit(URL, [{"type": "rename", "chapter_id": "gone", "title": "Second"}])[0]
    assert first.public()["undo_available"] is False
    with pytest.raises((ValueError, RuntimeError), match="Undo"):
        queue.undo(first.id)
    apply_ready(queue)
    assert second.status == "failed" and not second.public()["undo_available"]
    assert not first.public()["undo_available"]


def test_expiry_reset_and_api_identity_change_revoke_undo(queue, cloud_api):
    cloud, api = cloud_api
    action = queue.submit(URL, [{"type": "rename", "chapter_id": "one", "title": "First"}])[0]
    apply_ready(queue)
    assert action.public().get("undo_available") is True
    queue._clock = lambda: 10**15
    with pytest.raises((ValueError, RuntimeError), match="Undo"):
        queue.undo(action.id)
    queue._clock = time.monotonic
    action = queue.submit(URL, [{"type": "rename", "chapter_id": "one", "title": "Next"}])[0]
    apply_ready(queue)
    queue._api = lambda: object()
    with pytest.raises((ValueError, RuntimeError), match="Undo"):
        queue.undo(action.id)
    assert len(cloud.patches) == 2
    queue._api = lambda: api
    pending = queue.submit(URL, [{"type": "delete", "chapter_id": "one"}])[0]
    queue.reset()
    assert pending.status == "canceled" and not pending.public()["undo_available"]
    assert apply_ready(queue) == [] and len(cloud.patches) == 2


def test_upload_undo_deletes_only_confirmed_permanent_id(queue, cloud_api, monkeypatch):
    cloud, _ = cloud_api
    # Simulate the transfer boundary only; real API apply assigns and confirms identity.
    monkeypatch.setattr(queue, "_transfer", lambda action: None)
    action = queue.submit_upload(URL, Path("unused.mp3"), "New")
    queue._transfers.shutdown(wait=True)
    action.status = "ready"
    action.params.update(file_id="staging", seconds=1)
    apply_ready(queue)
    assert action.status == "done" and action.chapter_id == "permanent"
    assert action.public().get("undo_available") is True
    queue.undo(action.id)
    assert [c["id"] for c in cloud.chapters] == ["one", "two"]
    assert len(cloud.patches) == 2


@pytest.mark.parametrize("finished", [False, True])
def test_upload_undo_still_works_after_tonies_processes_the_audio(queue, cloud_api, monkeypatch, finished):
    """Tonies rewrites a new chapter's file reference and duration while processing it;
    that is not a library change by someone else and must not block Undo."""
    cloud, _ = cloud_api
    monkeypatch.setattr(queue, "_transfer", lambda action: None)
    action = queue.submit_upload(URL, Path("unused.mp3"), "New")
    queue._transfers.shutdown(wait=True)
    action.status = "ready"
    action.params.update(file_id="staging", seconds=1)
    apply_ready(queue)
    assert action.chapter_id == "permanent"
    uploaded = cloud.chapters[-1]
    uploaded.update(file="final-audio-reference", seconds=187.4)
    if finished:
        uploaded["transcoding"] = False
    queue.undo(action.id)
    assert [c["id"] for c in cloud.chapters] == ["one", "two"]
    assert action.status == "undone"


def test_rename_undo_still_works_while_an_earlier_upload_finishes_processing(queue, cloud_api, monkeypatch):
    cloud, _ = cloud_api
    monkeypatch.setattr(queue, "_transfer", lambda action: None)
    upload = queue.submit_upload(URL, Path("unused.mp3"), "New")
    queue._transfers.shutdown(wait=True)
    upload.status = "ready"
    upload.params.update(file_id="staging", seconds=1)
    apply_ready(queue)
    rename = queue.submit(URL, [{"type": "rename", "chapter_id": "one", "title": "Renamed"}])[0]
    apply_ready(queue)
    cloud.chapters[-1].update(file="final-audio-reference", seconds=187.4, transcoding=False)
    queue.undo(rename.id)
    assert cloud.chapters[0]["title"] == "Same" and rename.status == "undone"
    assert [c["id"] for c in cloud.chapters] == ["one", "two", "permanent"]


def test_undo_still_refuses_when_another_device_edits_a_title(queue, cloud_api):
    cloud, _ = cloud_api
    action = queue.submit(URL, [{"type": "rename", "chapter_id": "one", "title": "Renamed"}])[0]
    apply_ready(queue)
    cloud.chapters[1]["title"] = "Changed elsewhere"
    patches = len(cloud.patches)
    with pytest.raises(RuntimeError):
        queue.undo(action.id)
    assert len(cloud.patches) == patches


@pytest.mark.parametrize("failure", ["timeout", "readback"])
def test_failed_inverse_is_consumed_and_never_retried(queue, cloud_api, failure):
    cloud, api = cloud_api
    action = queue.submit(URL, [{"type": "rename", "chapter_id": "one", "title": "First"}])[0]
    apply_ready(queue)
    assert action.public().get("undo_available") is True
    if failure == "timeout":
        cloud.patch_timeout = True
    else:
        original = cloud.__call__
        def handler(request):
            if len(cloud.patches) > 1 and request.url.path == "/v2/graphql":
                raise httpx.ReadTimeout("private detail", request=request)
            return original(request)
        api._client.close()
        api._client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match="unconfirmed|not confirmed"):
        queue.undo(action.id)
    with pytest.raises((ValueError, RuntimeError), match="Undo"):
        queue.undo(action.id)
    assert not action.public()["undo_available"] and len(cloud.patches) == 2


def test_double_undo_race_runs_only_one_inverse(queue, cloud_api):
    cloud, _ = cloud_api
    action = queue.submit(URL, [{"type": "rename", "chapter_id": "one", "title": "First"}])[0]
    apply_ready(queue)
    assert action.public().get("undo_available") is True
    results = []
    def run():
        try:
            queue.undo(action.id)
            results.append("done")
        except (RuntimeError, ValueError):
            results.append("refused")
    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(2)
        assert not thread.is_alive()
    assert sorted(results) == ["done", "refused"] and len(cloud.patches) == 2


def test_concurrent_submission_order_matches_last_submission_capability(queue, cloud_api):
    cloud, api = cloud_api
    entered, release = threading.Event(), threading.Event()
    first_result = []
    def getter():
        if threading.current_thread().name == "slow-submit":
            entered.set()
            assert release.wait(2)
        return api
    queue._api = getter
    thread = threading.Thread(name="slow-submit", target=lambda: first_result.extend(queue.submit(
        URL, [{"type": "rename", "chapter_id": "one", "title": "First request"}])))
    thread.start()
    assert entered.wait(2)
    second = queue.submit(URL, [{"type": "rename", "chapter_id": "one", "title": "Second request"}])[0]
    release.set()
    thread.join(2)
    assert not thread.is_alive()
    apply_ready(queue)
    first = first_result[0]
    assert cloud.chapters[0]["title"] == "First request"
    assert first.public()["undo_available"] and not second.public()["undo_available"]
    queue.undo(first.id)
    assert cloud.chapters[0]["title"] == "Second request"


def test_pending_earlier_transfer_prevents_completed_later_edit_undo(queue, cloud_api, monkeypatch):
    cloud, _ = cloud_api
    monkeypatch.setattr(queue, "_transfer", lambda action: None)
    upload = queue.submit_upload(URL, Path("unused.mp3"), "New")
    edited = queue.submit(URL, [{"type": "rename", "chapter_id": "one", "title": "Changed"}])[0]
    apply_ready(queue)
    assert edited.status == "done" and upload.status == "queued"
    assert not edited.public()["undo_available"]
    with pytest.raises(ValueError, match="Undo"):
        queue.undo(edited.id)
    assert len(cloud.patches) == 1


def test_pending_delete_worker_remains_asleep_until_deadline_then_runs(cloud_api):
    cloud, api = cloud_api
    q = ActionQueue(lambda: api, batch_window=0)
    try:
        deleted = q.submit(URL, [{"type": "delete", "chapter_id": "one"}])[0]
        later = q.submit(URL, [{"type": "rename", "chapter_id": "two", "title": "After"}])[0]
        assert deleted.ready_at - time.monotonic() > 4
        assert not later.finished.wait(0.03)
        assert not cloud.calls
        # Reading is independent of the server-side deletion grace period.
        assert len(api.get_content(URL)["chapters"]) == 2
        with q._cond:
            q._clock = lambda: deleted.ready_at + 1
            q._cond.notify_all()
        assert deleted.finished.wait(2) and later.finished.wait(2)
        assert deleted.status == later.status == "done"
        assert [c["id"] for c in cloud.chapters] == ["two"]
        assert len(cloud.patches) == 1
    finally:
        q._transfers.shutdown(wait=True)


def test_reset_during_transfer_prevents_late_attachment(cloud_api, tmp_path, monkeypatch):
    cloud, api = cloud_api
    entered, release = threading.Event(), threading.Event()
    def prepare(path, target):
        entered.set()
        assert release.wait(2)
        return {"file_id": "staging", "seconds": 1}
    monkeypatch.setattr(api, "prepare_upload", prepare)
    q = ActionQueue(lambda: api, batch_window=0)
    try:
        action = q.submit_upload(URL, tmp_path / "unused.mp3", "New")
        assert entered.wait(2)
        q.reset()
        release.set()
        q._transfers.shutdown(wait=True)
        assert action.status == "canceled" and action.finished.is_set()
        assert not q._ready_batch(URL) and not action.public()["undo_available"]
        assert not cloud.patches
    finally:
        release.set()
        q._transfers.shutdown(wait=True)


def test_closed_session_public_availability_and_private_snapshot(queue, cloud_api):
    _, api = cloud_api
    action = queue.submit(URL, [{"type": "rename", "chapter_id": "one", "title": "Changed"}])[0]
    apply_ready(queue)
    record = action.public()
    assert record["undo_available"] and record["undo_expires_at"].endswith("Z")
    assert not {"_undo", "undo_group", "expected", "steps", "owner", "api_session"} & record.keys()
    api.close()
    assert not action.public()["undo_available"]
    assert action.undo_group.receipt is None


def test_target_editor_query_alias_cannot_bypass_one_level(queue, cloud_api):
    cloud, _ = cloud_api
    first = queue.submit(URL, [{"type": "rename", "chapter_id": "one", "title": "First"}])[0]
    apply_ready(queue)
    alias = URL.split("?")[0] if "?" in URL else URL + "?withUpload=true"
    latest = queue.submit(alias, [{"type": "rename", "chapter_id": "one", "title": "Second"}])[0]
    apply_ready(queue)
    assert not first.public()["undo_available"] and latest.public()["undo_available"]
    queue.undo(latest.id)
    assert cloud.chapters[0]["title"] == "First"


@pytest.mark.parametrize("failure", ["timeout", "readback", "rejected"])
def test_failed_forward_write_never_grants_undo(queue, cloud_api, failure):
    cloud, api = cloud_api
    original = cloud.__call__
    def handler(request):
        if request.method == "PATCH" and failure == "rejected":
            cloud.patches.append({})
            return httpx.Response(400)
        if cloud.patches and request.url.path == "/v2/graphql" and failure == "readback":
            raise httpx.ReadTimeout("private detail", request=request)
        return original(request)
    cloud.patch_timeout = failure == "timeout"
    api._client.close()
    api._client = httpx.Client(transport=httpx.MockTransport(handler))
    action = queue.submit(URL, [{"type": "rename", "chapter_id": "one", "title": "Changed"}])[0]
    apply_ready(queue)
    assert action.status == "failed" and not action.public()["undo_available"]
    with pytest.raises(ValueError, match="Undo"):
        queue.undo(action.id)
    assert len(cloud.patches) == 1


def test_uploader_reset_revokes_queue_and_publishes_real_cancellation(queue, cloud_api, monkeypatch):
    from app import events, uploader
    _, api = cloud_api
    completed = queue.submit(URL, [{"type": "rename", "chapter_id": "one", "title": "Changed"}])[0]
    apply_ready(queue)
    pending = queue.submit(URL, [{"type": "delete", "chapter_id": "two"}])[0]
    published = []
    monkeypatch.setattr(events, "publish", lambda kind, payload: published.append((kind, payload)))
    monkeypatch.setattr(uploader, "action_queue", queue)
    monkeypatch.setattr(uploader, "_api_client", api)
    monkeypatch.setattr(uploader, "_account_fingerprint", b"synthetic-fingerprint")
    uploader.reset_upload_session()
    assert pending.status == "canceled" and pending.error is None
    assert pending.finished.is_set() and not pending.public()["undo_available"]
    assert not completed.public()["undo_available"]
    assert uploader._api_client is None and uploader._account_fingerprint is None
    assert api.closed and not queue._undo.latest and not apply_ready(queue)
    kind, payload = published[-1]
    assert kind == "tonies_actions" and payload["pending"] == 0
    assert payload["actions"][0]["status"] == "canceled"
    assert "content" not in payload


def test_cancel_is_terminal_not_failed_and_revokes_pending_undo(queue, cloud_api, monkeypatch):
    from app import events
    cloud, _ = cloud_api
    published = []
    monkeypatch.setattr(events, "publish", lambda kind, payload: published.append(payload))
    action = queue.submit(URL, [{"type": "delete", "chapter_id": "one"}])[0]
    assert action.public()["undo_pending"]
    assert queue.cancel(action.id)
    assert action.status == "canceled" and action.error is None and action.finished.is_set()
    assert not action.public()["undo_available"] and not queue.cancel(action.id)
    assert published[-1]["actions"][0]["status"] == "canceled"
    assert published[-1]["pending"] == 0 and not cloud.calls


def test_failed_undo_publishes_refresh_required_not_a_success(queue, cloud_api, monkeypatch):
    from app import events
    cloud, _ = cloud_api
    action = queue.submit(URL, [{"type": "rename", "chapter_id": "one", "title": "Changed"}])[0]
    apply_ready(queue)
    published = []
    monkeypatch.setattr(events, "publish", lambda kind, payload: published.append(payload))
    cloud.patch_timeout = True
    with pytest.raises(RuntimeError, match="unconfirmed"):
        queue.undo(action.id)
    record = published[-1]["actions"][0]
    assert record["needs_refresh"] is True and "Undo" in record["error"]
    assert not record["undo_available"] and "content" not in published[-1]
    assert len(cloud.patches) == 2


def test_new_submission_waits_for_inverse_and_uses_post_undo_snapshot(queue, cloud_api, monkeypatch):
    cloud, api = cloud_api
    original_title = cloud.chapters[0]["title"]
    action = queue.submit(URL, [{"type": "rename", "chapter_id": "one", "title": "First"}])[0]
    apply_ready(queue)
    entered, release = threading.Event(), threading.Event()
    original_undo = api.undo
    errors = []
    def blocked_undo(*args):
        entered.set()
        assert release.wait(2)
        return original_undo(*args)
    monkeypatch.setattr(api, "undo", blocked_undo)
    def run():
        try:
            queue.undo(action.id)
        except Exception as error:
            errors.append(error)
    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert entered.wait(2)
        later = queue.submit(URL, [{"type": "rename", "chapter_id": "one", "title": "Second"}])[0]
        assert not apply_ready(queue) and len(cloud.patches) == 1
    finally:
        release.set()
        thread.join(2)
    assert not thread.is_alive() and not errors
    assert cloud.chapters[0]["title"] == original_title
    apply_ready(queue)
    assert later.public()["undo_available"] and not action.public()["undo_available"]
    queue.undo(later.id)
    assert cloud.chapters[0]["title"] == original_title and len(cloud.patches) == 4


def test_undo_store_is_bounded_and_expired_receipts_are_released(queue, cloud_api, monkeypatch):
    from app import undo
    from app.tonies_api import editor_url
    from tonies_fake import HOUSEHOLD
    _, api = cloud_api
    monkeypatch.setattr(undo, "MAX_UNDO_TARGETS", 2)
    old = queue.submit(URL, [{"type": "rename", "chapter_id": "one", "title": "First"}])[0]
    apply_ready(queue)
    assert old.undo_group.receipt is not None
    for target in ("0000000000000001", "0000000000000002"):
        queue.submit(editor_url(HOUSEHOLD, target), [{"type": "delete", "chapter_id": "one"}])
    assert len(queue._undo.latest) == 2 and not old.public()["undo_available"]
    assert old.undo_group.receipt is None
    queue._clock = lambda: 10**15
    queue.submit(URL, [{"type": "rename", "chapter_id": "one", "title": "Next"}])
    assert len(queue._undo.latest) == 1
    assert all(group.owner is api for group in queue._undo.latest.values())


@pytest.mark.parametrize("upload", [False, True], ids=["edit", "upload"])
def test_reset_during_session_lookup_refuses_stale_submission(queue, cloud_api, monkeypatch, upload):
    from app import uploader
    _, api = cloud_api
    entered, release = threading.Event(), threading.Event()
    result, errors = [], []
    def getter():
        entered.set()
        assert release.wait(2)
        return api
    queue._api = getter
    monkeypatch.setattr(uploader, "action_queue", queue)
    monkeypatch.setattr(uploader, "_api_client", api)
    monkeypatch.setattr(uploader, "_account_fingerprint", b"synthetic")
    def submit():
        try:
            result.append(queue.submit_upload(URL, Path("unused.mp3"), "Upload") if upload else
                          queue.submit(URL, [{"type": "delete", "chapter_id": "one"}]))
        except RuntimeError as error:
            errors.append(str(error))
    thread = threading.Thread(target=submit)
    thread.start()
    try:
        assert entered.wait(2)
        uploader.reset_upload_session()
    finally:
        release.set()
        thread.join(2)
    assert not thread.is_alive()
    assert not result and len(errors) == 1 and "session" in errors[0]
    assert queue.recent() == [] and not queue._undo.latest


def test_reset_publishes_completed_undo_revocation_without_replaying_callback(queue, monkeypatch):
    from app import events
    action = queue.submit(URL, [{"type": "rename", "chapter_id": "one", "title": "Changed"}])[0]
    apply_ready(queue)
    assert action.public()["undo_available"]
    callbacks, published = [], []
    action.on_change = lambda item: callbacks.append(item.status)
    monkeypatch.setattr(events, "publish", lambda kind, payload: published.append((kind, payload)))
    queue.reset()
    assert not action.public()["undo_available"] and not callbacks
    assert published, "Session reset must publish completed capability revocation"
    kind, payload = published[-1]
    assert kind == "tonies_actions" and payload["pending"] == 0
    assert payload["actions"][0]["id"] == action.id
    assert payload["actions"][0]["undo_available"] is False
    assert payload["actions"][0]["status"] == "done" and "content" not in payload
