"""Own Tonies uploads, guarded batch mutations, undo and the public client interface."""
from __future__ import annotations

import copy
import math
from pathlib import Path
from threading import RLock
from typing import cast
from urllib.parse import urlsplit

from . import tonies_batch
from .tonies_batch import ATTACH_MARK, OpError, apply_ops
from .tonies_content import (
    TRACK_TOKEN, ToniesContent, content_view, is_processing, matches_chapters,
    validated_chapters, writable_chapters, editor_url, parse_target_url,
)
from .tonies_session import checked_number
from .undo import UndoReceipt, apply_inverse, inverse_steps

__all__ = ["ToniesAPI", "UnconfirmedWriteError", "editor_url", "parse_target_url", "renamed_title"]


def renamed_title(current: str, requested: str) -> str:
    try:
        return tonies_batch.renamed_title(current, requested)
    except OpError as error:
        raise RuntimeError(str(error)) from None


def _audio_seconds(path: Path) -> float:
    from .media_process import probe_seconds

    try:
        seconds = probe_seconds(path, timeout=30)
    except (OSError, ValueError, RuntimeError):
        raise RuntimeError("Could not measure audio duration with ffprobe") from None
    if not math.isfinite(seconds) or seconds <= 0:
        raise RuntimeError("Audio duration must be positive") from None
    return seconds


def _signed_storage_request(allocation: dict, existing: list[dict]) -> tuple[str, str, dict]:
    """Validate a POST /file allocation: a fresh file id and a presigned S3 form with plain string fields."""
    identity, signed = allocation.get("fileId"), allocation.get("request")
    if not isinstance(identity, str) or not identity or not isinstance(signed, dict):
        raise RuntimeError("Invalid Tonies upload allocation")
    if any(identity in (c["id"], c["file"]) for c in existing):
        raise RuntimeError("Tonies upload allocation collides with existing content")
    try:
        storage = urlsplit(signed.get("url", ""))
        host = storage.hostname or ""
        valid = (storage.scheme == "https" and host.endswith(".amazonaws.com")
                 and any(part == "s3" or part.startswith("s3-") for part in host.split("."))
                 and not storage.username and not storage.password and storage.port in (None, 443) and not storage.fragment)
    except ValueError:
        valid = False
    fields = signed.get("fields")
    if (not valid or not isinstance(fields, dict) or "file" in fields
            or any(not isinstance(k, str) or not isinstance(v, str) for k, v in fields.items())):
        raise RuntimeError("Invalid Tonies signed storage request")
    return identity, signed["url"], fields


class UnconfirmedWriteError(RuntimeError):
    """A write was accepted, but a fresh inventory could not confirm its outcome.

    ``attachment_receipt`` is historical PATCH evidence, not current content. Only
    validated attachment identities appear in ``results``; callers must not retry
    the mutation merely because confirmation failed.
    """

    def __init__(self, results: list[dict], attachment_receipt: dict | None):
        message = "Saved Tonies content was not confirmed; refresh before another action"
        if attachment_receipt is not None:
            message += ". Tonies accepted the upload attachment. Do not upload again."
        super().__init__(message)
        self.results = copy.deepcopy(results)
        self.attachment_receipt = copy.deepcopy(attachment_receipt)
        self.needs_refresh = True


