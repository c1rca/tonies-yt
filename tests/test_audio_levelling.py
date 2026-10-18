"""Volume levelling and trimming with the real ffmpeg (offline, generated audio)."""
import subprocess
from pathlib import Path

import pytest

from app import downloader, media_files, media_process
from app.media_process import Trim, convert_to_mp3, measure_loudness, probe_seconds

MAX_BYTES = 50 * 1024 * 1024


def _source(tmp_path: Path, name: str, gain_db: float, seconds: float = 8, kind: str = "pink") -> Path:
    """Pink noise at 0 dB measures about -14.6 LUFS; a sine at +12 dB about -9 LUFS."""
    path = tmp_path / f"{name}.wav"
    generator = f"anoisesrc=color=pink:duration={seconds}:seed=7" if kind == "pink" else f"sine=frequency=440:duration={seconds}"
    subprocess.run([
        "ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
        "-i", f"{generator},volume={gain_db}dB",
        "-ac", "2", "-ar", "44100", str(path),
    ], check=True)
    return path


def _converted(tmp_path: Path, source: Path, trim: Trim | None = None) -> Path:
    destination = tmp_path / f"{source.stem}-out.mp3"
    convert_to_mp3(source, destination, max_bytes=MAX_BYTES, trim=trim)
    return destination


def test_quiet_and_loud_sources_come_out_at_the_same_loudness(tmp_path):
    quiet = _converted(tmp_path, _source(tmp_path, "quiet", -10))
    loud = _converted(tmp_path, _source(tmp_path, "loud", 12, kind="sine"))
    levels = [measure_loudness(quiet), measure_loudness(loud)]
    assert all(abs(level - media_process.TARGET_LUFS) <= 1.5 for level in levels), levels
    assert abs(levels[0] - levels[1]) <= 1.0, levels


def test_measurement_tracks_real_level_differences(tmp_path):
    quiet = measure_loudness(_source(tmp_path, "quiet", -30))
    louder = measure_loudness(_source(tmp_path, "louder", -10))
    assert 19 <= louder - quiet <= 21


def test_boost_is_capped_so_very_quiet_audio_is_not_blown_up(tmp_path):
    source = _source(tmp_path, "whisper", -30)
    before = measure_loudness(source)
    after = measure_loudness(_converted(tmp_path, source))
    assert after - before == pytest.approx(media_process.MAX_BOOST_DB, abs=1.0)


def test_silence_converts_without_gain(tmp_path):
    source = tmp_path / "silence.wav"
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "anullsrc=r=44100:cl=stereo", "-t", "3", str(source)], check=True)
    output = _converted(tmp_path, source)
    assert probe_seconds(output, timeout=10) == pytest.approx(3, abs=0.2)


def test_trim_keeps_only_the_requested_section_and_levels_it(tmp_path):
    output = _converted(tmp_path, _source(tmp_path, "long", -10, seconds=10), Trim(start=2, end=5))
    assert probe_seconds(output, timeout=10) == pytest.approx(3, abs=0.15)
    assert abs(measure_loudness(output) - media_process.TARGET_LUFS) <= 1.5


def test_trim_end_past_the_audio_keeps_the_rest(tmp_path):
    output = _converted(tmp_path, _source(tmp_path, "short", -20, seconds=4), Trim(start=1, end=60))
    assert probe_seconds(output, timeout=10) == pytest.approx(3, abs=0.15)


def test_trim_starting_after_the_audio_is_refused(tmp_path):
    with pytest.raises(RuntimeError, match="Trim starts after the end"):
        _converted(tmp_path, _source(tmp_path, "brief", -20, seconds=2), Trim(start=5))


def test_unmeasurable_audio_is_still_converted_unchanged(tmp_path, monkeypatch):
    source = _source(tmp_path, "plain", -30)
    monkeypatch.setattr(media_process, "_measure", lambda *_a, **_kw: None)
    output = _converted(tmp_path, source)
    assert measure_loudness(output) == pytest.approx(measure_loudness(source), abs=1.0)


