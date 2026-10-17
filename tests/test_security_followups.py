"""Follow-ups from the independent security review: version probes and static dot segments."""
import asyncio
import os
import time
from pathlib import Path

import pytest

from app import auth, main


def fake_tool(tmp_path, script: str) -> str:
    tool = tmp_path / "fake-tool"
    tool.write_text("#!/bin/sh\n" + script)
    tool.chmod(0o755)
    return str(tool)


def test_version_probe_never_reflects_stderr(tmp_path):
    tool = fake_tool(tmp_path, "echo 'private-diagnostic token=abc' >&2\nexit 1\n")
    assert main._cmd_version([tool, "--version"]) == "unknown"
    tool = fake_tool(tmp_path, "echo 'private-diagnostic' >&2\necho 'tool 1.2.3'\n")
    assert main._cmd_version([tool, "--version"]) == "tool 1.2.3"


def test_version_probe_output_is_bounded(tmp_path):
    tool = fake_tool(tmp_path, "head -c 100000 /dev/zero | tr '\\0' x\n")
    assert main._cmd_version([tool, "--version"]) == "unknown"


def test_version_probe_timeout_kills_descendants(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "VERSION_PROBE_TIMEOUT_SEC", 0.3)
    pid_file = tmp_path / "child.pid"
    tool = fake_tool(tmp_path, f"sleep 30 &\necho $! > {pid_file}\nsleep 30\n")
    started = time.monotonic()
    assert main._cmd_version([tool, "--version"]) == "unknown"
    assert time.monotonic() - started < 5
    pid = int(pid_file.read_text())
    stat = Path(f"/proc/{pid}/stat")
    deadline = time.monotonic() + 2
    while stat.exists() and stat.read_text().split()[2] != "Z" and time.monotonic() < deadline:
        time.sleep(0.05)
    alive = stat.exists() and stat.read_text().split()[2] != "Z"
    if alive:
        os.kill(pid, 9)
    assert not alive


@pytest.mark.parametrize("path", ["/static/assets/../index.html", "/static/assets/./../account.html",
                                  "/static/assets/x/../../index.html"])
def test_static_dot_segments_are_not_public(path):
    assert auth._is_public(path) is False


@pytest.mark.real_auth
def test_signed_out_dot_segment_page_request_is_refused():
    sent = []
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
             "scheme": "http", "path": "/static/assets/../index.html", "raw_path": b"/static/assets/../index.html",
             "query_string": b"", "headers": [(b"host", b"testserver")], "client": ("127.0.0.1", 1),
             "server": ("testserver", 80), "root_path": ""}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(main.app(scope, receive, send))
    assert sent[0]["status"] in (302, 303, 307, 401)
    assert b"<html" not in b"".join(m.get("body", b"") for m in sent[1:]).lower()
