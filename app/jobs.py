from __future__ import annotations
import threading
import queue
import uuid
import subprocess
import multiprocessing as mp
import time
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Any

from .models import JobState
from .ai import parse_request
from .downloader import download_mp3, search_youtube
from .uploader import upload_to_tonies, reset_upload_session, get_tonies_content
from .credentials import get_credentials
from .config import settings
from .logger import get_logger

logger = get_logger(__name__)

_jobs: Dict[str, JobState] = {}
_q: queue.Queue[str] = queue.Queue()
_upload_q: queue.Queue[dict[str, Any]] = queue.Queue()
_lock = threading.Lock()
_upload_lock = threading.Lock()
_event_cond = threading.Condition()
_event_seq = 0
_events: list[dict[str, Any]] = []
_MAX_EVENTS = 500
_event_stats: dict[str, int] = {
    "emitted": 0,
    "created": 0,
    "updated": 0,
    "logs": 0,
    "selected": 0,
}

_STAGE_TIMEOUT_SEC: dict[str, int] = {
    "parsing": 90,
    "searching": 180,
    "queued_download": 120,
    "downloading": 900,
    "queued_prepare": 120,
    "preparing": 900,
    "waiting_upload": 600,
    "uploading": 420,
}

_UPLOAD_ATTEMPT_TIMEOUT_SEC = 420
_JOBS_STATE_FILE = settings.data_dir / "jobs-state.json"
_MAX_PERSISTED_JOBS = 200


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _emit_event(kind: str, job_id: Optional[str] = None):
    global _event_seq
    payload: dict[str, Any] = {"kind": kind, "at": now_iso()}
    if job_id:
        with _lock:
            st = _jobs.get(job_id)
            payload["job"] = st.model_dump() if st else None
            payload["job_id"] = job_id
    with _event_cond:
        _event_seq += 1
        evt = {"id": _event_seq, **payload}
        _events.append(evt)
        if len(_events) > _MAX_EVENTS:
            del _events[: len(_events) - _MAX_EVENTS]

        _event_stats["emitted"] = _event_stats.get("emitted", 0) + 1
        if kind == "job_created":
            _event_stats["created"] = _event_stats.get("created", 0) + 1
        elif kind == "job_updated":
            _event_stats["updated"] = _event_stats.get("updated", 0) + 1
        elif kind == "job_log":
            _event_stats["logs"] = _event_stats.get("logs", 0) + 1
        elif kind == "job_selected":
            _event_stats["selected"] = _event_stats.get("selected", 0) + 1

        _event_cond.notify_all()


def get_events_since(last_id: int, timeout_sec: float = 15.0) -> list[dict[str, Any]]:
    with _event_cond:
        if _event_seq <= last_id:
            _event_cond.wait(timeout=timeout_sec)
        return [e for e in _events if e["id"] > last_id]


def get_event_stats() -> dict[str, int]:
    with _event_cond:
        return {**_event_stats, "last_event_id": _event_seq, "buffered": len(_events)}


def get_queue_stats() -> dict[str, int]:
    with _lock:
        in_flight = sum(1 for j in _jobs.values() if str(j.status) in {
            "queued_download", "downloading", "queued_prepare", "preparing", "waiting_upload", "uploading"
        })
    return {
        "search_queue_depth": _q.qsize(),
        "upload_queue_depth": _upload_q.qsize(),
        "in_flight_jobs": in_flight,
    }


def _persist_jobs_state() -> None:
    try:
        with _lock:
            ordered = sorted(_jobs.values(), key=lambda j: j.created_at, reverse=True)[:_MAX_PERSISTED_JOBS]
            payload = [j.model_dump() for j in ordered]
        _JOBS_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _JOBS_STATE_FILE.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(_JOBS_STATE_FILE)
    except Exception as e:
        logger.warning('persist_jobs_state failed: %s', e)


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


def _ensure_tonies_capacity(target_url: Optional[str], incoming_seconds: float) -> None:
    if not target_url or incoming_seconds <= 0:
        return
    try:
        content = get_tonies_content(target_url)
    except Exception as e:
        raise RuntimeError(f"Could not verify free minutes before upload: {e}")

    free_minutes = content.get('free_minutes')
    if free_minutes is None:
        return

    free_seconds = max(float(free_minutes), 0.0) * 60.0
    if incoming_seconds > free_seconds + 1.0:
        need = incoming_seconds / 60.0
        have = free_seconds / 60.0
        raise RuntimeError(f"Not enough free space on selected Creative Tonie (need {need:.1f} min, have {have:.1f} min).")


