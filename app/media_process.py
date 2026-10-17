"""Shared native-media execution policy (POSIX process groups and bounded IPC)."""
import json
from pathlib import Path

from .subprocess_runner import run_bounded

# File/pipe alone is insufficient: playlist demuxers can read other files.
# Exclude HLS, DASH, concat and SDP for both library and cloud-upload probes.
AUDIO_DEMUXERS = "aac,aiff,ape,asf,au,flac,matroska,webm,mov,mp3,ogg,wav"
MEDIA_INPUT_OPTIONS = ["-protocol_whitelist", "file,pipe", "-format_whitelist", AUDIO_DEMUXERS]
PROBE_OUTPUT_BYTES = 64 * 1024


def run_conversion(cmd: list[str]) -> None:
    # Conversion diagnostics and stdout have no consumer; never retain them.
    run_bounded(cmd, operation="Audio conversion", timeout=240)


def probe_duration(path: Path, *, timeout: float) -> float | None:
    """Parse at most 64 KiB of JSON; None when ffprobe reports no duration.

    Callers enforce duration range semantics (finite, positive).
    """
    output = run_bounded([
        "ffprobe", "-v", "error", *MEDIA_INPUT_OPTIONS, "-show_entries", "format=duration",
        "-of", "json", str(path),
    ], operation="Audio probe", timeout=timeout, max_output_bytes=PROBE_OUTPUT_BYTES)
    try:
        data = json.loads(output)
        media_format = data.get("format", {}) if isinstance(data, dict) else None
        if not isinstance(media_format, dict):
            raise ValueError("unexpected ffprobe JSON")
        duration = media_format.get("duration")
        return None if duration is None else float(duration)
    except (TypeError, ValueError, OverflowError):
        raise RuntimeError("Could not parse audio duration") from None


def probe_seconds(path: Path, *, timeout: float) -> float:
    seconds = probe_duration(path, timeout=timeout)
    if seconds is None:
        raise RuntimeError("Could not parse audio duration")
    return seconds
