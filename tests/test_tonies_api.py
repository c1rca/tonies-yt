"""Offline fixtures for the observed my-tonies OAuth/v2 contract."""
import copy
import json
import threading
from urllib.parse import parse_qs

import httpx
import pytest

HOUSEHOLD = "11111111-2222-4333-8444-555555555555"
TONIE = "aBcD0123456789EF"
URL = f"https://my.tonies.com/creative-tonies/{HOUSEHOLD}/{TONIE}/edit?withUpload=true"


def chapter(identity, title="Same", seconds=12):
    return {"id": identity, "title": title, "file": f"file-{identity}", "seconds": seconds, "type": "file", "transcoding": False}


class Cloud:
    def __init__(self):
        self.chapters = [chapter("one"), chapter("two")]
        self.live = False
        self.processing = False
        self.calls = []
        self.patches = []
        self.tokens = []
        self.storage = []
        self.graphql_error = False
        self.patch_timeout = False
        self.apply_patch = True
        self.concurrent_change = False
        self.reads = 0

    def snapshot(self):
        return {"data": {"config": {"maxSeconds": 5400, "maxChapters": 99, "maxBytes": 10000000, "accepts": ["audio/mpeg", "audio/wav"]}, "households": [{"id": HOUSEHOLD, "name": "Synthetic household", "creativeTonies": [{"id": TONIE, "name": "Blue", "imageUrl": "https://images.example.invalid/blue.png", "live": self.live, "transcoding": self.processing, "secondsRemaining": 5376, "chapters": copy.deepcopy(self.chapters)}]}]}}

    def __call__(self, request):
        self.calls.append(request)
        if request.url.host == "login.tonies.com":
            data = parse_qs(request.content.decode())
            self.tokens.append(data)
            assert request.url.path == "/auth/realms/tonies/protocol/openid-connect/token"
            assert data["client_id"] == ["my-tonies"]
            return httpx.Response(200, json={"access_token": "synthetic-access", "refresh_token": f"synthetic-refresh-{len(self.tokens)}", "expires_in": 300})
        if request.url.host == "synthetic.s3.amazonaws.com":
            assert "authorization" not in request.headers
            self.storage.append(request)
            return httpx.Response(204)
        assert request.url.host == "api.prod.tcs.toys"
        assert request.headers["authorization"] == "Bearer synthetic-access"
        if request.url.path == "/v2/graphql":
            self.reads += 1
            body = json.loads(request.content)
            assert "config" in body["query"]
            if body.get("variables", {}).get("householdId"):
                assert body["variables"] == {"householdId": HOUSEHOLD, "creativeTonieId": TONIE}
            if self.concurrent_change and self.reads == 2:
                self.chapters.append(chapter("foreign"))
            if self.graphql_error:
                return httpx.Response(200, json={"data": self.snapshot()["data"], "errors": [{"message": "private diagnostic"}]})
            return httpx.Response(200, json=self.snapshot())
        if request.url.path == "/v2/file":
            assert json.loads(request.content) == {"headers": {}}
            return httpx.Response(200, json={"fileId": "staging", "request": {"url": "https://synthetic.s3.amazonaws.com/", "fields": {"key": "signed-key", "policy": "synthetic-policy", "x-amz-signature": "synthetic-signature"}}})
        assert request.method == "PATCH"
        assert request.url.path == f"/v2/households/{HOUSEHOLD}/creativetonies/{TONIE}"
        payload = json.loads(request.content)
        self.patches.append(payload)
        result = copy.deepcopy(payload["chapters"])
        for item in result:
            if item["id"] == "staging":
                item["id"] = "permanent"
                item["file"] = "transcoded-reference"
                item["transcoding"] = True
        if self.apply_patch:
            self.chapters = copy.deepcopy(result)
        if self.patch_timeout:
            raise httpx.ReadTimeout("sensitive-request-query-token", request=request)
        return httpx.Response(200, json={"id": TONIE, "chapters": result, "transcoding": self.processing})


@pytest.fixture
def cloud_api():
    from app.tonies_api import ToniesAPI
    cloud = Cloud()
    api = ToniesAPI("synthetic@example.invalid", "synthetic-password", client=httpx.Client(transport=httpx.MockTransport(cloud)), poll_timeout=0.01, poll_interval=0)
    yield cloud, api
    api.close()


