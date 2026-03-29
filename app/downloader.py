import subprocess
import sys
from pathlib import Path
import re
import json
import time
import logging
from threading import Lock
from .config import settings


# No artificial yt-dlp throttling; run as fast as possible.
_MIN_YTDLP_SEARCH_SPACING_SEC = 0.0
_MIN_YTDLP_DOWNLOAD_SPACING_SEC = 0.0
_SEARCH_CACHE_TTL_SEC = 0
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


def _cookie_args() -> list[str]:
    # Cookies disabled by request.
    return []


def sanitize_filename(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", name).strip("_")[:120] or "audio"


def search_youtube(youtube_query: str, limit: int = 3) -> list[dict]:
    t0 = time.time()
    key = " ".join((youtube_query or "").lower().split())
    now = time.time()

    raw_limit = max(1, min(limit, 20))
    cmd = [
        sys.executable,
        "-m", "yt_dlp",
        "--dump-json",
        "--flat-playlist",
        "--socket-timeout", "12",
        # Keep search path lean/fast; JS runtime remote components are mainly needed for extraction/download.
        *_cookie_args(),
        f"ytsearch{raw_limit}:{youtube_query}",
    ]

    _throttle_ytdlp_calls(_MIN_YTDLP_SEARCH_SPACING_SEC)
    proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
    lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]

    candidates: list[dict] = []
    for ln in lines:
        try:
            j = json.loads(ln)
            vid = j.get("id")
            title = j.get("title") or "Untitled"
            url = j.get("url") or (f"https://www.youtube.com/watch?v={vid}" if vid else None)
            if not url:
                continue
            candidates.append({
                "id": vid,
                "title": title,
                "uploader": j.get("uploader") or "",
                "duration": j.get("duration") or 0,
                "url": url,
            })
        except Exception:
            continue

    out = candidates[:limit]
    logger.info("yt_search query=%r limit=%s cache=disabled results=%s duration_ms=%d", youtube_query, limit, len(out), int((time.time() - t0) * 1000))
    return out


def download_mp3(youtube_query_or_url: str, out_dir: Path, preferred_title: str = None) -> Path:
    base = sanitize_filename(preferred_title or youtube_query_or_url)
    output_template = str(out_dir / f"{base}.%(ext)s")

    # Strict single-attempt download (no retry/fallback here).
    cmd = [
        sys.executable,
        "-m", "yt_dlp",
        "-x",
        "--format", "bestaudio/best",
        "--concurrent-fragments", "4",
        "--audio-format", "mp3",
        "--socket-timeout", "20",
        "--js-runtimes", "deno",
        "--remote-components", "ejs:github",
        *_cookie_args(),
        "-o", output_template,
        youtube_query_or_url,
    ]

    _throttle_ytdlp_calls(_MIN_YTDLP_DOWNLOAD_SPACING_SEC)
    try:
        proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        out = (e.stdout or "") + "\n" + (e.stderr or "")
        raise RuntimeError(
            f"yt-dlp failed for URL/query: {youtube_query_or_url}. Command: {' '.join(cmd)}. Error: exit={e.returncode}; yt-dlp output: {out[-900:]}"
        )

    matches = sorted(out_dir.glob(f"{base}*.mp3"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not matches:
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        raise RuntimeError(f"Download command finished but no MP3 file found. yt-dlp output: {out[-700:]}")
    return matches[0]
