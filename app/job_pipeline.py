"""Own search execution, candidate selection, the download pool and editor fetches."""
from __future__ import annotations
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from . import job_store as store, job_upload as uploads
from .ai import parse_request
from .attach_order import AttachOrder
from .config import settings
from .downloader import PLAYLIST_MAX, classify_download_error, download_mp3, search_youtube
from .events import now_iso
from .models import JobState
from .youtube_source import is_playlist_url, normalize_youtube_input

MAX_PARALLEL_DOWNLOADS = 3
# First in, first out: picks start downloading in the order they were added.
_downloads = ThreadPoolExecutor(max_workers=MAX_PARALLEL_DOWNLOADS, thread_name_prefix="youtube-download")
# The editor gets its own lane: someone waiting at an open editor never queues behind adds.
_editor_downloads = ThreadPoolExecutor(max_workers=1, thread_name_prefix="editor-download")
_fetches: dict[str, Future] = {}  # video URL -> download for the editor (in flight or done)


def run_job(job_id: str):
    try:
        if store.cancel_requested(job_id):
            store.update_job(job_id, status="failed", error="Canceled by user")
            return
        store.update_job(job_id, status="parsing")
        store.log_job(job_id, "Parsing request (exact-text mode)")
        parsed = parse_request(store.get_job(job_id).user_message)
        if store.cancel_requested(job_id):
            store.update_job(job_id, status="failed", error="Canceled by user")
            return
        store.update_job(job_id, parsed=parsed.model_dump(), status="searching")

        store.log_job(job_id, f"Searching YouTube: {parsed.youtube_query}")
        with store.state_lock:
            job = store.get_job(job_id)
            lim = int((job.search_limit if job else 5) or 5)
        if lim not in (5, 10, 15, 20):
            lim = 5
        candidates = search_youtube(parsed.youtube_query, limit=lim)
        if not candidates:
            raise RuntimeError("No YouTube results found")

        playlist = is_playlist_url(normalize_youtube_input(parsed.youtube_query))
        store.update_job(job_id, candidates=candidates, playlist=playlist, status="awaiting_selection")
        store.log_job(job_id, f"Found {len(candidates)} candidates; waiting for selection")
    except Exception as e:
        store.update_job(job_id, status="failed", error=str(e))
        store.log_job(job_id, f"Failed: {e}")


def worker_loop():
    while True:
        job_id = store.next_search_job()
        try:
            run_job(job_id)
        finally:
            store.finish_search_job()


def select_candidate_and_continue(job_id: str, index: int, target_url: Optional[str] = None,
                                  position: Optional[dict] = None) -> str:
    with store.state_lock:
        st = store.get_job(job_id)
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
            new_id = _spawn_selection_job(st, selected, target_url, position).id
        else:
            st.selected_candidate = selected
            st.status = "queued_download"
            st.target_url = target_url
            st.target_position = position
            st.updated_at = now_iso()
            new_id = None

    store.persist_state()
    if new_id:
        store.emit_job_event("job_created", new_id)
        store.emit_job_event("job_selected", new_id)
        run_selected_candidate_async(new_id, selected, target_url, position)
        return new_id

    store.emit_job_event("job_selected", job_id)
    run_selected_candidate_async(job_id, selected, target_url, position)
    return job_id


def _spawn_selection_job(st: JobState, selected: dict, target_url: Optional[str],
                         position: Optional[dict]) -> JobState:
    """A new job for one picked result; caller holds store.state_lock."""
    new_state = JobState(
        id=str(uuid.uuid4()),
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
        logs=[f"Spawned from search job {st.id}", f"Selected candidate: {selected.get('title', 'unknown')}"],
    )
    store.add_job(new_state)
    return new_state


def select_candidates_and_continue(job_id: str, indexes: list[int], target_url: Optional[str] = None,
                                   position: Optional[dict] = None) -> list[str]:
    """Add several results at once; they attach in list order (top-down, wherever they land)."""
    if (not isinstance(indexes, list) or not 1 <= len(indexes) <= PLAYLIST_MAX
            or len(set(indexes)) != len(indexes)):
        raise ValueError(f"Choose between 1 and {PLAYLIST_MAX} different videos")
    with store.state_lock:
        st = store.get_job(job_id)
        if not st:
            raise ValueError("Search not found; search again")
        if any(not isinstance(i, int) or isinstance(i, bool) or not 0 <= i < len(st.candidates) for i in indexes):
            raise ValueError("A chosen video is no longer in the results; search again")
        spawned = [_spawn_selection_job(st, st.candidates[i], target_url, position) for i in indexes]
    store.persist_state()
    # Each one lands at the chosen edge, so adding to the top hands them over last-first.
    # They also start downloading in hand-over order, so the first to finish goes first.
    hand_over = spawned[::-1] if (position or {}).get("edge") == "start" else spawned
    order = AttachOrder([job.id for job in hand_over])
    for job in hand_over:
        store.emit_job_event("job_created", job.id)
        run_selected_candidate_async(job.id, job.selected_candidate, target_url, position, order=order)
    return [job.id for job in spawned]