def test_graphql_maps_ids_native_order_durations_capacity_and_processing(cloud_api):
    cloud, api = cloud_api
    cloud.processing = True
    cloud.chapters[0]["seconds"] = 3661
    content = api.get_content(URL)
    assert [c["chapter_id"] for c in content["chapters"]] == ["one", "two"]
    assert content["chapters"][0]["duration"] == "01:01:01"
    assert content["free_seconds"] == 5376
    assert content["total_seconds"] == 5400
    assert content["free_minutes"] == 89
    assert content["used_minutes"] == 1
    assert content["processing"] is True
    assert api.list_targets() == [{"name": "Blue", "image": "https://images.example.invalid/blue.png", "edit_url": URL}]
    assert len(cloud.tokens) == 1
    assert cloud.tokens[0]["grant_type"] == ["password"]
    assert cloud.tokens[0]["scope"] == ["openid"]


def test_expiry_refresh_is_serialized_and_rotated_in_memory(cloud_api):
    cloud, api = cloud_api
    api.get_content(URL)
    api._expires_at = 0
    threads = [threading.Thread(target=api.get_content, args=(URL,)) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(2)
        assert not thread.is_alive()
    assert len(cloud.tokens) == 2
    assert cloud.tokens[1]["grant_type"] == ["refresh_token"]
    assert cloud.tokens[1]["refresh_token"] == ["synthetic-refresh-1"]
    api._expires_at = 0
    api.get_content(URL)
    assert cloud.tokens[2]["refresh_token"] == ["synthetic-refresh-2"]


@pytest.mark.parametrize("url", [URL.replace("https:", "http:"), URL.replace("my.tonies.com", "my.tonies.com.evil.invalid"), URL.replace("my.tonies.com", "user:pass@my.tonies.com"), URL.replace(TONIE, "aBcD0123456789EG"), URL.replace(HOUSEHOLD, "bad-uuid"), URL.replace(TONIE, TONIE + "0"), URL.replace("/edit", "/other"), URL.replace("my.tonies.com", "my.tonies.com:8443")])
def test_invalid_target_refused_before_auth_or_network(cloud_api, url):
    cloud, api = cloud_api
    with pytest.raises(RuntimeError, match="target URL"):
        api.get_content(url)
    assert not cloud.calls


def test_graphql_errors_are_not_silently_empty_and_not_leaked(cloud_api):
    cloud, api = cloud_api
    cloud.graphql_error = True
    with pytest.raises(RuntimeError, match="GraphQL") as error:
        api.get_content(URL)
    assert "private diagnostic" not in str(error.value)


def test_streamed_large_upload_maps_server_assigned_id_and_file_rewrite(cloud_api, tmp_path, monkeypatch):
    from app import tonies_api
    cloud, api = cloud_api
    monkeypatch.setattr(tonies_api, "_audio_seconds", lambda path: 2.5)
    path = tmp_path / "large.mp3"
    path.write_bytes(b"synthetic-audio" * 100000)
    original = copy.deepcopy(cloud.chapters)
    result = api.upload(path, URL, "New [oc:abc123]")
    assert result["chapter_id"] == "permanent"
    assert result["target_url"] == URL
    assert result["content"]["chapters"][-1]["chapter_id"] == "permanent"
    assert result["content"]["chapters"][-1]["processing"] is True
    assert result["content"]["processing"] is True
    assert len(cloud.storage) == len(cloud.patches) == 1
    body = cloud.storage[0].content
    assert b'filename="staging"' in body
    for value in [b"signed-key", b"synthetic-policy", b"synthetic-signature"]:
        assert value in body
    assert len(body) > 1048576
    assert cloud.patches[0]["chapters"][:-1] == [{k: c[k] for k in ("id", "title", "file", "seconds", "type")} for c in original]


def test_live_target_prepends_upload_without_reordering_originals(cloud_api, tmp_path, monkeypatch):
    from app import tonies_api
    cloud, api = cloud_api
    cloud.live = True
    monkeypatch.setattr(tonies_api, "_audio_seconds", lambda path: 1)
    path = tmp_path / "clip.wav"
    path.write_bytes(b"synthetic")
    assert api.upload(path, URL, "New")["chapter_id"] == "permanent"
    assert [c["id"] for c in cloud.chapters] == ["permanent", "one", "two"]


def test_rename_reorder_delete_use_exact_identity_and_preserve_others(cloud_api):
    cloud, api = cloud_api
    cloud.chapters[1]["title"] = "Same [oc:abc123]"
    api.rename(URL, 1, "Unicode 🎵", "two")
    assert cloud.chapters[1]["title"] == "Unicode 🎵 [oc:abc123]"
    api.reorder(URL, 1, 0, "two")
    assert [c["id"] for c in cloud.chapters] == ["two", "one"]
    api.delete(URL, 1, chapter_id="one", chapter_title="Same")
    assert [c["id"] for c in cloud.chapters] == ["two"]
    assert all(request.method != "DELETE" for request in cloud.calls)


@pytest.mark.parametrize("action", [lambda api: api.rename(URL, 0, "New", "two"), lambda api: api.reorder(URL, 0, 1, "two"), lambda api: api.delete(URL, 0, chapter_id="one", chapter_title="Different"), lambda api: api.delete(URL, 0, chapter_title="Sam"), lambda api: api.delete(URL, 0, chapter_title="Same")])
def test_stale_or_ambiguous_selectors_never_write(cloud_api, action):
    cloud, api = cloud_api
    with pytest.raises(RuntimeError, match="stale|ambiguous|match"):
        action(api)
    assert not cloud.patches


def test_delete_cloud_id_can_follow_index_shift_but_not_conflicting_token(cloud_api):
    cloud, api = cloud_api
    cloud.chapters[1]["title"] = "Same [oc:123abc]"
    with pytest.raises(RuntimeError, match="match"):
        api.delete(URL, 0, chapter_id="two", app_track_token="ffffff")
    api.delete(URL, 0, chapter_id="two", app_track_token="123abc")
    assert [c["id"] for c in cloud.chapters] == ["one"]


def test_fresh_prewrite_guard_stops_concurrent_change(cloud_api):
    cloud, api = cloud_api
    cloud.concurrent_change = True
    with pytest.raises(RuntimeError, match="changed"):
        api.rename(URL, 0, "New", "one")
    assert not cloud.patches
    assert [c["id"] for c in cloud.chapters] == ["one", "two", "foreign"]


def test_ambiguous_patch_is_not_retried_or_fallen_back(cloud_api):
    cloud, api = cloud_api
    cloud.patch_timeout = True
    with pytest.raises(RuntimeError, match="unconfirmed") as error:
        api.rename(URL, 0, "New", "one")
    assert len(cloud.patches) == 1
    assert "sensitive-request-query-token" not in str(error.value)


def test_missing_persisted_state_is_bounded_not_false_success(cloud_api):
    cloud, api = cloud_api
    cloud.apply_patch = False
    with pytest.raises(RuntimeError, match="not confirmed"):
        api.rename(URL, 0, "New", "one")
    assert len(cloud.patches) == 1


def test_delete_all_only_patches_chapters_to_empty_and_reads_back(cloud_api):
    cloud, api = cloud_api
    result = api.delete_all(URL)
    assert result["chapters"] == []
    assert cloud.patches == [{"chapters": []}]
    assert all(request.method != "DELETE" for request in cloud.calls)


@pytest.mark.parametrize("where", ["token", "api", "storage"])
def test_redirects_never_forward_credentials_or_bearer(where, tmp_path, monkeypatch):
    from app import tonies_api
    calls = []
    cloud = Cloud()

    def handler(request):
        calls.append(request)
        hit = {"token": request.url.host == "login.tonies.com", "api": request.url.path == "/v2/graphql", "storage": request.url.host == "synthetic.s3.amazonaws.com"}[where]
        if hit:
            return httpx.Response(307, headers={"location": "https://attacker.invalid/steal"})
        return cloud(request)

    api = tonies_api.ToniesAPI("synthetic@example.invalid", "synthetic-password", client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True))
    monkeypatch.setattr(tonies_api, "_audio_seconds", lambda path: 1)
    path = tmp_path / "clip.mp3"
    path.write_bytes(b"synthetic")
    with pytest.raises(RuntimeError, match="HTTP 307"):
        api.upload(path, URL, "New") if where == "storage" else api.get_content(URL)
    assert not any(r.url.host == "attacker.invalid" for r in calls)
    api.close()