class ToniesAPI(ToniesContent):

    def _target_lock(self, ids):
        with self._locks_lock:
            return self._target_locks.setdefault(ids, RLock())

    def prepare_upload(self, path: Path, target_url: str) -> dict:
        """Send one audio file to Tonies storage. It is not on the Tonie until ``apply`` attaches it.

        Safe to run in parallel with other transfers and with ``apply`` on the same Tonie.
        """
        ids = parse_target_url(target_url)
        path = Path(path)
        if not path.is_file() or path.stat().st_size <= 0:
            raise RuntimeError("Audio file is missing or empty")
        seconds = _audio_seconds(path)
        target, config = self._snapshot(ids)
        if seconds > checked_number(target["secondsRemaining"], "remaining capacity"):
            raise RuntimeError("Not enough free Tonies capacity")
        if len(target["chapters"]) >= checked_number(config.get("maxChapters"), "chapter limit"):
            raise RuntimeError("Tonies chapter limit reached")
        if path.stat().st_size > checked_number(config.get("maxBytes"), "file size limit"):
            raise RuntimeError("Audio exceeds the Tonies file size limit")
        allocation = self._api("POST", "/file", {"headers": {}})
        identity, storage_url, fields = _signed_storage_request(allocation, target["chapters"])
        mime = "audio/wav" if path.suffix.lower() == ".wav" else "audio/mpeg"
        try:
            with path.open("rb") as audio:
                response = self._request("POST", storage_url, storage=True, data=fields, files={"file": (identity, audio, mime)})
        except OSError:
            raise RuntimeError("Could not read the audio file") from None
        if not 200 <= response.status_code < 300:
            raise RuntimeError(f"Tonies storage upload failed (HTTP {response.status_code})")
        return {"file_id": identity, "seconds": seconds, "live": bool(target.get("live"))}

    def apply(self, target_url: str, ops: list[dict]) -> dict:
        """Apply a batch of edits with one read, one write and one confirming read-back.

        Returns ``{"content": ..., "results": [{"error": str | None, "chapter_id": str | None}]}``
        with one result per op. Ops that fail validation are skipped; if the write itself
        fails or can't be confirmed, this raises and no op may be assumed applied.
        ``UnconfirmedWriteError`` retains validated attachment IDs and the historical
        PATCH receipt, never confirmed ``content``. Refresh instead of retrying.
        """
        ids = parse_target_url(target_url)
        with self._target_lock(ids):
            before, config = self._snapshot(ids)
            chapters = before["chapters"]
            # secondsRemaining lags behind tracks still processing, so it alone can overfill the
            # Tonie (Tonies then silently drops a track). Never exceed the Tonie's total.
            capacity = min(checked_number(config.get("maxSeconds"), "capacity"),
                           checked_number(before["secondsRemaining"], "remaining capacity")
                           + sum(checked_number(c["seconds"], "chapter duration") for c in chapters))
            desired, errors = apply_ops(chapters, ops, capacity_seconds=capacity,
                                        max_chapters=checked_number(config.get("maxChapters"), "chapter limit"))
            results = [{"error": error, "chapter_id": None} for error in errors]
            if writable_chapters(desired) == writable_chapters(chapters):
                return {"content": content_view(before, config), "results": results}
            processing_ids = {c["id"] for c in chapters if is_processing(c.get("transcoding"))}
            # The provider has no proven CAS/ETag contract; another device can still race this PATCH.
            saved = self._api("PATCH", f"/households/{ids[0]}/creativetonies/{ids[1]}", {"chapters": writable_chapters(desired)})
            expected = desired
            attachment_receipt = None
            if any(ATTACH_MARK in c for c in desired):
                expected = self._confirm_attachments(saved, chapters, desired, processing_ids, results)
                attachment_receipt = {"chapters": expected}
                processing_ids |= {r["chapter_id"] for r in results if r["chapter_id"]}
            try:
                content, confirmed = cast(tuple[dict, list[dict]], self._readback(
                    ids, expected, processing_ids=processing_ids, with_snapshot=True))
            except RuntimeError:
                raise UnconfirmedWriteError(results, attachment_receipt) from None
            receipt = UndoReceipt(self, ids, writable_chapters(confirmed), inverse_steps(chapters, ops, results),
                                  processing_ids=frozenset(processing_ids))
            return {"content": content, "results": results, "_undo": receipt}

    def undo(self, target_url: str, receipt: UndoReceipt, indices: list[int]) -> dict:
        """One guarded inverse PATCH; only the queue supplies the private receipt.

        The caller consumes the capability before entering here, including on failure.
        This is not atomic CAS: the provider exposes no proven version/ETag guard.
        """
        ids = parse_target_url(target_url)
        if not isinstance(receipt, UndoReceipt) or receipt.owner is not self or receipt.target != ids:
            raise RuntimeError("Undo account session changed; refresh the library")
        with self._target_lock(ids):
            before, config = self._snapshot(ids)
            if not matches_chapters(before["chapters"], receipt.expected, receipt.processing_ids):
                raise RuntimeError("Undo inventory changed; refresh the library")
            steps = [receipt.steps[index] for index in reversed(indices) if receipt.steps[index] is not None]
            if not steps or len(steps) != len(indices):
                raise RuntimeError("Undo is not available for this action")
            desired = apply_inverse(before["chapters"], cast(list[dict], steps))
            if writable_chapters(desired) == writable_chapters(before["chapters"]):
                return content_view(before, config)
            self._api("PATCH", f"/households/{ids[0]}/creativetonies/{ids[1]}", {"chapters": writable_chapters(desired)})
            try:
                still_processing = {c["id"] for c in before["chapters"] if is_processing(c.get("transcoding"))}
                return cast(dict, self._readback(ids, desired, processing_ids=set(receipt.processing_ids) | still_processing))
            except RuntimeError:
                raise UnconfirmedWriteError([], None) from None

    @staticmethod
    def _confirm_attachments(saved, before, desired, processing_ids, results):
        """Map each attached upload to the chapter ID Tonies assigned, by position and title."""
        returned = validated_chapters(saved.get("chapters"))
        original_ids = {c["id"] for c in before}
        if len(returned) != len(desired):
            raise RuntimeError("Upload attachment identity was not confirmed; refresh before another action")
        kept_returned, kept_desired = [], []
        for got, want in zip(returned, desired):
            if ATTACH_MARK in want:
                if got["id"] in original_ids or got["title"] != want["title"] or got["type"] != "file":
                    raise RuntimeError("Upload attachment identity was not confirmed; refresh before another action")
                results[want[ATTACH_MARK]]["chapter_id"] = got["id"]
            else:
                kept_returned.append(got)
                kept_desired.append(want)
        if not matches_chapters(kept_returned, kept_desired, processing_ids):
            raise RuntimeError("Upload attachment identity was not confirmed; refresh before another action")
        return copy.deepcopy(returned)

    def _apply_one(self, target_url, op):
        outcome = self.apply(target_url, [op])
        error = outcome["results"][0]["error"]
        if error:
            raise RuntimeError(error)
        return outcome

    # Single-edit helpers for callers that don't batch (and older API clients).

    def rename(self, target_url, index, title, chapter_id=None):
        renamed_title("", title)
        op = {"type": "rename", "chapter_id": chapter_id, "index": index, "expect_index": index, "title": title}
        return self._apply_one(target_url, op)["content"]

    def reorder(self, target_url, from_index, to_index, chapter_id=None):
        op = {"type": "move", "chapter_id": chapter_id, "index": from_index, "expect_index": from_index,
              "position": {"index": to_index}}
        if not isinstance(to_index, int) or to_index < 0:
            raise RuntimeError("Invalid chapter index; refresh stale content")
        return self._apply_one(target_url, op)["content"]

    def delete(self, target_url, index, chapter_title=None, chapter_id=None, chapter_fingerprint=None,
               app_track_token=None):
        if not chapter_id or chapter_title or chapter_fingerprint or app_track_token:
            chapter_id = self._resolve_legacy_delete(target_url, index, chapter_title, chapter_id,
                                                     chapter_fingerprint, app_track_token)
        return self._apply_one(target_url, {"type": "delete", "chapter_id": chapter_id})["content"]

    def _resolve_legacy_delete(self, target_url, index, chapter_title, chapter_id, chapter_fingerprint,
                               app_track_token):
        """Older clients name a delete by index plus title/token/fingerprint checks."""
        before, config = self._snapshot(parse_target_url(target_url))
        chapters = before["chapters"]
        if chapter_id:
            matches = [i for i, c in enumerate(chapters) if c["id"] == chapter_id]
            if len(matches) != 1:
                raise RuntimeError("Chapter identity does not match; refresh stale content")
            index = matches[0]
        elif not isinstance(index, int) or not 0 <= index < len(chapters):
            raise RuntimeError("Invalid chapter index; refresh stale content")
        selected = chapters[index]
        token = TRACK_TOKEN.search(selected["title"])
        if app_track_token and (not token or token.group(1).lower() != app_track_token.lower()):
            raise RuntimeError("Chapter token does not match")
        if chapter_title and chapter_title not in (selected["title"], TRACK_TOKEN.sub("", selected["title"]).strip()):
            raise RuntimeError("Chapter title does not match")
        if chapter_fingerprint:
            selected_content = content_view(before, config)["chapters"][index]
            if chapter_fingerprint != f'{selected["title"]}|{selected_content["duration"]}':
                raise RuntimeError("Chapter fingerprint does not match")
        if not chapter_id and not app_track_token and chapter_title:
            matches = [c for c in chapters if chapter_title in (c["title"], TRACK_TOKEN.sub("", c["title"]).strip())]
            if len(matches) != 1:
                raise RuntimeError("Chapter title is ambiguous; select by chapter ID")
        if not chapter_id and app_track_token:
            matches = [c for c in chapters if (match := TRACK_TOKEN.search(c["title"])) and match.group(1).lower() == app_track_token.lower()]
            if len(matches) != 1:
                raise RuntimeError("Chapter token is ambiguous")
        return selected["id"]

    def delete_all(self, target_url):
        return self._apply_one(target_url, {"type": "clear"})["content"]

    def upload(self, path: Path, target_url: str, title: str, position: dict | None = None):
        """Transfer and attach one file. Without a position it goes first on a live Tonie, else last."""
        prepared = self.prepare_upload(path, target_url)
        if position is None:
            position = {"edge": "start" if prepared["live"] else "end"}
        op = {"type": "attach", "file_id": prepared["file_id"], "title": title, "seconds": prepared["seconds"],
              "position": position}
        outcome = self._apply_one(target_url, op)
        return {"chapter_id": outcome["results"][0]["chapter_id"], "target_url": target_url, "content": outcome["content"]}
