"""Private, ephemeral inverse recipes. Never accept these from a request body.

Only rename, move and a confirmed new attachment have inverses. A recipe is not a
backup: deleted file references cannot establish retained/recoverable audio.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, TYPE_CHECKING

from .tonies_batch import apply_ops

if TYPE_CHECKING:
    from .actions import Action

DELETE_DELAY_SEC = 5.0
UNDO_TTL_SEC = 600.0
MAX_UNDO_TARGETS = 300


def target_key(url: str) -> tuple[str, str] | str:
    # Import lazily: the HTTP adapter also uses the private inverse helpers here.
    from .tonies_api import parse_target_url
    try:
        return parse_target_url(url)
    except RuntimeError:
        return url  # Preserve offline/in-process adapter contracts with opaque targets.


@dataclass
class UndoReceipt:
    owner: Any
    target: tuple[str, str]
    expected: list[dict]
    steps: list[dict | None]


@dataclass
class UndoGroup:
    actions: list[Action]
    owner: Any
    deadline: float
    expires_at: str
    pending: bool = False
    active: bool = True
    receipt: UndoReceipt | None = None
    indices: list[int] = field(default_factory=list)

    def available(self, now: float) -> bool:
        if now >= self.deadline or self.owner.closed:
            self.invalidate()
        return self.active and (self.pending or self.receipt is not None)

    def invalidate(self) -> None:
        self.active, self.receipt = False, None


class UndoStore:
    """One bounded, expiring submission per target, guarded by the queue condition.

    This class owns capability lifecycle only, not work scheduling or HTTP calls.
    All methods are called while the queue's condition is held.
    """
    def __init__(self):
        self.latest: dict[tuple[str, str] | str, UndoGroup] = {}

    def register(self, actions: list[Action], owner: Any, now: float) -> list[Action]:
        if not actions:
            return []
        key = target_key(actions[0].target_url)
        old = self.latest.pop(key, None)
        if old:
            old.invalidate()
        for stale_key, group in list(self.latest.items()):
            if now >= group.deadline:
                group.invalidate()
                del self.latest[stale_key]
        while len(self.latest) >= MAX_UNDO_TARGETS:
            self.latest.pop(next(iter(self.latest))).invalidate()
        pending = any(a.kind == "delete" for a in actions) and not any(a.kind == "clear" for a in actions)
        duration = DELETE_DELAY_SEC if pending else UNDO_TTL_SEC
        group = UndoGroup(actions, owner, now + duration, self._expires(duration), pending=pending)
        self.latest[key] = group
        for action in actions:
            action.undo_group, action.api_session = group, owner
            action.ready_at = group.deadline if pending else 0
        return old.actions if old else []

    def complete(self, batch: list[Action], receipt: UndoReceipt | None, *, has_pending: bool, now: float) -> None:
        group = self.latest.get(target_key(batch[0].target_url))
        if not group or not group.active or has_pending:
            return
        group.pending = False
        if not receipt or any(a.status != "done" or a not in batch for a in group.actions):
            group.invalidate()
            return
        indices = [batch.index(a) for a in group.actions]
        if any(receipt.steps[index] is None for index in indices):
            group.invalidate()
            return
        group.receipt, group.indices = receipt, indices
        group.deadline, group.expires_at = now + UNDO_TTL_SEC, self._expires(UNDO_TTL_SEC)

    def take(self, action: Action, owner: Any, now: float) -> tuple[UndoGroup, UndoReceipt | None]:
        group = self.latest.get(target_key(action.target_url))
        if not group or action not in group.actions or not group.available(now):
            raise ValueError("Undo is no longer available for this action")
        if group.owner is not owner:
            group.invalidate()
            raise RuntimeError("Undo account session changed; refresh the library")
        if group.pending and any(a.status != "queued" for a in group.actions):
            group.invalidate()
            raise ValueError("Undo deletion window has ended")
        receipt = group.receipt
        group.invalidate()  # Consume before a remote call, even if it fails/loses acknowledgement.
        return group, receipt

    def clear(self) -> None:
        for group in self.latest.values():
            group.invalidate()
        self.latest.clear()

    @staticmethod
    def _expires(seconds: float) -> str:
        return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def inverse_steps(before: list[dict], ops: list[dict], results: list[dict]) -> list[dict | None]:
    """Capture exact native fields/anchors before each successful operation."""
    work = copy.deepcopy(before)
    steps = []
    assigned = {op.get("file_id"): result["chapter_id"] for op, result in zip(ops, results)
                if op.get("type") == "attach" and result.get("chapter_id")}
    for op, result in zip(ops, results):
        inverse = None
        if not result.get("error"):
            kind = op.get("type")
            if kind in {"rename", "move"}:
                index = next((i for i, c in enumerate(work) if c["id"] == op.get("chapter_id")), op.get("index"))
                if not isinstance(index, int):
                    raise RuntimeError("Invalid inverse identity")
                selected = work[index]
                if kind == "rename":
                    inverse = {"type": "title", "chapter_id": selected["id"], "title": selected["title"]}
                else:
                    if index + 1 < len(work):
                        position = {"before": work[index + 1]["id"]}
                    elif index:
                        position = {"after": work[index - 1]["id"]}
                    else:
                        position = {"edge": "start"}
                    inverse = {"type": "move", "chapter_id": selected["id"], "position": position}
            elif kind == "attach" and result.get("chapter_id"):
                inverse = {"type": "delete", "chapter_id": result["chapter_id"]}
            work, _ = apply_ops(work, [op], capacity_seconds=float("inf"), max_chapters=float("inf"))
        if inverse:
            inverse["chapter_id"] = assigned.get(inverse["chapter_id"], inverse["chapter_id"])
            if "position" in inverse:
                inverse["position"] = {key: assigned.get(value, value) for key, value in inverse["position"].items()}
        steps.append(inverse)
    return steps


def apply_inverse(chapters: list[dict], steps: list[dict]) -> list[dict]:
    """Apply server-owned recipes to a *fresh* list, never restore a saved list."""
    work = copy.deepcopy(chapters)
    for step in steps:
        matches = [i for i, c in enumerate(work) if c["id"] == step["chapter_id"]]
        if len(matches) != 1:
            raise RuntimeError("Undo inventory changed; refresh the library")
        index = matches[0]
        if step["type"] == "title":
            work[index]["title"] = step["title"]
        elif step["type"] == "delete":
            work.pop(index)
        elif step["type"] == "move":
            selected = work.pop(index)
            position = step["position"]
            if position.get("edge") == "start":
                index = 0
            else:
                key = "before" if "before" in position else "after"
                anchors = [i for i, c in enumerate(work) if c["id"] == position[key]]
                if len(anchors) != 1:
                    raise RuntimeError("Undo position changed; refresh the library")
                index = anchors[0] + (key == "after")
            work.insert(index, selected)
        else:
            raise RuntimeError("Undo is not available for this action")
    return work