def test_new_chapter_duration_can_finish_processing_without_losing_identity(cloud_api, tmp_path, monkeypatch):
    from app import tonies_api
    cloud, api = cloud_api
    monkeypatch.setattr(tonies_api, "_audio_seconds", lambda _: 1)
    path = tmp_path / "clip.mp3"
    path.write_bytes(b"synthetic")
    original = cloud.__call__

    def handler(request):
        response = original(request)
        if request.method == "PATCH":
            cloud.chapters[-1]["seconds"] = 1.05
            cloud.chapters[-1]["file"] = "final-rewritten-reference"
        return response

    api._client.close()
    api._client = httpx.Client(transport=httpx.MockTransport(handler))
    result = api.upload(path, URL, "New")
    assert result["chapter_id"] == "permanent"
    assert result["content"]["chapters"][-1]["seconds"] == 1.05


def test_duration_retains_fractional_api_seconds(cloud_api):
    cloud, api = cloud_api
    cloud.chapters[0]["seconds"] = 12.75
    assert api.get_content(URL)["chapters"][0]["seconds"] == 12.75


def test_malformed_graphql_records_are_sanitized_before_mapping(cloud_api):
    cloud, api = cloud_api
    original = cloud.__call__

    def handler(request):
        if request.url.path == "/v2/graphql":
            return httpx.Response(200, json={"data": {"config": {}, "households": ["private-account-record"]}})
        return original(request)

    api._client.close()
    api._client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match="Invalid Tonies GraphQL") as error:
        api.get_content(URL)
    assert "private-account-record" not in str(error.value)


