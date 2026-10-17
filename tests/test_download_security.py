"""Offline trust-boundary regressions; no yt-dlp/provider network calls."""
import json
import logging
import subprocess
import sys
import time

import pytest

from app import downloader

VIDEO_ID = "AbCdEf_12-3"
URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"


@pytest.fixture
def child(monkeypatch):
    """Run a real local child in place of yt-dlp, preserving pipe/deadline behavior."""
    real_popen = subprocess.Popen
    state = {"calls": [], "code": "pass", "mp3": None}

    def launch(cmd, **kwargs):
        state["calls"].append((cmd, kwargs))
        if state["mp3"] is not None:
            from pathlib import Path
            output = Path(cmd[cmd.index("-o") + 1].replace("%(ext)s", "mp3"))
            output.write_bytes(state["mp3"])
        proc = real_popen([sys.executable, "-c", state["code"]], **kwargs)
        state["proc"] = proc
        return proc

    monkeypatch.setattr(subprocess, "Popen", launch)
    return state


@pytest.mark.parametrize("value", [
    "--exec=touch /somewhere", "-o /somewhere", "", "   ",
    "file:///etc/passwd", "/etc/passwd", "../private.mp3", "\\\\server\\private",
    "https://127.0.0.1/a", "http://169.254.169.254/latest/meta-data/",
    "https://example.com/video", "https://youtube.com.evil.test/watch?v=AbCdEf_12-3",
    "https://evil.youtube.com/watch?v=AbCdEf_12-3",
    "https://user:secret@www.youtube.com/watch?v=AbCdEf_12-3",
    "https://www.youtube.com:8443/watch?v=AbCdEf_12-3",
    "https://www.youtube.com:443/watch?v=AbCdEf_12-3",
    "ftp://www.youtube.com/watch?v=AbCdEf_12-3",
    "//www.youtube.com/watch?v=AbCdEf_12-3",
    "https://www.youtube.com/playlist?list=123",
    "https://www.youtube.com/redirect?q=https://127.0.0.1",
    "https://www.youtube.com/watch?v=short",
    "https://www.youtube.com/watch?v=AbCdEf_12-3extra",
    "https://www.youtube.com/watch?v=AbCdEf_12-3&v=AbCdEf_12-3",
    "https://youtu.be/AbCdEf_12-3/extra", "https://youtube.com/shorts/AbCdEf_12-3/extra",
    "https://www.youtube.com/watch?v=AbCdEf%5f12-3",
    "bedtime\n--exec evil", "a" * 1001,
    "ytsearch20:private", "https://www.youtube.com\\@example.com/watch?v=AbCdEf_12-3",
])
@pytest.mark.parametrize("operation", ["search", "download"])
def test_invalid_source_refused_before_subprocess(value, operation, child, tmp_path):
    with pytest.raises(ValueError, match="YouTube"):
        if operation == "search":
            downloader.search_youtube(value)
        else:
            downloader.download_mp3(value, tmp_path)
    assert child["calls"] == []


@pytest.mark.parametrize("value", [
    URL, f"https://youtu.be/{VIDEO_ID}?si=private-tracking",
    f"https://m.youtube.com/shorts/{VIDEO_ID}?feature=share",
    f"http://youtube.com/watch?v={VIDEO_ID}&list=ignored&t=30#fragment",
    f"www.youtube.com/watch?v={VIDEO_ID}",
])
def test_supported_urls_are_canonical_single_video_targets(value, child):
    downloader.search_youtube(value)
    cmd = child["calls"][0][0]
    assert cmd[-2:] == ["--", URL]


def test_plain_search_is_data_and_commands_disable_ambient_code(child):
    downloader.search_youtube("children's bedtime songs", limit=3)
    cmd = child["calls"][0][0]
    assert cmd[-2:] == ["--", "ytsearch3:children's bedtime songs"]
    for flag in ("--ignore-config", "--no-plugin-dirs", "--no-remote-components", "--no-playlist"):
        assert flag in cmd
    assert cmd[cmd.index("--use-extractors") + 1] == "youtube,youtube:search"
    assert "--enable-file-urls" not in cmd
    assert not any("cookies" in arg or "ejs:github" in arg for arg in cmd)


