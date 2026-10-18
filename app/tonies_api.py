"""HTTP-only Tonies cloud client. Tokens live only in this process's memory."""
from __future__ import annotations

import copy
import math
import re
import time
from pathlib import Path
from threading import Lock, RLock
from typing import cast
from urllib.parse import urlsplit

import httpx

from . import tonies_batch
from .tonies_batch import ATTACH_MARK, OpError, apply_ops
from .undo import UndoReceipt, apply_inverse, inverse_steps

API_BASE = "https://api.prod.tcs.toys/v2"
TOKEN_URL = "https://login.tonies.com/auth/realms/tonies/protocol/openid-connect/token"
_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_TARGET = re.compile(rf"/creative-tonies/({_UUID})/([0-9a-fA-F]{{16}})/edit")
_TRACK = re.compile(r"\s\[(?:oc:)?([a-f0-9]{4,12})\]$", re.I)
_TIMEOUT = httpx.Timeout(30, connect=10, write=120, pool=10)
QUERY = """query ($householdId: String, $creativeTonieId: String) {
  config { maxChapters maxSeconds maxBytes accepts }
  households(id: $householdId) { id name
    creativeTonies(id: $creativeTonieId) {
      id name imageUrl live transcoding secondsRemaining
      chapters { id title file seconds transcoding type }
    }
  }
}"""


def parse_target_url(value: str) -> tuple[str, str]:
    match = None
    try:
        parsed = urlsplit(value)
        match = _TARGET.fullmatch(parsed.path)
        valid = (parsed.scheme == "https" and parsed.netloc == "my.tonies.com"
                 and not parsed.fragment and parsed.query in ("", "withUpload=true") and match)
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise RuntimeError("Invalid Tonies target URL; select a Creative-Tonie editor on my.tonies.com")
    return match.group(1), match.group(2)


def editor_url(household_id: str, tonie_id: str) -> str:
    value = f"https://my.tonies.com/creative-tonies/{household_id}/{tonie_id}/edit?withUpload=true"
    parse_target_url(value)
    return value


