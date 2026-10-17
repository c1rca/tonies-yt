"""App-facing Tonies operations backed by one warm, in-memory HTTP session."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from threading import RLock
from uuid import uuid4

from .config import settings
from .credentials import get_credentials
from .actions import ActionQueue
from .tonies_api import ToniesAPI, parse_target_url, renamed_title

_client_lock = RLock()
_api_client: ToniesAPI | None = None
_account_fingerprint: bytes | None = None


def reset_upload_session() -> None:
    """Invalidate tokens on vault lock/update; retained name is the app's reset hook."""
    global _api_client, _account_fingerprint
    with _client_lock:
        if _api_client is not None:
            _api_client.close()
        _api_client = None
        _account_fingerprint = None


def _get_api_client(tonies_email_override=None, tonies_password_override=None) -> ToniesAPI:
    global _api_client, _account_fingerprint
    with _client_lock:
        credentials = get_credentials()
        email = (credentials.get("email") if tonies_email_override is None else tonies_email_override) or ""
        password = (credentials.get("password") if tonies_password_override is None else tonies_password_override) or ""
        email = email.strip()
        if not email or not password:
            reset_upload_session()
            raise RuntimeError("Tonies credentials are required. Open /setup to configure credentials.")
        fingerprint = hashlib.sha256(json.dumps([email, password]).encode()).digest()
        if _api_client is None or fingerprint != _account_fingerprint:
            reset_upload_session()
            _api_client = ToniesAPI(email, password)
            _account_fingerprint = fingerprint
        return _api_client


def get_tonies_content(target_url: str) -> dict:
    return _get_api_client().get_content(target_url)


def list_creative_tonies() -> list[dict]:
    return _get_api_client().list_targets()


def _resolve_target(api: ToniesAPI, target_character_name: str | None, target_url: str | None) -> str:
    if target_url:
        parse_target_url(target_url)
        return target_url
    if target_character_name:
        name = target_character_name
    elif settings.tonies_creative_upload_url:
        parse_target_url(settings.tonies_creative_upload_url)
        return settings.tonies_creative_upload_url
    else:
        name = settings.tonies_character_name
    targets = api.list_targets()
    matches = [target for target in targets if not name or target["name"] == name]
    if len(matches) != 1:
        raise RuntimeError("Select a unique Creative-Tonie target before uploading")
    return matches[0]["edit_url"]


def upload_title(path: Path) -> str:
    """Chapter title for an upload: the file name plus a hidden token that keeps it unique."""
    path = Path(path)
    title = renamed_title(path.stem, path.stem)
    return title if " [oc:" in title else f"{title} [oc:{uuid4().hex[:6]}]"


def resolve_upload_target(target_character_name: str | None = None, target_url: str | None = None) -> str:
    return _resolve_target(_get_api_client(), target_character_name, target_url)


def upload_to_tonies(mp3_path: Path, target_character_name: str | None = None, target_url: str | None = None,
                     tonies_email_override: str | None = None, tonies_password_override: str | None = None) -> dict:
    """Upload one file directly (outside the queue) and return the confirmed receipt."""
    api = _get_api_client(tonies_email_override, tonies_password_override)
    resolved = _resolve_target(api, target_character_name, target_url)
    return api.upload(Path(mp3_path), resolved, upload_title(mp3_path))


# Every library change from the app goes through this queue (see app/actions.py).
action_queue = ActionQueue(lambda: _get_api_client())


def rename_tonies_chapter(target_url: str, index: int, title: str, chapter_id: str | None = None) -> dict:
    return _get_api_client().rename(target_url, index, title, chapter_id)


def reorder_tonies_chapter(target_url: str, from_index: int, to_index: int, chapter_id: str | None = None) -> dict:
    return _get_api_client().reorder(target_url, from_index, to_index, chapter_id)


def delete_tonies_chapter(target_url: str, index: int, chapter_title: str | None = None,
                          chapter_id: str | None = None, chapter_fingerprint: str | None = None,
                          app_track_token: str | None = None) -> dict:
    return _get_api_client().delete(target_url, index, chapter_title, chapter_id, chapter_fingerprint, app_track_token)


def delete_all_tonies_content(target_url: str) -> dict:
    return _get_api_client().delete_all(target_url)