def test_untrusted_result_urls_are_replaced_and_invalid_ids_ignored(child, caplog):
    payload = [
        {"id": VIDEO_ID, "title": "Good", "url": "http://127.0.0.1/private"},
        {"id": "../private", "title": "Bad", "url": URL},
        {"id": None, "url": URL},
        ["not", "a", "record"],
    ]
    child["code"] = f"print({chr(10).join(json.dumps(row) for row in payload)!r})"
    with caplog.at_level(logging.INFO, logger=downloader.__name__):
        results = downloader.search_youtube("private-search-secret")
    assert len(results) == 1
    assert results[0]["url"] == URL
    assert "private-search-secret" not in caplog.text


@pytest.mark.parametrize("operation", ["search", "download"])
def test_child_failure_does_not_publish_command_output_or_context(child, tmp_path, operation):
    child["code"] = "import sys; print('private-output'); print('cookie=private-secret', file=sys.stderr); sys.exit(1)"
    with pytest.raises(RuntimeError) as error:
        if operation == "search":
            downloader.search_youtube("private-query")
        else:
            downloader.download_mp3(URL + "&si=private-tracker", tmp_path, "private-title")
    assert str(error.value) in {"YouTube search failed", "YouTube download failed"}
    assert error.value.__suppress_context__
    assert "private" not in str(error.value)
    assert str(tmp_path) not in str(error.value)


def test_search_output_is_bounded_and_child_reaped(child, monkeypatch):
    monkeypatch.setattr(downloader, "_MAX_SEARCH_OUTPUT_BYTES", 100, raising=False)
    child["code"] = "import os,time; os.write(1, b'x' * 101); time.sleep(5)"
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="YouTube search output limit exceeded"):
        downloader.search_youtube("bedtime")
    assert time.monotonic() - started < 2
    assert child["proc"].poll() is not None


@pytest.mark.parametrize("operation", ["search", "download"])
def test_deadline_kills_and_reaps_child(child, monkeypatch, tmp_path, operation):
    monkeypatch.setattr(downloader, "_SEARCH_TIMEOUT_SEC", 0.1, raising=False)
    monkeypatch.setattr(downloader, "_DOWNLOAD_TIMEOUT_SEC", 0.1, raising=False)
    child["code"] = "import time; time.sleep(0.4)"
    with pytest.raises(RuntimeError, match=f"YouTube {operation} timed out"):
        if operation == "search":
            downloader.search_youtube("bedtime")
        else:
            downloader.download_mp3(URL, tmp_path)
    assert child["proc"].poll() is not None


def test_download_search_is_single_result_with_size_limit_and_clean_template(child, tmp_path):
    with pytest.raises(RuntimeError, match="no MP3 file found"):
        downloader.download_mp3("bedtime story", tmp_path, "../../ %(title)s")
    cmd, kwargs = child["calls"][0]
    assert cmd[-2:] == ["--", "ytsearch1:bedtime story"]
    assert cmd[cmd.index("--max-filesize") + 1] == str(downloader._MAX_DOWNLOAD_BYTES)
    assert "--no-playlist" in cmd
    assert "--no-remote-components" in cmd
    assert "--no-plugin-dirs" in cmd
    assert "--ignore-config" in cmd
    assert cmd[cmd.index("--format") + 1] == "bestaudio/best"
    assert "%(title)" not in cmd[cmd.index("-o") + 1]
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["stderr"] == subprocess.DEVNULL
    assert kwargs["stdout"] == subprocess.DEVNULL
    assert kwargs["start_new_session"] is True


def test_valid_download_publishes_new_audio_without_overwriting_existing(child, tmp_path):
    stale = tmp_path / "Song.mp3"
    stale.write_bytes(b"old-audio")
    child["mp3"] = b"synthetic-new-audio"
    result = downloader.download_mp3(URL, tmp_path, "Song")
    assert result.parent == tmp_path
    assert result != stale
    assert result.read_bytes() == b"synthetic-new-audio"
    assert stale.read_bytes() == b"old-audio"
    assert result.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob(".yt-audio-*"))
    cmd = child["calls"][0][0]
    assert cmd[-2:] == ["--", URL]


def test_aggregate_staging_size_is_checked_and_partial_files_removed(child, tmp_path, monkeypatch):
    monkeypatch.setattr(downloader, "_MAX_DOWNLOAD_BYTES", 10)
    child["mp3"] = b"x" * 11
    child["code"] = "import time; time.sleep(1)"
    with pytest.raises(RuntimeError, match="YouTube download size limit exceeded"):
        downloader.download_mp3(URL, tmp_path)
    assert child["proc"].poll() is not None
    assert list(tmp_path.iterdir()) == []