def _number(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise RuntimeError(f"Invalid Tonies {label}")
    return value


def _processing(value) -> bool:
    if isinstance(value, str) and value.lower() in {"error", "failed", "failure"}:
        raise RuntimeError("Tonies audio processing failed")
    return bool(value)


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


def _chapters(value) -> list[dict]:
    if not isinstance(value, list):
        raise RuntimeError("Invalid Tonies chapter response")
    identities = set()
    for chapter in value:
        if not isinstance(chapter, dict) or any(not isinstance(chapter.get(key), str) for key in ("id", "title", "file", "type")):
            raise RuntimeError("Invalid Tonies chapter response")
        if not chapter["id"] or chapter["id"] in identities:
            raise RuntimeError("Invalid or duplicate Tonies chapter identity")
        identities.add(chapter["id"])
        _number(chapter.get("seconds"), "chapter duration")
        _processing(chapter.get("transcoding"))
    return value


def _writable(chapters: list[dict]) -> list[dict]:
    return [{key: chapter[key] for key in ("id", "title", "file", "seconds", "type")} for chapter in chapters]


def _matches_chapters(actual, expected, processing_ids):
    comparable, expected = _writable(actual), _writable(expected)
    for chapter, original in zip(comparable, expected):
        if chapter["id"] == original["id"] and chapter["id"] in processing_ids:
            # Only established processing identities may acquire a new file/duration.
            chapter["file"], chapter["seconds"] = original["file"], original["seconds"]
    return comparable == expected


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


def _content(target: dict, config: dict) -> dict:
    total = _number(config.get("maxSeconds"), "capacity")
    free = _number(target.get("secondsRemaining"), "remaining capacity")
    chapters = []
    for index, chapter in enumerate(_chapters(target.get("chapters"))):
        seconds = _number(chapter["seconds"], "chapter duration")
        rounded = int(seconds)
        token = _TRACK.search(chapter["title"])
        chapters.append({"index": index, "chapter_id": chapter["id"], "title": chapter["title"],
                         "display_title": _TRACK.sub("", chapter["title"]).strip() or chapter["title"],
                         "app_track_token": token.group(1).lower() if token else "",
                         "duration": f"{rounded // 3600:02}:{rounded // 60 % 60:02}:{rounded % 60:02}",
                         "seconds": seconds,
                         "processing": _processing(chapter.get("transcoding"))})
    return {"free_minutes": int(free // 60), "total_minutes": int(total // 60),
            "used_minutes": math.ceil(max(total - free, 0) / 60),
            "free_seconds": free, "total_seconds": total,
            "processing": _processing(target.get("transcoding")) or any(c["processing"] for c in chapters),
            "chapters": chapters}


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


class ToniesAPI:
    def __init__(self, email: str, password: str, *, client: httpx.Client | None = None,
                 poll_timeout: float = 8, poll_interval: float = 0.25):
        self._email, self._password = email, password
        self._client = client or httpx.Client(timeout=_TIMEOUT, follow_redirects=False, trust_env=False)
        self._access_token = self._refresh_token = ""
        self._expires_at = 0.0
        self._auth_lock = RLock()
        self._locks_lock = Lock()
        self._target_locks: dict[tuple[str, str], RLock] = {}
        self._closed = False
        self._poll_timeout, self._poll_interval = poll_timeout, poll_interval

    @property
    def closed(self) -> bool:
        """True once the session was ended (lock, sign-out or account change); never reused."""
        return self._closed

    def close(self):
        with self._auth_lock:
            self._closed = True
            self._access_token = self._refresh_token = self._email = self._password = ""
            self._expires_at = 0
            self._client.close()

    def _request(self, method, url, *, storage=False, **kwargs):
        # httpx.Client is thread-safe: reads, storage transfers and writes run concurrently.
        if self._closed:
            raise RuntimeError("Tonies account session was reset; retry with current credentials")
        try:
            request = self._client.build_request(method, url, timeout=_TIMEOUT, **kwargs)
            # No client defaults/cookies/auth may leak into the signed storage POST.
            if storage:
                for header in ("authorization", "cookie"):
                    request.headers.pop(header, None)
            return self._client.send(request, auth=None, follow_redirects=False)
        except (httpx.HTTPError, RuntimeError):
            if self._closed:
                raise RuntimeError("Tonies account session was reset; retry with current credentials") from None
            suffix = "; write outcome unconfirmed; refresh before another action" if method == "PATCH" else ""
            raise RuntimeError(f"Tonies HTTP transport failed{suffix}") from None

    @staticmethod
    def _json(response, operation):
        if not 200 <= response.status_code < 300:
            raise RuntimeError(f"Tonies {operation} failed (HTTP {response.status_code})")
        try:
            data = response.json()
        except ValueError:
            raise RuntimeError(f"Invalid Tonies {operation} response") from None
        if not isinstance(data, dict):
            raise RuntimeError(f"Invalid Tonies {operation} response")
        return data

    def _token(self):
        with self._auth_lock:
            if self._closed:
                raise RuntimeError("Tonies account session was reset; retry with current credentials")
            if self._access_token and time.monotonic() < self._expires_at:
                return self._access_token
            form = {"client_id": "my-tonies"}
            if self._refresh_token:
                form.update(grant_type="refresh_token", refresh_token=self._refresh_token)
            else:
                form.update(grant_type="password", scope="openid", username=self._email, password=self._password)
            response = self._request("POST", TOKEN_URL, data=form)
            if form["grant_type"] == "refresh_token" and response.status_code in (400, 401):
                # Explicit refresh rejection is not an ambiguous mutation. Reauthenticate once.
                self._access_token = self._refresh_token = ""
                response = self._request("POST", TOKEN_URL, data={"client_id": "my-tonies", "grant_type": "password", "scope": "openid", "username": self._email, "password": self._password})
            data = self._json(response, "authentication")
            if not isinstance(data.get("access_token"), str) or not data["access_token"]:
                raise RuntimeError("Invalid Tonies authentication response")
            lifetime = _number(data.get("expires_in"), "token lifetime")
            refresh = data.get("refresh_token", self._refresh_token)
            if not isinstance(refresh, str):
                raise RuntimeError("Invalid Tonies authentication response")
            self._access_token, self._refresh_token = data["access_token"], refresh
            self._expires_at = time.monotonic() + max(0, lifetime - min(15, lifetime / 10))
            return self._access_token

    def _api(self, method: str, path: str, payload: dict, *, read=False) -> dict:
        token = self._token()
        response = self._request(method, API_BASE + path, json=payload, headers={"Authorization": f"Bearer {token}"})
        if read and response.status_code == 401:
            with self._auth_lock:
                if self._access_token == token:
                    self._expires_at = 0
                token = self._token()
            response = self._request(method, API_BASE + path, json=payload, headers={"Authorization": f"Bearer {token}"})
        return self._json(response, "API request")

    def _query(self, ids=None):
        variables = {"householdId": ids[0], "creativeTonieId": ids[1]} if ids else {}
        result = self._api("POST", "/graphql", {"query": QUERY, "variables": variables}, read=True)
        if result.get("errors"):
            raise RuntimeError("Tonies GraphQL request failed")
        data = result.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("config"), dict) or not isinstance(data.get("households"), list):
            raise RuntimeError("Invalid Tonies GraphQL response")
        for household in data["households"]:
            if (not isinstance(household, dict) or not isinstance(household.get("id"), str)
                    or not isinstance(household.get("creativeTonies"), list)
                    or any(not isinstance(target, dict) or not isinstance(target.get("id"), str)
                           for target in household["creativeTonies"])):
                raise RuntimeError("Invalid Tonies GraphQL response")
        return data

    def _snapshot(self, ids):
        data = self._query(ids)
        targets = [target for house in data["households"] if house.get("id") == ids[0]
                   for target in house.get("creativeTonies", []) if target.get("id") == ids[1]]
        if len(targets) != 1:
            raise RuntimeError("Tonies target was not uniquely found in this account")
        _content(targets[0], data["config"])
        return targets[0], data["config"]

    def get_content(self, target_url):
        target, config = self._snapshot(parse_target_url(target_url))
        return _content(target, config)

    def list_targets(self):
        data = self._query()
        result = []
        seen = set()
        for house in data["households"]:
            for target in house.get("creativeTonies", []):
                url = editor_url(house.get("id"), target.get("id"))
                if url in seen:
                    raise RuntimeError("Duplicate Tonies target identity")
                seen.add(url)
                result.append({"name": target.get("name") or "Creative-Tonie", "image": target.get("imageUrl") or "", "edit_url": url})
        return result

    def _target_lock(self, ids):
        with self._locks_lock:
            return self._target_locks.setdefault(ids, RLock())

    def _readback(self, ids, expected, *, processing_ids, with_snapshot=False):
        deadline = time.monotonic() + self._poll_timeout
        for _ in range(40):
            target, config = self._snapshot(ids)
            if _matches_chapters(target["chapters"], expected, processing_ids):
                content = _content(target, config)
                return (content, copy.deepcopy(target["chapters"])) if with_snapshot else content
            if time.monotonic() >= deadline:
                break
            time.sleep(min(self._poll_interval, max(0, deadline - time.monotonic())))
        raise RuntimeError("Saved Tonies content was not confirmed; refresh before another action")

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
        if seconds > _number(target["secondsRemaining"], "remaining capacity"):
            raise RuntimeError("Not enough free Tonies capacity")
        if len(target["chapters"]) >= _number(config.get("maxChapters"), "chapter limit"):
            raise RuntimeError("Tonies chapter limit reached")
        if path.stat().st_size > _number(config.get("maxBytes"), "file size limit"):
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
            capacity = min(_number(config.get("maxSeconds"), "capacity"),
                           _number(before["secondsRemaining"], "remaining capacity")
                           + sum(_number(c["seconds"], "chapter duration") for c in chapters))
            desired, errors = apply_ops(chapters, ops, capacity_seconds=capacity,
                                        max_chapters=_number(config.get("maxChapters"), "chapter limit"))
            results = [{"error": error, "chapter_id": None} for error in errors]
            if _writable(desired) == _writable(chapters):
                return {"content": _content(before, config), "results": results}
            processing_ids = {c["id"] for c in chapters if _processing(c.get("transcoding"))}
            # The provider has no proven CAS/ETag contract; another device can still race this PATCH.
            saved = self._api("PATCH", f"/households/{ids[0]}/creativetonies/{ids[1]}", {"chapters": _writable(desired)})
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
            receipt = UndoReceipt(self, ids, _writable(confirmed), inverse_steps(chapters, ops, results),
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
            if not _matches_chapters(before["chapters"], receipt.expected, receipt.processing_ids):
                raise RuntimeError("Undo inventory changed; refresh the library")
            steps = [receipt.steps[index] for index in reversed(indices) if receipt.steps[index] is not None]
            if not steps or len(steps) != len(indices):
                raise RuntimeError("Undo is not available for this action")
            desired = apply_inverse(before["chapters"], cast(list[dict], steps))
            if _writable(desired) == _writable(before["chapters"]):
                return _content(before, config)
            self._api("PATCH", f"/households/{ids[0]}/creativetonies/{ids[1]}", {"chapters": _writable(desired)})
            try:
                still_processing = {c["id"] for c in before["chapters"] if _processing(c.get("transcoding"))}
                return cast(dict, self._readback(ids, desired, processing_ids=set(receipt.processing_ids) | still_processing))
            except RuntimeError:
                raise UnconfirmedWriteError([], None) from None

    @staticmethod
    def _confirm_attachments(saved, before, desired, processing_ids, results):
        """Map each attached upload to the chapter ID Tonies assigned, by position and title."""
        returned = _chapters(saved.get("chapters"))
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
        if not _matches_chapters(kept_returned, kept_desired, processing_ids):
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
        token = _TRACK.search(selected["title"])
        if app_track_token and (not token or token.group(1).lower() != app_track_token.lower()):
            raise RuntimeError("Chapter token does not match")
        if chapter_title and chapter_title not in (selected["title"], _TRACK.sub("", selected["title"]).strip()):
            raise RuntimeError("Chapter title does not match")
        if chapter_fingerprint:
            selected_content = _content(before, config)["chapters"][index]
            if chapter_fingerprint != f'{selected["title"]}|{selected_content["duration"]}':
                raise RuntimeError("Chapter fingerprint does not match")
        if not chapter_id and not app_track_token and chapter_title:
            matches = [c for c in chapters if chapter_title in (c["title"], _TRACK.sub("", c["title"]).strip())]
            if len(matches) != 1:
                raise RuntimeError("Chapter title is ambiguous; select by chapter ID")
        if not chapter_id and app_track_token:
            matches = [c for c in chapters if (match := _TRACK.search(c["title"])) and match.group(1).lower() == app_track_token.lower()]
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
