"""Audio editor backend: waveform outline, lossless clips of library files, YouTube to library."""
import hashlib
import json
import math
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app import job_pipeline as jobs, job_store, main, media_files
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
    assert max(quiet) * 4 < min(loud), "loud half must stand out without mono-summing gain"


def _limited(path: Path) -> Path:
    """Sparse transients then a sustained tone: near-equal limited peaks, different RMS."""
    subprocess.run([
        "ffmpeg", "-nostdin", "-v", "error", "-y",
        "-f", "lavfi", "-i", r"aevalsrc=if(lt(mod(t\,0.02)\,0.001)\,1.8\,0.01*sin(2*PI*330*t)):s=44100:d=4",
        "-f", "lavfi", "-i", "aevalsrc=1.8*sin(2*PI*330*t):s=44100:d=4",
        "-filter_complex", "[0][1]concat=n=2:v=0:a=1,alimiter=limit=0.891:level=false",
        "-ac", "2", "-b:a", "128k", str(path),
    ], check=True)
    return path


def test_waveform_levels_show_loudness_even_when_peaks_are_flat(tmp_path):
    # Levelled audio has almost every peak at the limiter; the outline must still show the
    # quiet and loud parts, so it also carries each slice's average loudness (RMS).
    outline = waveform(_limited(tmp_path / "a.mp3"), points=400)
    levels = outline["levels"]
    assert len(levels) == len(outline["peaks"]) and all(0 <= v <= 1 for v in levels)
    assert all(v <= p + 1e-3 for v, p in zip(levels, outline["peaks"])), "an average never exceeds its peak"
    quiet, loud = levels[20:180], levels[220:380]
    assert max(quiet) * 2 < min(loud)
    peaks = outline["peaks"]
    assert 0.85 < sum(peaks[20:180]) / sum(peaks[220:380]) < 1.15, "similar peaks alone hide these dynamics"


def test_waveform_of_a_window_zooms_in(tmp_path):
    source = _mp3(tmp_path / "a.mp3", quiet=4, loud=4)
    outline = waveform(source, window=Trim(start=3, end=5), points=200)
    assert outline["seconds"] == pytest.approx(8, abs=0.1)
    assert (outline["start"], outline["end"]) == (3, pytest.approx(5, abs=0.05))
    first, second = outline["peaks"][:90], outline["peaks"][110:]
    assert max(first) * 4 < min(second)


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
    job = job_store.create_job("song")
    jobs.run_job(job.id)
    return job.id


def test_a_youtube_result_can_be_fetched_into_the_library_for_editing(searched, client, library):
    response = client.post(f"/api/jobs/{searched}/fetch", json={"index": 0})
    assert response.status_code == 200 and response.json() == {"filename": "Twinkle.mp3"}
    assert (library / "Twinkle.mp3").exists()
    assert job_store.get_job(searched).status == "awaiting_selection", "fetching doesn't add anything to a Tonie"


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
    with job_store.state_lock:
        job = job_store.get_job(searched)
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


def test_editing_does_not_wait_behind_downloads_being_added(searched, library, downloads, monkeypatch):
    # Three adds hold every download slot; opening the editor must not queue behind them.
    busy = ThreadPoolExecutor(max_workers=1)
    release = threading.Event()
    busy.submit(release.wait, 5)
    monkeypatch.setattr(jobs, "_downloads", busy)
    monkeypatch.setattr(jobs, "_library_copy", lambda _url: None)  # force a real download
    try:
        assert jobs.fetch_candidate(searched, 0).result(3).is_file()
        assert len(downloads) == 1
    finally:
        release.set()
        busy.shutdown(wait=True)


@pytest.mark.parametrize("audio", ["anullsrc=r=44100:cl=stereo:d=2",
    "aevalsrc=0.4*sin(2*PI*330*t)|-0.4*sin(2*PI*330*t):s=44100:d=2"])
def test_waveform_silence_and_opposite_phase_stereo_are_finite(tmp_path, audio):
    source = tmp_path / "stereo.wav"
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", audio, str(source)], check=True)
    outline = waveform(source, points=2000)
    assert len(outline["levels"]) == len(outline["peaks"]) == 2000
    for values in (outline["levels"], outline["peaks"]):
        assert all(math.isfinite(v) and 0 <= v <= 1 for v in values)
        if audio.startswith("anullsrc"):
            assert set(values) == {0}
        else:
            assert max(values) > 0.2, "opposite-phase audio must not disappear into a mono mix"


def test_rms_window_dynamics_and_native_resource_caps_stay_bounded(tmp_path, monkeypatch):
    from app import media_process as media
    source = _mp3(tmp_path / "window.mp3", quiet=2, loud=2)
    calls, real = [], media.run_bounded

    def observed(cmd, **kwargs):
        output = real(cmd, **kwargs)
        calls.append((cmd, kwargs, len(output)))
        return output

    monkeypatch.setattr(media, "run_bounded", observed)
    outline = waveform(source, window=Trim(start=1, end=3), points=2000)
    assert sum(outline["levels"][:900]) * 8 < sum(outline["levels"][1100:1900])
    cmd, policy, size = next(call for call in calls if call[1]["operation"] == "Waveform")
    assert policy == {"operation": "Waveform", "timeout": 240.5, "max_output_bytes": 2000 * 128 + 4096}
    assert 0 < size <= policy["max_output_bytes"]
    assert cmd[cmd.index("-protocol_whitelist") + 1] == "file,pipe"
    assert cmd[cmd.index("-ss") + 1] == "1.000" and cmd[cmd.index("-t") + 1] == "2.000"
    assert next(call[1] for call in calls if call[1]["operation"] == "Audio probe") == {
        "operation": "Audio probe", "timeout": 30, "max_output_bytes": 64 * 1024}


