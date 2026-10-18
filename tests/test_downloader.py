import pytest

from app import downloader
from app.downloader import sanitize_filename


def test_sanitize_filename_removes_unsafe_chars():
    raw = "Reese's Puffs / bedtime: story?*"
    safe = sanitize_filename(raw)
    assert safe
    assert "/" not in safe
    assert " " not in safe


def test_sanitize_filename_has_reasonable_max_length():
    raw = "a" * 500
    safe = sanitize_filename(raw)
    assert len(safe) <= 120


def test_sanitize_filename_fallback_name():
    raw = "***"
    safe = sanitize_filename(raw)
    assert safe == "audio"


def test_download_mp3_does_not_return_preexisting_same_prefix_file(monkeypatch, tmp_path):
    stale = tmp_path / "Song.mp3"
    stale.write_bytes(b"old")
    monkeypatch.setattr(downloader, "_run_ytdlp", lambda *_a, **_kw: b"")

    with pytest.raises(RuntimeError, match="no audio file found"):
        downloader.download_mp3("https://www.youtube.com/watch?v=AbCdEf_12-3", tmp_path, "Song")
