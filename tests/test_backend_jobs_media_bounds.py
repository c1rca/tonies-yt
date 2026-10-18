"""Job-local duration checks use the shared native process policy, offline."""
import json
import os
import socket
import subprocess
import sys
import time
import wave

import pytest

from app import job_upload as jobs, media_process


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Jobs media tests forbid network")
    monkeypatch.setattr(socket.socket, "connect", denied)


@pytest.fixture
def native_child(monkeypatch):
    original = subprocess.Popen
    state = {"code": "pass", "calls": [], "processes": []}

    def launch(cmd, *args, **kwargs):
        assert cmd[0] == "ffprobe"
        state["calls"].append((cmd, kwargs.copy()))
        proc = original([sys.executable, "-c", state["code"]], *args, **kwargs)
        state["processes"].append(proc)
        return proc

    monkeypatch.setattr(subprocess, "Popen", launch)
    yield state
    for proc in state["processes"]:
        if proc.poll() is None:
            if os.getpgid(proc.pid) == proc.pid:
                os.killpg(proc.pid, 9)
            else:
                proc.kill()
        proc.wait(timeout=3)


def reaped(state):
    assert state["processes"]
    for proc in state["processes"]:
        assert proc.poll() is not None
        with pytest.raises(ChildProcessError):
            os.waitpid(proc.pid, os.WNOHANG)


def test_jobs_probe_rejects_oversized_otherwise_valid_json(native_child, tmp_path):
    payload = json.dumps({"format": {"duration": "1.25"}}).encode()
    native_child["code"] = f"import os; os.write(1,{payload!r}+b' '*65536)"
    with pytest.raises(RuntimeError, match="output limit exceeded"):
        jobs._probe_audio_duration_seconds(tmp_path / "synthetic.mp3")
    reaped(native_child)


def test_jobs_probe_exact_budget_positive_and_private_diagnostic_discard(native_child, tmp_path):
    payload = json.dumps({"format": {"duration": "1.25"}}).encode()
    native_child["code"] = f"import os; os.write(2,b'private '*40000); os.write(1,{payload!r}+b' '*{65536-len(payload)})"
    assert jobs._probe_audio_duration_seconds(tmp_path / "synthetic.mp3") == 1.25
    cmd, kwargs = native_child["calls"][0]
    assert kwargs["stdin"] == subprocess.DEVNULL and kwargs["stderr"] == subprocess.DEVNULL
    assert kwargs["stdout"] == subprocess.PIPE and kwargs["start_new_session"] is True
    assert cmd[cmd.index("-protocol_whitelist") + 1] == "file,pipe"
    assert "concat" not in cmd[cmd.index("-format_whitelist") + 1].split(",")
    reaped(native_child)


@pytest.mark.parametrize("payload", [b"{}", b'{"format":{}}', b'{"format":{"duration":null}}'])
def test_jobs_probe_preserves_missing_duration_as_zero(payload, native_child, tmp_path):
    native_child["code"] = f"import os; os.write(1,{payload!r})"
    assert jobs._probe_audio_duration_seconds(tmp_path / "synthetic.mp3") == 0.0
    reaped(native_child)


@pytest.mark.parametrize("payload", [b"invalid private", b"[]", b'{"format":null}', b'{"format":{"duration":"NaN"}}', b'{"format":{"duration":"inf"}}'])
def test_jobs_probe_malformed_or_nonfinite_output_is_sanitized(payload, native_child, tmp_path):
    native_child["code"] = f"import os; os.write(1,{payload!r}); os.write(2,b'private diagnostic')"
    with pytest.raises(RuntimeError) as caught:
        jobs._probe_audio_duration_seconds(tmp_path / "private.mp3")
    assert "private" not in str(caught.value) and str(tmp_path) not in str(caught.value)
    reaped(native_child)


def test_jobs_probe_nonzero_exit_does_not_reflect_stderr(native_child, tmp_path):
    native_child["code"] = "import os,sys; os.write(2,b'private diagnostic'); sys.exit(7)"
    with pytest.raises(RuntimeError) as caught:
        jobs._probe_audio_duration_seconds(tmp_path / "private.mp3")
    assert "private" not in str(caught.value)
    reaped(native_child)


@pytest.mark.parametrize("mode", ["running", "closed_pipe", "descendant"])
def test_jobs_probe_deadline_reaps_child_and_pipe_descendants(mode, native_child, monkeypatch, tmp_path):
    from app import subprocess_runner
    original = subprocess_runner.run_bounded
    observed = []

    def short(cmd, **kwargs):
        observed.append(kwargs["timeout"])
        return original(cmd, **{**kwargs, "timeout": 0.2})

    # Patch all shared launch seams, not the production duration behavior.
    monkeypatch.setattr(subprocess_runner, "run_bounded", short)
    monkeypatch.setattr(media_process, "run_bounded", short)
    if hasattr(jobs, "run_bounded"):
        monkeypatch.setattr(jobs, "run_bounded", short)
    pid_file = tmp_path / "child.pid"
    if mode == "descendant":
        native_child["code"] = (
            "import os,time; from pathlib import Path; pid=os.fork(); "
            f"Path({str(pid_file)!r}).write_text(str(pid)) if pid else time.sleep(1)"
        )
    else:
        native_child["code"] = "import os,time; " + ("os.close(1); " if mode == "closed_pipe" else "") + "time.sleep(1)"
    started = time.monotonic()
    try:
        with pytest.raises(RuntimeError, match="timed out"):
            jobs._probe_audio_duration_seconds(tmp_path / "synthetic.mp3")
        assert time.monotonic() - started < 0.9
        assert observed == [30]
        reaped(native_child)
        if pid_file.exists():
            stat = __import__("pathlib").Path(f"/proc/{int(pid_file.read_text())}/stat")
            until = time.monotonic() + 0.5
            while stat.exists() and stat.read_text().split()[2] != "Z" and time.monotonic() < until:
                time.sleep(0.01)
            assert not stat.exists() or stat.read_text().split()[2] == "Z"
    finally:
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text()), 9)
            except ProcessLookupError:
                pass


def test_jobs_probe_real_wav_and_malformed_concat_refusal(tmp_path):
    path = tmp_path / "synthetic.wav"
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\0\0" * 8000)
    assert jobs._probe_audio_duration_seconds(path) == pytest.approx(1.0)
    for payload in (b"private malformed", f"ffconcat version 1.0\nfile '{path}'\n".encode()):
        manifest = tmp_path / "synthetic.mp3"
        manifest.write_bytes(payload)
        with pytest.raises(RuntimeError) as caught:
            jobs._probe_audio_duration_seconds(manifest)
        assert "private" not in str(caught.value) and str(tmp_path) not in str(caught.value)
