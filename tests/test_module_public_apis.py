"""Offline contracts for the job store and cross-module job/Tonies APIs."""
import ast
import importlib.util
import queue
from pathlib import Path
import threading

import pytest

from app import downloader, events, job_pipeline, job_store, job_upload, tonies_content, tonies_session
from app.models import JobState

ROOT = Path(__file__).parents[1]


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(job_store, "_jobs", {})
    monkeypatch.setattr(job_store, "_q", queue.Queue())
    monkeypatch.setattr(job_store, "_JOBS_STATE_FILE", tmp_path / "jobs.json")
    return job_store


def state(identity, created="2026-01-01T00:00:00Z"):
    return JobState(id=identity, status="done", created_at=created, updated_at=created,
                    user_message=identity)


def test_add_job_only_registers_state_leaving_publication_to_the_caller(store, monkeypatch):
    packets = []
    monkeypatch.setattr(events, "publish", lambda *args: packets.append(args))
    job = state("one")
    store.add_job(job)
    assert store.get_job("one") is job
    assert not store._JOBS_STATE_FILE.exists()
    assert packets == []
    store.persist_state()
    store.emit_job_event("job_created", "one")
    assert store._JOBS_STATE_FILE.exists()
    assert packets == [("job_created", {"job": job.model_dump(), "job_id": "one"})]


def test_public_store_operations_can_share_one_atomic_selection_lock(store):
    finished = threading.Event()

    def selection():
        with store.state_lock:
            store.add_job(state("older"))
            store.add_job(state("newer", "2026-01-02T00:00:00Z"))
            assert store.get_job("older").id == "older"
            assert [job.id for job in store.job_states()] == ["older", "newer"]
            assert [job.id for job in store.list_jobs()] == ["newer", "older"]
        finished.set()

    worker = threading.Thread(target=selection, daemon=True)
    worker.start()
    assert finished.wait(2), "Public store APIs must not deadlock inside the selection lock"
    worker.join(2)


def test_search_queue_public_api_preserves_fifo_and_completion(store):
    first = store.create_job("first")
    second = store.create_job("second")
    assert store.next_search_job() == first.id
    store.finish_search_job()
    assert store.next_search_job() == second.id
    store.finish_search_job()
    assert store.get_queue_stats()["search_queue_depth"] == 0
    assert store._q.unfinished_tasks == 0


def test_public_store_mutations_keep_events_logs_cancel_and_restore(store, monkeypatch):
    packets = []
    monkeypatch.setattr(events, "publish", lambda kind, payload: packets.append((kind, payload)))
    job = state("one")
    store.add_job(job)
    store.update_job(job.id, status="searching")
    store.log_job(job.id, "Searching")
    assert not store.cancel_requested(job.id)
    store.cancel_job(job.id)
    assert store.cancel_requested(job.id)
    assert job.status == "failed"
    assert job.logs == ["Searching", "Canceled by user"]
    assert [kind for kind, _ in packets] == ["job_updated", "job_log", "job_updated", "job_log"]
    store.load_state()
    assert store.get_job(job.id).model_dump() == job.model_dump()


@pytest.mark.parametrize("message, expected", [
    ("Video unavailable", "Video unavailable"),
    ("Private video", "Private video"),
    ("Members-only", "Members-only video"),
    ("Sign in to confirm age", "Age/login restricted"),
    ("Timed out", "Network timeout"),
    ("nsig failure", "YouTube extraction/signature issue"),
    # Keep existing precedence: the broad 'age' match also matches 'images'.
    ("only images", "Age/login restricted"),
    ("unknown", "Download failed"),
])
def test_download_error_classification_is_owned_by_downloader(message, expected):
    assert downloader.classify_download_error(RuntimeError(message)) == expected


def test_job_modules_use_only_public_cross_module_names():
    for module in (job_pipeline, job_upload):
        tree = ast.parse(Path(module.__file__).read_text())
        private = [node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
                   and isinstance(node.value, ast.Name) and node.value.id in {"store", "uploads"}
                   and node.attr.startswith("_")]
        assert private == []
        assert ast.get_docstring(tree)


def test_tonies_modules_import_only_public_helpers():
    for name in ("tonies_api", "tonies_content"):
        tree = ast.parse((ROOT / "app" / f"{name}.py").read_text())
        private = [alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                   and node.module in {"tonies_content", "tonies_session"}
                   for alias in node.names if alias.name.startswith("_")]
        assert private == []
    assert tonies_session.checked_number(2, "duration") == 2
    assert tonies_content.writable_chapters([]) == []


def test_live_acceptance_script_imports_public_helpers_without_running_live_work():
    path = ROOT / "scripts" / "live_queue_acceptance.py"
    spec = importlib.util.spec_from_file_location("offline_live_queue_acceptance", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.writable_chapters is tonies_content.writable_chapters
    assert module.validated_chapters is tonies_content.validated_chapters
    assert module.writable_chapters([]) == []
