"""Offline native-media bounds at real upload, listing and Tonies entrypoints."""
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import wave

from fastapi.testclient import TestClient
import pytest

from app import credentials, main
from tonies_fake import URL

PROBE_BUDGET = 64 * 1024
SECRET = "synthetic-private-diagnostic"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(main.settings, "data_dir", tmp_path)
    monkeypatch.setattr(main.settings, "tonies_email", "")
    monkeypatch.setattr(main.settings, "tonies_password", "")
    monkeypatch.setattr(main, "reset_upload_session", lambda: None)
    monkeypatch.setattr(main, "_cmd_version", lambda _: "offline")

    def forbidden(*args, **kwargs):
        pytest.fail("Real network/worker activity forbidden")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(main, "start_worker", forbidden)
    credentials.lock_runtime()
    yield
    credentials.lock_runtime()


@pytest.fixture
def browser():
    # Real auth, no lifespan: no application worker is started.
    client = TestClient(main.app)
    result = client.post("/api/setup/init", json={
        "username": "synthetic", "app_password": "synthetic app password",
        "tonies_email": "synthetic@example.invalid", "tonies_password": "synthetic cloud password",
    })
    assert result.status_code == 200 and result.json()["ok"]
    assert client.get("/api/files").status_code == 200
    return client


@pytest.fixture
def native_child(monkeypatch):
    """Substitute only the executable; retain real pipes, groups, waits and EOF."""
    original = subprocess.Popen
    state = {"ffmpeg": "pass", "ffprobe": "pass", "calls": [], "processes": []}

    def launch(cmd, *args, **kwargs):
        if cmd[0] not in ("ffmpeg", "ffprobe"):
            return original(cmd, *args, **kwargs)
        state["calls"].append((cmd, kwargs.copy()))
        code = state[cmd[0]]
        if callable(code):
            code = code(cmd)
        assert isinstance(code, str)
        if cmd[0] == "ffmpeg":
            code = f"from pathlib import Path; Path({cmd[-1]!r}).write_bytes(b'audio'); " + code
        proc = original([sys.executable, "-c", code], *args, **kwargs)
        state["processes"].append(proc)
        return proc

    monkeypatch.setattr(subprocess, "Popen", launch)
    yield state
    for proc in state["processes"]:
        if proc.poll() is None:
            if proc.pid == os.getpgid(proc.pid):
                os.killpg(proc.pid, 9)
            else:
                proc.kill()
        proc.wait(timeout=5)


def probe_reply(cmd_json=True, *, padding=0, duration="1.25"):
    # Support the historical plain-text seam as well as the shared JSON probe.
    data = json.dumps({"format": {"duration": duration}}) if cmd_json else duration
    return f"import os; os.write(1, {data.encode()!r} + b' ' * {padding})"


def assert_reaped(state):
    assert state["processes"]
    for proc in state["processes"]:
        assert proc.poll() is not None
        with pytest.raises(ChildProcessError):
            os.waitpid(proc.pid, os.WNOHANG)


@pytest.mark.real_auth
def test_converter_discards_noisy_diagnostics_at_authenticated_upload(browser, native_child, tmp_path):
    native_child["ffmpeg"] = "import os; os.write(1, b'x'*262144); os.write(2, b'x'*262144)"
    response = browser.post("/api/files/upload", files={"file": ("synthetic.wav", b"synthetic", "audio/wav")})
    assert response.status_code == 200
    cmd, launch = native_child["calls"][0]
    assert cmd[0] == "ffmpeg"
    assert launch["stderr"] == subprocess.DEVNULL
    assert launch["stdout"] == subprocess.DEVNULL
    assert launch["stdin"] == subprocess.DEVNULL
    assert launch["start_new_session"] is True
    assert list((tmp_path / "uploads").iterdir()) == []
    assert len(list((tmp_path / "downloads").iterdir())) == 1
    assert_reaped(native_child)


@pytest.mark.real_auth
def test_library_probe_refuses_oversized_valid_json(browser, native_child, tmp_path):
    root = tmp_path / "downloads"
    root.mkdir(exist_ok=True)
    (root / "synthetic.mp3").write_bytes(b"audio")
    native_child["ffprobe"] = probe_reply(padding=PROBE_BUDGET) + "; import time; time.sleep(3)"
    started = time.monotonic()
    response = browser.get("/api/files")
    assert time.monotonic() - started < 2
    assert response.status_code == 200
    assert response.json()[0]["duration_sec"] is None
    assert_reaped(native_child)


