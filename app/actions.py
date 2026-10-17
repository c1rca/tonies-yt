"""Queue of Tonies library actions, batched per Creative-Tonie.

Every delete, rename, move, clear and upload is queued here instead of hitting the API
on its own. Each Tonie has one worker that waits a moment for more actions to arrive,
then applies everything that is ready in one read, one write and one read-back
(``ToniesAPI.apply``). Uploads first send their audio to Tonies storage, up to
``TRANSFER_WORKERS`` at a time and in parallel with edits; they are attached in the
order they were queued.

Actions are referenced by chapter ID, not by row number, so edits queued against a list
that has since changed still hit the right track (or fail on their own, leaving the rest
of the batch intact).
"""
from __future__ import annotations

import copy
import itertools
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import events
from .tonies_batch import clean_position
from .tonies_api import UnconfirmedWriteError
from .logger import get_logger
from .undo import UndoGroup, UndoStore, target_key

logger = get_logger(__name__)

EDIT_KINDS = frozenset({"delete", "rename", "move", "clear"})
TRANSFER_WORKERS = 3
BATCH_WINDOW_SEC = 0.15
MAX_HISTORY = 300

# queued -> (transferring -> ready ->) applying -> done | failed
WAITING = frozenset({"queued", "transferring", "ready", "applying"})


@dataclass
class Action:
    id: str
    seq: int
    target_url: str
    kind: str
    params: dict[str, Any]
    status: str = "queued"
    error: str | None = None
    chapter_id: str | None = None
    created_at: str = field(default_factory=events.now_iso)
    updated_at: str = field(default_factory=events.now_iso)
    content: dict | None = None
    attachment_receipt: dict | None = None
    needs_refresh: bool = False
    path: Path | None = None
    on_change: Callable[[Action], None] | None = None
    finished: threading.Event = field(default_factory=threading.Event)
    undo_group: UndoGroup | None = field(default=None, repr=False)
    api_session: Any = field(default=None, repr=False)
    ready_at: float = 0

    def public(self) -> dict[str, Any]:
        undo_available = bool(self.undo_group and self.undo_group.available(time.monotonic()))
        params = {k: v for k, v in self.params.items() if k not in ("file_id", "seconds")}
        if self.path is not None:
            params["filename"] = self.path.name
        return {"id": self.id, "target_url": self.target_url, "type": self.kind, "params": params,
                "status": self.status, "error": self.error, "chapter_id": self.chapter_id,
                "attachment_receipt": copy.deepcopy(self.attachment_receipt), "needs_refresh": self.needs_refresh,
                "undo_available": undo_available,
                "undo_pending": bool(undo_available and self.undo_group and self.undo_group.pending),
                "undo_expires_at": self.undo_group.expires_at if undo_available and self.undo_group else None,
                "created_at": self.created_at, "updated_at": self.updated_at}

    def op(self) -> dict[str, Any]:
        if self.kind == "upload":
            return {"type": "attach", "file_id": self.params["file_id"], "title": self.params["title"],
                    "seconds": self.params["seconds"], "position": self.params.get("position")}
        return {"type": self.kind, **self.params}


