"""Wire-level SSE delivery for the browser's EventSource.onmessage handler."""
import asyncio
import json

from starlette.requests import Request

from app import main


def test_job_events_dispatch_to_default_message_handler_and_disconnect_cleanly(monkeypatch):
    event = {"id": 17, "kind": "job_updated", "job_id": "synthetic-job"}
    valid = iter([True, False])
    monkeypatch.setattr(main.auth, "session_valid", lambda _session: next(valid))
    monkeypatch.setattr(main, "get_events_since", lambda _last, timeout_sec: [event])
    active = main._sse_stats["active_connections"]
    disconnected = main._sse_stats["disconnects"]
    response = main.events(Request({"type": "http", "headers": []}), lastEventId=16)

    async def collect():
        chunks = [chunk async for chunk in response.body_iterator]
        return "".join(chunk if isinstance(chunk, str) else bytes(chunk).decode() for chunk in chunks)

    wire = asyncio.run(collect())
    assert "event:" not in wire, "Named job events bypass EventSource.onmessage"
    assert wire == f"id: 17\ndata: {json.dumps(event)}\n\n"
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no"
    assert main._sse_stats["active_connections"] == active
    assert main._sse_stats["disconnects"] == disconnected + 1
