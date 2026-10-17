"""Bounded POSIX child execution with no retained provider diagnostics."""
import os
import selectors
import signal
import subprocess
import time
from collections.abc import Callable
from typing import Literal


def _stop_process_group(proc: subprocess.Popen) -> None:
    """Reap the direct child and terminate ffmpeg/JS descendants as well."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait(timeout=5)


def run_bounded(
    cmd: list[str], *, operation: str, timeout: float,
    max_output_bytes: int | None = None, resource_check: Callable[[], None] | None = None,
) -> bytes:
    """Capture bounded stdout when requested; otherwise discard all output.

    ``operation`` is a caller-authored label, never command/output text. Stderr
    is always discarded. The child cannot inherit interactive stdin and starts
    a new process group. The deadline also covers descendants retaining stdout.
    """
    proc = None
    output = bytearray()
    selector = selectors.DefaultSelector()
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if max_output_bytes is not None else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True,
        )
        if proc.stdout is not None:
            selector.register(proc.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + timeout
        while True:
            if time.monotonic() >= deadline:
                raise RuntimeError(f"{operation} timed out") from None
            if resource_check is not None:
                resource_check()
            if selector.get_map():
                for key, _event in selector.select(timeout=0.05):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        if max_output_bytes is None or len(output) + len(chunk) > max_output_bytes:
                            raise RuntimeError(f"{operation} output limit exceeded") from None
                        output.extend(chunk)
            elif proc.poll() is None:
                time.sleep(0.05)
            if proc.poll() is not None and not selector.get_map():
                if proc.returncode != 0:
                    raise RuntimeError(f"{operation} failed") from None
                return bytes(output)
    except OSError:
        raise RuntimeError(f"{operation} failed") from None
    finally:
        selector.close()
        if proc is not None:
            try:
                _stop_process_group(proc)
            except (OSError, subprocess.TimeoutExpired):
                raise RuntimeError(f"{operation} cleanup failed") from None
            finally:
                if proc.stdout is not None:
                    proc.stdout.close()


def run_ytdlp(
    cmd: list[str], *, operation: Literal["search", "download"], timeout: float,
    max_output_bytes: int = 0, resource_check: Callable[[], None] | None = None,
) -> bytes:
    """Preserve the downloader's metadata-only capture and fixed error policy."""
    return run_bounded(
        cmd, operation=f"YouTube {operation}", timeout=timeout,
        max_output_bytes=max_output_bytes if operation == "search" else None,
        resource_check=resource_check,
    )
