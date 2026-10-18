from fastapi.testclient import TestClient
from unittest.mock import Mock
import pytest

from app import main
from app.main import app
from app.routes import files


def test_health_endpoint():
    client = TestClient(app)
    r = client.get('/api/health')
    assert r.status_code == 200
    assert r.json()['status'] == 'ok'


def test_favicon_is_served():
    r = TestClient(app).get('/favicon.ico')
    assert r.status_code == 200
    assert r.content == open('web/favicon.ico', 'rb').read()


def test_chat_creates_job_and_job_is_fetchable():
    client = TestClient(app)
    r = client.post('/api/chat', json={'message': 'find lullabies'})
    assert r.status_code == 200
    data = r.json()
    assert data['job_id']

    r2 = client.get(f"/api/jobs/{data['job_id']}")
    assert r2.status_code == 200
    assert r2.json()['id'] == data['job_id']


def test_missing_library_file_returns_404(monkeypatch, tmp_path):
    monkeypatch.setattr(main.settings, 'data_dir', tmp_path)
    client = TestClient(app)
    response = client.get('/api/files/missing.mp3')
    assert response.status_code == 404


@pytest.mark.parametrize('inline,disposition', [('', 'attachment;'), ('?inline=true', 'inline;')])
def test_library_media_preserves_downloads_and_supports_seeking(monkeypatch, tmp_path, inline, disposition):
    from urllib.parse import quote

    monkeypatch.setattr(main.settings, 'data_dir', tmp_path)
    library = tmp_path / 'downloads'
    library.mkdir()
    name = 'Kid\'s "song" & 雪 #?.mp3'
    (library / name).write_bytes(b'0123456789')
    client = TestClient(app)
    url = '/api/files/' + quote(name, safe='') + inline
    response = client.get(url)
    assert response.status_code == 200 and response.content == b'0123456789'
    assert response.headers['content-type'] == 'audio/mpeg'
    assert response.headers['content-disposition'].startswith(disposition)
    assert response.headers['accept-ranges'] == 'bytes'
    if inline:
        assert response.headers['cache-control'] == 'no-store'
    for value, expected, span in [('bytes=2-5', b'2345', '2-5'), ('bytes=7-', b'789', '7-9'), ('bytes=-2', b'89', '8-9')]:
        part = client.get(url, headers={'Range': value})
        assert part.status_code == 206 and part.content == expected
        assert part.headers['content-range'] == f'bytes {span}/10'
        assert int(part.headers['content-length']) == len(expected)
    invalid = client.get(url, headers={'Range': 'bytes=20-'})
    assert invalid.status_code == 416
    assert invalid.headers['content-range'] == 'bytes */10'


@pytest.mark.parametrize('kind', ['non-mp3', 'directory', 'symlink-outside', 'symlink-inside', 'fifo'])
def test_library_media_refuses_nonregular_or_escaped_mp3s(monkeypatch, tmp_path, kind):
    import os

    monkeypatch.setattr(main.settings, 'data_dir', tmp_path)
    library = tmp_path / 'downloads'
    library.mkdir()
    name = 'not-audio.txt' if kind == 'non-mp3' else 'blocked.mp3'
    path = library / name
    if kind == 'directory':
        path.mkdir()
    elif kind.startswith('symlink'):
        target = (tmp_path if kind == 'symlink-outside' else library) / 'target.mp3'
        target.write_bytes(b'private')
        path.symlink_to(target)
    elif kind == 'fifo':
        os.mkfifo(path)
    else:
        path.write_bytes(b'private')
    response = TestClient(app).get('/api/files/' + name + '?inline=true')
    assert response.status_code == 404
    assert b'private' not in response.content


@pytest.mark.parametrize('filename', ['../secret.mp3', '/secret.mp3', 'nested/song.mp3', r'nested\song.mp3', '\x00.mp3'])
def test_library_media_validates_basename_before_file_access(monkeypatch, tmp_path, filename):
    monkeypatch.setattr(main.settings, 'data_dir', tmp_path)
    (tmp_path / 'downloads').mkdir()
    (tmp_path / 'secret.mp3').write_bytes(b'private')
    with pytest.raises(files.HTTPException) as error:
        files.download_file(filename)
    assert error.value.status_code == 404


