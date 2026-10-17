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
from .logger import get_logger

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
    path: Path | None = None
    on_change: Callable[[Action], None] | None = None
    finished: threading.Event = field(default_factory=threading.Event)

    def public(self) -> dict[str, Any]:
        params = {k: v for k, v in self.params.items() if k not in ("file_id", "seconds")}
        if self.path is not None:
            params["filename"] = self.path.name
        return {"id": self.id, "target_url": self.target_url, "type": self.kind, "params": params,
                "status": self.status, "error": self.error, "chapter_id": self.chapter_id,
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
        self._workers: set[str] = set()
        self._transfers = ThreadPoolExecutor(transfer_workers, thread_name_prefix="tonies-transfer")

    # Submitting

    def submit(self, target_url: str, specs: list[dict]) -> list[Action]:
        """Queue library edits; each spec is ``{"type": ..., ...}`` as in ``tonies_batch``."""
        actions = [self._new(target_url, _edit_kind(spec), _edit_params(spec)) for spec in specs]
        with self._cond:
            for action in actions:
                self._actions[action.id] = action
            self._ensure_worker(target_url)
            self._cond.notify_all()
        self._publish(target_url, actions)
        return actions

    def submit_upload(self, target_url: str, path: Path, title: str, position: dict | None = None,
                      on_change: Callable[[Action], None] | None = None) -> Action:
        action = self._new(target_url, "upload", {"title": title, "position": clean_position(position)})
        action.path, action.on_change = Path(path), on_change
        with self._cond:
            self._actions[action.id] = action
            self._ensure_worker(target_url)
        self._publish(target_url, [action])
        self._transfers.submit(self._transfer, action)
        return action

    def cancel(self, action_id: str) -> bool:
        """Cancel an action that hasn't started; returns whether it was cancelled."""
        with self._cond:
            action = self._actions.get(action_id)
            if not action or action.status != "queued":
                return False
            self._finish(action, error="Cancelled")
        self._notify([action])
        return True

    # Reading

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
        if target_url not in self._workers:
            self._workers.add(target_url)
            threading.Thread(target=self._worker, args=(target_url,), daemon=True,
                             name=f"tonies-actions-{len(self._workers)}").start()

    def _transfer(self, action: Action) -> None:
        with self._cond:
            if action.status != "queued":
                return
            self._set(action, "transferring")
        self._notify([action])
        try:
            prepared = self._api().prepare_upload(action.path, action.target_url)
        except Exception as error:  # noqa: BLE001 - every failure is reported on the action
            with self._cond:
                self._finish(action, error=str(error))
                self._cond.notify_all()
        else:
            with self._cond:
                action.params.update(file_id=prepared["file_id"], seconds=prepared["seconds"])
                self._set(action, "ready")
                self._cond.notify_all()
        self._notify([action])

    def _ready_batch(self, target_url: str) -> list[Action]:
        """Edits that are queued, plus finished transfers whose earlier uploads are all done."""
        pending = sorted((a for a in self._actions.values() if a.target_url == target_url and a.status in WAITING),
                         key=lambda a: a.seq)
        batch, upload_blocked = [], False
        for action in pending:
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
                    self._cond.wait()
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
            outcome = self._api().apply(target_url, [a.op() for a in batch])
        except Exception as error:  # noqa: BLE001 - the whole write failed or is unconfirmed
            logger.warning("tonies batch failed size=%s error=%s", len(batch), error)
            with self._cond:
                for action in batch:
                    self._finish(action, error=str(error))
                self._prune()
            self._notify(batch)
            return
        content = outcome["content"]
        with self._cond:
            for action, result in zip(batch, outcome["results"]):
                action.chapter_id = result.get("chapter_id") or action.params.get("chapter_id")
                self._finish(action, error=result.get("error"), content=content)
            self._prune()
        logger.info("tonies batch applied size=%s failed=%s seconds=%.2f", len(batch),
                    sum(1 for a in batch if a.status == "failed"), time.monotonic() - started)
        self._notify(batch, content)

    def _set(self, action: Action, status: str) -> None:
        action.status, action.updated_at = status, events.now_iso()

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
        if content is not None:
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