@pytest.mark.parametrize("limitation", ["capacity", "bytes", "chapters"])
def test_upload_limits_checked_before_allocating_or_streaming(cloud_api, tmp_path, monkeypatch, limitation):
    from app import tonies_api
    cloud, api = cloud_api
    original_snapshot = cloud.snapshot

    def snapshot():
        data = original_snapshot()
        if limitation == "capacity":
            data["data"]["households"][0]["creativeTonies"][0]["secondsRemaining"] = 0
        elif limitation == "bytes":
            data["data"]["config"]["maxBytes"] = 1
        else:
            data["data"]["config"]["maxChapters"] = 2
        return data

    cloud.snapshot = snapshot
    monkeypatch.setattr(tonies_api, "_audio_seconds", lambda _: 1)
    path = tmp_path / "clip.mp3"
    path.write_bytes(b"synthetic")
    with pytest.raises(RuntimeError, match="capacity|limit"):
        api.upload(path, URL, "New")
    assert not cloud.storage
    assert not cloud.patches
    assert not any(r.url.path == "/v2/file" for r in cloud.calls)


def test_httpx_streams_multipart_in_bounded_chunks_without_read_bytes(tmp_path, monkeypatch):
    from app import tonies_api
    from pathlib import Path
    cloud = Cloud()
    chunks = []

    class Transport(httpx.BaseTransport):
        def handle_request(self, request):
            if request.url.host == "synthetic.s3.amazonaws.com":
                assert "authorization" not in request.headers
                assert "cookie" not in request.headers
                for part in request.stream:
                    chunks.append(len(part))
                return httpx.Response(204)
            request.read()
            return cloud(request)

    client = httpx.Client(transport=Transport(), headers={"cookie": "synthetic-private-cookie"})
    api = tonies_api.ToniesAPI("synthetic@example.invalid", "synthetic-password", client=client)
    monkeypatch.setattr(tonies_api, "_audio_seconds", lambda _: 1)
    monkeypatch.setattr(Path, "read_bytes", lambda _: (_ for _ in ()).throw(AssertionError("must stream file")))
    path = tmp_path / "large.mp3"
    path.write_bytes(b"synthetic-audio" * 100000)
    try:
        assert api.upload(path, URL, "New")["chapter_id"] == "permanent"
    finally:
        api.close()
    assert sum(chunks) > 1048576
    assert max(chunks) <= 65536


def test_real_ffprobe_reads_synthetic_wav_duration(tmp_path):
    import wave
    from app.tonies_api import _audio_seconds
    path = tmp_path / "clip.wav"
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\x00\x00" * 8000)
    assert _audio_seconds(path) == pytest.approx(1.0)


def test_refresh_rejection_reauthenticates_once_without_leaking_body(cloud_api):
    cloud, api = cloud_api
    api.get_content(URL)
    api._expires_at = 0
    original = cloud.__call__
    rejected = []

    def handler(request):
        if request.url.host == "login.tonies.com" and b"grant_type=refresh_token" in request.content:
            rejected.append(request)
            return httpx.Response(400, json={"error": "invalid_grant", "error_description": "private-secret-detail"})
        return original(request)

    api._client.close()
    api._client = httpx.Client(transport=httpx.MockTransport(handler))
    assert api.get_content(URL)["chapters"][0]["chapter_id"] == "one"
    assert len(rejected) == 1
    assert len(cloud.tokens) == 2
    assert cloud.tokens[-1]["grant_type"] == ["password"]


