"""Trim a YouTube pick before it is added: request validation and the job hand-off."""
from pathlib import Path
import time

from fastapi.testclient import TestClient
import pytest

from app import jobs, main
from app.media_process import Trim
from app.models import ParseIntent


@pytest.fixture
def searched(monkeypatch, tmp_path):
    """A finished search job with one candidate; downloads are recorded, never run."""
    monkeypatch.setattr(jobs.settings, "data_dir", tmp_path)
    (tmp_path / "downloads").mkdir()
    downloads = []

    def fake_download(url, out_dir: Path, preferred_title=None, trim=None):
        downloads.append({"url": url, "trim": trim})
        path = out_dir / f"track-{len(downloads)}.mp3"
        path.write_bytes(b"audio")
        return path

    monkeypatch.setattr(jobs, "parse_request", lambda _m: ParseIntent(youtube_query="song"))
    monkeypatch.setattr(jobs, "search_youtube", lambda _q, limit=5: [
        {"title": "Song", "url": "https://www.youtube.com/watch?v=AbCdEf_12-3", "duration": 120}])
    monkeypatch.setattr(jobs, "download_mp3", fake_download)
    monkeypatch.setattr(jobs, "_enqueue_upload",
                        lambda job_id, path, **_kw: jobs._update(job_id, status="done", output_file=str(path)))
    job = jobs.create_job("song")
    jobs.run_job(job.id)
    return job.id, downloads


def _wait_for(downloads, count):
    deadline = time.time() + 2
    while len(downloads) < count and time.time() < deadline:
        time.sleep(0.01)
    return downloads


def test_trim_reaches_the_download(searched):
    job_id, downloads = searched
    jobs.select_candidate_and_continue(job_id, 0, trim=Trim(start=5, end=90))
    assert _wait_for(downloads, 1)[0]["trim"] == Trim(start=5, end=90)
    assert jobs.get_job(job_id).trim == {"start": 5, "end": 90}


def test_no_trim_downloads_the_whole_track(searched):
    job_id, downloads = searched
    jobs.select_candidate_and_continue(job_id, 0)
    assert _wait_for(downloads, 1)[0]["trim"] is None


def test_same_video_with_a_different_trim_is_a_separate_add(searched):
    job_id, downloads = searched
    jobs.select_candidate_and_continue(job_id, 0, trim=Trim(start=0, end=30))
    _wait_for(downloads, 1)
    jobs.select_candidate_and_continue(job_id, 0, trim=Trim(start=30, end=60))
    assert [d["trim"] for d in _wait_for(downloads, 2)] == [Trim(0, 30), Trim(30, 60)]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main.auth, "session_valid", lambda _session: True)
    return TestClient(main.app)


@pytest.mark.parametrize("trim", [{"start": -1}, {"start": 10, "end": 10}, {"start": 20, "end": 5},
                                  {"start": 0, "end": 90000}, {"start": "a"}])
def test_invalid_trims_are_refused_before_anything_runs(client, monkeypatch, trim):
    monkeypatch.setattr(main, "select_candidate_and_continue",
                        lambda *_a, **_kw: pytest.fail("an invalid trim must not start a download"))
    response = client.post("/api/jobs/any/select", json={"index": 0, "trim": trim})
    assert response.status_code == 422


def test_valid_trim_is_passed_to_the_job(client, monkeypatch):
    seen = {}

    def select(job_id, index, target_url=None, position=None, trim=None):
        seen.update(job_id=job_id, trim=trim)
        return job_id

    monkeypatch.setattr(main, "select_candidate_and_continue", select)
    monkeypatch.setattr(main, "get_job", lambda _id: None)
    client.post("/api/jobs/j1/select", json={"index": 0, "trim": {"start": 3.5, "end": 61}})
    assert seen == {"job_id": "j1", "trim": Trim(start=3.5, end=61)}
