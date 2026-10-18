"""YouTube commands, download resource policy and exclusive audio publication."""
import logging
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import time
from threading import Lock
from uuid import uuid4

from .media_process import Trim, convert_to_mp3
from .subprocess_runner import ChildFailed, run_ytdlp as _run_ytdlp
from .youtube_source import is_playlist_url, normalize_youtube_input, parse_candidates

_SEARCH_TIMEOUT_SEC = 60
_DOWNLOAD_TIMEOUT_SEC = 600
_MAX_SEARCH_OUTPUT_BYTES = 1024 * 1024
PLAYLIST_MAX = 50  # videos listed from one playlist
_MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024
_MIN_YTDLP_SEARCH_SPACING_SEC = 0.0
_MIN_YTDLP_DOWNLOAD_SPACING_SEC = 0.0
# YouTube occasionally refuses one of several parallel downloads; a download has no side
# effects, so a plain failure (ChildFailed, not a timeout or size limit) is tried once more.
_DOWNLOAD_ATTEMPTS = 2
_RETRY_DELAY_SEC = 3.0
_search_cache: dict[str, tuple[float, list[dict]]] = {}
_last_ytdlp_call_at = 0.0
_call_lock = Lock()
logger = logging.getLogger(__name__)


def clear_search_cache() -> None:
    with _call_lock:
        _search_cache.clear()


def _throttle_ytdlp_calls(min_spacing_sec: float):
    global _last_ytdlp_call_at
    with _call_lock:
        now = time.time()
        wait = min_spacing_sec - (now - _last_ytdlp_call_at)
        if wait > 0:
            time.sleep(wait)
        _last_ytdlp_call_at = time.time()


def sanitize_filename(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", name).strip("_.")[:120] or "audio"


def _base_command(*, playlist: bool = False) -> list[str]:
    # Isolated Python ignores PYTHONPATH/user-site code. Configuration/plugins,
    # cookies and runtime component downloads are never opted into. Install the
    # matching yt-dlp-ejs wheel and Deno alongside yt-dlp; no runtime GitHub fetch.
    # Playlist listing is its own narrow mode: only the playlist page extractor.
    if playlist:
        scope = ["--use-extractors", "youtube:tab", "--yes-playlist", "--playlist-end", str(PLAYLIST_MAX)]
    else:
        scope = ["--use-extractors", "youtube,youtube:search", "--no-playlist"]
    return [
        sys.executable, "-I", "-m", "yt_dlp",
        "--ignore-config", "--no-plugin-dirs", "--no-remote-components", *scope,
        "--no-progress", "--no-warnings", "--no-cache-dir",
        "--retries", "0", "--fragment-retries", "0", "--extractor-retries", "0",
    ]


def _check_download_size(directory: Path) -> None:
    """Sample aggregate staging bytes; this is not an OS-level disk quota."""
    total = 0
    for index, path in enumerate(directory.rglob("*")):
        if index >= 100:
            raise RuntimeError("YouTube download size limit exceeded") from None
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode):
            raise RuntimeError("YouTube download produced an invalid file") from None
        total += info.st_size
        if total > _MAX_DOWNLOAD_BYTES:
            raise RuntimeError("YouTube download size limit exceeded") from None


def search_youtube(youtube_query: str, limit: int = 3) -> list[dict]:
    """Search results, one linked video, or the first PLAYLIST_MAX videos of a playlist."""
    text = normalize_youtube_input(youtube_query)
    playlist = is_playlist_url(text)
    raw_limit = PLAYLIST_MAX if playlist else max(1, min(int(limit), 20))
    target = text if text.startswith("https://") else f"ytsearch{raw_limit}:{text}"
    cmd = [*_base_command(playlist=playlist), "--dump-json", "--flat-playlist", "--socket-timeout", "12", "--", target]
    _throttle_ytdlp_calls(_MIN_YTDLP_SEARCH_SPACING_SEC)
    output = _run_ytdlp(cmd, operation="search", timeout=_SEARCH_TIMEOUT_SEC, max_output_bytes=_MAX_SEARCH_OUTPUT_BYTES)
    candidates, rejected = parse_candidates(output, raw_limit)
    logger.info("yt_search results=%d rejected_metadata=%d", len(candidates), rejected)
    return candidates


def _publish_audio(source: Path, out_dir: Path, base: str) -> Path:
    source.chmod(0o600)
    destination = out_dir / f"{base}.mp3"
    try:
        # Atomic, exclusive publication: don't overwrite a job or local symlink.
        os.link(source, destination)
    except FileExistsError:
        destination = out_dir / f"{base}_{uuid4().hex}.mp3"
        os.link(source, destination)
    return destination


def download_mp3(youtube_query_or_url: str, out_dir: Path, preferred_title: str | None = None,
                 trim: Trim | None = None) -> Path:
    """Download the original audio, then encode it once: levelled, trimmed if asked, MP3."""
    text = normalize_youtube_input(youtube_query_or_url)
    target = text if text.startswith("https://") else f"ytsearch1:{text}"
    base = sanitize_filename(preferred_title or "audio")
    for attempt in range(1, _DOWNLOAD_ATTEMPTS + 1):
        try:
            return _download_once(target, out_dir, base, trim)
        except ChildFailed:
            if attempt == _DOWNLOAD_ATTEMPTS:
                raise
            logger.info("yt_download retry attempt=%d", attempt + 1)
            time.sleep(_RETRY_DELAY_SEC)
    raise AssertionError("unreachable")


def _download_once(target: str, out_dir: Path, base: str, trim: Trim | None) -> Path:
    try:
        # A private operation directory per attempt prevents stale/concurrent-file reuse.
        with tempfile.TemporaryDirectory(prefix=".yt-audio-", dir=out_dir) as staging:
            directory = Path(staging)
            output_template = str(directory / f"{base}.%(ext)s")
            cmd = [
                *_base_command(), "-x", "--format", "bestaudio/best",
                "--concurrent-fragments", "4",
                "--socket-timeout", "20", "--js-runtimes", "deno",
                "--max-filesize", str(_MAX_DOWNLOAD_BYTES), "-o", output_template,
                "--", target,
            ]
            _throttle_ytdlp_calls(_MIN_YTDLP_DOWNLOAD_SPACING_SEC)
            _run_ytdlp(
                cmd, operation="download", timeout=_DOWNLOAD_TIMEOUT_SEC,
                resource_check=lambda: _check_download_size(directory),
            )
            _check_download_size(directory)
            matches = list(directory.iterdir())
            if len(matches) != 1 or matches[0].stat().st_size == 0:
                raise RuntimeError("Download command finished but no audio file found") from None
            converted = directory / ".converted.mp3"
            try:
                convert_to_mp3(matches[0], converted, max_bytes=_MAX_DOWNLOAD_BYTES, trim=trim)
            except ChildFailed:
                raise RuntimeError("Audio conversion failed") from None  # deterministic: not retried
            return _publish_audio(converted, out_dir, base)
    except OSError:
        raise ChildFailed("YouTube download failed") from None
