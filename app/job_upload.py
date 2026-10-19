"""Own upload hand-off, callbacks, local-file preparation and job media checks."""
from __future__ import annotations
import copy
import math
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from . import job_store as store, uploader
from .downloader import classify_download_error
from .events import now_iso
from .media_process import probe_duration
from .models import JobState

_upload_mirror_lock = threading.Lock()


def _probe_audio_duration_seconds(path: Path) -> float:
    """Bounded ffprobe through the shared media policy; 0.0 when no duration is reported."""
    seconds = probe_duration(path, timeout=30)
    if seconds is None:
        return 0.0
    if not math.isfinite(seconds) or seconds < 0:
        raise RuntimeError("Audio duration is not a valid number")
    return seconds


def enqueue_upload(job_id: str, mp3_path: Path, target: Optional[str], target_url: Optional[str],
                    position: Optional[dict] = None):
    store.update_job(job_id, status="waiting_upload", output_file=str(mp3_path), target_url=target_url)
    store.log_job(job_id, "Queued for Tonies")
    url = uploader.resolve_upload_target(target, target_url)
    action = uploader.action_queue.submit_upload(url, Path(mp3_path), uploader.upload_title(mp3_path), position,
                                                on_change=lambda a: _on_upload_action(job_id, a))
    store.update_job(job_id, tonies_action_id=action.id, target_url=url)


def _on_upload_action(job_id: str, action) -> None:
    """Serialize upload callbacks so late forward events cannot revive Undo."""
    with _upload_mirror_lock:
        _mirror_upload_action(job_id, action)


def _mirror_upload_action(job_id: str, action) -> None:
    job = store.get_job(job_id)
    if job is None or job.status in store.REVERSED_STATUSES:
        return
    if action.status in store.REVERSED_STATUSES:
        status = action.status
        store.update_job(job_id, status=status, cancel_requested=True, error=None, needs_refresh=False,
                      uploaded_chapter_id=None, tonies_content=None, attachment_receipt=None, upload_positioned=False)
        store.log_job(job_id, "Upload undone" if status == "undone" else "Upload canceled before attachment")
        return
    if action.status == "done" and action.needs_refresh:
        # The inverse was attempted, not confirmed. Forward acceptance remains
        # historical evidence, but is never promoted to current inventory.
        store.update_job(job_id, error=action.error, needs_refresh=True)
        store.log_job(job_id, "Undo outcome unconfirmed; refresh the Tonie before another action")
        return
    if action.status == "transferring":
        if job.status == "waiting_upload":
            store.update_job(job_id, status="uploading")
        store.log_job(job_id, "Sending audio to Tonies")
    elif action.status == "ready":
        store.log_job(job_id, "Audio sent; adding it to the Tonie")
    elif action.status == "failed":
        if job.status not in {"done", "failed"}:
            store.update_job(job_id, status="failed", error=action.error)
        accepted = _accepted_attachment(action)
        if accepted:
            store.update_job(job_id, error=action.error, **accepted)
        store.log_job(job_id, f"Upload failed: {action.error}")
    elif action.status == "done":
        content = action.content or {}
        if not action.chapter_id or sum(row.get("chapter_id") == action.chapter_id
                                        for row in content.get("chapters", [])) != 1:
            store.update_job(job_id, status="failed", error="Tonies upload returned no confirmed chapter; refresh the library before retrying")
            return
        receipt = {"uploaded_chapter_id": action.chapter_id, "tonies_content": content,
                   "target_url": action.target_url, "upload_positioned": action.params.get("position") is not None}
        if job.status == "failed":
            store.update_job(job_id, **receipt)
            store.log_job(job_id, "Tonies accepted the upload after the job stopped; the recorded chapter is present. Do not upload it again.")
            return
        store.update_job(job_id, status="done", error=None, **receipt)
        store.log_job(job_id, "Audio added to Tonies; processing continues in the library" if content.get("processing") else "Completed")


def _accepted_attachment(action) -> dict | None:
    """Tonies accepted the attachment but its read-back failed: keep the receipt so the
    page blocks a duplicate upload, without presenting it as the current library."""
    if not (action.attachment_receipt and action.chapter_id):
        return None
    return {"uploaded_chapter_id": action.chapter_id, "attachment_receipt": copy.deepcopy(action.attachment_receipt),
            "needs_refresh": True, "tonies_content": None, "target_url": action.target_url}


def create_upload_only_job(file_path: str, note: str = "upload existing file", target_url: Optional[str] = None,
                           position: Optional[dict] = None) -> JobState:
    # Prevent a second tap from queuing the same file while the first one is active.
    with store.state_lock:
        for existing in store.job_states():
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
    store.add_job(st)
    store.emit_job_event("job_created", job_id)

    def _runner():
        t0 = time.time()
        try:
            if store.cancel_requested(job_id):
                store.update_job(job_id, status="failed", error="Canceled by user")
                return
            p = Path(file_path)
            if not p.exists():
                raise RuntimeError(f"File not found: {file_path}")
            store.update_job(job_id, status="preparing")
            store.log_job(job_id, "Checking local audio for Tonies")
            incoming_seconds = _probe_audio_duration_seconds(p)
            if incoming_seconds <= 0:
                raise RuntimeError('Local audio duration could not be verified')

            if store.cancel_requested(job_id):
                store.update_job(job_id, status="failed", error="Canceled by user")
                return

            enqueue_upload(job_id, p, target=None, target_url=target_url, position=position)
            store.log_job(job_id, f"Timing: total upload-only->queued_upload in {time.time() - t0:.1f}s")
        except Exception as e:
            reason = classify_download_error(e)
            store.update_job(job_id, status="failed", error=str(e))
            store.log_job(job_id, f"Failed ({reason}): {e}")

    threading.Thread(target=_runner, daemon=True).start()
    return st