def test_staging_entry_count_is_bounded(child, tmp_path, monkeypatch):
    def many_directories(cmd, **kwargs):
        from pathlib import Path
        directory = Path(cmd[cmd.index("-o") + 1]).parent
        for index in range(101):
            (directory / str(index)).mkdir()
        return b""

    monkeypatch.setattr(downloader, "_run_ytdlp", many_directories)
    with pytest.raises(RuntimeError, match="YouTube download size limit exceeded"):
        downloader.download_mp3(URL, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_no_symlink_output_is_published(child, tmp_path, monkeypatch):
    private = tmp_path / "private.mp3"
    private.write_bytes(b"do-not-publish")

    def link_output(cmd, **kwargs):
        from pathlib import Path
        output = Path(cmd[cmd.index("-o") + 1].replace("%(ext)s", "mp3"))
        output.symlink_to(private)
        return b""

    monkeypatch.setattr(downloader, "_run_ytdlp", link_output)
    with pytest.raises(RuntimeError, match="YouTube download produced an invalid file"):
        downloader.download_mp3(URL, tmp_path)
    assert list(tmp_path.iterdir()) == [private]


@pytest.mark.parametrize("query", ["Story: bedtime", "AC/DC lullaby", "雪だるま bedtime", "a.b song"])
def test_ordinary_search_punctuation_is_preserved(query, child):
    downloader.search_youtube(query)
    assert child["calls"][0][0][-1] == f"ytsearch3:{query}"


def test_pipe_eof_does_not_disable_search_deadline(child, monkeypatch):
    monkeypatch.setattr(downloader, "_SEARCH_TIMEOUT_SEC", 0.1)
    child["code"] = "import os,time; os.close(1); time.sleep(0.4)"
    with pytest.raises(RuntimeError, match="YouTube search timed out"):
        downloader.search_youtube("bedtime")
    assert child["proc"].poll() is not None


def test_deadline_covers_descendants_holding_metadata_pipe(child, monkeypatch, tmp_path):
    import os
    from pathlib import Path

    monkeypatch.setattr(downloader, "_SEARCH_TIMEOUT_SEC", 0.3)
    pid_file = tmp_path / "descendant.pid"
    child["code"] = (
        "import os,time; from pathlib import Path; pid=os.fork(); "
        f"Path({str(pid_file)!r}).write_text(str(pid)) if pid else time.sleep(5)"
    )
    try:
        with pytest.raises(RuntimeError, match="YouTube search timed out"):
            downloader.search_youtube("bedtime")
        pid = int(pid_file.read_text())
        assert pid > 0
        proc_stat = Path(f"/proc/{pid}/stat")
        deadline = time.monotonic() + 1
        while proc_stat.exists() and proc_stat.read_text().split()[2] != "Z" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not proc_stat.exists() or proc_stat.read_text().split()[2] == "Z"
        assert child["proc"].poll() is not None
    finally:
        # A failed regression must not leak the synthetic grandchild.
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text()), 9)
            except ProcessLookupError:
                pass


def test_installed_ytdlp_accepts_policy_flags_without_network():
    from yt_dlp import parse_options

    parsed = parse_options(downloader._base_command()[4:] + ["--", URL])
    assert parsed.ydl_opts.get("allowed_extractors") == ["youtube", "youtube:search"]
    assert parsed.ydl_opts.get("remote_components") == []
    assert parsed.ydl_opts.get("enable_file_urls") is False
    assert parsed.ydl_opts.get("cookiesfrombrowser") is None
    assert parsed.ydl_opts.get("cookiefile") is None
    assert parsed.options.exec_cmd == {}
    assert parsed.options.plugin_dirs == []


def test_ambient_ytdlp_config_cannot_enable_exec_plugins_or_browser_cookies(tmp_path, monkeypatch):
    from yt_dlp import parse_options

    config_dir = tmp_path / "yt-dlp"
    config_dir.mkdir()
    (config_dir / "config").write_text(
        "--exec 'echo synthetic-command'\n--cookies-from-browser firefox\n--plugin-dirs synthetic-plugins\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["yt-dlp"])
    control = parse_options()
    assert control.options.exec_cmd
    assert control.ydl_opts.get("cookiesfrombrowser") is not None

    monkeypatch.setattr(sys, "argv", ["yt-dlp", *downloader._base_command()[4:]])
    protected = parse_options()
    assert protected.options.exec_cmd == {}
    assert protected.ydl_opts.get("cookiesfrombrowser") is None
    assert protected.options.plugin_dirs == []
