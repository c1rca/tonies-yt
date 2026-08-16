from pathlib import Path
import threading
import time

from app import jobs
from app.models import ParseIntent


def test_run_job_success(monkeypatch, tmp_path):
    monkeypatch.setattr(jobs.settings, 'data_dir', tmp_path)
    (tmp_path / 'downloads').mkdir(parents=True, exist_ok=True)

    def fake_parse(msg: str):
        return ParseIntent(youtube_query='abc', target_character_name='Blue', preferred_title='track')

    def fake_search(query: str, limit: int = 5):
        return [{"title": "Candidate 1", "url": "https://youtube.com/watch?v=abc"}]

    def fake_download(query: str, out_dir: Path, preferred_title: str | None = None):
        p = out_dir / 'track.mp3'
        p.write_bytes(b'fake')
        return p

    monkeypatch.setattr(jobs, 'parse_request', fake_parse)
    monkeypatch.setattr(jobs, 'search_youtube', fake_search)
    monkeypatch.setattr(jobs, 'download_mp3', fake_download)
    monkeypatch.setattr(jobs, '_probe_audio_duration_seconds', lambda _path: 1.0)
    monkeypatch.setattr(jobs, '_ensure_tonies_capacity', lambda *_args: None)
    monkeypatch.setattr(
        jobs,
        '_enqueue_upload',
        lambda job_id, path, **_kwargs: jobs._update(job_id, status='done', output_file=str(path)),
    )

    st = jobs.create_job('test')
    jobs.run_job(st.id)

    mid = jobs.get_job(st.id)
    assert mid is not None
    assert mid.status == 'awaiting_selection'
    assert len(mid.candidates) == 1

    jobs.select_candidate_and_continue(st.id, 0)
    deadline = time.time() + 2
    while jobs.get_job(st.id).status != 'done' and time.time() < deadline:
        time.sleep(0.01)
    out = jobs.get_job(st.id)
    assert out is not None
    assert out.status == 'done'
    assert out.output_file and out.output_file.endswith('.mp3')


def test_run_job_failure(monkeypatch):
    def fake_parse(msg: str):
        raise RuntimeError('boom')

    monkeypatch.setattr(jobs, 'parse_request', fake_parse)

    st = jobs.create_job('test-fail')
    jobs.run_job(st.id)
    out = jobs.get_job(st.id)
    assert out is not None
    assert out.status == 'failed'
    assert 'boom' in (out.error or '')


def test_failed_job_cannot_enqueue_upload_after_download_returns(monkeypatch, tmp_path):
    monkeypatch.setattr(jobs.settings, 'data_dir', tmp_path)
    (tmp_path / 'downloads').mkdir(parents=True)
    started = threading.Event()
    release = threading.Event()
    uploads = []

    def blocked_download(_url, out_dir, _title):
        started.set()
        release.wait(2)
        path = out_dir / 'late.mp3'
        path.write_bytes(b'late')
        return path

    monkeypatch.setattr(jobs, 'download_mp3', blocked_download)
    monkeypatch.setattr(jobs, '_probe_audio_duration_seconds', lambda _path: 1.0)
    monkeypatch.setattr(jobs, '_ensure_tonies_capacity', lambda *_args: None)
    monkeypatch.setattr(jobs, '_enqueue_upload', lambda *args, **kwargs: uploads.append((args, kwargs)))

    st = jobs.create_job('late download')
    jobs._update(st.id, parsed={}, status='queued_download')
    jobs.run_selected_candidate_async(st.id, {'title': 'late', 'url': 'https://example.test/late'})
    assert started.wait(1)
    jobs._update(st.id, status='failed', error='watchdog timeout')
    release.set()

    deadline = time.time() + 1
    while jobs.get_job(st.id).status != 'failed' and time.time() < deadline:
        time.sleep(0.01)
    time.sleep(0.05)
    assert uploads == []
    assert jobs.get_job(st.id).error == 'watchdog timeout'
