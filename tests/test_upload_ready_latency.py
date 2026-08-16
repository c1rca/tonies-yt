from pathlib import Path


def test_upload_save_wait_is_short_after_named_row_is_stable():
    source = (Path(__file__).parents[1] / "app" / "uploader.py").read_text()
    assert "stable_for_4_seconds" in source
    assert "idx - new_row_seen_poll >= 4" in source
    assert "stable_for_15_seconds" not in source
