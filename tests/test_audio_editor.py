"""Audio editor backend: waveform outline, lossless clips of library files, YouTube to library."""
import subprocess
import threading
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app import jobs, main, media_files
from app.media_process import Trim, TrimOutsideAudio, cut_mp3, probe_seconds, waveform
from app.models import ParseIntent


def _mp3(path: Path, *, quiet: float, loud: float) -> Path:
    """`quiet` seconds of soft noise, then `loud` seconds of loud noise, as a 128 kbps MP3."""
    subprocess.run([
        "ffmpeg", "-nostdin", "-v", "error", "-y",
        "-f", "lavfi", "-i", f"anoisesrc=color=pink:duration={quiet}:seed=1,volume=-30dB",
        "-f", "lavfi", "-i", f"anoisesrc=color=pink:duration={loud}:seed=2",
        "-filter_complex", "[0][1]concat=n=2:v=0:a=1", "-ac", "2", "-b:a", "128k", str(path),
    ], check=True)
    return path


def test_waveform_outlines_the_whole_track(tmp_path):
    outline = waveform(_mp3(tmp_path / "a.mp3", quiet=4, loud=4), points=400)
    peaks = outline["peaks"]
    assert outline["seconds"] == pytest.approx(8, abs=0.1)
    assert (outline["start"], outline["end"]) == (0, pytest.approx(8, abs=0.1))
    assert 390 <= len(peaks) <= 400 and all(0 <= p <= 1 for p in peaks)
    quiet, loud = peaks[: len(peaks) // 2 - 10], peaks[len(peaks) // 2 + 10:]
    assert max(quiet) < 0.2 < min(loud), "loud half must stand out"


def test_waveform_of_a_window_zooms_in(tmp_path):
    source = _mp3(tmp_path / "a.mp3", quiet=4, loud=4)
    outline = waveform(source, window=Trim(start=3, end=5), points=200)
    assert outline["seconds"] == pytest.approx(8, abs=0.1)
    assert (outline["start"], outline["end"]) == (3, pytest.approx(5, abs=0.05))
    first, second = outline["peaks"][:90], outline["peaks"][110:]
    assert max(first) < 0.2 < min(second)


def test_waveform_window_after_the_end_is_refused(tmp_path):
    with pytest.raises(TrimOutsideAudio):
        waveform(_mp3(tmp_path / "a.mp3", quiet=1, loud=1), window=Trim(start=5))


def test_cut_keeps_only_the_window_without_re_encoding(tmp_path):
    source = _mp3(tmp_path / "a.mp3", quiet=4, loud=6)
    clip = tmp_path / "clip.mp3"
    cut_mp3(source, clip, Trim(start=2, end=7))
    assert probe_seconds(clip, timeout=10) == pytest.approx(5, abs=0.1)
    # Stream copy: same 128 kbps frames, so size scales with length.
    assert clip.stat().st_size == pytest.approx(source.stat().st_size / 2, rel=0.08)
    assert probe_seconds(source, timeout=10) == pytest.approx(10, abs=0.1)


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr(main.settings, "data_dir", tmp_path)
    root = tmp_path / "downloads"
    root.mkdir()
    media_files.clear_waveform_cache()
    _mp3(root / "Song.mp3", quiet=4, loud=6)
    return root


@pytest.fixture
def client():
    return TestClient(main.app)


def test_waveform_endpoint(library, client):
    response = client.get("/api/files/Song.mp3/waveform", params={"points": 300})
    assert response.status_code == 200
    body = response.json()
    assert body["seconds"] == pytest.approx(10, abs=0.1) and 290 <= len(body["peaks"]) <= 300
    zoom = client.get("/api/files/Song.mp3/waveform", params={"start": 2, "end": 4, "points": 100}).json()
    assert (zoom["start"], zoom["end"]) == (2, pytest.approx(4, abs=0.05))


@pytest.mark.parametrize("params", [{"points": 5}, {"points": 5000}, {"start": -1}, {"start": 4, "end": 2},
                                    {"start": 50}])
def test_waveform_endpoint_refuses_bad_requests(library, client, params):
    assert client.get("/api/files/Song.mp3/waveform", params=params).status_code == 422


def test_waveform_endpoint_only_reads_library_files(library, client):
    assert client.get("/api/files/missing.mp3/waveform").status_code == 404
    assert client.get("/api/files/..%2Fsecrets.mp3/waveform").status_code == 404


def test_clip_endpoint_saves_a_new_library_file(library, client):
    response = client.post("/api/files/Song.mp3/clip", json={"start": 2, "end": 7.5})
    assert response.status_code == 200
    name = response.json()["filename"]
    assert name == "Song_0-02-0-07.mp3"
    assert probe_seconds(library / name, timeout=10) == pytest.approx(5.5, abs=0.1)
    assert (library / "Song.mp3").exists()
    again = client.post("/api/files/Song.mp3/clip", json={"start": 2, "end": 7.5}).json()["filename"]
    assert again != name and again.startswith("Song_0-02-0-07_") and (library / name).exists()
    assert {f["name"] for f in client.get("/api/files").json()} == {"Song.mp3", name, again}


@pytest.mark.parametrize("body", [{"start": 3, "end": 3}, {"start": 5, "end": 1}, {"start": 30, "end": 40},
                                  {"start": -1, "end": 2}, {"start": 0}])
def test_clip_endpoint_refuses_bad_windows(library, client, body):
    assert client.post("/api/files/Song.mp3/clip", json=body).status_code == 422
    assert [p.name for p in library.iterdir()] == ["Song.mp3"]


@pytest.fixture
def downloads():
    return []


@pytest.fixture
def searched(monkeypatch, tmp_path, library, downloads):
    def fake_download(url, out_dir: Path, preferred_title=None):
        downloads.append(url)
        path = out_dir / f"{preferred_title}.mp3"
        path.write_bytes((library / "Song.mp3").read_bytes())
        return path

    monkeypatch.setattr(jobs, "parse_request", lambda _m: ParseIntent(youtube_query="song"))
    monkeypatch.setattr(jobs, "search_youtube", lambda _q, limit=5: [
        {"title": "Twinkle", "url": "https://www.youtube.com/watch?v=AbCdEf_12-3", "duration": 10}])
    monkeypatch.setattr(jobs, "download_mp3", fake_download)
    monkeypatch.setattr(jobs, "_fetches", {})
    job = jobs.create_job("song")
    jobs.run_job(job.id)
    return job.id


def test_a_youtube_result_can_be_fetched_into_the_library_for_editing(searched, client, library):
    response = client.post(f"/api/jobs/{searched}/fetch", json={"index": 0})
    assert response.status_code == 200 and response.json() == {"filename": "Twinkle.mp3"}
    assert (library / "Twinkle.mp3").exists()
    assert jobs.get_job(searched).status == "awaiting_selection", "fetching doesn't add anything to a Tonie"


@pytest.mark.parametrize("body", [{"index": 3}, {"index": -1}, {"index": True}])
def test_fetch_refuses_unknown_results(searched, client, body):
    assert client.post(f"/api/jobs/{searched}/fetch", json=body).status_code == 422


def test_editing_the_same_video_again_reuses_its_file(searched, client, library, downloads):
    first = client.post(f"/api/jobs/{searched}/fetch", json={"index": 0}).json()
    again = client.post(f"/api/jobs/{searched}/fetch", json={"index": 0}).json()
    assert first == again == {"filename": "Twinkle.mp3"}
    assert len(downloads) == 1
    (library / "Twinkle.mp3").unlink()
    assert client.post(f"/api/jobs/{searched}/fetch", json={"index": 0}).json() == {"filename": "Twinkle.mp3"}
    assert len(downloads) == 2, "a deleted copy is downloaded again"


def test_two_edits_while_downloading_share_one_download(searched, library, downloads, monkeypatch):
    gate = threading.Event()
    real = jobs.download_mp3
    monkeypatch.setattr(jobs, "download_mp3", lambda *a: gate.wait(5) and real(*a))
    first, second = jobs.fetch_candidate(searched, 0), jobs.fetch_candidate(searched, 0)
    assert first is second
    gate.set()
    assert first.result(5).name == "Twinkle.mp3" and len(downloads) == 1


def test_a_video_already_added_is_not_downloaded_again(searched, client, library, downloads):
    added = library / "Added.mp3"
    added.write_bytes((library / "Song.mp3").read_bytes())
    with jobs._lock:
        job = jobs._jobs[searched]
        job.selected_candidate = dict(job.candidates[0])
        job.output_file = str(added)
    assert client.post(f"/api/jobs/{searched}/fetch", json={"index": 0}).json() == {"filename": "Added.mp3"}
    assert downloads == []


def test_editor_ffmpeg_work_is_limited_to_two_at_a_time(library, client, monkeypatch):
    monkeypatch.setattr(media_files, "_EDITOR_SLOTS", threading.BoundedSemaphore(0))
    busy = client.get("/api/files/Song.mp3/waveform")
    assert busy.status_code == 429 and busy.headers["retry-after"] == "3"
    assert client.post("/api/files/Song.mp3/clip", json={"start": 1, "end": 2}).status_code == 429
    assert [p.name for p in library.iterdir()] == ["Song.mp3"], "no clip or temp file is left behind"
