"""Accepted attachment identity survives failed verification, never as live inventory."""
import copy
import socket
import threading

import httpx
import pytest

from app import tonies_api
from app.actions import ActionQueue
from tonies_fake import URL


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("offline attachment tests must not open sockets")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(tonies_api, "_audio_seconds", lambda _: 1.0)


def fail_after_patch(cloud, api, failure):
    receipt = {}

    def handler(request):
        if cloud.patches and request.url.path == "/v2/graphql":
            if failure == "transport":
                raise httpx.ReadTimeout("private transport detail", request=request)
            if failure == "malformed":
                return httpx.Response(200, json={"data": {}})
        response = cloud(request)
        if request.method == "PATCH":
            receipt.update(response.json())
            if failure == "mismatch":
                cloud.chapters = []
        return response

    api._client.close()
    api._client = httpx.Client(transport=httpx.MockTransport(handler))
    return receipt


@pytest.mark.parametrize("failure", ["transport", "malformed", "mismatch"])
def test_apply_preserves_all_accepted_ids_without_claiming_confirmation(cloud_api, failure):
    cloud, api = cloud_api
    receipt = fail_after_patch(cloud, api, failure)
    ops = [
        {"type": "attach", "file_id": "staging-a", "title": "First", "seconds": 1,
         "position": {"edge": "end"}},
        {"type": "delete", "chapter_id": "missing"},
        {"type": "attach", "file_id": "staging-b", "title": "Second", "seconds": 1,
         "position": {"edge": "start"}},
    ]
    with pytest.raises(RuntimeError, match="not confirmed.*refresh") as caught:
        api.apply(URL, ops)
    error = caught.value
    assert [r["chapter_id"] for r in getattr(error, "results", [])] == ["permanent-a", None, "permanent-b"]
    assert error.results[1]["error"] == "Track is no longer on this Tonie; refresh the library"
    assert error.attachment_receipt == {"chapters": receipt["chapters"]}
    assert error.needs_refresh is True
    assert not hasattr(error, "content")  # a PATCH receipt is not a confirmed inventory
    assert "private transport detail" not in str(error)
    assert len(cloud.patches) == 1


@pytest.mark.parametrize("failure", ["transport", "malformed", "mismatch"])
def test_queue_exposes_receipt_and_ids_but_no_inventory_or_success(cloud_api, monkeypatch, failure):
    from app import events
    cloud, api = cloud_api
    baseline = copy.deepcopy(cloud.chapters)
    receipt = fail_after_patch(cloud, api, failure)
    queue = ActionQueue(lambda: api, batch_window=0)
    published, callbacks = [], []
    monkeypatch.setattr(events, "publish", lambda kind, payload: published.append((kind, payload)))
    # Prepare ready uploads explicitly to exercise one deterministic multi-action batch.
    actions = []
    for i in range(3):
        action = queue._new(URL, "upload", {"file_id": f"staging-{i}", "seconds": 1,
                                             "title": f"Track {i}", "position": {"edge": "end"}})
        action.status = "ready"
        action.on_change = lambda a: callbacks.append(a.public())
        actions.append(action)
        queue._actions[action.id] = action
    try:
        queue._apply(URL, actions)
    finally:
        queue._transfers.shutdown(wait=True)
    assert [a.chapter_id for a in actions] == ["permanent-0", "permanent-1", "permanent-2"]
    assert all(a.status == "failed" and a.finished.is_set() for a in actions)
    for action in actions:
        assert action.needs_refresh is True
        assert action.attachment_receipt == {"chapters": receipt["chapters"]}
        assert action.content is None
        assert "not confirmed" in action.error and "refresh" in action.error
        assert "Do not upload again" in action.error
        assert action.public()["attachment_receipt"] == action.attachment_receipt
        assert action.public()["needs_refresh"] is True
    assert [a["chapter_id"] for a in callbacks] == ["permanent-0", "permanent-1", "permanent-2"]
    terminal = published[-1][1]
    assert "content" not in terminal  # never replace current UI inventory with the historical receipt
    assert terminal["pending"] == 0
    assert len(cloud.patches) == 1
    if failure != "mismatch":
        assert tonies_api._writable(cloud.chapters[:2]) == tonies_api._writable(baseline)


