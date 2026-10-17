"""Exact-ID ordering replaces the former dynamic-row drag tests."""
import asyncio
import copy

import pytest

from app import uploader
from tonies_fake import URL, chapter


def test_upload_from_running_event_loop_needs_no_thread_handoff(monkeypatch):
    monkeypatch.setattr(uploader, "get_credentials", lambda: {})

    async def run():
        with pytest.raises(RuntimeError, match="credentials"):
            uploader.upload_to_tonies("missing.mp3")

    asyncio.run(run())


@pytest.mark.parametrize("source,destination,expected", [(1, 3, ["one", "three", "four", "two"]), (3, 1, ["one", "four", "two", "three"]), (3, 0, ["four", "one", "two", "three"]), (0, 0, ["one", "two", "three", "four"])])
def test_reorder_inserts_exact_duplicate_title_id_at_native_index(cloud_api, source, destination, expected):
    cloud, api = cloud_api
    cloud.chapters = [chapter(identity) for identity in ["one", "two", "three", "four"]]
    before = {c["id"]: copy.deepcopy(c) for c in cloud.chapters}
    result = api.reorder(URL, source, destination, cloud.chapters[source]["id"])
    assert [c["chapter_id"] for c in result["chapters"]] == expected
    for current in cloud.chapters:
        for key in ("id", "title", "file", "seconds", "type"):
            assert current[key] == before[current["id"]][key]
    assert len(cloud.patches) == (0 if source == destination else 1)


def test_reorder_waits_only_for_expected_order_not_processing(cloud_api):
    cloud, api = cloud_api
    cloud.processing = True
    result = api.reorder(URL, 1, 0, "two")
    assert [c["chapter_id"] for c in result["chapters"]] == ["two", "one"]
    assert result["processing"] is True


def test_reorder_never_reports_a_wrong_settled_order_as_success(cloud_api):
    cloud, api = cloud_api
    cloud.apply_patch = False
    with pytest.raises(RuntimeError, match="not confirmed"):
        api.reorder(URL, 1, 0, "two")
    assert len(cloud.patches) == 1


def test_read_rejects_absent_or_wrong_case_target_instead_of_empty_editor(cloud_api):
    import httpx
    import json

    cloud, api = cloud_api
    original = cloud.__call__

    def handler(request):
        if request.url.path == "/v2/graphql":
            assert json.loads(request.content)["variables"]["creativeTonieId"] == "abcd0123456789EF"
            return httpx.Response(200, json=cloud.snapshot())
        return original(request)

    api._client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match="not uniquely found"):
        api.get_content(URL.replace("aBcD", "abcd"))
    assert not cloud.patches
