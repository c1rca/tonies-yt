"""Local library policy and bounded, network-disabled audio conversion."""
from __future__ import annotations

from collections import OrderedDict
import math
import os
from pathlib import Path
import stat
import tempfile
import threading
import uuid

from fastapi import HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from .config import settings
from .downloader import sanitize_filename
from .media_process import Trim, TrimOutsideAudio, convert_to_mp3, cut_mp3, probe_seconds, waveform

CHUNK_BYTES = 64 * 1024
_WAVEFORM_CACHE_SIZE = 16
_waveforms: OrderedDict[tuple, dict] = OrderedDict()
_waveforms_lock = threading.Lock()


def library_file(filename: str) -> Path:
    """Resolve a plain MP3 basename, refusing links, pipes and directories."""
    if (Path(filename).name != filename or "\\" in filename or "\x00" in filename
            or Path(filename).suffix.lower() != ".mp3"):
        raise HTTPException(status_code=404, detail="Library file not found")
    root = settings.data_dir / "downloads"
    path = root / filename
    try:
        if root.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
            raise OSError
    except OSError:
        raise HTTPException(status_code=404, detail="Library file not found") from None
    return path


def library_files() -> list[dict]:
    root = settings.data_dir / "downloads"
    if root.is_symlink():
        return []
    records = []
    for candidate in root.glob("*.mp3"):
        try:
            path = library_file(candidate.name)
            info = path.stat()
        except (HTTPException, OSError):
            continue
        records.append({"name": path.name, "path": path.name, "size_bytes": info.st_size,
                        "duration_sec": _duration(path), "modified": info.st_mtime})
    return sorted(records, key=lambda record: record["modified"], reverse=True)


def _duration(path: Path) -> float | None:
    try:
        value = probe_seconds(path, timeout=10)
        return value if math.isfinite(value) and value > 0 else None
    except Exception:
        return None


def _convert(source: Path, destination: Path) -> None:
    try:
        convert_to_mp3(source, destination, max_bytes=settings.max_upload_bytes)
    except (RuntimeError, OSError):
        raise HTTPException(status_code=400, detail="Audio conversion failed; upload a supported audio file") from None
    if destination.stat().st_size >= settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="Converted audio exceeds the file size limit")


async def store_upload(file: UploadFile) -> dict:
    """Stream a bounded upload into private unique files; remove all partials."""
    uploads, downloads = settings.data_dir / "uploads", settings.data_dir / "downloads"
    if uploads.is_symlink() or downloads.is_symlink():
        raise HTTPException(status_code=400, detail="Audio storage is unavailable")
    uploads.mkdir(mode=0o700, parents=True, exist_ok=True)
    downloads.mkdir(mode=0o700, parents=True, exist_ok=True)
    base = sanitize_filename(Path(file.filename or "upload").stem)[:100] or "upload"
    final = downloads / f"{base}_{uuid.uuid4().hex}.mp3"
    source_fd, source_name = tempfile.mkstemp(prefix="audio-", dir=uploads)
    source = Path(source_name)
    temporary = None
    try:
        with os.fdopen(source_fd, "wb") as stream:
            total = 0
            while chunk := await file.read(CHUNK_BYTES):
                total += len(chunk)
                if total > settings.max_upload_bytes:
                    raise HTTPException(status_code=413, detail="Uploaded audio exceeds the file size limit")
                stream.write(chunk)
        if not total:
            raise HTTPException(status_code=400, detail="Uploaded audio is empty")
        output_fd, output_name = tempfile.mkstemp(prefix=".converting-", dir=downloads)
        os.close(output_fd)
        temporary = Path(output_name)
        await run_in_threadpool(_convert, source, temporary)
        os.replace(temporary, final)
        return {"ok": True, "filename": final.name}
    finally:
        source.unlink(missing_ok=True)
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        await file.close()


def _editor_error(error: Exception) -> HTTPException:
    if isinstance(error, (ValueError, TrimOutsideAudio)):
        return HTTPException(status_code=422, detail=str(error))
    return HTTPException(status_code=400, detail="This audio file can't be read")


def clear_waveform_cache() -> None:
    with _waveforms_lock:
        _waveforms.clear()


def library_waveform(filename: str, *, start: float, end: float | None, points: int) -> dict:
    """Waveform of a library file (or a window of it); cached per file version."""
    path = library_file(filename)
    info = path.stat()
    key = (path.name, info.st_mtime_ns, info.st_size, start, end, points)
    with _waveforms_lock:
        if key in _waveforms:
            _waveforms.move_to_end(key)
            return _waveforms[key]
    window = None if start == 0 and end is None else Trim(start=start, end=end)
    try:
        outline = waveform(path, window=window, points=points)
    except (ValueError, RuntimeError) as error:
        raise _editor_error(error) from None
    with _waveforms_lock:
        _waveforms[key] = outline
        while len(_waveforms) > _WAVEFORM_CACHE_SIZE:
            _waveforms.popitem(last=False)
    return outline


def _clock_label(seconds: float) -> str:
    whole = int(seconds)
    hours, minutes, secs = whole // 3600, whole // 60 % 60, whole % 60
    return f"{hours}-{minutes:02}-{secs:02}" if hours else f"{minutes}-{secs:02}"


async def clip_library_file(filename: str, start: float, end: float) -> dict:
    """Save ``start``..``end`` of a library file as a new library file; the original stays."""
    source = library_file(filename)
    try:
        trim = Trim(start=start, end=end).validate()
    except ValueError as error:
        raise _editor_error(error) from None
    downloads = source.parent
    base = f"{Path(source.name).stem[:90]}_{_clock_label(start)}-{_clock_label(end)}"
    fd, name = tempfile.mkstemp(prefix=".clipping-", suffix=".part", dir=downloads)
    os.close(fd)
    temporary = Path(name)
    try:
        await run_in_threadpool(cut_mp3, source, temporary, trim)
        final = downloads / f"{base}.mp3"
        try:
            os.link(temporary, final)  # exclusive: never overwrite another file
        except FileExistsError:
            final = downloads / f"{base}_{uuid.uuid4().hex[:8]}.mp3"
            os.link(temporary, final)
        return {"ok": True, "filename": final.name}
    except (ValueError, RuntimeError) as error:
        raise _editor_error(error) from None
    finally:
        temporary.unlink(missing_ok=True)
