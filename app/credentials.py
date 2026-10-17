"""Password-wrapped encrypted credentials; v1 vaults remain readable in place."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os

from cryptography.fernet import Fernet

from .config import settings
from .vault_storage import (VaultError, _runtime_lock, _serialized, _vault_meta_file,
                            _vault_data_file, _read_text, _read_json, _write_text)

_runtime_unlocked = False
_runtime_username = ""
_runtime_email = ""
_runtime_password = ""
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 1024


def _pbkdf2(password: str, salt_b64: str, iterations: int = 390000) -> bytes:
    salt = base64.urlsafe_b64decode(salt_b64.encode("utf-8"))
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, dklen=32)


def _derive_fernet_key(password: str, salt_b64: str) -> bytes:
    return base64.urlsafe_b64encode(_pbkdf2(password, salt_b64))


def _new_salt_b64() -> str:
    return base64.urlsafe_b64encode(os.urandom(16)).decode("utf-8")


def _validate_new_password(password: str) -> None:
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH or password.isspace():
        raise VaultError("App password must contain 12 to 1024 characters and cannot be only whitespace")


def _verified_meta(username: str, password: str) -> tuple[dict, str]:
    meta = _read_json(_vault_meta_file())
    if not meta:
        raise VaultError("Vault not initialized or metadata is invalid")
    # Old installations stripped app passwords before deriving their keys.
    if meta.get("password_whitespace") != "preserve":
        password = password.strip()

    try:
        if username.strip() != meta["username"]:
            raise VaultError("Invalid username or password")
        salt = meta["verifier_salt"]
        expected = meta["verifier_hash"]
        if not isinstance(salt, str) or len(base64.urlsafe_b64decode(salt)) != 16:
            raise ValueError
        actual = base64.urlsafe_b64encode(_pbkdf2(password, salt)).decode("utf-8")
        if not isinstance(expected, str) or not hmac.compare_digest(actual, expected):
            raise VaultError("Invalid username or password")
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, VaultError):
            raise
        raise VaultError("Vault metadata is invalid; restore a known-good backup") from error
    return meta, password


def _unwrap(meta: dict, password: str) -> bytes:
    key = _derive_fernet_key(password, meta["wrap_salt"])
    return Fernet(key).decrypt(meta["wrapped_vault_key"].encode("utf-8"))


def _payload(key: bytes) -> dict:
    decrypted = Fernet(key).decrypt(_read_text(_vault_data_file()).encode("utf-8"))
    data = json.loads(decrypted)
    if not isinstance(data, dict) or any(not isinstance(data.get(field, ""), str)
                                        for field in ("tonies_email", "tonies_password")):
        raise VaultError("Stored credentials are invalid")
    return data


def _set_runtime_unlocked(username: str, email: str, password: str) -> None:
    global _runtime_unlocked, _runtime_username, _runtime_email, _runtime_password
    _runtime_unlocked, _runtime_username = True, username
    _runtime_email, _runtime_password = email, password


def _clear_runtime() -> None:
    global _runtime_unlocked, _runtime_username, _runtime_email, _runtime_password
    _runtime_unlocked = False
    _runtime_username = _runtime_email = _runtime_password = ""


def vault_configured() -> bool:
    # A missing/corrupt half is NOT a fresh installation and must never be reset.
    directory = settings.data_dir / "secrets"
    return any(os.path.lexists(directory / name) for name in ("vault-meta.json", "vault-credentials.enc"))


def vault_unlocked() -> bool:
    return _runtime_unlocked


def get_credentials() -> dict:
    email = (settings.tonies_email or "").strip()
    password = settings.tonies_password or ""
    if email and password:
        return {"email": email, "password": password, "source": "env"}
    with _runtime_lock:
        if _runtime_unlocked and _runtime_email and _runtime_password:
            return {"email": _runtime_email, "password": _runtime_password, "source": "vault"}
    return {"email": "", "password": "", "source": "none"}


def env_credentials_set() -> bool:
    return bool((settings.tonies_email or "").strip() and settings.tonies_password)


def setup_status() -> dict:
    configured = vault_configured()
    try:
        meta = _read_json(_vault_meta_file()) if configured else {}
    except (OSError, VaultError):
        meta = {}
    return {"configured": configured, "unlocked": vault_unlocked(),
            "source": "env" if env_credentials_set() else ("vault" if configured else "none"),
            "username": meta.get("username", "") if isinstance(meta.get("username", ""), str) else ""}


@_serialized
def initialize_vault(username: str, app_password: str, tonies_email: str, tonies_password: str) -> dict:
    username, tonies_email = username.strip(), tonies_email.strip()
    if vault_configured():
        raise VaultError("The app is already set up; sign in instead")
    if not username or len(username) > 256:
        raise VaultError("Username is required and must not exceed 256 characters")
    _validate_new_password(app_password)
    if not (tonies_email and tonies_password) and not env_credentials_set():
        raise VaultError("Tonies email and Tonies password are required")
    vault_key = Fernet.generate_key()
    verifier_salt, wrap_salt = _new_salt_b64(), _new_salt_b64()
    meta = {
        "username": username, "verifier_salt": verifier_salt,
        "verifier_hash": base64.urlsafe_b64encode(_pbkdf2(app_password, verifier_salt)).decode("utf-8"),
        "wrap_salt": wrap_salt,
        "wrapped_vault_key": Fernet(_derive_fernet_key(app_password, wrap_salt)).encrypt(vault_key).decode("utf-8"),
        "kdf": "pbkdf2-sha256", "kdf_iterations": 390000, "version": 1,
        "password_whitespace": "preserve",
    }
    payload = json.dumps({"tonies_email": tonies_email, "tonies_password": tonies_password})
    encrypted = Fernet(vault_key).encrypt(payload.encode("utf-8")).decode("utf-8")
    # Metadata is the final commit marker. A power loss between files fails closed
    # as an incomplete vault, rather than allowing public setup to overwrite it.
    _write_text(_vault_data_file(), encrypted)
    _write_text(_vault_meta_file(), json.dumps(meta, indent=2))
    _set_runtime_unlocked(username, tonies_email, tonies_password)
    return {"ok": True}


@_serialized
def login_unlock(username: str, app_password: str) -> dict:
    try:
        meta, password = _verified_meta(username, app_password)
        payload = _payload(_unwrap(meta, password))
        email, tonies_password = payload.get("tonies_email", "").strip(), payload.get("tonies_password", "")
        if not (email and tonies_password) and not env_credentials_set():
            raise VaultError("Stored credentials are invalid")
        _set_runtime_unlocked(username.strip(), email, tonies_password)
        return {"ok": True}
    except VaultError as error:
        return {"ok": False, "error": str(error)}
    except Exception:
        return {"ok": False, "error": "Could not decrypt vault; restore a known-good backup"}


@_serialized
def update_tonies_credentials(username: str, app_password: str, tonies_email: str = "", tonies_password: str = "") -> dict:
    try:
        meta, password = _verified_meta(username, app_password)
        key = _unwrap(meta, password)
        payload = _payload(key)
        email = tonies_email.strip() or payload.get("tonies_email", "").strip()
        next_password = tonies_password or payload.get("tonies_password", "")
        if not email or not next_password:
            raise VaultError("Tonies email and Tonies password are required")
        encrypted = Fernet(key).encrypt(json.dumps({"tonies_email": email, "tonies_password": next_password}).encode("utf-8"))
        _write_text(_vault_data_file(), encrypted.decode("utf-8"))
        if _runtime_unlocked and _runtime_username == username.strip():
            _set_runtime_unlocked(username.strip(), email, next_password)
        return {"ok": True}
    except VaultError as error:
        return {"ok": False, "error": str(error)}
    except Exception:
        return {"ok": False, "error": "Could not update Tonies credentials"}


@_serialized
def change_app_password(username: str, current_password: str, new_password: str) -> dict:
    try:
        _validate_new_password(new_password)
        meta, password = _verified_meta(username, current_password)
        key = _unwrap(meta, password)
        _payload(key)  # Refuse to publish new metadata for corrupt/missing credentials.
        salt, wrap_salt = _new_salt_b64(), _new_salt_b64()
        meta.update(verifier_salt=salt,
                    verifier_hash=base64.urlsafe_b64encode(_pbkdf2(new_password, salt)).decode("utf-8"),
                    wrap_salt=wrap_salt,
                    wrapped_vault_key=Fernet(_derive_fernet_key(new_password, wrap_salt)).encrypt(key).decode("utf-8"),
                    password_whitespace="preserve")
        _write_text(_vault_meta_file(), json.dumps(meta, indent=2))
        return {"ok": True}
    except VaultError as error:
        return {"ok": False, "error": str(error)}
    except Exception:
        return {"ok": False, "error": "Could not update password"}


def lock_runtime() -> dict:
    with _runtime_lock:
        _clear_runtime()
    return {"ok": True}
