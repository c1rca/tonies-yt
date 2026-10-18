"""Upload jobs run through the Tonies action queue."""
import threading
import time

import pytest

from app import job_store as jobs, job_upload, uploader
from app.actions import ActionQueue


class FakeTonies:
    """Stands in for ToniesAPI: records transfers and batches, returns a growing chapter list."""

    closed = False

    def __init__(self):
        self.chapters, self.transfers, self.batches = [], [], []
        self.fail_apply = None
        self.drop_chapter = False
        self.gate = threading.Event()
        self.gate.set()

    def prepare_upload(self, path, target_url):
        assert self.gate.wait(5)
        self.transfers.append(path.name)
        return {"file_id": f"file-{path.stem}", "seconds": 1.0, "live": False}

    def apply(self, target_url, ops):
        self.batches.append(ops)
        if self.fail_apply:
            raise RuntimeError(self.fail_apply)
        results = []
        for op in ops:
            chapter_id = f"chapter-{op['file_id']}"
            if not self.drop_chapter:
                self.chapters.append({"chapter_id": chapter_id, "title": op["title"]})
            results.append({"error": None, "chapter_id": chapter_id})
        return {"content": {"chapters": list(self.chapters), "processing": True}, "results": results}


@pytest.fixture
def cloud(monkeypatch):
    fake = FakeTonies()
    monkeypatch.setattr(uploader, "action_queue", ActionQueue(lambda: fake, batch_window=0.05))
    monkeypatch.setattr(uploader, "resolve_upload_target", lambda target, url: url or "synthetic-target")
    monkeypatch.setattr(uploader, "upload_title", lambda path: f"{path.stem} [oc:abc123]")
    return fake


def upload_job(tmp_path, name="track", position=None):
    path = tmp_path / f"{name}.mp3"
    path.write_bytes(b"synthetic-audio")
    state = jobs.create_job(f"synthetic {name}")
    job_upload._enqueue_upload(state.id, path, target=None, target_url="synthetic-target", position=position)
    return state.id


def wait_for(job_id, statuses=("done", "failed"), timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = jobs.get_job(job_id)
        if job.status in statuses:
            return job
        time.sleep(0.01)
    raise AssertionError(f"job stayed {jobs.get_job(job_id).status}")


def test_upload_job_finishes_with_the_confirmed_chapter_and_position(cloud, tmp_path):
    job = wait_for(upload_job(tmp_path, position={"edge": "start"}))
    assert job.status == "done"
    assert job.uploaded_chapter_id == "chapter-file-track"
    assert job.tonies_content["chapters"] == [{"chapter_id": "chapter-file-track", "title": "track [oc:abc123]"}]
    assert job.upload_positioned is True
    assert cloud.batches[0][0]["position"] == {"edge": "start"}
    assert any("Sending audio to Tonies" in line for line in job.logs)


def test_an_unconfirmed_write_fails_the_job_and_is_not_retried(cloud, tmp_path):
    cloud.fail_apply = "Saved Tonies content was not confirmed; refresh before another action"
    job = wait_for(upload_job(tmp_path))
    assert job.status == "failed" and "not confirmed" in job.error
    assert len(cloud.transfers) == 1 and len(cloud.batches) == 1


def test_a_receipt_without_the_uploaded_chapter_cannot_mark_the_job_done(cloud, tmp_path):
    cloud.drop_chapter = True
    job = wait_for(upload_job(tmp_path))
    assert job.status == "failed" and "confirmed" in job.error


def test_cancelling_before_the_audio_is_sent_never_reaches_tonies(cloud, tmp_path):
    cloud.gate.clear()  # hold the transfer slots
    blockers = [upload_job(tmp_path, f"blocker{i}") for i in range(3)]
    job_id = upload_job(tmp_path, "queued")
    jobs.cancel_job(job_id)
    cloud.gate.set()
    for blocker in blockers:
        wait_for(blocker)
    assert jobs.get_job(job_id).status == "canceled"
    assert jobs.get_job(job_id).error is None
    assert "queued.mp3" not in cloud.transfers
    assert all(op["file_id"] != "file-queued" for batch in cloud.batches for op in batch)


def test_many_uploads_transfer_in_parallel_and_attach_in_queued_order(cloud, tmp_path):
    cloud.gate.clear()
    ids = [upload_job(tmp_path, f"song{i}") for i in range(6)]
    cloud.gate.set()
    for job_id in ids:
        assert wait_for(job_id).status == "done"
    attached = [op["file_id"] for batch in cloud.batches for op in batch]
    assert attached == [f"file-song{i}" for i in range(6)]
    assert len(cloud.batches) < 6  # several uploads shared a write


def test_upload_queue_does_not_hold_credentials(cloud, tmp_path):
    job = wait_for(upload_job(tmp_path))
    action = uploader.action_queue.get(job.tonies_action_id)
    assert not {"tonies_email", "tonies_password", "email", "password"} & set(action.params)