def test_upload_existing_rejects_path_outside_library(monkeypatch, tmp_path):
    monkeypatch.setattr(main.settings, 'data_dir', tmp_path)
    (tmp_path / 'downloads').mkdir()
    (tmp_path / 'secret.mp3').write_bytes(b'secret')
    client = TestClient(app)
    response = client.post('/api/upload-existing', json={'filename': '../secret.mp3'})
    assert response.status_code == 400


def test_rename_and_reorder_forward_expected_chapter_identity(monkeypatch):
    rename = Mock(return_value={'chapters': []})
    reorder = Mock(return_value={'chapters': []})
    monkeypatch.setattr(main, 'rename_tonies_chapter', rename)
    monkeypatch.setattr(main, 'reorder_tonies_chapter', reorder)
    client = TestClient(app)
    assert client.post('/api/tonies-content/rename', json={'target_url': 'synthetic', 'index': 2, 'title': 'New', 'chapter_id': 'expected-id'}).json() == {'chapters': []}
    assert rename.call_args.kwargs.get('chapter_id') == 'expected-id'
    assert client.post('/api/tonies-content/reorder', json={'target_url': 'synthetic', 'from_index': 2, 'to_index': 0, 'chapter_id': 'expected-id'}).json() == {'chapters': []}
    assert reorder.call_args.kwargs.get('chapter_id') == 'expected-id'


@pytest.mark.parametrize('route,operation,payload', [
    ('/api/setup/lock', 'lock_runtime', {}),
    ('/api/setup/login', 'login_unlock', {'username': 'synthetic', 'app_password': 'synthetic'}),
    ('/api/setup/init', 'initialize_vault', {'username': 'synthetic', 'app_password': 'synthetic', 'tonies_email': 'synthetic@example.invalid', 'tonies_password': 'synthetic'}),
    ('/api/setup/update-tonies-credentials', 'update_tonies_credentials', {'username': 'synthetic', 'app_password': 'synthetic', 'tonies_email': 'replacement@example.invalid', 'tonies_password': 'replacement'})
])
def test_credential_lifecycle_invalidates_warm_api_client(monkeypatch, route, operation, payload):
    monkeypatch.setattr(main, operation, Mock(return_value={'ok': True}))
    reset = Mock()
    monkeypatch.setattr(main, 'reset_upload_session', reset, raising=False)
    assert TestClient(app).post(route, json=payload).json()['ok']
    reset.assert_called_once()


def test_failed_login_does_not_reset_another_active_api_session(monkeypatch):
    monkeypatch.setattr(main, 'login_unlock', Mock(return_value={'ok': False, 'error': 'Invalid username or password'}))
    reset = Mock()
    monkeypatch.setattr(main, 'reset_upload_session', reset, raising=False)
    assert not TestClient(app).post('/api/setup/login', json={'username': 'synthetic', 'app_password': 'wrong'}).json()['ok']
    reset.assert_not_called()


def test_tonies_actions_endpoint_queues_edits_and_lists_them(monkeypatch):
    from app.actions import ActionQueue
    url = "https://my.tonies.com/creative-tonies/11111111-2222-4333-8444-555555555555/aBcD0123456789EF/edit"

    class Fake:
        closed = False

        def apply(self, target_url, ops):
            return {"content": {"chapters": []}, "results": [{"error": None, "chapter_id": op.get("chapter_id")} for op in ops]}

    fake = Fake()  # The queue intentionally pins one stable account session.
    queue = ActionQueue(lambda: fake, batch_window=0)
    monkeypatch.setattr(main, "action_queue", queue)
    client = TestClient(app)
    r = client.post("/api/tonies-actions", json={"target_url": url, "actions": [
        {"type": "delete", "chapter_id": "a"},
        {"type": "move", "chapter_id": "b", "position": {"before": "c", "index": 2}},
    ]})
    assert r.status_code == 200
    ids = [a["id"] for a in r.json()["actions"]]
    for action_id in ids:
        assert queue.wait(action_id, 10).status == "done"
    listed = client.get("/api/tonies-actions", params={"target_url": url}).json()["actions"]
    assert [a["id"] for a in listed] == ids and {a["status"] for a in listed} == {"done"}


