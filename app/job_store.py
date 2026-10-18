# Owns job state, persistence, events, cancellation, expiry and the watchdog.
from __future__ import annotations
import os
import tempfile
import threading
import queue
import uuid
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Any

from . import events, uploader
from .models import JobState
from .config import settings
from .logger import get_logger

logger = get_logger(f"{__package__}.jobs")

# Undone/canceled jobs are final: no later queue callback may revive them.
REVERSED_STATUSES = frozenset({"canceled", "undone"})
TERMINAL_STATUSES = frozenset({"done", "failed"}) | REVERSED_STATUSES

_jobs: Dict[str, JobState] = {}
_q: queue.Queue[str] = queue.Queue()
_lock = threading.Lock()
_persist_lock = threading.Lock()
_STAGE_TIMEOUT_SEC: dict[str, int] = {
    "parsing": 90,
    "searching": 180,
    # "queued_download" waits in the FIFO download pool and has no limit: every download
    # ahead of it is itself bounded. Download+retry+levelling of long audio can take a while.
    "downloading": 3600,
    "queued_prepare": 120,
    "preparing": 900,
    # Uploads wait in the action queue behind others; each step there refreshes the job.
    "waiting_upload": 1800,
    "uploading": 420,
}

_JOBS_STATE_FILE = settings.data_dir / "jobs-state.json"
_MAX_PERSISTED_JOBS = 200

now_iso = events.now_iso


def _emit_event(kind: str, job_id: Optional[str] = None):
    payload: dict[str, Any] = {}
    if job_id:
        with _lock:
            st = _jobs.get(job_id)
            payload["job"] = st.model_dump() if st else None
            payload["job_id"] = job_id
    events.publish(kind, payload)


get_events_since = events.events_since
get_event_stats = events.stats


def get_queue_stats() -> dict[str, int]:
    with _lock:
        in_flight = sum(1 for j in _jobs.values() if str(j.status) in {
            "queued_download", "downloading", "queued_prepare", "preparing", "waiting_upload", "uploading"
        })
    return {
        "search_queue_depth": _q.qsize(),
        "upload_queue_depth": sum(1 for a in uploader.action_queue.recent() if a["type"] == "upload"
                                  and a["status"] in ("queued", "transferring", "ready", "applying")),
        "in_flight_jobs": in_flight,
    }


def _persist_jobs_state() -> None:
    """Atomically write the newest job snapshot.

    Workers persist concurrently. One writer at a time (snapshot taken inside the same
    lock) keeps an older snapshot from replacing a newer one, and a unique temp file per
    write keeps two writers from renaming the same temp path.
    """
    tmp: Path | None = None
    try:
        with _persist_lock:
            with _lock:
                ordered = sorted(_jobs.values(), key=lambda j: j.created_at, reverse=True)[:_MAX_PERSISTED_JOBS]
                payload = [j.model_dump() for j in ordered]
            _JOBS_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix=_JOBS_STATE_FILE.name + '.', suffix='.tmp', dir=_JOBS_STATE_FILE.parent)
            tmp = Path(name)
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.chmod(tmp, 0o644)
            tmp.replace(_JOBS_STATE_FILE)
            tmp = None
    except Exception as e:
        logger.warning('persist_jobs_state failed: %s', e)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def _load_jobs_state() -> None:
    if not _JOBS_STATE_FILE.exists():
        return
    try:
        raw = json.loads(_JOBS_STATE_FILE.read_text(encoding='utf-8'))
        restored: dict[str, JobState] = {}
        for item in raw if isinstance(raw, list) else []:
            st = JobState(**item)
            if str(st.status) in {"queued", "parsing", "searching", "awaiting_selection", "queued_download", "downloading", "queued_prepare", "preparing", "waiting_upload", "uploading"}:
                st.status = 'failed'
                prev = item.get('status')
                st.error = f"Recovered after restart from in-progress state '{prev}'"
                st.logs = [*st.logs, f"Recovered at startup: marked failed from in-progress state '{prev}'"]
                st.updated_at = now_iso()
            restored[st.id] = st
        with _lock:
            _jobs.clear()
            _jobs.update(restored)
    except Exception as e:
        logger.warning('load_jobs_state failed: %s', e)


