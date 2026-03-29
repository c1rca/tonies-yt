from fastapi.testclient import TestClient

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