def test_api_unauthorized_read_refreshes_once_but_patch_is_never_replayed(cloud_api):
    cloud, api = cloud_api
    original = cloud.__call__
    unauthorized = []

    def handler(request):
        if request.url.path == "/v2/graphql" and not unauthorized:
            unauthorized.append(request)
            return httpx.Response(401)
        if request.method == "PATCH":
            unauthorized.append(request)
            return httpx.Response(401, text="private-secret-detail")
        return original(request)

    api._client.close()
    api._client = httpx.Client(transport=httpx.MockTransport(handler))
    assert api.get_content(URL)["chapters"][0]["chapter_id"] == "one"
    assert len(cloud.tokens) == 2
    with pytest.raises(RuntimeError, match="HTTP 401") as error:
        api.rename(URL, 0, "New", "one")
    assert "private-secret-detail" not in str(error.value)
    assert len([r for r in unauthorized if r.method == "PATCH"]) == 1


@pytest.fixture
def after_patch(cloud_api):
    """Model provider changes after the strict prewrite snapshot, never before."""
    cloud, api = cloud_api

    def install(change, *, attachment=False):
        original = cloud.__call__

        def handler(request):
            response = original(request)
            if request.method == "PATCH":
                change(cloud.chapters)
                if attachment:
                    return httpx.Response(200, json={"id": TONIE, "chapters": copy.deepcopy(cloud.chapters)})
            return response

        api._client.close()
        api._client = httpx.Client(transport=httpx.MockTransport(handler))

    return install


def finish_processing(chapters):
    processing = next(c for c in chapters if c["id"] == "one")
    processing.update(file="final-processing-reference", seconds=12.05, transcoding=False)


@pytest.mark.parametrize("action", ["rename", "reorder", "delete"])
@pytest.mark.parametrize("still_processing", [False, True])
def test_existing_processing_transition_confirms_edit_by_identity(
    cloud_api, after_patch, action, still_processing
):
    cloud, api = cloud_api
    cloud.chapters[0].update(file="pending-processing-reference", transcoding=True)

    def transition(chapters):
        finish_processing(chapters)
        next(c for c in chapters if c["id"] == "one")["transcoding"] = still_processing

    after_patch(transition)
    if action == "rename":
        result = api.rename(URL, 0, "Renamed", chapter_id="one")
        expected_ids, expected_titles = ["one", "two"], ["Renamed", "Same"]
    elif action == "reorder":
        result = api.reorder(URL, 0, 1, chapter_id="one")
        expected_ids, expected_titles = ["two", "one"], ["Same", "Same"]
    else:
        result = api.delete(URL, 1, chapter_id="two")
        expected_ids, expected_titles = ["one"], ["Same"]
    assert [c["chapter_id"] for c in result["chapters"]] == expected_ids
    assert [c["title"] for c in result["chapters"]] == expected_titles
    processed = next(c for c in result["chapters"] if c["chapter_id"] == "one")
    assert processed["seconds"] == 12.05
    assert processed["processing"] is still_processing
    assert next(c for c in cloud.chapters if c["id"] == "one")["file"] == "final-processing-reference"
    assert len(cloud.patches) == 1


@pytest.mark.parametrize("attachment", [False, True], ids=["readback", "attachment"])
@pytest.mark.parametrize("live", [False, True], ids=["append", "prepend"])
def test_existing_processing_transition_preserves_new_upload_receipt(
    cloud_api, after_patch, tmp_path, monkeypatch, attachment, live
):
    from app import tonies_api
    cloud, api = cloud_api
    cloud.live = live
    cloud.chapters[0].update(file="pending-processing-reference", transcoding=True)
    after_patch(finish_processing, attachment=attachment)
    monkeypatch.setattr(tonies_api, "_audio_seconds", lambda _: 1)
    path = tmp_path / "clip.mp3"
    path.write_bytes(b"synthetic")
    result = api.upload(path, URL, "New")
    expected_ids = ["permanent", "one", "two"] if live else ["one", "two", "permanent"]
    assert result["chapter_id"] == "permanent"
    assert result["target_url"] == URL
    assert [c["chapter_id"] for c in result["content"]["chapters"]] == expected_ids
    processed = next(c for c in result["content"]["chapters"] if c["chapter_id"] == "one")
    assert processed["seconds"] == 12.05
    assert processed["processing"] is False
    assert next(c for c in cloud.chapters if c["id"] == "two") == {
        "id": "two", "title": "Same", "file": "file-two", "seconds": 12, "type": "file"
    }
    assert len(cloud.patches) == len(cloud.storage) == 1