def test_tonies_upload_probe_refuses_oversized_valid_output(cloud_api, native_child, tmp_path):
    cloud, api = cloud_api
    path = tmp_path / "synthetic.mp3"
    path.write_bytes(b"audio")
    # Both the historical scalar and shared JSON are valid positive durations.
    native_child["ffprobe"] = lambda cmd: probe_reply(
        cmd[cmd.index("-of") + 1] == "json", padding=PROBE_BUDGET) + "; import time; time.sleep(3)"
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="Could not measure audio duration with ffprobe"):
        api.prepare_upload(path, URL)
    assert time.monotonic() - started < 2
    assert cloud.calls == []
    assert_reaped(native_child)


@pytest.mark.parametrize("entry", ["library", "tonies"])
def test_probe_at_budget_accepts_duration_and_discards_stderr(entry, native_child, tmp_path, cloud_api):
    from app import media_files
    cloud, api = cloud_api
    path = tmp_path / "synthetic.mp3"
    path.write_bytes(b"audio")
    value = json.dumps({"format": {"duration": "1.25"}}).encode()
    def reply(cmd):
        data = value if cmd[cmd.index("-of") + 1] == "json" else b"1.25"
        return f"import os; os.write(2,b'x'*262144); os.write(1,{data!r}+b' '*{PROBE_BUDGET-len(data)})"

    native_child["ffprobe"] = reply
    if entry == "library":
        assert media_files._duration(path) == 1.25
    else:
        assert api.prepare_upload(path, URL)["seconds"] == 1.25
        assert len(cloud.storage) == 1
    cmd, launch = native_child["calls"][0]
    assert launch["stdout"] == subprocess.PIPE
    assert launch["stderr"] == subprocess.DEVNULL
    assert launch["stdin"] == subprocess.DEVNULL
    assert launch["start_new_session"] is True
    assert_reaped(native_child)


