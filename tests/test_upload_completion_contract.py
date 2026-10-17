"""Completion means persisted chapter identity, not S3/count/UI hints."""
import httpx
import pytest

from test_tonies_api import URL, chapter, cloud_api


def test_upload_returns_identity_and_fresh_content_while_processing(cloud_api, tmp_path, monkeypatch):
    from app import tonies_api
    cloud, api = cloud_api
    monkeypatch.setattr(tonies_api, "_audio_seconds", lambda path: 1)
    path = tmp_path / "clip.mp3"
    path.write_bytes(b"synthetic")
    result = api.upload(path, URL, "New")
    assert result["chapter_id"] == "permanent"
    assert result["content"]["chapters"][-1]["chapter_id"] == result["chapter_id"]
    assert result["content"]["processing"] is True
    assert cloud.reads == 3  # Initial, immediate pre-write safety, authoritative readback.


def test_successful_storage_alone_cannot_complete_an_upload(cloud_api, tmp_path, monkeypatch):
    from app import tonies_api
    cloud, api = cloud_api
    monkeypatch.setattr(tonies_api, "_audio_seconds", lambda path: 1)
    path = tmp_path / "clip.mp3"
    path.write_bytes(b"synthetic")
    original = cloud.__call__

    def handler(request):
        if request.method == "PATCH":
            return httpx.Response(200, json={"chapters": cloud.chapters})
        return original(request)

    api._client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match="attachment identity"):
        api.upload(path, URL, "New")
    assert len(cloud.storage) == 1


def test_unrelated_new_row_count_cannot_confirm_uploaded_identity(cloud_api, tmp_path, monkeypatch):
    from app import tonies_api
    cloud, api = cloud_api
    monkeypatch.setattr(tonies_api, "_audio_seconds", lambda path: 1)
    path = tmp_path / "clip.mp3"
    path.write_bytes(b"synthetic")
    original = cloud.__call__

    def handler(request):
        response = original(request)
        if request.method == "PATCH":
            cloud.chapters[-1] = chapter("unrelated", "New")
        return response

    api._client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match="not confirmed"):
        api.upload(path, URL, "New")
    assert len(cloud.patches) == 1
