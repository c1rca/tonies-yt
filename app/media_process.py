"""Shared native-media execution policy (POSIX process groups and bounded IPC)."""
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re

from .subprocess_runner import run_bounded

# File/pipe alone is insufficient: playlist demuxers can read other files.
# Exclude HLS, DASH, concat and SDP for both library and cloud-upload probes.
AUDIO_DEMUXERS = "aac,aiff,ape,asf,au,flac,matroska,webm,mov,mp3,ogg,wav"
MEDIA_INPUT_OPTIONS = ["-protocol_whitelist", "file,pipe", "-format_whitelist", AUDIO_DEMUXERS]
PROBE_OUTPUT_BYTES = 64 * 1024

# Volume levelling: one static gain per track towards a common loudness (EBU R128),
# with a peak limiter. Static gain keeps the track's own dynamics; boost is capped
# so near-silent recordings aren't turned into noise.
TARGET_LUFS = -14.0
MAX_BOOST_DB = 12.0
_SILENCE_LUFS = -50.0
_PEAK_LIMIT = 0.891  # -1 dBFS
_MAX_SECONDS = 24 * 3600
_LOUDNESS = re.compile(rb"lavfi\.r128\.I=(-?\d+(?:\.\d+)?)")


@dataclass(frozen=True)
class Trim:
    """Keep only ``start``..``end`` seconds; ``end=None`` keeps the rest of the audio."""
    start: float = 0.0
    end: float | None = None

    def validate(self) -> "Trim":
        values = [self.start] + ([] if self.end is None else [self.end])
        if not all(isinstance(v, (int, float)) and math.isfinite(v) and 0 <= v <= _MAX_SECONDS for v in values):
            raise ValueError("Trim times must be between 0 and 24 hours")
        if self.end is not None and self.end <= self.start:
            raise ValueError("Trim end must be after the start")
        return self

    @property
    def active(self) -> bool:
        return self.start > 0 or self.end is not None


def run_conversion(cmd: list[str]) -> None:
    # Conversion diagnostics and stdout have no consumer; never retain them.
    run_bounded(cmd, operation="Audio conversion", timeout=240)


def _trimmed_input(source: Path, trim: Trim | None) -> list[str]:
    window = []
    if trim is not None and trim.active:
        window = ["-ss", f"{trim.start:.3f}"]
        if trim.end is not None:
            window += ["-t", f"{trim.end - trim.start:.3f}"]
    return [*MEDIA_INPUT_OPTIONS, *window, "-i", str(source)]


def _trimmed_seconds(source: Path, trim: Trim | None) -> float | None:
    total = probe_duration(source, timeout=30)
    if total is None or not math.isfinite(total) or total <= 0:
        return None
    if trim is None:
        return total
    if trim.start >= total:
        raise RuntimeError("Trim starts after the end of the audio")
    return min(total, trim.end if trim.end is not None else total) - trim.start


def measure_loudness(source: Path, *, trim: Trim | None = None) -> float | None:
    """Integrated loudness (LUFS) of the (trimmed) audio; None when it can't be measured.

    ebur128 reports a running value per 100 ms frame; only the final frames are
    printed so the captured output stays a few hundred bytes for any length.
    """
    try:
        seconds = _trimmed_seconds(source, trim)
        if seconds is None:
            return None
        tail = f"{max(0.0, seconds - 0.5):.3f}"
        output = run_bounded([
            "ffmpeg", "-nostdin", "-v", "error", *_trimmed_input(source, trim), "-map", "0:a:0",
            "-af", f"ebur128=metadata=1,aselect=gte(t\\,{tail}),ametadata=mode=print:key=lavfi.r128.I:file=-",
            "-f", "null", "-",
        ], operation="Loudness measurement", timeout=240, max_output_bytes=PROBE_OUTPUT_BYTES)
    except RuntimeError:
        return None
    values = _LOUDNESS.findall(output)
    return float(values[-1]) if values else None


def _level_filter(loudness: float | None) -> list[str]:
    if loudness is None or loudness <= _SILENCE_LUFS:
        return []
    gain = min(TARGET_LUFS - loudness, MAX_BOOST_DB)
    return ["-af", f"volume={gain:.2f}dB,alimiter=limit={_PEAK_LIMIT}:level=false"]


def convert_to_mp3(source: Path, destination: Path, *, max_bytes: int, trim: Trim | None = None) -> None:
    """Convert any supported audio to a levelled 128 kbps stereo MP3, trimmed if asked."""
    if trim is not None:
        trim.validate()
        _trimmed_seconds(source, trim)  # refuse a window that starts after the audio
    run_conversion([
        "ffmpeg", "-nostdin", "-v", "error", "-y", *_trimmed_input(source, trim),
        "-map", "0:a:0", "-vn", *_level_filter(measure_loudness(source, trim=trim)),
        "-ar", "44100", "-ac", "2", "-codec:a", "libmp3lame", "-b:a", "128k", "-fs", str(max_bytes),
        "-f", "mp3", str(destination),
    ])


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