@pytest.mark.parametrize("operation,attachment", [("reorder", False), ("upload", False), ("upload", True)])
@pytest.mark.parametrize("corruption", [
    "processing-title", "processing-id", "processing-type", "order",
    "stable-file", "stable-seconds", "stable-title", "stable-id", "stable-type", "missing", "extra",
])
def test_processing_transition_does_not_hide_unrelated_mutation(
    cloud_api, after_patch, tmp_path, monkeypatch, operation, attachment, corruption
):
    from app import tonies_api
    cloud, api = cloud_api
    cloud.chapters[0].update(file="pending-processing-reference", transcoding=True)

    def corrupt(chapters):
        finish_processing(chapters)
        if corruption == "order":
            chapters.reverse()
        elif corruption == "missing":
            chapters.remove(next(c for c in chapters if c["id"] == "two"))
        elif corruption == "extra":
            chapters.append(chapter("foreign"))
        else:
            owner, field = corruption.split("-")
            selected = next(c for c in chapters if c["id"] == ("one" if owner == "processing" else "two"))
            selected[field] = 99 if field == "seconds" else "foreign"

    after_patch(corrupt, attachment=attachment)
    monkeypatch.setattr(tonies_api, "_audio_seconds", lambda _: 1)
    path = tmp_path / "clip.mp3"
    path.write_bytes(b"synthetic")
    with pytest.raises(RuntimeError, match="not confirmed"):
        if operation == "upload":
            api.upload(path, URL, "New")
        else:
            api.reorder(URL, 0, 1, chapter_id="one")
    assert len(cloud.patches) == 1
    assert len(cloud.storage) == (1 if operation == "upload" else 0)


@pytest.mark.parametrize("operation", ["reorder", "upload"])
@pytest.mark.parametrize("eligibility", ["none", "target-only", "attachment-only"])
def test_processing_readback_requires_original_chapter_eligibility(
    cloud_api, tmp_path, monkeypatch, operation, eligibility
):
    from app import tonies_api
    cloud, api = cloud_api
    cloud.processing = eligibility == "target-only"
    original = cloud.__call__

    def handler(request):
        response = original(request)
        if request.method == "PATCH":
            if eligibility == "attachment-only":
                data = response.json()
                next(c for c in data["chapters"] if c["id"] == "one")["transcoding"] = True
                response = httpx.Response(200, json=data)
            finish_processing(cloud.chapters)
        return response

    api._client.close()
    api._client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(tonies_api, "_audio_seconds", lambda _: 1)
    path = tmp_path / "clip.mp3"
    path.write_bytes(b"synthetic")
    with pytest.raises(RuntimeError, match="not confirmed"):
        if operation == "upload":
            api.upload(path, URL, "New")
        else:
            api.reorder(URL, 0, 1, chapter_id="one")
    assert len(cloud.patches) == 1


@pytest.mark.parametrize("operation", ["reorder", "upload"])
@pytest.mark.parametrize("field,value", [("file", "finished-before-patch"), ("seconds", 12.05)])
def test_processing_transition_before_write_still_refuses_stale_snapshot(
    cloud_api, tmp_path, monkeypatch, operation, field, value
):
    from app import tonies_api
    cloud, api = cloud_api
    cloud.chapters[0]["transcoding"] = True
    original = cloud.__call__

    def handler(request):
        if request.url.path == "/v2/graphql" and cloud.reads == 1:
            cloud.chapters[0][field] = value
            cloud.chapters[0]["transcoding"] = False
        return original(request)

    api._client.close()
    api._client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(tonies_api, "_audio_seconds", lambda _: 1)
    path = tmp_path / "clip.mp3"
    path.write_bytes(b"synthetic")
    with pytest.raises(RuntimeError, match="changed before saving"):
        if operation == "upload":
            api.upload(path, URL, "New")
        else:
            api.reorder(URL, 0, 1, chapter_id="one")
    assert not cloud.patches