def _stream_receipt(source):
    return json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
        "stream=codec_name,bit_rate,sample_rate,channels:packet=data_hash", "-show_packets",
        "-show_data_hash", "sha256", "-of", "json", str(source)]))


@pytest.mark.parametrize("bitrate", [128, 320])
def test_waveform_and_clip_preserve_original_bytes_and_encoded_audio_frames(tmp_path, bitrate, record_property):
    source, clip = tmp_path / "source.mp3", tmp_path / "clip.mp3"
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=330:duration=4", "-ar", "44100", "-ac", "2", "-b:a", f"{bitrate}k", str(source)], check=True)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    original = _stream_receipt(source)
    waveform(source, points=200)
    cut_mp3(source, clip, Trim(start=1, end=3))
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    kept = _stream_receipt(clip)
    assert kept["streams"] == original["streams"] == [{
        "codec_name": "mp3", "sample_rate": "44100", "channels": 2, "bit_rate": str(bitrate * 1000)}]
    packets = [p["data_hash"] for p in original["packets"]]
    cut_packets = [p["data_hash"] for p in kept["packets"]]
    assert len(cut_packets) > 20
    assert any(packets[i:i + len(cut_packets)] == cut_packets for i in range(len(packets))), "every compressed frame is copied"
    record_property("bitrate_proof", json.dumps({"source_sha256": before, "after_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "source_stream": original["streams"][0], "clip_stream": kept["streams"][0],
        "source_packets": len(packets), "copied_packets": len(cut_packets), "all_packets_identical": True}))


def test_uncached_editor_download_uses_original_container_and_one_unchanged_128k_encoder(tmp_path, monkeypatch, record_property):
    from app import downloader, media_process as media
    original = tmp_path / "provider.webm"
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=330:duration=2", "-c:a", "libopus", "-b:a", "160k", str(original)], check=True)
    source_hash = hashlib.sha256(original.read_bytes()).hexdigest()
    commands, conversions, real = [], [], media.run_conversion

    def provider(cmd, **kwargs):
        commands.append(cmd)
        shutil.copyfile(original, cmd[cmd.index("-o") + 1].replace("%(ext)s", "webm"))
        kwargs["resource_check"]()

    def encode(cmd, **kwargs):
        conversions.append(cmd)
        real(cmd, **kwargs)

    monkeypatch.setattr(downloader, "_run_ytdlp", provider)
    monkeypatch.setattr(media, "run_conversion", encode)
    result = downloader.download_mp3("https://www.youtube.com/watch?v=AbCdEf_12-3", tmp_path, "Offline")
    assert len(commands) == len(conversions) == 1
    assert "-x" not in commands[0] and "--extract-audio" not in commands[0]
    assert commands[0][commands[0].index("--format") + 1] == "bestaudio/best"
    cmd = conversions[0]
    for option, value in {"-ar": "44100", "-ac": "2", "-codec:a": "libmp3lame", "-b:a": "128k"}.items():
        assert cmd[cmd.index(option) + 1] == value
    assert "alimiter=limit=0.891:level=false" in cmd[cmd.index("-af") + 1]
    assert _stream_receipt(result)["streams"][0]["bit_rate"] == "128000"
    record_property("encoding_proof", json.dumps({"provider_sha256": source_hash, "encoder_calls": len(conversions),
        "format": "bestaudio/best", "extract_audio": False, "output_stream": _stream_receipt(result)["streams"][0]}))
    assert hashlib.sha256(original.read_bytes()).hexdigest() == source_hash
    assert not list(tmp_path.glob(".yt-audio-*"))


@pytest.mark.parametrize("extra", [0, 1])
def test_waveform_native_output_budget_is_enforced_and_child_reaped(tmp_path, monkeypatch, extra):
    import os
    import sys
    source = _mp3(tmp_path / "source.mp3", quiet=1, loud=1)
    original, children = subprocess.Popen, []
    budget = 100 * 128 + 4096
    data = b"frame:0 pts:0 pts_time:0\nlavfi.astats.Overall.Peak_level=-6\nlavfi.astats.Overall.RMS_level=-12\n"

    def launch(cmd, *args, **kwargs):
        if cmd[0] == "ffmpeg":
            cmd = [sys.executable, "-c", f"import os; os.write(1, {data!r} + b' ' * {budget - len(data) + extra})"]
            child = original(cmd, *args, **kwargs)
            children.append(child)
            return child
        return original(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", launch)
    if extra:
        with pytest.raises(RuntimeError, match="Waveform output limit exceeded"):
            waveform(source, points=100)
    else:
        outline = waveform(source, points=100)
        assert outline["peaks"] == [0.501] and outline["levels"] == [0.251]
    assert len(children) == 1 and children[0].poll() is not None
    with pytest.raises(ChildProcessError):
        os.waitpid(children[0].pid, os.WNOHANG)
