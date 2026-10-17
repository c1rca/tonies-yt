from __future__ import annotations
import os
import tempfile
import threading
import queue
import uuid
import subprocess
import time
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Any

from .models import JobState
from .ai import parse_request
from .downloader import download_mp3, search_youtube
from . import events
from .uploader import action_queue, resolve_upload_target, upload_title
from .config import settings
from .logger import get_logger

logger = get_logger(__name__)

_jobs: Dict[str, JobState] = {}
_q: queue.Queue[str] = queue.Queue()
_lock = threading.Lock()
_persist_lock = threading.Lock()
_STAGE_TIMEOUT_SEC: dict[str, int] = {
    "parsing": 90,
    "searching": 180,
    "queued_download": 120,
    "downloading": 900,
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
        "upload_queue_depth": sum(1 for a in action_queue.recent() if a["type"] == "upload"
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


def _is_cancelled(job_id: str) -> bool:
    with _lock:
        st = _jobs.get(job_id)
        return bool(st and st.cancel_requested)


def cancel_job(job_id: str) -> JobState:
    with _lock:
        st = _jobs.get(job_id)
        if not st:
            raise RuntimeError("Job not found")
        if st.status in {"done", "failed"}:
            return st
        st.cancel_requested = True
        # Immediate terminal cancel for pre-selection states.
        if st.status in {"queued", "parsing", "searching", "awaiting_selection", "queued_download", "queued_prepare", "waiting_upload"}:
            st.status = "failed"
            st.error = "Cancelled by user"
        st.updated_at = now_iso()
        action_id = st.tonies_action_id
    if action_id:
        action_queue.cancel(action_id)  # only succeeds before the audio starts sending
    _emit_event("job_updated", job_id)
    _log(job_id, "Cancelled by user")
    return get_job(job_id)


def run_job(job_id: str):
    try:
        if _is_cancelled(job_id):
            _update(job_id, status="failed", error="Cancelled by user")
            return
        _update(job_id, status="parsing")
        _log(job_id, "Parsing request (exact-text mode)")
        parsed = parse_request(_jobs[job_id].user_message)
        if _is_cancelled(job_id):
            _update(job_id, status="failed", error="Cancelled by user")
            return
        _update(job_id, parsed=parsed.model_dump(), status="searching")

        _log(job_id, f"Searching YouTube: {parsed.youtube_query}")
        with _lock:
            lim = int((_jobs.get(job_id).search_limit if _jobs.get(job_id) else 5) or 5)
        if lim not in (5, 10, 15, 20):
            lim = 5
        candidates = search_youtube(parsed.youtube_query, limit=lim)
        if not candidates:
            raise RuntimeError("No YouTube results found")

        _update(job_id, candidates=candidates, status="awaiting_selection")
        _log(job_id, f"Found {len(candidates)} candidates; waiting for selection")
    except Exception as e:
        _update(job_id, status="failed", error=str(e))
        _log(job_id, f"Failed: {e}")


def worker_loop():
    while True:
        job_id = _q.get()
        try:
            run_job(job_id)
        finally:
            _q.task_done()


def _parse_iso(ts: str) -> datetime:
    s = str(ts or "").strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


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
            with _lock:
                cur = _jobs.get(jid)
                if not cur:
                    continue
                if cur.status != status:
                    continue
                cur.status = "failed"
                cur.error = f"Timed out in state '{status}' after {age}s"
                cur.updated_at = now_iso()
            _emit_event("job_updated", jid)
            _log(jid, f"Failed (watchdog timeout): state={status} age={age}s")

        threading.Event().wait(5.0)


def _probe_audio_duration_seconds(path: Path) -> float:
    cmd = [
        'ffprobe', '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'json',
        str(path)
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {proc.stderr.strip()}")
    data = json.loads(proc.stdout or '{}')
    dur = data.get('format', {}).get('duration')
    return float(dur) if dur is not None else 0.0


def _classify_download_error(err: Exception) -> str:
    msg = str(err).lower()
    if "not available" in msg or "video unavailable" in msg:
        return "Video unavailable"
    if "private" in msg:
        return "Private video"
    if "members-only" in msg or "membership" in msg:
        return "Members-only video"
    if "sign in" in msg or "age" in msg:
        return "Age/login restricted"
    if "timed out" in msg or "timeout" in msg:
        return "Network timeout"
    if "signature" in msg or "nsig" in msg or "precondition" in msg:
        return "YouTube extraction/signature issue"
    if "requested format is not available" in msg or "only images" in msg:
        return "No playable audio format"
    return "Download failed"


def _enqueue_upload(job_id: str, mp3_path: Path, target: Optional[str], target_url: Optional[str],
                    position: Optional[dict] = None):
    _update(job_id, status="waiting_upload", output_file=str(mp3_path), target_url=target_url)
    _log(job_id, "Queued for Tonies")
    url = resolve_upload_target(target, target_url)
    action = action_queue.submit_upload(url, Path(mp3_path), upload_title(mp3_path), position,
                                        on_change=lambda a: _on_upload_action(job_id, a))
    _update(job_id, tonies_action_id=action.id, target_url=url)


def _on_upload_action(job_id: str, action) -> None:
    """Mirror the queued upload's progress onto its job."""
    job = get_job(job_id)
    if job is None:
        return
    if action.status == "transferring":
        if job.status == "waiting_upload":
            _update(job_id, status="uploading")
        _log(job_id, "Sending audio to Tonies")
    elif action.status == "ready":
        _log(job_id, "Audio sent; adding it to the Tonie")
    elif action.status == "failed":
        if job.status not in {"done", "failed"}:
            _update(job_id, status="failed", error=action.error)
        _log(job_id, f"Upload failed: {action.error}")
    elif action.status == "done":
        content = action.content or {}
        if not action.chapter_id or sum(row.get("chapter_id") == action.chapter_id
                                        for row in content.get("chapters", [])) != 1:
            _update(job_id, status="failed", error="Tonies upload returned no confirmed chapter; refresh the library before retrying")
            return
        receipt = {"uploaded_chapter_id": action.chapter_id, "tonies_content": content,
                   "target_url": action.target_url, "upload_positioned": action.params.get("position") is not None}
        if job.status == "failed":
            _update(job_id, **receipt)
            _log(job_id, "Tonies accepted the upload after the job stopped; the recorded chapter is present. Do not upload it again.")
            return
        _update(job_id, status="done", error=None, **receipt)
        _log(job_id, "Audio added to Tonies; processing continues in the library" if content.get("processing") else "Completed")


def select_candidate_and_continue(job_id: str, index: int, target_url: Optional[str] = None,
                                  position: Optional[dict] = None) -> str:
    with _lock:
        st = _jobs.get(job_id)
        if not st:
            raise RuntimeError("Job not found")
        if index < 0 or index >= len(st.candidates):
            raise RuntimeError("Invalid candidate index")

        selected = st.candidates[index]

        # Idempotent re-select: if same candidate is already in-flight/done, no-op.
        in_flight_states = {"queued_download", "downloading", "queued_prepare", "preparing", "waiting_upload", "uploading", "done"}
        already_same = bool(st.selected_candidate and st.selected_candidate.get("url") == selected.get("url"))
        if st.status in in_flight_states and already_same:
            return job_id

        # If the original search job already moved past selection, spawn a sibling upload job
        # so multiple candidates from one search result set can be queued independently.
        if st.status != "awaiting_selection":
            new_id = str(uuid.uuid4())
            new_state = JobState(
                id=new_id,
                status="queued_download",
                created_at=now_iso(),
                updated_at=now_iso(),
                user_message=st.user_message,
                search_limit=st.search_limit,
                parsed=st.parsed,
                candidates=st.candidates,
                selected_candidate=selected,
                target_url=target_url,
                target_position=position,
                logs=[f"Spawned from search job {job_id}", f"Selected candidate: {selected.get('title', 'unknown')}"]
            )
            _jobs[new_id] = new_state
        else:
            st.selected_candidate = selected
            st.status = "queued_download"
            st.target_url = target_url
            st.target_position = position
            st.updated_at = now_iso()
            new_id = None

    _persist_jobs_state()
    if new_id:
        _emit_event("job_created", new_id)
        _emit_event("job_selected", new_id)
        run_selected_candidate_async(new_id, selected, target_url, position)
        return new_id

    _emit_event("job_selected", job_id)
    run_selected_candidate_async(job_id, selected, target_url, position)
    return job_id


def run_selected_candidate_async(job_id: str, selected: dict, target_url: Optional[str] = None,
                                 position: Optional[dict] = None):
    def _runner():
        t0 = time.time()
        try:
            if _is_cancelled(job_id):
                _update(job_id, status="failed", error="Cancelled by user")
                return
            _log(job_id, f"Selected candidate: {selected.get('title', 'unknown')}")
            parsed = _jobs[job_id].parsed or {}
            preferred_title = selected.get("title") or parsed.get("preferred_title")

            _update(job_id, status="downloading")
            _log(job_id, f"Downloading selected URL only: {selected.get('url')}")
            dl_started = time.time()
            mp3 = download_mp3(selected["url"], settings.data_dir / "downloads", preferred_title)
            _log(job_id, f"Timing: download+extract completed in {time.time() - dl_started:.1f}s")

            if _is_cancelled(job_id):
                _update(job_id, status="failed", error="Cancelled by user")
                return
            if get_job(job_id).status in {"done", "failed"}:
                return

            target = (parsed.get("target_character_name") if isinstance(parsed, dict) else None)
            _enqueue_upload(job_id, Path(mp3), target=target, target_url=target_url, position=position)
            _log(job_id, f"Timing: total select->queued_upload in {time.time() - t0:.1f}s")
        except Exception as e:
            reason = _classify_download_error(e)
            _update(job_id, status="failed", error=f"{e}")
            _log(job_id, f"Failed ({reason}): {e}")

    threading.Thread(target=_runner, daemon=True).start()


def create_upload_only_job(file_path: str, note: str = "upload existing file", target_url: Optional[str] = None,
                           position: Optional[dict] = None) -> JobState:
    # Prevent a second tap from queuing the same file while the first one is active.
    with _lock:
        for existing in _jobs.values():
            if existing.user_message == note and existing.target_url == target_url and existing.status in {"queued_prepare", "preparing", "waiting_upload", "uploading"}:
                return existing
    job_id = str(uuid.uuid4())
    st = JobState(
        id=job_id,
        status="queued_prepare",
        created_at=now_iso(),
        updated_at=now_iso(),
        user_message=note,
        logs=["Upload-only job created"],
        output_file=file_path,
        target_url=target_url,
        target_position=position,
    )
    with _lock:
        _jobs[job_id] = st
    _emit_event("job_created", job_id)

    def _runner():
        t0 = time.time()
        try:
            if _is_cancelled(job_id):
                _update(job_id, status="failed", error="Cancelled by user")
                return
            p = Path(file_path)
            if not p.exists():
                raise RuntimeError(f"File not found: {file_path}")
            _update(job_id, status="preparing")
            _log(job_id, "Checking local audio for Tonies")
            incoming_seconds = _probe_audio_duration_seconds(p)
            if incoming_seconds <= 0:
                raise RuntimeError('Local audio duration could not be verified')

            if _is_cancelled(job_id):
                _update(job_id, status="failed", error="Cancelled by user")
                return

            _enqueue_upload(job_id, p, target=None, target_url=target_url, position=position)
            _log(job_id, f"Timing: total upload-only->queued_upload in {time.time() - t0:.1f}s")
        except Exception as e:
            reason = _classify_download_error(e)
            _update(job_id, status="failed", error=str(e))
            _log(job_id, f"Failed ({reason}): {e}")

    threading.Thread(target=_runner, daemon=True).start()
    return st


def start_worker():
    _load_jobs_state()
    _persist_jobs_state()
    t = threading.Thread(target=worker_loop, daemon=True)
    t.start()
    w = threading.Thread(target=watchdog_loop, daemon=True)
    w.start()