@pytest.mark.parametrize("body", [
    {"actions": [{"type": "delete", "chapter_id": "a"}]},
    {"target_url": "https://example.com/x", "actions": [{"type": "delete", "chapter_id": "a"}]},
    {"target_url": "VALID", "actions": []},
    {"target_url": "VALID", "actions": [{"type": "explode"}]},
    {"target_url": "VALID", "actions": [{"type": "delete"}]},
    {"target_url": "VALID", "actions": [{"type": "move", "chapter_id": "a", "position": {"edge": "middle"}}]},
    {"target_url": "VALID", "actions": [{"type": "rename", "chapter_id": "a", "title": "  "}]},
])
def test_tonies_actions_endpoint_rejects_bad_requests(body):
    url = "https://my.tonies.com/creative-tonies/11111111-2222-4333-8444-555555555555/aBcD0123456789EF/edit"
    if body.get("target_url") == "VALID":
        body = {**body, "target_url": url}
    r = TestClient(app).post("/api/tonies-actions", json=body)
    assert r.status_code in (400, 422)


def test_upload_existing_passes_a_validated_position(monkeypatch, tmp_path):
    monkeypatch.setattr(main.settings, 'data_dir', tmp_path)
    (tmp_path / 'downloads').mkdir()
    (tmp_path / 'downloads' / 'song.mp3').write_bytes(b'x')
    seen = {}

    def fake_job(path, note, target_url, position):
        seen.update(position=position)
        return Mock(id='job', status='queued_prepare')

    monkeypatch.setattr(main, 'create_upload_only_job', fake_job)
    client = TestClient(app)
    ok = client.post('/api/upload-existing', json={'filename': 'song.mp3', 'position': {'before': 'abc', 'index': 3}})
    assert ok.status_code == 200 and seen['position'] == {'before': 'abc', 'index': 3}
    bad = client.post('/api/upload-existing', json={'filename': 'song.mp3', 'position': {'edge': 'sideways'}})
    assert bad.status_code == 400


@pytest.fixture
def undo_client(cloud_api, monkeypatch):
    from app.actions import ActionQueue
    _, api = cloud_api
    queue = ActionQueue(lambda: api, batch_window=0)
    monkeypatch.setattr(queue, "_ensure_worker", lambda target: None)
    monkeypatch.setattr(main, "action_queue", queue)
    yield TestClient(app), queue
    queue._transfers.shutdown(wait=True)


def finish_edits(queue, target):
    with queue._cond:
        batch = queue._ready_batch(target)
        for action in batch:
            queue._set(action, "applying")
    queue._apply(target, batch)


def test_undo_route_returns_only_server_owned_inverse_and_confirmed_content(undo_client, cloud_api):
    from tonies_fake import URL
    cloud, _ = cloud_api
    client, queue = undo_client
    response = client.post('/api/tonies-actions', json={"target_url": URL, "actions": [
        {"type": "rename", "chapter_id": "one", "title": "Changed"}]})
    action_id = response.json()["actions"][0]["id"]
    finish_edits(queue, URL)
    response = client.post(f'/api/tonies-actions/{action_id}/undo', json={
        "target_url": "attacker", "expected": [], "chapters": [], "steps": [{"type": "clear"}]})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"actions", "content"}
    assert body["actions"][0]["status"] == "undone"
    assert body["actions"][0]["undo_available"] is False
    assert body["content"]["chapters"][0]["title"] == "Same"
    assert [c["id"] for c in cloud.chapters] == ["one", "two"]
    assert len(cloud.patches) == 2
    refused = client.post(f'/api/tonies-actions/{action_id}/undo')
    assert refused.status_code == 409 and refused.json()["needs_refresh"] is True
    assert len(cloud.patches) == 2


