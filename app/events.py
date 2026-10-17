"""Shared event stream (server-sent events) for jobs and Tonies actions."""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

MAX_EVENTS = 500

_cond = threading.Condition()
_seq = 0
_events: list[dict[str, Any]] = []
_stats: dict[str, int] = {"emitted": 0}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"


def publish(kind: str, payload: dict[str, Any]) -> None:
    global _seq
    with _cond:
        _seq += 1
        _events.append({"id": _seq, "kind": kind, "at": now_iso(), **payload})
        del _events[:-MAX_EVENTS]
        _stats["emitted"] += 1
        _stats[kind] = _stats.get(kind, 0) + 1
        _cond.notify_all()


def events_since(last_id: int, timeout_sec: float = 15.0) -> list[dict[str, Any]]:
    with _cond:
        if _seq <= last_id:
            _cond.wait(timeout=timeout_sec)
        return [e for e in _events if e["id"] > last_id]


def stats() -> dict[str, int]:
    with _cond:
        return {**_stats, "last_event_id": _seq, "buffered": len(_events)}
