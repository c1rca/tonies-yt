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
