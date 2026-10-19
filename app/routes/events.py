"""Server-sent job events, connection statistics, and diagnostic log routes."""
import json
import logging
from collections import deque

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, StreamingResponse

from .. import auth
from ..config import settings
from ..events import events_since, stats
from ..job_store import get_queue_stats

router = APIRouter()
# Keep the pre-router logger name so log output stays unchanged.
logger = logging.getLogger("app.main")
_sse_stats = {
    "active_connections": 0,
    "total_connections": 0,
    "disconnects": 0,
}


@router.get('/api/events')
def events(request: Request, lastEventId: int = 0):
    last_id = int(lastEventId or 0)
    session = auth.request_session(request)
    _sse_stats["total_connections"] += 1
    _sse_stats["active_connections"] += 1
    logger.info("sse connect active=%s total=%s lastEventId=%s", _sse_stats["active_connections"], _sse_stats["total_connections"], last_id)

    def gen():
        nonlocal last_id
        try:
            while auth.session_valid(session):
                events_batch = events_since(last_id, timeout_sec=15.0)
                if not events_batch:
                    yield ": keepalive\n\n"
                    continue

                for evt in events_batch:
                    last_id = max(last_id, int(evt.get("id", 0)))
                    # Unnamed events reach EventSource.onmessage in every app page.
                    yield f"id: {last_id}\n"
                    yield f"data: {json.dumps(evt)}\n\n"
        except GeneratorExit:
            pass
        finally:
            _sse_stats["active_connections"] = max(0, _sse_stats["active_connections"] - 1)
            _sse_stats["disconnects"] += 1
            logger.info("sse disconnect active=%s disconnects=%s", _sse_stats["active_connections"], _sse_stats["disconnects"])

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)


@router.get('/api/events/stats')
def events_stats():
    return {
        "sse": dict(_sse_stats),
        "jobs": stats(),
        "queues": get_queue_stats(),
    }


@router.get('/api/logs')
def api_logs(lines: int = 300):
    lines = max(10, min(lines, 2000))
    if not settings.log_file.exists():
        return {"lines": [], "file": str(settings.log_file), "level": settings.log_level}

    with settings.log_file.open('r', encoding='utf-8', errors='replace') as f:
        tail = list(deque(f, maxlen=lines))

    return {
        "lines": [x.rstrip('\n') for x in tail],
        "file": str(settings.log_file),
        "level": settings.log_level,
    }


@router.get('/api/logs/export')
def api_logs_export(lines: int = 2000):
    lines = max(10, min(lines, 10000))
    if not settings.log_file.exists():
        content = ""
    else:
        with settings.log_file.open('r', encoding='utf-8', errors='replace') as f:
            tail = list(deque(f, maxlen=lines))
        content = "".join(tail)

    headers = {
        "Content-Disposition": 'attachment; filename="tonies-yt-logs.txt"'
    }
    return PlainTextResponse(content=content, headers=headers)
