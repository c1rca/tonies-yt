from pathlib import Path

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
