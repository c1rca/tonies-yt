"""Slow editor downloads must not hold a request beyond the proxy deadline."""
import asyncio
import gc
import threading
from concurrent.futures import Future

from fastapi.testclient import TestClient
import pytest

from app import job_pipeline, job_store, main
from app.events import now_iso
from app.models import JobState
from app.routes import jobs


@pytest.fixture
def pending_fetch(monkeypatch, tmp_path):
    future = Future()
    path = tmp_path / "Ready.mp3"
    monkeypatch.setattr(jobs, "fetch_candidate", lambda *_, **_kwargs: future)
    monkeypatch.setattr(jobs, "_EDITOR_FETCH_WAIT_SEC", 0.01, raising=False)
    return future, path


def test_pending_download_answers_before_completion_and_survives_poll_timeout(pending_fetch):
    future, path = pending_fetch
    # Bound the old blocking implementation as well: RED must be an assertion, not a hang.
    finish = threading.Timer(0.3, lambda: future.set_result(path) if not future.done() else None)
    finish.start()
    try:
        client = TestClient(main.app)
        first = client.post("/api/jobs/search/fetch", json={"index": 0})
        assert first.status_code == 202
        assert first.json() == {"status": "preparing"}
        assert first.headers["retry-after"] == "1"
        assert not future.cancelled() and not future.done()
        again = client.post("/api/jobs/search/fetch", json={"index": 0, "poll": True})
        assert again.status_code == 202 and not future.cancelled()
        finish.join()
        ready = client.post("/api/jobs/search/fetch", json={"index": 0, "poll": True})
        assert ready.status_code == 200 and ready.json() == {"filename": "Ready.mp3"}
    finally:
        finish.cancel()
        finish.join()


@pytest.mark.parametrize("error,status,detail", [
    (ValueError("That video is no longer in the results; search again"), 422,
     "That video is no longer in the results; search again"),
    (RuntimeError("private provider diagnostics"), 502, "Could not download this video; try again"),
])
def test_completed_download_errors_keep_their_safe_contract(pending_fetch, error, status, detail):
    future, _ = pending_fetch
    future.set_exception(error)
    response = TestClient(main.app).post("/api/jobs/search/fetch", json={"index": 0})
    assert response.status_code == status and response.json() == {"detail": detail}


def test_failure_after_a_pending_response_does_not_log_unretrieved_provider_errors(pending_fetch):
    future, _ = pending_fetch

    async def scenario():
        unhandled = []
        asyncio.get_running_loop().set_exception_handler(lambda _loop, context: unhandled.append(context))
        pending = await jobs.fetch_candidate_to_library("search", jobs.FetchRequest(index=0))
        assert pending.status_code == 202
        future.set_exception(RuntimeError("private provider diagnostics"))
        await asyncio.sleep(0.02)
        gc.collect()
        assert unhandled == [], "a closed polling request must still consume late future exceptions"

    asyncio.run(scenario())


def test_disconnected_poll_never_cancels_the_shared_download(pending_fetch):
    future, path = pending_fetch

    async def scenario():
        poll = asyncio.create_task(jobs.fetch_candidate_to_library("search", jobs.FetchRequest(index=0)))
        await asyncio.sleep(0)
        poll.cancel()
        with pytest.raises(asyncio.CancelledError):
            await poll
        assert not future.cancelled()
        future.set_result(path)
        await asyncio.sleep(0)
        assert await jobs.fetch_candidate_to_library("search", jobs.FetchRequest(index=0)) == {"filename": "Ready.mp3"}

    asyncio.run(scenario())


@pytest.fixture
def real_pipeline(monkeypatch):
    class Executor:
        def __init__(self):
            self.submissions = []

        def submit(self, *_args):
            future = Future()
            self.submissions.append(future)
            return future

    executor = Executor()
    monkeypatch.setattr(job_pipeline, "_fetches", {})
    monkeypatch.setattr(job_pipeline, "_editor_downloads", executor)
    monkeypatch.setattr(job_store, "_jobs", {})
    monkeypatch.setattr(jobs, "_EDITOR_FETCH_WAIT_SEC", 0.001)
    job_store.add_job(JobState(id="search", status="awaiting_selection", created_at=now_iso(),
                             updated_at=now_iso(), user_message="synthetic", candidates=[{
                                 "url": "https://www.youtube.com/watch?v=AbCdEf_12-3", "title": "Synthetic"}]))
    return executor


def test_real_pipeline_poll_reports_failure_without_starting_another_download(real_pipeline):
    async def scenario():
        first = await jobs.fetch_candidate_to_library("search", jobs.FetchRequest(index=0))
        assert first.status_code == 202 and len(real_pipeline.submissions) == 1
        real_pipeline.submissions[0].set_exception(RuntimeError("private provider diagnostics"))
        with pytest.raises(Exception) as refused:
            await jobs.fetch_candidate_to_library("search", jobs.FetchRequest(index=0, poll=True))
        assert refused.value.status_code == 502
        assert refused.value.detail == "Could not download this video; try again"
        assert len(real_pipeline.submissions) == 1
        retry = await jobs.fetch_candidate_to_library("search", jobs.FetchRequest(index=0))
        assert retry.status_code == 202 and len(real_pipeline.submissions) == 2
        real_pipeline.submissions[1].set_result("Ready.mp3")
        await asyncio.sleep(0)

    asyncio.run(scenario())


def test_poll_without_a_preparation_never_starts_a_download(real_pipeline):
    response = TestClient(main.app).post("/api/jobs/search/fetch", json={"index": 0, "poll": True})
    assert response.status_code == 422
    assert response.json() == {"detail": "Audio preparation expired; close and reopen the editor"}
    assert real_pipeline.submissions == []


def test_replaced_failed_download_cannot_leak_unretrieved_provider_diagnostics(real_pipeline):
    async def scenario():
        unhandled = []
        asyncio.get_running_loop().set_exception_handler(lambda _loop, context: unhandled.append(context))
        await jobs.fetch_candidate_to_library("search", jobs.FetchRequest(index=0))
        old = real_pipeline.submissions.pop()
        old.set_exception(RuntimeError("private provider diagnostics"))
        await asyncio.sleep(0.01)
        await jobs.fetch_candidate_to_library("search", jobs.FetchRequest(index=0))
        del old
        await asyncio.sleep(0.01)
        gc.collect()
        assert unhandled == []
        real_pipeline.submissions[0].set_result("Ready.mp3")
        await asyncio.sleep(0)

    asyncio.run(scenario())
