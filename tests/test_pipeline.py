from pathlib import Path

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

    def fake_upload(path: Path, target: str | None = None):
        assert path.exists()

    monkeypatch.setattr(jobs, 'parse_request', fake_parse)
    monkeypatch.setattr(jobs, 'search_youtube', fake_search)
    monkeypatch.setattr(jobs, 'download_mp3', fake_download)
    monkeypatch.setattr(jobs, 'upload_to_tonies', fake_upload)

    st = jobs.create_job('test')
    jobs.run_job(st.id)

    mid = jobs.get_job(st.id)
    assert mid is not None
    assert mid.status == 'awaiting_selection'
    assert len(mid.candidates) == 1

    jobs.select_candidate_and_continue(st.id, 0)
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