def test_success_still_returns_confirmed_content_and_marks_action_done(cloud_api):
    cloud, api = cloud_api
    queue = ActionQueue(lambda: api)
    action = queue._new(URL, "upload", {"file_id": "staging", "seconds": 1, "title": "Track"})
    try:
        queue._apply(URL, [action])
    finally:
        queue._transfers.shutdown(wait=True)
    assert action.status == "done" and action.chapter_id == "permanent"
    assert action.content is not None
    assert [c["chapter_id"] for c in action.content["chapters"]] == ["one", "two", "permanent"]
    assert action.public().get("needs_refresh", False) is False
    assert action.public().get("attachment_receipt") is None
    assert len(cloud.patches) == 1


@pytest.mark.parametrize("failure", ["timeout", "rejected", "invalid-identity"])
def test_no_attachment_identity_is_invented_without_a_valid_receipt(cloud_api, failure):
    cloud, api = cloud_api
    original = cloud.__call__

    def handler(request):
        if request.method == "PATCH" and failure == "rejected":
            cloud.patches.append({})
            return httpx.Response(400, json={"detail": "private rejection"})
        response = original(request)
        if request.method == "PATCH" and failure == "invalid-identity":
            data = response.json()
            data["chapters"][-1]["id"] = "one"
            return httpx.Response(200, json=data)
        return response

    cloud.patch_timeout = failure == "timeout"
    api._client.close()
    api._client = httpx.Client(transport=httpx.MockTransport(handler))
    queue = ActionQueue(lambda: api)
    action = queue._new(URL, "upload", {"file_id": "staging", "seconds": 1, "title": "Track"})
    try:
        queue._apply(URL, [action])
    finally:
        queue._transfers.shutdown(wait=True)
    assert action.status == "failed"
    assert action.chapter_id is None and action.content is None
    assert action.public().get("attachment_receipt") is None
    assert len(cloud.patches) == 1


def test_real_worker_retains_three_transfer_receipts_for_exact_id_cleanup(cloud_api, tmp_path):
    cloud, api = cloud_api
    baseline = copy.deepcopy(cloud.chapters)
    storage_ready = threading.Barrier(3)
    lock = threading.Lock()
    received = []
    reads_fail = True

    def handler(request):
        if request.url.host == "synthetic.s3.amazonaws.com":
            storage_ready.wait(timeout=5)
        with lock:
            if reads_fail and cloud.patches and request.url.path == "/v2/graphql":
                raise httpx.ReadTimeout("synthetic verification failure", request=request)
            response = cloud(request)
            if request.method == "PATCH" and not received:
                received.extend(c["id"] for c in response.json()["chapters"][2:])
            return response

    api._client.close()
    api._client = httpx.Client(transport=httpx.MockTransport(handler))
    queue = ActionQueue(lambda: api, batch_window=0.15)
    try:
        actions = []
        for i in range(3):
            path = tmp_path / f"track-{i}.wav"
            path.write_bytes(b"synthetic audio")
            actions.append(queue.submit_upload(URL, path, f"Track {i}", {"edge": "end"}))
        for action in actions:
            assert action.finished.wait(5), f"action stalled: {action.status}"
        assert len(cloud.storage) == 3 and len(cloud.patches) == 1
        assert len(received) == len(set(received)) == 3
        assert [a.chapter_id for a in actions] == received
        assert all(a.status == "failed" and a.needs_refresh and a.content is None for a in actions)
        assert [c["title"] for c in cloud.chapters[2:]] == [a.params["title"] for a in actions]
        assert [a["chapter_id"] for a in queue.recent(URL)] == received
        # A consumer cannot rewrite the queue's retained historical receipt.
        actions[0].public()["attachment_receipt"]["chapters"].clear()
        assert len(actions[0].attachment_receipt["chapters"]) == 5

        reads_fail = False
        deletes = queue.submit(URL, [{"type": "delete", "chapter_id": a.chapter_id} for a in actions])
        assert all(a.public()["undo_pending"] for a in deletes)
        with queue._cond:
            queue._clock = lambda: max(a.ready_at for a in deletes) + 1
            queue._cond.notify_all()
        for action in deletes:
            assert action.finished.wait(5) and action.status == "done"
        assert tonies_api._writable(cloud.chapters) == tonies_api._writable(baseline)
        assert len(cloud.patches) == 2  # one attach and one explicit exact-ID cleanup, no replay
    finally:
        queue._transfers.shutdown(wait=True)
