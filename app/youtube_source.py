"""Pure YouTube source policy; never resolves hosts or executes a provider."""
import json
import re
from urllib.parse import urlsplit

MAX_INPUT_LENGTH = 1000
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}", re.ASCII)
_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "www.youtu.be"}
_SCHEME_PREFIX = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_BARE_HOST_PATH = re.compile(r"^[^\s/]+\.[^\s/]+/")
_INPUT_ERROR = "Enter a YouTube video link or plain search text (maximum 1000 characters)"


def _canonical_video_url(text: str) -> str:
    try:
        parts = urlsplit(text)
        # Comparing the entire authority rejects credentials, ports, subdomain
        # tricks, escaped hosts and trailing dots without repairing them.
        if parts.scheme not in {"http", "https"} or parts.netloc.lower() not in _YOUTUBE_HOSTS:
            raise ValueError
        if parts.netloc.lower() in {"youtu.be", "www.youtu.be"}:
            video_id = parts.path.removeprefix("/")
        elif parts.path == "/watch":
            ids = [item[2:] for item in parts.query.split("&") if item.startswith("v=")]
            if len(ids) != 1:
                raise ValueError
            video_id = ids[0]
        elif parts.path.startswith("/shorts/"):
            video_id = parts.path[len("/shorts/"):]
        else:
            raise ValueError
        if not VIDEO_ID.fullmatch(video_id):
            raise ValueError
    except ValueError:
        raise ValueError(_INPUT_ERROR) from None
    return f"https://www.youtube.com/watch?v={video_id}"


def normalize_youtube_input(value: str) -> str:
    """Return ordinary query text or a canonical, single-video YouTube URL.

    URL extras (including tracking, playlist and fragment fields) are discarded.
    A descriptive colon followed by a space remains ordinary search text.
    This is an input policy, not a transitive network/SSRF sandbox.
    """
    if not isinstance(value, str) or len(value) > MAX_INPUT_LENGTH or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(_INPUT_ERROR) from None
    text = value.strip()
    if not text or text.startswith(("-", "/", "\\", ".", "~")) or "\\" in text:
        raise ValueError(_INPUT_ERROR) from None
    if text.split("/", 1)[0].lower() in _YOUTUBE_HOSTS:
        return _canonical_video_url("https://" + text)
    scheme = _SCHEME_PREFIX.match(text)
    if scheme:
        rest = text[scheme.end():]
        if not rest.startswith(" ") or rest.lstrip().startswith("/"):
            return _canonical_video_url(text)
    if "://" in text or _BARE_HOST_PATH.match(text):
        return _canonical_video_url(text)
    return text


def parse_candidates(output: bytes, limit: int) -> tuple[list[dict], int]:
    """Copy bounded display metadata; reconstruct URLs only from valid IDs."""
    candidates: list[dict] = []
    rejected = 0
    for line in output.splitlines():
        try:
            row = json.loads(line)
        except (ValueError, UnicodeError, RecursionError):
            rejected += 1
            continue
        if not isinstance(row, dict):
            rejected += 1
            continue
        video_id = row.get("id")
        if not isinstance(video_id, str) or not VIDEO_ID.fullmatch(video_id):
            rejected += 1
            continue
        title = row.get("title")
        uploader = row.get("uploader")
        duration = row.get("duration")
        candidates.append({
            "id": video_id,
            "title": title[:500] if isinstance(title, str) and title else "Untitled",
            "uploader": uploader[:500] if isinstance(uploader, str) else "",
            "duration": duration if isinstance(duration, (int, float)) and 0 <= duration <= 86400 else 0,
            "url": f"https://www.youtube.com/watch?v={video_id}",
        })
        if len(candidates) >= limit:
            break
    return candidates, rejected
