"""Refreshes are cheap HTTP reads, never a stale/browser-backed cache."""
from concurrent.futures import ThreadPoolExecutor

from tonies_fake import URL, chapter


def test_concurrent_refreshes_share_auth_but_each_read_is_fresh(cloud_api):
    cloud, api = cloud_api
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(api.get_content, [URL] * 3))
    assert len(cloud.tokens) == 1
    assert cloud.reads == 3
    assert all([c["chapter_id"] for c in result["chapters"]] == ["one", "two"] for result in results)
    cloud.chapters.append(chapter("new"))
    assert api.get_content(URL)["chapters"][-1]["chapter_id"] == "new"
    assert cloud.reads == 4
