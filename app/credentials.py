from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet

from .config import settings


def _secrets_dir() -> Path:
    p = settings.data_dir / "secrets"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _vault_meta_file() -> Path:
    return _secrets_dir() / "vault-meta.json"


def _vault_data_file() -> Path:
    return _secrets_dir() / "vault-credentials.enc"


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def _pbkdf2(password: str, salt_b64: str, iterations: int = 390000) -> bytes:
    salt = base64.urlsafe_b64decode(salt_b64.encode("utf-8"))
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, dklen=32)


def _derive_fernet_key(password: str, salt_b64: str) -> bytes:
    return base64.urlsafe_b64encode(_pbkdf2(password, salt_b64))


def _new_salt_b64() -> str:
    return base64.urlsafe_b64encode(os.urandom(16)).decode("utf-8")


# In-memory unlocked state (single-process local app)
_runtime_unlocked: bool = False
_runtime_username: str = ""
_runtime_email: str = ""
_runtime_password: str = ""


def _set_runtime_unlocked(username: str, email: str, password: str) -> None:
    global _runtime_unlocked, _runtime_username, _runtime_email, _runtime_password
    _runtime_unlocked = True
    _runtime_username = username
    _runtime_email = email
    _runtime_password = password


def _clear_runtime() -> None:
    global _runtime_unlocked, _runtime_username, _runtime_email, _runtime_password
    _runtime_unlocked = False
    _runtime_username = ""
    _runtime_email = ""
    _runtime_password = ""


def vault_configured() -> bool:
    return _vault_meta_file().exists() and _vault_data_file().exists()


def vault_unlocked() -> bool:
    return _runtime_unlocked


def get_credentials() -> dict:
    # Backward compatibility: env still works and bypasses vault.
    env_email = (settings.tonies_email or "").strip()
    env_password = (settings.tonies_password or "").strip()
    if env_email and env_password:
        return {"email": env_email, "password": env_password, "source": "env"}

    if _runtime_unlocked and _runtime_email and _runtime_password:
        return {"email": _runtime_email, "password": _runtime_password, "source": "vault"}

    return {"email": "", "password": "", "source": "none"}


def setup_status() -> dict:
    if (settings.tonies_email or "").strip() and (settings.tonies_password or "").strip():
        return {"configured": True, "unlocked": True, "source": "env", "username": "env"}

    meta = _read_json(_vault_meta_file())
    configured = vault_configured()
    return {
        "configured": configured,
        "unlocked": vault_unlocked(),
        "source": "vault" if configured else "none",
        "username": meta.get("username", "") if configured else "",
    }


def initialize_vault(username: str, app_password: str, tonies_email: str, tonies_password: str) -> dict:
    username = (username or "").strip()
    app_password = (app_password or "").strip()
    tonies_email = (tonies_email or "").strip()
    tonies_password = (tonies_password or "").strip()

    if not username or not app_password or not tonies_email or not tonies_password:
        raise ValueError("Username, app password, Tonies email and Tonies password are required")

    vault_key = Fernet.generate_key()  # key used to encrypt Tonies credentials blob

    # Password verifier
    verifier_salt = _new_salt_b64()
    verifier_hash = base64.urlsafe_b64encode(_pbkdf2(app_password, verifier_salt)).decode("utf-8")

    # Wrap vault key with password-derived key
    wrap_salt = _new_salt_b64()
    wrap_key = _derive_fernet_key(app_password, wrap_salt)
    wrapped_vault_key = Fernet(wrap_key).encrypt(vault_key).decode("utf-8")

    meta = {
        "username": username,
        "verifier_salt": verifier_salt,
        "verifier_hash": verifier_hash,
        "wrap_salt": wrap_salt,
        "wrapped_vault_key": wrapped_vault_key,
        "kdf": "pbkdf2-sha256",
        "kdf_iterations": 390000,
        "version": 1,
    }

    creds_payload = json.dumps({"tonies_email": tonies_email, "tonies_password": tonies_password})
    creds_enc = Fernet(vault_key).encrypt(creds_payload.encode("utf-8")).decode("utf-8")

    _write_text(_vault_meta_file(), json.dumps(meta, indent=2))
    _write_text(_vault_data_file(), creds_enc)

    _set_runtime_unlocked(username, tonies_email, tonies_password)
    return {"ok": True}