@pytest.mark.parametrize("trim", [Trim(start=-1), Trim(start=3, end=3), Trim(start=4, end=2),
                                  Trim(start=float("nan")), Trim(start=0, end=float("inf")),
                                  Trim(start=0, end=24 * 3600 + 1)])
def test_invalid_trims_are_rejected(trim):
    with pytest.raises(ValueError):
        trim.validate()


def test_youtube_download_is_encoded_once_levelled_and_trimmed(tmp_path, monkeypatch):
    source = _source(tmp_path, "provider", -10, seconds=10)
    commands = []

    def fake_ytdlp(cmd, **_kwargs):
        commands.append(cmd)
        Path(cmd[cmd.index("-o") + 1].replace("%(ext)s", "webm")).write_bytes(source.read_bytes())
        return b""

    monkeypatch.setattr(downloader, "_run_ytdlp", fake_ytdlp)
    out = tmp_path / "library"
    out.mkdir()
    result = downloader.download_mp3("https://youtu.be/AbCdEf_12-3", out, "Song", trim=Trim(start=1, end=4))
    assert result.parent == out and result.suffix == ".mp3"
    assert "--audio-format" not in commands[0], "yt-dlp must not encode; the app encodes once"
    assert probe_seconds(result, timeout=10) == pytest.approx(3, abs=0.15)
    assert abs(measure_loudness(result) - media_process.TARGET_LUFS) <= 1.5
    assert [p.name for p in out.iterdir()] == [result.name]


def test_local_import_is_levelled(tmp_path):
    destination = tmp_path / "imported.mp3"
    media_files._convert(_source(tmp_path, "local", -10), destination)
    assert abs(measure_loudness(destination) - media_process.TARGET_LUFS) <= 1.5


def _flaky_provider(monkeypatch, source, failures):
    calls = []

    def fake_ytdlp(cmd, **_kwargs):
        calls.append(cmd)
        if len(calls) <= failures:
            raise downloader.ChildFailed("YouTube download failed")
        Path(cmd[cmd.index("-o") + 1].replace("%(ext)s", "webm")).write_bytes(source.read_bytes())
        return b""

    monkeypatch.setattr(downloader, "_run_ytdlp", fake_ytdlp)
    monkeypatch.setattr(downloader, "_RETRY_DELAY_SEC", 0)
    return calls


def test_a_failed_youtube_download_is_tried_once_more(tmp_path, monkeypatch):
    calls = _flaky_provider(monkeypatch, _source(tmp_path, "src", -10, seconds=2), failures=1)
    out = tmp_path / "library"
    out.mkdir()
    assert downloader.download_mp3("https://youtu.be/AbCdEf_12-3", out, "Song").exists()
    assert len(calls) == 2


def test_a_download_that_fails_twice_reports_the_failure(tmp_path, monkeypatch):
    calls = _flaky_provider(monkeypatch, _source(tmp_path, "src", -10, seconds=2), failures=2)
    with pytest.raises(RuntimeError, match="YouTube download failed"):
        downloader.download_mp3("https://youtu.be/AbCdEf_12-3", tmp_path, "Song")
    assert len(calls) == 2


def test_the_retry_is_decided_by_failure_type_not_message(tmp_path, monkeypatch):
    calls = []

    def renamed(cmd, **_kwargs):
        calls.append(cmd)
        raise downloader.ChildFailed("some other wording")

    monkeypatch.setattr(downloader, "_run_ytdlp", renamed)
    monkeypatch.setattr(downloader, "_RETRY_DELAY_SEC", 0)
    with pytest.raises(RuntimeError):
        downloader.download_mp3("https://youtu.be/AbCdEf_12-3", tmp_path, "Song")
    assert len(calls) == 2


def test_size_limit_failures_are_not_retried(tmp_path, monkeypatch):
    calls = []

    def too_big(cmd, **_kwargs):
        calls.append(cmd)
        raise RuntimeError("YouTube download size limit exceeded")

    monkeypatch.setattr(downloader, "_run_ytdlp", too_big)
    with pytest.raises(RuntimeError, match="size limit"):
        downloader.download_mp3("https://youtu.be/AbCdEf_12-3", tmp_path, "Song")
    assert len(calls) == 1