def _normalize_local_mp3_for_tonies(src: Path) -> Path:
    staging_root = settings.data_dir / "staging"
    # Keep a clean filename for Tonies display while still isolating each job in a unique folder.
    job_stage = staging_root / f"job-{uuid.uuid4().hex[:8]}"
    job_stage.mkdir(parents=True, exist_ok=True)
    out = job_stage / f"{src.stem}.mp3"

    cmd = [
        "ffmpeg", "-y",
        "-i", str(src),
        "-vn",
        "-ar", "44100",
        "-ac", "2",
        "-codec:a", "libmp3lame",
        "-b:a", "128k",
        str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return out


def _upload_subprocess_main(result_q: mp.Queue, file_path: str, target: Optional[str], target_url: Optional[str], verify_strict: bool, tonies_email: str = "", tonies_password: str = ""):
    try:
        upload_to_tonies(
            Path(file_path),
            target_character_name=target,
            target_url=target_url,
            verify_strict=verify_strict,
            tonies_email_override=tonies_email,
            tonies_password_override=tonies_password,
        )
        result_q.put({"ok": True})
    except Exception as e:
        result_q.put({"ok": False, "error": str(e)})


def _upload_once_with_timeout(job_id: str, file_path: Path, target: Optional[str], target_url: Optional[str], verify_strict: bool, tonies_email: str = "", tonies_password: str = "", timeout_sec: int = _UPLOAD_ATTEMPT_TIMEOUT_SEC):
    q: mp.Queue = mp.Queue(maxsize=1)
    p = mp.Process(target=_upload_subprocess_main, args=(q, str(file_path), target, target_url, verify_strict, tonies_email, tonies_password), daemon=True)
    t0 = time.time()
    p.start()
    p.join(timeout=timeout_sec)
    elapsed = int(time.time() - t0)
    if p.is_alive():
        _log(job_id, f"Upload attempt timed out after {elapsed}s; terminating worker process")
        p.terminate()
        p.join(timeout=5)
        raise RuntimeError(f"Upload attempt timed out after {elapsed}s")
    try:
        result = q.get_nowait()
    except Exception:
        result = {"ok": p.exitcode == 0, "error": f"Upload worker exited with code {p.exitcode}"}
    if not result.get("ok"):
        raise RuntimeError(result.get("error") or "Upload attempt failed")


def _upload_to_tonies_with_retry(job_id: str, file_path: Path, target: Optional[str] = None, target_url: Optional[str] = None, verify_strict: bool = True, attempts: int = 2, tonies_email: str = "", tonies_password: str = ""):
    last_err: Optional[Exception] = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            _log(job_id, f"Upload attempt {attempt}/{max(1, attempts)} starting for {file_path.name}")
            _upload_once_with_timeout(job_id, file_path, target=target, target_url=target_url, verify_strict=verify_strict, tonies_email=tonies_email, tonies_password=tonies_password)
            _log(job_id, f"Upload attempt {attempt}/{max(1, attempts)} finished successfully")
            return
        except Exception as e:
            last_err = e
            msg = str(e).lower()
            if "cannot switch to a different thread" in msg or "asyncio loop" in msg:
                _log(job_id, "Resetting upload browser session after Playwright thread/loop error…")
                try:
                    reset_upload_session()
                except Exception:
                    pass
            if attempt < max(1, attempts):
                _log(job_id, f"Upload to Tonies failed, retrying once… ({e})")
            else:
                break
    if last_err:
        raise last_err


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


def _enqueue_upload(job_id: str, mp3_path: Path, target: Optional[str], target_url: Optional[str], fallback_path: Optional[Path] = None):
    creds = get_credentials()
    tonies_email = str(creds.get("email") or "").strip()
    tonies_password = str(creds.get("password") or "").strip()

    _update(job_id, status="waiting_upload", output_file=str(mp3_path), target_url=target_url)
    _log(job_id, "Waiting for Tonies uploader slot")
    _upload_q.put({
        "job_id": job_id,
        "mp3_path": str(mp3_path),
        "target": target,
        "target_url": target_url,
        "fallback_path": str(fallback_path) if fallback_path else None,
        "tonies_email": tonies_email,
        "tonies_password": tonies_password,
    })


def upload_worker_loop():
    while True:
        item = _upload_q.get()
        try:
            job_id = item["job_id"]
            mp3_path = Path(item["mp3_path"])
            target = item.get("target")
            target_url = item.get("target_url")
            fallback_path = Path(item["fallback_path"]) if item.get("fallback_path") else None
            tonies_email = str(item.get("tonies_email") or "")
            tonies_password = str(item.get("tonies_password") or "")

            if _is_cancelled(job_id):
                _update(job_id, status="failed", error="Cancelled by user")
                continue

            with _upload_lock:
                if _is_cancelled(job_id):
                    _update(job_id, status="failed", error="Cancelled by user")
                    continue
                _update(job_id, status="uploading")
                _log(job_id, "Uploading to Tonies")
                try:
                    _upload_to_tonies_with_retry(job_id, mp3_path, target=target, target_url=target_url, verify_strict=False, attempts=1, tonies_email=tonies_email, tonies_password=tonies_password)
                except Exception:
                    if fallback_path and fallback_path.exists():
                        _log(job_id, "Prepared file upload failed; retrying once with original file")
                        _upload_to_tonies_with_retry(job_id, fallback_path, target=target, target_url=target_url, verify_strict=False, attempts=1, tonies_email=tonies_email, tonies_password=tonies_password)
                    else:
                        raise

            _update(job_id, status="done")
            _log(job_id, "Completed")
        except Exception as e:
            reason = _classify_download_error(e)
            _update(job_id, status="failed", error=f"{e}")
            _log(job_id, f"Failed ({reason}): {e}")
        finally:
            _upload_q.task_done()


def select_candidate_and_continue(job_id: str, index: int, target_url: Optional[str] = None) -> str:
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
                logs=[f"Spawned from search job {job_id}", f"Selected candidate: {selected.get('title', 'unknown')}"]
            )
            _jobs[new_id] = new_state
        else:
            st.selected_candidate = selected
            st.status = "queued_download"
            st.target_url = target_url
            st.updated_at = now_iso()
            new_id = None

    _persist_jobs_state()
    if new_id:
        _emit_event("job_created", new_id)
        _emit_event("job_selected", new_id)
        run_selected_candidate_async(new_id, selected, target_url)
        return new_id

    _emit_event("job_selected", job_id)
    run_selected_candidate_async(job_id, selected, target_url)
    return job_id


