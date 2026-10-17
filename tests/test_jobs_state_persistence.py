import json
import threading

from app import jobs
from app.models import JobState


def _job(i: int) -> JobState:
    return JobState(id=f"job-{i}", status="done", created_at=f"2026-01-01T00:00:{i:02d}Z",
                    updated_at="2026-01-01T00:00:00Z", user_message=f"m{i}", logs=[])


def test_concurrent_persists_never_fail_and_leave_the_newest_snapshot(tmp_path, monkeypatch, caplog):
    state = tmp_path / "jobs-state.json"
    monkeypatch.setattr(jobs, "_JOBS_STATE_FILE", state)
    monkeypatch.setattr(jobs, "_jobs", {})
    start = threading.Barrier(16)

    def writer(i: int) -> None:
        start.wait()
        with jobs._lock:
            jobs._jobs[f"job-{i}"] = _job(i)
        jobs._persist_jobs_state()

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert "persist_jobs_state failed" not in caplog.text
    assert sorted(j["id"] for j in json.loads(state.read_text())) == sorted(f"job-{i}" for i in range(16))
    assert [p.name for p in tmp_path.iterdir()] == ["jobs-state.json"]


def test_failed_write_leaves_previous_state_and_no_temp_file(tmp_path, monkeypatch):
    state = tmp_path / "jobs-state.json"
    state.write_text("[]")
    monkeypatch.setattr(jobs, "_JOBS_STATE_FILE", state)
    monkeypatch.setattr(jobs, "_jobs", {"x": _job(1)})
    monkeypatch.setattr(jobs.json, "dump", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))

    jobs._persist_jobs_state()

    assert state.read_text() == "[]"
    assert [p.name for p in tmp_path.iterdir()] == ["jobs-state.json"]