def test_undo_route_cancels_pending_delete_without_fake_content(undo_client, cloud_api):
    from tonies_fake import URL
    cloud, _ = cloud_api
    client, queue = undo_client
    action = queue.submit(URL, [{"type": "delete", "chapter_id": "one"}])[0]
    response = client.post(f'/api/tonies-actions/{action.id}/undo')
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"actions"} and body["actions"][0]["status"] == "canceled"
    assert body["actions"][0]["error"] is None and not body["actions"][0]["undo_available"]
    assert not cloud.calls


@pytest.mark.parametrize("failure", ["unknown", "stale", "timeout"])
def test_undo_route_refuses_once_with_sanitized_refresh_guidance(undo_client, cloud_api, failure):
    from tonies_fake import URL
    cloud, _ = cloud_api
    client, queue = undo_client
    action = queue.submit(URL, [{"type": "rename", "chapter_id": "one", "title": "Changed"}])[0]
    finish_edits(queue, URL)
    action_id = action.id
    if failure == "unknown":
        action_id = "unknown"
    elif failure == "stale":
        cloud.chapters[1]["title"] = "Outside"
    else:
        cloud.patch_timeout = True
    response = client.post(f'/api/tonies-actions/{action_id}/undo')
    assert response.status_code == 409
    assert response.json() == {"detail": "Undo unavailable or outcome unconfirmed; refresh the Tonie before another action", "needs_refresh": True}
    assert "sensitive" not in response.text
    assert client.post(f'/api/tonies-actions/{action_id}/undo').status_code == 409
    assert len(cloud.patches) == (2 if failure == "timeout" else 1)


@pytest.mark.parametrize('route,operation,payload', [
    ('/api/setup/lock', 'lock_runtime', {}),
    ('/api/setup/login', 'login_unlock', {'username': 'synthetic', 'app_password': 'synthetic'}),
    ('/api/setup/init', 'initialize_vault', {'username': 'synthetic', 'app_password': 'synthetic'}),
    ('/api/setup/change-password', 'change_app_password', {'username': 'synthetic', 'current_password': 'synthetic', 'new_password': 'replacement synthetic'}),
    ('/api/setup/update-tonies-credentials', 'update_tonies_credentials', {'username': 'synthetic', 'app_password': 'synthetic', 'tonies_email': 'replacement@example.invalid', 'tonies_password': 'replacement'}),
])
def test_account_routes_revoke_real_queue_capabilities_and_unapplied_work(undo_client, cloud_api, monkeypatch, route, operation, payload):
    from app import uploader
    from tonies_fake import URL
    cloud, api = cloud_api
    client, queue = undo_client
    # Use two different targets so the completed and pending capabilities coexist.
    completed = queue.submit(URL, [{"type": "rename", "chapter_id": "one", "title": "Changed"}])[0]
    finish_edits(queue, URL)
    pending = queue.submit(URL.replace('/aBcD0123456789EF/', '/0000000000000001/'),
                           [{"type": "delete", "chapter_id": "one"}])[0]
    assert completed.public()["undo_available"] and pending.public()["undo_available"]
    monkeypatch.setattr(main, operation, Mock(return_value={'ok': True}))
    monkeypatch.setattr(main, 'reset_upload_session', uploader.reset_upload_session)
    monkeypatch.setattr(uploader, 'action_queue', queue)
    monkeypatch.setattr(uploader, '_api_client', api)
    monkeypatch.setattr(uploader, '_account_fingerprint', b'synthetic')
    assert client.post(route, json=payload).json()['ok']
    assert not completed.public()["undo_available"] and pending.status == "canceled"
    assert not queue._undo.latest and api.closed and len(cloud.patches) == 1
