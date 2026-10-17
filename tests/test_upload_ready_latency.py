"""Persisted API readback is immediate; no page-ready stabilization waits."""
from test_tonies_api import URL, cloud_api


def test_upload_never_sleeps_once_authoritative_attachment_is_visible(cloud_api, tmp_path, monkeypatch):
    from app import tonies_api
    _, api = cloud_api
    monkeypatch.setattr(tonies_api, "_audio_seconds", lambda path: 1)
    monkeypatch.setattr(tonies_api.time, "sleep", lambda _: (_ for _ in ()).throw(AssertionError("no stabilization delay")))
    path = tmp_path / "clip.mp3"
    path.write_bytes(b"synthetic")
    assert api.upload(path, URL, "New")["chapter_id"] == "permanent"
