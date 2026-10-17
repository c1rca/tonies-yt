"""Plan a batch of library edits against one Creative-Tonie's chapter list.

Pure functions, no I/O. The action queue collects every edit queued for a Tonie, and
``apply_ops`` turns them into one desired chapter list, so a whole batch costs one
read, one write and one read-back instead of a round trip per edit.

Ops (dicts, applied in order, each against the result of the ones before it):
- ``{"type": "delete", "chapter_id": ...}``
- ``{"type": "rename", "chapter_id": ..., "title": ...}``
- ``{"type": "move", "chapter_id": ..., "position": ...}``
- ``{"type": "attach", "file_id": ..., "title": ..., "seconds": ..., "position": ...}``
- ``{"type": "clear"}``

``delete``/``rename``/``move`` may name the chapter by ``index`` instead of ``chapter_id``
(older clients), and may add ``expect_index`` to refuse if the chapter has moved.

A position is ``{"edge": "start" | "end"}``, ``{"before": chapter_id}``,
``{"after": chapter_id}`` or ``{"index": n}``. An anchor that has gone (for example
deleted earlier in the same batch) falls back to ``index`` when one is given.

A failing op is skipped with its error; the others still apply.
"""
from __future__ import annotations

import copy
import re

_TRACK = re.compile(r"\s\[(?:oc:)?([a-f0-9]{4,12})\]$", re.I)
ATTACH_MARK = "_attach"


class OpError(Exception):
    pass


def clean_position(value) -> dict | None:
    """Validate a client-supplied position; raises ValueError."""
    if value is None:
        return None
    if not isinstance(value, dict) or not value or set(value) - {"edge", "before", "after", "index"}:
        raise ValueError("Invalid position")
    if "edge" in value and value["edge"] not in ("start", "end"):
        raise ValueError("Invalid position edge")
    for key in ("before", "after"):
        if key in value and (not isinstance(value[key], str) or not value[key]):
            raise ValueError(f"Invalid position {key}")
    if "index" in value and (not isinstance(value["index"], int) or isinstance(value["index"], bool)
                             or value["index"] < 0):
        raise ValueError("Invalid position index")
    return dict(value)


def renamed_title(current: str, requested: str) -> str:
    """Rename while keeping the app's hidden track token, so the chapter stays recognisable."""
    clean = _TRACK.sub("", str(requested or "")).strip()
    if not clean:
        raise OpError("Title cannot be empty")
    token = _TRACK.search(str(current or ""))
    return f"{clean} [oc:{token.group(1).lower()}]" if token else clean


def _find(work: list[dict], chapter_id: str) -> int:
    matches = [i for i, chapter in enumerate(work) if chapter["id"] == chapter_id]
    if len(matches) != 1:
        raise OpError("Track is no longer on this Tonie; refresh the library")
    return matches[0]


def _target(work: list[dict], op: dict) -> int:
    chapter_id, index = op.get("chapter_id"), op.get("index")
    if chapter_id:
        found = _find(work, chapter_id)
    elif isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(work):
        found = index
    else:
        raise OpError("Invalid track position; refresh the library")
    expected = op.get("expect_index")
    if expected is not None and expected != found:
        raise OpError("Track position is stale; refresh the library")
    return found


def _insertion_index(work: list[dict], position: dict | None, *, strict_index: bool = False) -> int:
    """Index to insert at in ``work`` (which no longer contains the chapter being placed)."""
    position = position or {"edge": "end"}
    if not isinstance(position, dict):
        raise OpError("Invalid position")
    edge = position.get("edge")
    if edge == "start":
        return 0
    if edge == "end":
        return len(work)
    for key, offset in (("before", 0), ("after", 1)):
        anchor = position.get(key)
        if anchor:
            matches = [i for i, chapter in enumerate(work) if chapter["id"] == anchor]
            if len(matches) == 1:
                return matches[0] + offset
            if "index" not in position:
                raise OpError("Drop position no longer exists; try again")
    index = position.get("index")
    if not isinstance(index, int) or isinstance(index, bool):
        raise OpError("Invalid position")
    if strict_index and not 0 <= index <= len(work):
        raise OpError("Invalid track position; refresh the library")
    return max(0, min(len(work), index))


def apply_ops(chapters: list[dict], ops: list[dict], *, capacity_seconds: float,
              max_chapters: float) -> tuple[list[dict], list[str | None]]:
    """Return the desired chapter list and one error (or None) per op."""
    work = copy.deepcopy(chapters)
    existing_ids = {chapter["id"] for chapter in chapters} | {chapter.get("file") for chapter in chapters}
    attached_titles: set[str] = set()
    errors: list[str | None] = []
    for number, op in enumerate(ops):
        candidate = copy.deepcopy(work)
        try:
            _apply_one(candidate, op, number, existing_ids, attached_titles, capacity_seconds, max_chapters)
        except OpError as error:
            errors.append(str(error))
            continue
        work = candidate
        errors.append(None)
    return work, errors


def _apply_one(work, op, number, existing_ids, attached_titles, capacity_seconds, max_chapters):
    kind = op.get("type")
    if kind == "clear":
        work.clear()
    elif kind == "delete":
        work.pop(_target(work, op))
    elif kind == "rename":
        index = _target(work, op)
        work[index]["title"] = renamed_title(work[index]["title"], op.get("title", ""))
    elif kind == "move":
        chapter = work.pop(_target(work, op))
        work.insert(_insertion_index(work, op.get("position"), strict_index=True), chapter)
    elif kind == "attach":
        file_id, title = op.get("file_id"), op.get("title")
        if not isinstance(file_id, str) or not file_id or file_id in existing_ids:
            raise OpError("Upload allocation collides with existing content")
        if not isinstance(title, str) or not title or title in attached_titles:
            raise OpError("Upload title must be unique in a batch")
        if len(work) + 1 > max_chapters:
            raise OpError("Tonies chapter limit reached")
        seconds = float(op.get("seconds") or 0)
        if sum(float(chapter.get("seconds") or 0) for chapter in work) + seconds > capacity_seconds + 1:
            raise OpError("Not enough free Tonies capacity")
        chapter = {"id": file_id, "file": file_id, "title": title, "seconds": seconds, "type": "file",
                   ATTACH_MARK: number}
        work.insert(_insertion_index(work, op.get("position")), chapter)
        attached_titles.add(title)
    else:
        raise OpError(f"Unknown action {kind!r}")