def login_unlock(username: str, app_password: str) -> dict:
    username = (username or "").strip()
    app_password = (app_password or "").strip()
    meta = _read_json(_vault_meta_file())
    if not meta:
        return {"ok": False, "error": "Vault not initialized"}

    if username != str(meta.get("username", "")):
        return {"ok": False, "error": "Invalid username or password"}

    verifier_salt = str(meta.get("verifier_salt", ""))
    expected = str(meta.get("verifier_hash", ""))
    if not verifier_salt or not expected:
        return {"ok": False, "error": "Vault metadata is invalid"}

    actual = base64.urlsafe_b64encode(_pbkdf2(app_password, verifier_salt)).decode("utf-8")
    if not hmac.compare_digest(actual, expected):
        return {"ok": False, "error": "Invalid username or password"}

    wrap_salt = str(meta.get("wrap_salt", ""))
    wrapped = str(meta.get("wrapped_vault_key", ""))
    if not wrap_salt or not wrapped:
        return {"ok": False, "error": "Vault metadata is invalid"}

    try:
        wrap_key = _derive_fernet_key(app_password, wrap_salt)
        vault_key = Fernet(wrap_key).decrypt(wrapped.encode("utf-8"))
        enc = _vault_data_file().read_text(encoding="utf-8")
        decrypted = Fernet(vault_key).decrypt(enc.encode("utf-8"))
        payload = json.loads(decrypted.decode("utf-8"))
        tonies_email = str(payload.get("tonies_email", "")).strip()
        tonies_password = str(payload.get("tonies_password", "")).strip()
        if not tonies_email or not tonies_password:
            return {"ok": False, "error": "Stored credentials are invalid"}
        _set_runtime_unlocked(username, tonies_email, tonies_password)
        return {"ok": True}
    except Exception:
        return {"ok": False, "error": "Could not decrypt vault"}


def update_tonies_credentials(username: str, app_password: str, tonies_email: str = "", tonies_password: str = "") -> dict:
    username = (username or "").strip()
    app_password = (app_password or "").strip()
    tonies_email = (tonies_email or "").strip()
    tonies_password = (tonies_password or "").strip()

    if not username or not app_password:
        return {"ok": False, "error": "Username and app password are required"}

    meta = _read_json(_vault_meta_file())
    if not meta:
        return {"ok": False, "error": "Vault not initialized"}
    if username != str(meta.get("username", "")):
        return {"ok": False, "error": "Invalid app password"}

    verifier_salt = str(meta.get("verifier_salt", ""))
    expected = str(meta.get("verifier_hash", ""))
    actual = base64.urlsafe_b64encode(_pbkdf2(app_password, verifier_salt)).decode("utf-8")
    if not hmac.compare_digest(actual, expected):
        return {"ok": False, "error": "Invalid app password"}

    try:
        wrap_key = _derive_fernet_key(app_password, str(meta.get("wrap_salt", "")))
        vault_key = Fernet(wrap_key).decrypt(str(meta.get("wrapped_vault_key", "")).encode("utf-8"))

        enc = _vault_data_file().read_text(encoding="utf-8")
        decrypted = Fernet(vault_key).decrypt(enc.encode("utf-8"))
        payload = json.loads(decrypted.decode("utf-8"))

        existing_email = str(payload.get("tonies_email", "")).strip()
        existing_password = str(payload.get("tonies_password", "")).strip()

        next_email = tonies_email or existing_email
        next_password = tonies_password or existing_password
        if not next_email or not next_password:
            return {"ok": False, "error": "Tonies email and Tonies password are required"}

        next_payload = json.dumps({"tonies_email": next_email, "tonies_password": next_password})
        next_enc = Fernet(vault_key).encrypt(next_payload.encode("utf-8")).decode("utf-8")
        _write_text(_vault_data_file(), next_enc)

        # Keep current process session in sync when unlocked.
        if _runtime_unlocked and _runtime_username == username:
            _set_runtime_unlocked(username, next_email, next_password)

        return {"ok": True}
    except Exception:
        return {"ok": False, "error": "Could not update Tonies credentials"}


def change_app_password(username: str, current_password: str, new_password: str) -> dict:
    username = (username or "").strip()
    current_password = (current_password or "").strip()
    new_password = (new_password or "").strip()

    if not username or not current_password or not new_password:
        return {"ok": False, "error": "All fields are required"}

    meta = _read_json(_vault_meta_file())
    if not meta:
        return {"ok": False, "error": "Vault not initialized"}
    if username != str(meta.get("username", "")):
        return {"ok": False, "error": "Invalid username or password"}

    verifier_salt = str(meta.get("verifier_salt", ""))
    expected = str(meta.get("verifier_hash", ""))
    actual = base64.urlsafe_b64encode(_pbkdf2(current_password, verifier_salt)).decode("utf-8")
    if not hmac.compare_digest(actual, expected):
        return {"ok": False, "error": "Invalid username or password"}

    try:
        # unwrap old vault key
        wrap_key = _derive_fernet_key(current_password, str(meta.get("wrap_salt", "")))
        vault_key = Fernet(wrap_key).decrypt(str(meta.get("wrapped_vault_key", "")).encode("utf-8"))

        # re-wrap with new password
        new_verifier_salt = _new_salt_b64()
        new_verifier_hash = base64.urlsafe_b64encode(_pbkdf2(new_password, new_verifier_salt)).decode("utf-8")
        new_wrap_salt = _new_salt_b64()
        new_wrap_key = _derive_fernet_key(new_password, new_wrap_salt)
        new_wrapped = Fernet(new_wrap_key).encrypt(vault_key).decode("utf-8")

        meta["verifier_salt"] = new_verifier_salt
        meta["verifier_hash"] = new_verifier_hash
        meta["wrap_salt"] = new_wrap_salt
        meta["wrapped_vault_key"] = new_wrapped
        _write_text(_vault_meta_file(), json.dumps(meta, indent=2))

        return {"ok": True}
    except Exception:
        return {"ok": False, "error": "Could not update password"}


def lock_runtime() -> dict:
    _clear_runtime()
    return {"ok": True}