def wav_bytes():
    stream = io.BytesIO()
    with wave.open(stream, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\x00\x00" * 8000)
    return stream.getvalue()


@pytest.mark.real_auth
def test_real_wav_import_probes_and_malformed_concat_refusal(browser, cloud_api, tmp_path):
    cloud, api = cloud_api
    response = browser.post("/api/files/upload", files={"file": ("synthetic.wav", wav_bytes(), "audio/wav")})
    assert response.status_code == 200
    path = tmp_path / "downloads" / response.json()["filename"]
    assert path.stat().st_size > 0
    listed = browser.get("/api/files").json()
    assert len(listed) == 1 and listed[0]["duration_sec"] == pytest.approx(1, abs=0.1)
    assert api.prepare_upload(path, URL)["seconds"] == pytest.approx(1, abs=0.1)
    assert len(cloud.storage) == 1
    for payload in (b"not audio " + SECRET.encode(), f"ffconcat version 1.0\nfile '{path}'\n".encode()):
        refusal = browser.post("/api/files/upload", files={"file": ("invalid.wav", payload, "audio/wav")})
        assert refusal.status_code == 400
        assert SECRET not in refusal.text and str(tmp_path) not in refusal.text
        assert list((tmp_path / "uploads").iterdir()) == []
        assert list((tmp_path / "downloads").iterdir()) == [path]
    # The cloud duration entrypoint must enforce the same safe demuxer policy.
    manifest = tmp_path / "playlist.mp3"
    manifest.write_text(f"ffconcat version 1.0\nfile '{path}'\n")
    with pytest.raises(RuntimeError, match="Could not measure audio duration with ffprobe"):
        api.prepare_upload(manifest, URL)
    assert len(cloud.storage) == 1


@pytest.fixture
def short_deadline(monkeypatch):
    from app import media_process
    original = media_process.run_bounded
    observed = []

    def run(cmd, **kwargs):
        observed.append(kwargs["timeout"])
        return original(cmd, **{**kwargs, "timeout": 0.3})

    monkeypatch.setattr(media_process, "run_bounded", run)
    return observed


def invoke_probe(entry, path, api):
    if entry == "library":
        from app.media_files import _duration
        return _duration(path)
    return api.prepare_upload(path, URL)["seconds"]


@pytest.mark.parametrize("entry", ["library", "tonies"])
@pytest.mark.parametrize("mode", ["running", "closed_pipe", "descendant"])
def test_probe_deadline_covers_child_and_pipe_eof(entry, mode, native_child, short_deadline, tmp_path, cloud_api):
    _, api = cloud_api
    path = tmp_path / "synthetic.mp3"
    path.write_bytes(b"audio")
    pid_file = tmp_path / "descendant.pid"
    if mode == "descendant":
        if not hasattr(os, "fork"):
            pytest.skip("POSIX fork regression")
        code = (
            "import os,time; from pathlib import Path; pid=os.fork(); "
            f"Path({str(pid_file)!r}).write_text(str(pid)) if pid else time.sleep(5)"
        )
    else:
        code = "import os,time; " + ("os.close(1); " if mode == "closed_pipe" else "") + "time.sleep(5)"
    native_child["ffprobe"] = code
    started = time.monotonic()
    try:
        if entry == "library":
            assert invoke_probe(entry, path, api) is None
        else:
            with pytest.raises(RuntimeError, match="Could not measure audio duration with ffprobe"):
                invoke_probe(entry, path, api)
        assert time.monotonic() - started < 2
        assert short_deadline == [10 if entry == "library" else 30]
        assert_reaped(native_child)
        if mode == "descendant":
            pid = int(pid_file.read_text())
            proc_stat = Path(f"/proc/{pid}/stat")
            # Linux distinguishes a killed orphan (zombie) from a running child.
            # On other POSIX hosts the direct-child/EOF checks still run.
            if Path("/proc/self/stat").exists():
                deadline = time.monotonic() + 1
                while proc_stat.exists() and proc_stat.read_text().split()[2] != "Z" and time.monotonic() < deadline:
                    time.sleep(0.01)
                assert not proc_stat.exists() or proc_stat.read_text().split()[2] == "Z"
    finally:
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text()), 9)
            except ProcessLookupError:
                pass


@pytest.mark.real_auth
@pytest.mark.parametrize("failure", ["deadline", "exit", "size"])
def test_converter_failure_reaps_and_cleans_both_temporaries(failure, browser, native_child, tmp_path, short_deadline, monkeypatch):
    native_child["ffmpeg"] = {
        "deadline": "import time; time.sleep(5)",
        "exit": f"import os,sys; os.write(2,{SECRET.encode()!r}); sys.exit(7)",
        "size": "pass",
    }[failure]
    if failure == "size":
        monkeypatch.setattr(main.settings, "max_upload_bytes", 5)
    response = browser.post("/api/files/upload", files={"file": ("private.wav", b"data", "audio/wav")})
    assert response.status_code == (413 if failure == "size" else 400)
    assert SECRET not in response.text and str(tmp_path) not in response.text
    assert list((tmp_path / "uploads").iterdir()) == []
    assert list((tmp_path / "downloads").iterdir()) == []
    assert short_deadline == [240]
    assert_reaped(native_child)


@pytest.mark.parametrize("entry", ["library", "tonies"])
@pytest.mark.parametrize("payload", [b"invalid " + SECRET.encode(), b"[]", b'{"format":null}', b'{"format":{"duration":"nan"}}'])
def test_invalid_probe_output_is_sanitized(entry, payload, native_child, tmp_path, cloud_api):
    cloud, api = cloud_api
    path = tmp_path / "synthetic.mp3"
    path.write_bytes(b"audio")
    native_child["ffprobe"] = f"import os; os.write(1,{payload!r}); os.write(2,{SECRET.encode()!r})"
    if entry == "library":
        assert invoke_probe(entry, path, api) is None
    else:
        with pytest.raises(RuntimeError) as error:
            invoke_probe(entry, path, api)
        assert str(error.value) in {"Could not measure audio duration with ffprobe", "Audio duration must be positive"}
        assert error.value.__suppress_context__
        assert SECRET not in str(error.value)
    assert cloud.calls == []
    assert_reaped(native_child)
