"""Private, bounded vault reads and serialized atomic durable writes (Linux)."""
from __future__ import annotations

import fcntl
from functools import wraps
import json
import os
from pathlib import Path
import stat
import tempfile
import threading

from .config import settings

_runtime_lock = threading.RLock()

class VaultError(ValueError):
    """A non-secret error safe to expose at the account boundary."""


def _secrets_dir() -> Path:
    path = settings.data_dir / "secrets"
    if path.is_symlink():
        raise VaultError("Vault storage must be a private directory")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)  # Failure must stop access, not silently weaken privacy.
    return path


def _vault_meta_file() -> Path:
    return _secrets_dir() / "vault-meta.json"


def _vault_data_file() -> Path:
    return _secrets_dir() / "vault-credentials.enc"


def _serialized(function):
    """Serialize read/verify/write including setup, across threads and processes."""
    @wraps(function)
    def guarded(*args, **kwargs):
        with _runtime_lock:
            path = _secrets_dir() / ".vault.lock"
            descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise VaultError("Vault storage is unavailable")
                os.fchmod(descriptor, 0o600)
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                return function(*args, **kwargs)
            finally:
                os.close(descriptor)
    return guarded


def _read_text(path: Path) -> str:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "r", encoding="utf-8") as file:
            info = os.fstat(file.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 16384:
                raise VaultError("Vault storage is invalid; restore a known-good backup")
            os.fchmod(file.fileno(), 0o600)
            return file.read(16385)
    except (OSError, UnicodeError) as error:
        raise VaultError("Vault storage is unavailable; restore a known-good backup") from error


def _read_json(path: Path) -> dict:
    if not os.path.lexists(path):
        return {}
    try:
        data = json.loads(_read_text(path))
        if not isinstance(data, dict):
            raise ValueError
        return data
    except (ValueError, TypeError) as error:
        raise VaultError("Vault metadata is invalid; restore a known-good backup") from error


def _write_text(path: Path, content: str) -> None:
    """Create privately, fsync and atomically replace: never truncate a live vault."""
    descriptor, name = tempfile.mkstemp(prefix=".vault-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            os.chmod(temporary, 0o600)
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


