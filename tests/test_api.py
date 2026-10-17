from fastapi.testclient import TestClient
from unittest.mock import Mock
import pytest

from app import main
from app.main import app


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
        def apply(self, target_url, ops):
            return {"content": {"chapters": []}, "results": [{"error": None, "chapter_id": op.get("chapter_id")} for op in ops]}

    queue = ActionQueue(lambda: Fake(), batch_window=0)
    monkeypatch.setattr(main, "action_queue", queue)
    client = TestClient(app)
    r = client.post("/api/tonies-actions", json={"target_url": url, "actions": [
        {"type": "delete", "chapter_id": "a"},
        {"type": "move", "chapter_id": "b", "position": {"before": "c", "index": 2}},
    ]})
    assert r.status_code == 200
    ids = [a["id"] for a in r.json()["actions"]]
    for action_id in ids:
        assert queue.wait(action_id, 5).status == "done"
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