def _library_copy(url: str) -> Optional[Path]:
    """A previous download of this video, if still present; caller holds store.state_lock."""
    done = _fetches.get(url)
    if done is not None and done.done() and not done.exception() and Path(done.result()).is_file():
        return Path(done.result())
    for st in store.job_states():
        if (st.selected_candidate or {}).get("url") == url and st.output_file and Path(st.output_file).is_file():
            return Path(st.output_file)
    return None


def fetch_candidate(job_id: str, index: int) -> Future:
    """Download one result into the local library (for the editor) without adding it anywhere.

    Reuses a copy the library already has, and joins a download already running for the
    same video, so opening Edit twice never makes a second file.
    """
    with store.state_lock:
        st = store.get_job(job_id)
        if not st or not 0 <= index < len(st.candidates):
            raise ValueError("That video is no longer in the results; search again")
        selected = dict(st.candidates[index])
        url = selected["url"]
        existing = _library_copy(url)
        if existing is not None:
            ready: Future = Future()
            ready.set_result(existing)
            return ready
        running = _fetches.get(url)
        if running is not None and not running.done():
            return running
        title = selected.get("title") or (st.parsed or {}).get("preferred_title")
        future = _editor_downloads.submit(download_mp3, url, settings.data_dir / "downloads", title)
        _fetches[url] = future
    return future


def run_selected_candidate_async(job_id: str, selected: dict, target_url: Optional[str] = None,
                                 position: Optional[dict] = None, order: Optional[AttachOrder] = None):
    """Download on the shared FIFO pool, wait for its turn in a batch, then queue the upload."""
    def _stopped() -> bool:
        job = store.get_job(job_id)
        if job is None or job.status in store.TERMINAL_STATUSES:  # e.g. failed by the watchdog
            return True
        if store.cancel_requested(job_id):
            store.update_job(job_id, status="failed", error="Canceled by user")
            return True
        return False

    def _runner():
        t0 = time.time()
        try:
            if _stopped():
                return
            store.log_job(job_id, f"Selected candidate: {selected.get('title', 'unknown')}")
            parsed = store.get_job(job_id).parsed or {}
            preferred_title = selected.get("title") or parsed.get("preferred_title")

            store.update_job(job_id, status="downloading")
            store.log_job(job_id, f"Downloading selected URL only: {selected.get('url')}")
            dl_started = time.time()
            mp3 = download_mp3(selected["url"], settings.data_dir / "downloads", preferred_title)
            store.log_job(job_id, f"Timing: download+extract completed in {time.time() - dl_started:.1f}s")
            if _stopped():
                return

            if order is not None:
                store.update_job(job_id, status="waiting_upload")
                store.log_job(job_id, "Waiting for the tracks before it to be handed over")
                if not order.wait_turn(job_id):
                    store.update_job(job_id, status="failed",
                                  error="An earlier track in this batch didn't finish in time; add it again")
                    return
                if _stopped():
                    return

            target = (parsed.get("target_character_name") if isinstance(parsed, dict) else None)
            uploads.enqueue_upload(job_id, Path(mp3), target=target, target_url=target_url, position=position)
            store.log_job(job_id, f"Timing: total select->queued_upload in {time.time() - t0:.1f}s")
        except Exception as e:
            reason = classify_download_error(e)
            store.update_job(job_id, status="failed", error=f"{e}")
            store.log_job(job_id, f"Failed ({reason}): {e}")
        finally:
            if order is not None:
                order.done(job_id)

    _downloads.submit(_runner)


def start_worker():
    store.load_state()
    store.persist_state()
    t = threading.Thread(target=worker_loop, daemon=True)
    t.start()
    w = threading.Thread(target=store.watchdog_loop, daemon=True)
    w.start()
