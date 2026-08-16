from fastapi.testclient import TestClient

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