def create_job(message: str, search_limit: int = 5) -> JobState:
    job_id = str(uuid.uuid4())
    lim = int(search_limit or 5)
    if lim not in (5, 10, 15, 20):
        lim = 5
    st = JobState(
        id=job_id,
        status="queued",
        created_at=now_iso(),
        updated_at=now_iso(),
        user_message=message,
        search_limit=lim,
        logs=["Job created"],
    )
    with _lock:
        _jobs[job_id] = st
    _persist_jobs_state()
    _emit_event("job_created", job_id)
    _q.put(job_id)
    return st


def get_job(job_id: str) -> JobState:
    with _lock:
        return _jobs.get(job_id)


def list_jobs() -> list[JobState]:
    with _lock:
        return sorted(_jobs.values(), key=lambda j: j.created_at, reverse=True)


def _update(job_id: str, **kwargs):
    with _lock:
        st = _jobs[job_id]
        for k, v in kwargs.items():
            setattr(st, k, v)
        st.updated_at = now_iso()
    _persist_jobs_state()
    _emit_event("job_updated", job_id)


def _log(job_id: str, msg: str):
    with _lock:
        st = _jobs[job_id]
        st.logs = [*st.logs, msg]
        st.updated_at = now_iso()
        status = st.status
    _persist_jobs_state()
    logger.info("job=%s status=%s %s", job_id, status, msg)
    _emit_event("job_log", job_id)


def _cancel_requested(job_id: str) -> bool:
    with _lock:
        st = _jobs.get(job_id)
        return bool(st and st.cancel_requested)


def cancel_job(job_id: str) -> JobState:
    with _lock:
        st = _jobs.get(job_id)
        if not st:
            raise RuntimeError("Job not found")
        if st.status in TERMINAL_STATUSES:
            return st
        st.cancel_requested = True
        # Immediate terminal cancel for pre-selection states.
        if st.status in {"queued", "parsing", "searching", "awaiting_selection", "queued_download", "queued_prepare", "waiting_upload"}:
            st.status = "failed"
            st.error = "Canceled by user"
        st.updated_at = now_iso()
        action_id = st.tonies_action_id
    if action_id:
        uploader.action_queue.cancel(action_id)  # only succeeds before the audio starts sending
    _emit_event("job_updated", job_id)
    _log(job_id, "Canceled by user")
    return get_job(job_id)


def _parse_iso(ts: str) -> datetime:
    s = str(ts or "").strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def expire_job(job_id: str, error: str, *, only_if_status: Optional[str] = None) -> bool:
    """Fail a job and stop its worker from carrying on (it checks the cancel flag)."""
    with _lock:
        cur = _jobs.get(job_id)
        if not cur or cur.status in TERMINAL_STATUSES or (only_if_status and cur.status != only_if_status):
            return False
        cur.status = "failed"
        cur.error = error
        cur.cancel_requested = True
        cur.updated_at = now_iso()
    _persist_jobs_state()
    _emit_event("job_updated", job_id)
    return True


def watchdog_loop():
    while True:
        time_now = datetime.now(timezone.utc)
        timed_out: list[tuple[str, str, int]] = []
        with _lock:
            for jid, st in list(_jobs.items()):
                status = str(st.status or "")
                limit = _STAGE_TIMEOUT_SEC.get(status)
                if not limit:
                    continue
                try:
                    updated = _parse_iso(st.updated_at)
                except Exception:
                    continue
                age = int((time_now - updated).total_seconds())
                if age > limit:
                    timed_out.append((jid, status, age))

        for jid, status, age in timed_out:
            if expire_job(jid, f"Timed out in state '{status}' after {age}s", only_if_status=status):
                _log(jid, f"Failed (watchdog timeout): state={status} age={age}s")

        threading.Event().wait(5.0)
