"""YouTube playlists: link policy, the bounded listing command and ordered multi-add."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
import time

from fastapi.testclient import TestClient
import pytest

from app import attach_order, downloader, job_pipeline as jobs, job_store, job_upload, main
from app.models import ParseIntent
from app.routes import jobs as job_routes
from app.youtube_source import is_playlist_url, normalize_youtube_input

LIST_ID = "PLAbCdEfGhIjKlMnOpQrStUv_0123456-x"
PLAYLIST = f"https://www.youtube.com/playlist?list={LIST_ID}"


@pytest.mark.parametrize("value", [
    PLAYLIST, f"https://youtube.com/playlist?list={LIST_ID}&si=tracking",
    f"youtube.com/playlist?list={LIST_ID}", f"https://m.youtube.com/playlist?list={LIST_ID}",
    f"https://music.youtube.com/playlist?list={LIST_ID}",
])
def test_playlist_links_become_one_canonical_playlist_url(value):
    assert normalize_youtube_input(value) == PLAYLIST
    assert is_playlist_url(PLAYLIST)


@pytest.mark.parametrize("value", [
    "https://www.youtube.com/playlist?list=short", "https://www.youtube.com/playlist",
    f"https://www.youtube.com/playlist?list={LIST_ID}&list={LIST_ID}",
    "https://www.youtube.com/playlist?list=" + "A" * 65, f"https://www.youtube.com/playlist?list={LIST_ID}%2F",
    f"https://evil.test/playlist?list={LIST_ID}",
])
def test_malformed_playlist_links_are_refused(value):
    with pytest.raises(ValueError):
        normalize_youtube_input(value)


def test_a_video_playing_inside_a_playlist_is_still_that_one_video():
    url = f"https://www.youtube.com/watch?v=AbCdEf_12-3&list={LIST_ID}&index=4"
    assert normalize_youtube_input(url) == "https://www.youtube.com/watch?v=AbCdEf_12-3"
    assert not is_playlist_url(normalize_youtube_input(url))


def _entries(count):
    return b"\n".join(json.dumps({"id": f"Video{n:06d}", "title": f"Song {n}", "duration": 60 + n,
                                  "channel": "Channel"}).encode() for n in range(count))


def test_playlist_listing_is_flat_bounded_and_ignores_the_search_limit(monkeypatch):
    seen = {}

    def fake(cmd, **kwargs):
        seen.update(cmd=cmd, kwargs=kwargs)
        return _entries(80)

    monkeypatch.setattr(downloader, "_run_ytdlp", fake)
    downloader.clear_search_cache()
    results = downloader.search_youtube(PLAYLIST, limit=5)
    cmd = seen["cmd"]
    assert len(results) == downloader.PLAYLIST_MAX == 50
    assert [r["title"] for r in results[:2]] == ["Song 0", "Song 1"]
    assert results[0]["url"] == "https://www.youtube.com/watch?v=Video000000"
    assert results[0]["uploader"] == "Channel"
    assert cmd[-2:] == ["--", PLAYLIST]
    assert "--yes-playlist" in cmd and "--no-playlist" not in cmd
    assert "--flat-playlist" in cmd and cmd[cmd.index("--playlist-end") + 1] == "50"
    assert cmd[cmd.index("--use-extractors") + 1] == "youtube:tab"
    assert seen["kwargs"]["max_output_bytes"] == downloader._MAX_SEARCH_OUTPUT_BYTES


def test_ordinary_searches_still_refuse_playlists(monkeypatch):
    seen = {}
    monkeypatch.setattr(downloader, "_run_ytdlp", lambda cmd, **_kw: seen.update(cmd=cmd) or b"")
    downloader.clear_search_cache()
    downloader.search_youtube("lullabies", limit=5)
    assert "--no-playlist" in seen["cmd"] and "--yes-playlist" not in seen["cmd"]


@pytest.fixture
def playlist_job(monkeypatch, tmp_path):
    """A listed playlist of four videos; downloads are simulated, uploads recorded in order."""
    monkeypatch.setattr(jobs.settings, "data_dir", tmp_path)
    (tmp_path / "downloads").mkdir()
    state = {"enqueued": [], "running": 0, "peak": 0, "slow": set(), "fail": set()}
    lock = threading.Lock()

    def fake_download(url, out_dir: Path, preferred_title=None):
        with lock:
            state["running"] += 1
            state["peak"] = max(state["peak"], state["running"])
        try:
            time.sleep(0.3 if preferred_title in state["slow"] else 0.05)
            if preferred_title in state["fail"]:
                raise RuntimeError("YouTube download failed")
            path = out_dir / f"{preferred_title}.mp3"
            path.write_bytes(b"audio")
            return path
        finally:
            with lock:
                state["running"] -= 1

    def fake_enqueue(job_id, path, **kwargs):
        state["enqueued"].append((Path(path).stem, kwargs.get("position")))
        job_store._update(job_id, status="done", output_file=str(path))

    monkeypatch.setattr(jobs, "parse_request", lambda _m: ParseIntent(youtube_query=PLAYLIST))
    monkeypatch.setattr(jobs, "search_youtube", lambda _q, limit=5: [
        {"title": f"Song {n}", "url": f"https://www.youtube.com/watch?v=Video00000{n}", "duration": 60}
        for n in range(1, 5)])
    monkeypatch.setattr(jobs, "download_mp3", fake_download)
    monkeypatch.setattr(job_upload, "_enqueue_upload", fake_enqueue)
    job = job_store.create_job(PLAYLIST)
    jobs.run_job(job.id)
    return job.id, state


def _wait_done(ids):
    deadline = time.time() + 5
    while time.time() < deadline and any(job_store.get_job(i).status not in ("done", "failed") for i in ids):
        time.sleep(0.02)


def test_a_listed_playlist_is_marked_as_one(playlist_job):
    job_id, _state = playlist_job
    assert job_store.get_job(job_id).playlist is True
    assert len(job_store.get_job(job_id).candidates) == 4


def test_adding_several_videos_attaches_them_in_list_order_at_the_end(playlist_job):
    job_id, state = playlist_job
    state["slow"] = {"Song 1"}
    ids = jobs.select_candidates_and_continue(job_id, [0, 1, 2, 3], "tonie", {"edge": "end"})
    _wait_done(ids)
    assert [stem for stem, _ in state["enqueued"]] == ["Song 1", "Song 2", "Song 3", "Song 4"]
    assert job_store.get_job(job_id).status == "awaiting_selection", "the list stays available"


def test_adding_several_to_the_top_keeps_the_first_video_on_top(playlist_job):
    job_id, state = playlist_job
    ids = jobs.select_candidates_and_continue(job_id, [0, 1, 2], "tonie", {"edge": "start"})
    _wait_done(ids)
    # Each lands at the top, so the last one is attached first.
    assert [stem for stem, _ in state["enqueued"]] == ["Song 3", "Song 2", "Song 1"]


def test_one_failed_download_does_not_stall_the_rest(playlist_job):
    job_id, state = playlist_job
    state["fail"] = {"Song 2"}
    ids = jobs.select_candidates_and_continue(job_id, [0, 1, 2], "tonie", {"edge": "end"})
    _wait_done(ids)
    assert [stem for stem, _ in state["enqueued"]] == ["Song 1", "Song 3"]
    assert [job_store.get_job(i).status for i in ids] == ["done", "failed", "done"]


def test_adding_to_the_top_downloads_in_hand_over_order(playlist_job, monkeypatch):
    job_id, state = playlist_job
    # One worker makes start order observable: with three, which thread reaches
    # download_mp3 first is a scheduling race, even though the queue itself is FIFO.
    single = ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(jobs, "_downloads", single)
    started = []
    original = jobs.download_mp3
    monkeypatch_download = lambda url, out_dir, preferred_title=None: (  # noqa: E731
        started.append(preferred_title) or original(url, out_dir, preferred_title))
    jobs.download_mp3 = monkeypatch_download
    try:
        _wait_done(jobs.select_candidates_and_continue(job_id, [0, 1, 2, 3], "tonie", {"edge": "start"}))
    finally:
        jobs.download_mp3 = original
    single.shutdown(wait=True)
    assert started == ["Song 4", "Song 3", "Song 2", "Song 1"], "the first to hand over downloads first"


def test_a_job_that_failed_while_queued_never_downloads(playlist_job, monkeypatch):
    job_id, state = playlist_job
    gate = threading.Event()
    blocked = []

    def slow(url, out_dir, preferred_title=None):
        blocked.append(preferred_title)
        gate.wait(5)
        path = out_dir / f"{preferred_title}.mp3"
        path.write_bytes(b"audio")
        return path

    monkeypatch.setattr(jobs, "download_mp3", slow)
    ids = jobs.select_candidates_and_continue(job_id, [0, 1, 2, 3], "tonie", {"edge": "end"})
    deadline = time.time() + 2
    while len(blocked) < 3 and time.time() < deadline:
        time.sleep(0.01)
    job_store.expire_job(ids[3], "Timed out in state 'queued_download'")
    gate.set()
    _wait_done(ids)
    assert "Song 4" not in blocked
    assert job_store.get_job(ids[3]).status == "failed"
    assert [stem for stem, _ in state["enqueued"]] == ["Song 1", "Song 2", "Song 3"]


def test_a_track_whose_turn_never_comes_fails_instead_of_landing_out_of_order(playlist_job, monkeypatch):
    job_id, state = playlist_job
    monkeypatch.setattr(attach_order, "TURN_TIMEOUT_SEC", 0.2)
    release = threading.Event()

    def download(url, out_dir, preferred_title=None):
        if preferred_title == "Song 1":
            release.wait(5)
        path = out_dir / f"{preferred_title}.mp3"
        path.write_bytes(b"audio")
        return path

    monkeypatch.setattr(jobs, "download_mp3", download)
    ids = jobs.select_candidates_and_continue(job_id, [0, 1], "tonie", {"edge": "end"})
    deadline = time.time() + 3
    while job_store.get_job(ids[1]).status != "failed" and time.time() < deadline:
        time.sleep(0.02)
    release.set()
    _wait_done(ids)
    assert job_store.get_job(ids[1]).status == "failed"
    assert "add it again" in job_store.get_job(ids[1]).error
    assert [stem for stem, _ in state["enqueued"]] == ["Song 1"]


def test_downloads_run_at_most_three_at_a_time(playlist_job):
    job_id, state = playlist_job
    state["slow"] = {"Song 1", "Song 2", "Song 3", "Song 4"}
    _wait_done(jobs.select_candidates_and_continue(job_id, [0, 1, 2, 3], "tonie", {"edge": "end"}))
    assert state["peak"] == jobs.MAX_PARALLEL_DOWNLOADS == 3


@pytest.mark.parametrize("indexes", [[], [0, 0], [-1], [4], list(range(51))])
def test_invalid_index_lists_are_refused(playlist_job, indexes):
    job_id, state = playlist_job
    with pytest.raises(ValueError):
        jobs.select_candidates_and_continue(job_id, indexes, "tonie", None)
    assert state["enqueued"] == []


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main.auth, "session_valid", lambda _session: True)
    return TestClient(main.app)


def test_select_many_endpoint_returns_the_new_jobs(client, monkeypatch):
    seen = {}

    def select(job_id, indexes, target_url=None, position=None):
        seen.update(job_id=job_id, indexes=indexes, target_url=target_url, position=position)
        return ["a", "b"]

    monkeypatch.setattr(job_routes, "select_candidates_and_continue", select)
    response = client.post("/api/jobs/j1/select-many",
                           json={"indexes": [2, 0], "target_url": "t", "position": {"edge": "end"}})
    assert response.status_code == 200 and response.json() == {"job_ids": ["a", "b"]}
    assert seen == {"job_id": "j1", "indexes": [2, 0], "target_url": "t", "position": {"edge": "end"}}


def test_select_many_endpoint_refuses_bad_requests(client, monkeypatch):
    def select(*_a, **_kw):
        raise ValueError("Choose between 1 and 50 different videos")

    monkeypatch.setattr(job_routes, "select_candidates_and_continue", select)
    assert client.post("/api/jobs/j1/select-many", json={"indexes": [0, 0]}).status_code == 422
    assert client.post("/api/jobs/j1/select-many", json={"indexes": "x"}).status_code == 422


@pytest.mark.parametrize("indexes", [[True], ["1"], [1.0], [], list(range(51))])
def test_select_many_refuses_non_integer_or_oversized_lists_before_any_work(client, monkeypatch, indexes):
    monkeypatch.setattr(job_routes, "select_candidates_and_continue", lambda *_a, **_kw: pytest.fail("must not run"))
    assert client.post("/api/jobs/j1/select-many", json={"indexes": indexes}).status_code == 422