def run_selected_candidate_async(job_id: str, selected: dict, target_url: Optional[str] = None):
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

            target = (parsed.get("target_character_name") if isinstance(parsed, dict) else None)
            _ensure_tonies_capacity(target_url, _probe_audio_duration_seconds(Path(mp3)))
            _enqueue_upload(job_id, Path(mp3), target=target, target_url=target_url)
            _log(job_id, f"Timing: total select->queued_upload in {time.time() - t0:.1f}s")
        except Exception as e:
            reason = _classify_download_error(e)
            _update(job_id, status="failed", error=f"{e}")
            _log(job_id, f"Failed ({reason}): {e}")

    threading.Thread(target=_runner, daemon=True).start()


def create_upload_only_job(file_path: str, note: str = "upload existing file", target_url: Optional[str] = None) -> JobState:
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
            _log(job_id, "Preparing local file for Tonies (re-encode)")
            prep_started = time.time()
            prepared = _normalize_local_mp3_for_tonies(p)
            _log(job_id, f"Timing: local prepare completed in {time.time() - prep_started:.1f}s")

            if _is_cancelled(job_id):
                _update(job_id, status="failed", error="Cancelled by user")
                return

            _ensure_tonies_capacity(target_url, _probe_audio_duration_seconds(prepared))
            _enqueue_upload(job_id, prepared, target=None, target_url=target_url, fallback_path=p)
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
    u = threading.Thread(target=upload_worker_loop, daemon=True)
    u.start()
    w = threading.Thread(target=watchdog_loop, daemon=True)
    w.start()