class ActionQueue:
    def __init__(self, api: Callable[[], Any], *, transfer_workers: int = TRANSFER_WORKERS,
                 batch_window: float = BATCH_WINDOW_SEC):
        self._api = api
        self._batch_window = batch_window
        self._cond = threading.Condition()
        self._seq = itertools.count(1)
        self._actions: dict[str, Action] = {}
        self._workers: set[tuple[str, str] | str] = set()
        self._undo = UndoStore()
        self._undoing: set[tuple[str, str] | str] = set()
        # Newest library content produced per Tonie. A late notification may only publish
        # content that is still the newest, so a delayed forward result can never
        # overwrite the inverse (or a newer edit) that followed it.
        self._newest_content: dict[tuple[str, str] | str, object] = {}
        self._clock = time.monotonic
        self._transfers = ThreadPoolExecutor(transfer_workers, thread_name_prefix="tonies-transfer")

    # Submitting

    def submit(self, target_url: str, specs: list[dict]) -> list[Action]:
        """Queue library edits; each spec is ``{"type": ..., ...}`` as in ``tonies_batch``."""
        prepared = [(_edit_kind(spec), _edit_params(spec)) for spec in specs]
        if not prepared:
            return []
        api = self._api()
        with self._cond:
            if api.closed:
                raise RuntimeError("Tonies account session changed; submit again")
            actions = [self._new(target_url, kind, params) for kind, params in prepared]
            invalidated = self._undo.register(actions, api, self._clock())
            for action in actions:
                self._actions[action.id] = action
            self._ensure_worker(target_url)
            self._cond.notify_all()
        self._publish(target_url, invalidated + actions)
        return actions

    def submit_upload(self, target_url: str, path: Path, title: str, position: dict | None = None,
                      on_change: Callable[[Action], None] | None = None) -> Action:
        params = {"title": title, "position": clean_position(position)}
        api = self._api()
        with self._cond:
            if api.closed:
                raise RuntimeError("Tonies account session changed; submit again")
            action = self._new(target_url, "upload", params)
            action.path, action.on_change = Path(path), on_change
            invalidated = self._undo.register([action], api, self._clock())
            self._actions[action.id] = action
            self._ensure_worker(target_url)
        self._publish(target_url, invalidated + [action])
        self._transfers.submit(self._transfer, action)
        return action

    def cancel(self, action_id: str) -> bool:
        """Cancel an action that hasn't started; returns whether it was canceled."""
        with self._cond:
            action = self._actions.get(action_id)
            if not action or action.status != "queued":
                return False
            if action.undo_group:
                action.undo_group.invalidate()
            self._cancel(action)
            self._cond.notify_all()
        self._notify([action])
        return True

    # Reading

    def undo(self, action_id: str) -> list[Action]:
        """Consume the latest submission's server-owned capability, never a backup from a client."""
        api = self._api()  # Never acquire the account/session lock while holding the queue condition.
        with self._cond:
            action = self._actions.get(action_id)
            if action is None:
                raise ValueError("Undo action was not found")
            group, receipt = self._undo.take(action, api, self._clock())
            key = target_key(action.target_url)
            self._undoing.add(key)
            if receipt is not None:
                # From here on, no earlier content describes the Tonie, even if the inverse fails.
                self._newest_content[key] = object()
            if group.pending:
                for item in group.actions:
                    self._cancel(item)
        content = None
        try:
            if receipt is not None:
                content = api.undo(action.target_url, receipt, group.indices)
                with self._cond:
                    self._newest_content[key] = content
                    for item in group.actions:
                        item.content = content
                        self._set(item, "undone")
            return group.actions
        except Exception:
            with self._cond:
                for item in group.actions:
                    # Forward completion remains historical fact; inverse failure
                    # must not look like a new success or authorize another try.
                    item.error = "Undo unavailable or outcome unconfirmed; refresh the Tonie before another action"
                    item.needs_refresh = True
                    item.content = None
                    self._set(item, item.status)
            raise
        finally:
            with self._cond:
                self._undoing.discard(key)
                self._cond.notify_all()
            self._notify(group.actions, content)

    def reset(self) -> None:
        """Revoke ephemeral Undo and unapplied work on vault lock/account replacement."""
        with self._cond:
            revoked = [a for group in self._undo.latest.values() if group.active for a in group.actions]
            self._undo.clear()
            canceled = [a for a in self._actions.values() if a.status in WAITING and a.status != "applying"]
            for action in canceled:
                self._cancel(action)
            self._cond.notify_all()
        canceled_ids = {a.id for a in canceled}
        for action in revoked:
            if action.id not in canceled_ids:
                # Availability changed, not the historical job's completion.
                self._publish(action.target_url, [action])
        for action in canceled:
            self._notify([action])

    def get(self, action_id: str) -> Action | None:
        with self._cond:
            return self._actions.get(action_id)

    def wait(self, action_id: str, timeout: float) -> Action:
        action = self.get(action_id)
        if action is None:
            raise KeyError(action_id)
        action.finished.wait(timeout)
        return action

    def recent(self, target_url: str | None = None) -> list[dict]:
        with self._cond:
            return [a.public() for a in sorted(self._actions.values(), key=lambda a: a.seq)
                    if target_url in (None, a.target_url)]

    # Internals

    def _new(self, target_url: str, kind: str, params: dict) -> Action:
        if not isinstance(target_url, str) or not target_url:
            raise ValueError("target_url is required")
        return Action(id=uuid.uuid4().hex, seq=next(self._seq), target_url=target_url, kind=kind, params=params)

    def _ensure_worker(self, target_url: str) -> None:
        key = target_key(target_url)
        if key not in self._workers:
            self._workers.add(key)
            threading.Thread(target=self._worker, args=(target_url,), daemon=True,
                             name=f"tonies-actions-{len(self._workers)}").start()

    def _transfer(self, action: Action) -> None:
        with self._cond:
            if action.status != "queued":
                return
            self._set(action, "transferring")
        self._notify([action])
        try:
            api = self._api()
            if action.api_session is not None and action.api_session is not api:
                raise RuntimeError("Tonies account session changed; submit again")
            prepared = api.prepare_upload(action.path, action.target_url)
        except Exception as error:  # noqa: BLE001 - every failure is reported on the action
            with self._cond:
                if action.status != "canceled":
                    self._finish(action, error=str(error))
                self._cond.notify_all()
        else:
            with self._cond:
                if action.status != "canceled":
                    action.params.update(file_id=prepared["file_id"], seconds=prepared["seconds"])
                    self._set(action, "ready")
                self._cond.notify_all()
        self._notify([action])

    def _ready_batch(self, target_url: str) -> list[Action]:
        """Edits that are queued, plus finished transfers whose earlier uploads are all done."""
        key = target_key(target_url)
        if key in self._undoing:
            return []
        pending = sorted((a for a in self._actions.values() if target_key(a.target_url) == key and a.status in WAITING),
                         key=lambda a: a.seq)
        batch, upload_blocked = [], False
        for action in pending:
            if action.ready_at > self._clock():
                break  # A later transfer/edit must never wake a deferred delete early or overtake it.
            if action.kind != "upload":
                if action.status == "queued":
                    batch.append(action)
            elif action.status == "ready" and not upload_blocked:
                batch.append(action)
            else:
                upload_blocked = True  # keep attachments in the order they were queued
        return batch

    def _worker(self, target_url: str) -> None:
        while True:
            with self._cond:
                while not self._ready_batch(target_url):
                    deadlines = [a.ready_at for a in self._actions.values()
                                 if target_key(a.target_url) == target_key(target_url)
                                 and a.status == "queued" and a.ready_at > self._clock()]
                    self._cond.wait(max(0, min(deadlines) - self._clock()) if deadlines else None)
            time.sleep(self._batch_window)  # let quick successive clicks join this batch
            with self._cond:
                batch = self._ready_batch(target_url)
                for action in batch:
                    self._set(action, "applying")
            if batch:
                self._notify(batch)
                self._apply(target_url, batch)

    def _apply(self, target_url: str, batch: list[Action]) -> None:
        started = time.monotonic()
        try:
            api = self._api()
            if any(a.api_session is not None and a.api_session is not api for a in batch):
                raise RuntimeError("Tonies account session changed; submit again")
            outcome = api.apply(target_url, [a.op() for a in batch])
        except Exception as error:  # noqa: BLE001 - the whole write failed or is unconfirmed
            logger.warning("tonies batch failed size=%s error=%s", len(batch), error)
            with self._cond:
                for index, action in enumerate(batch):
                    if action.undo_group:
                        action.undo_group.invalidate()
                    result = error.results[index] if isinstance(error, UnconfirmedWriteError) else {}
                    action.chapter_id = result.get("chapter_id") or action.params.get("chapter_id")
                    if isinstance(error, UnconfirmedWriteError):
                        action.attachment_receipt = copy.deepcopy(error.attachment_receipt)
                        action.needs_refresh = error.needs_refresh
                    self._finish(action, error=result.get("error") or str(error))
                self._prune()
            self._notify(batch)
            return
        content = outcome["content"]
        with self._cond:
            self._newest_content[target_key(target_url)] = content
            for action, result in zip(batch, outcome["results"]):
                action.chapter_id = result.get("chapter_id") or action.params.get("chapter_id")
                self._finish(action, error=result.get("error"), content=content)
            self._undo.complete(batch, outcome.get("_undo"), has_pending=any(
                target_key(a.target_url) == target_key(target_url) and a.status in WAITING
                for a in self._actions.values()), now=self._clock())
            self._prune()
        logger.info("tonies batch applied size=%s failed=%s seconds=%.2f", len(batch),
                    sum(1 for a in batch if a.status == "failed"), time.monotonic() - started)
        self._notify(batch, content)

    def _set(self, action: Action, status: str) -> None:
        action.status, action.updated_at = status, events.now_iso()

    def _cancel(self, action: Action) -> None:
        action.error = None
        self._set(action, "canceled")
        action.finished.set()

    def _finish(self, action: Action, *, error: str | None, content: dict | None = None) -> None:
        action.error, action.content = error, content
        self._set(action, "failed" if error else "done")
        action.finished.set()

    def _prune(self) -> None:
        finished = sorted((a for a in self._actions.values() if a.status not in WAITING), key=lambda a: a.seq)
        for action in finished[:max(0, len(self._actions) - MAX_HISTORY)]:
            del self._actions[action.id]

    def _notify(self, actions: list[Action], content: dict | None = None) -> None:
        self._publish(actions[0].target_url, actions, content)
        for action in actions:
            if action.on_change:
                try:
                    action.on_change(action)
                except Exception:  # noqa: BLE001 - a listener must not break the queue
                    logger.exception("tonies action listener failed action=%s", action.id)

    def _publish(self, target_url: str, actions: list[Action], content: dict | None = None) -> None:
        with self._cond:
            payload = {"target_url": target_url, "actions": [a.public() for a in actions],
                       "pending": sum(1 for a in self._actions.values()
                                      if a.target_url == target_url and a.status in WAITING)}
            if content is not None and content is self._newest_content.get(target_key(target_url)):
                payload["content"] = content
        events.publish("tonies_actions", payload)


def _edit_kind(spec: dict) -> str:
    kind = spec.get("type") if isinstance(spec, dict) else None
    if kind not in EDIT_KINDS:
        raise ValueError(f"Unknown action type {kind!r}")
    return kind


def _edit_params(spec: dict) -> dict:
    kind = spec["type"]
    if kind == "clear":
        return {}
    chapter_id = spec.get("chapter_id")
    if not isinstance(chapter_id, str) or not chapter_id:
        raise ValueError(f"{kind} needs a chapter_id")
    params: dict[str, Any] = {"chapter_id": chapter_id}
    if kind == "rename":
        if not isinstance(spec.get("title"), str) or not spec["title"].strip():
            raise ValueError("rename needs a title")
        params["title"] = spec["title"]
    if kind == "move":
        if spec.get("position") is None:
            raise ValueError("move needs a position")
        params["position"] = clean_position(spec["position"])
    return params
