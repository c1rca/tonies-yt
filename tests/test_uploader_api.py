"""Public uploader contracts; no credentials, browser, or live HTTP."""
import builtins
from types import SimpleNamespace

import pytest

from app import uploader

URL = "https://my.tonies.com/creative-tonies/11111111-2222-4333-8444-555555555555/aBcD0123456789EF/edit?withUpload=true"


def test_reads_use_shared_http_client_not_a_browser(monkeypatch):
    content = {"chapters": [], "free_seconds": 5400}
    api = SimpleNamespace(get_content=lambda url: content, list_targets=lambda: [{"name": "Blue", "image": "", "edit_url": URL}])
    monkeypatch.setattr(uploader, "_get_api_client", lambda *a, **kw: api, raising=False)
    original = builtins.__import__

    def no_browser(name, *args, **kwargs):
        assert not name.startswith("playwright"), "reads must use the HTTP client, not a browser"
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_browser)
    assert uploader.get_tonies_content(URL) == content
    assert uploader.list_creative_tonies() == [{"name": "Blue", "image": "", "edit_url": URL}]


def test_singleton_reuses_tokens_across_threads_and_invalidates_credentials(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    made = []
    credentials = {"email": "synthetic@example.invalid", "password": "synthetic-only"}

    class Client:
        def __init__(self, email, password):
            self.closed = False
            made.append(self)

        def close(self):
            self.closed = True

    uploader.reset_upload_session()
    monkeypatch.setattr(uploader, "get_credentials", lambda: credentials)
    monkeypatch.setattr(uploader, "ToniesAPI", Client)
    with ThreadPoolExecutor(max_workers=4) as pool:
        clients = list(pool.map(lambda _: uploader._get_api_client(), range(8)))
    assert len(made) == 1
    assert all(client is made[0] for client in clients)
    credentials["password"] = "changed-synthetic-only"
    assert uploader._get_api_client() is not made[0]
    assert made[0].closed
    uploader.reset_upload_session()
    assert made[1].closed
    credentials.clear()
    with pytest.raises(RuntimeError, match="credentials"):
        uploader._get_api_client()


def test_upload_returns_authoritative_receipt_and_resolves_unique_default(monkeypatch, tmp_path):
    calls = []
    receipt = {"chapter_id": "permanent", "target_url": URL, "content": {"chapters": []}}
    api = SimpleNamespace(
        list_targets=lambda: [{"name": "Blue", "image": "", "edit_url": URL}],
        upload=lambda path, url, title: calls.append((path, url, title)) or receipt,
    )
    monkeypatch.setattr(uploader, "_get_api_client", lambda *a, **kw: api)
    monkeypatch.setattr(uploader.settings, "tonies_creative_upload_url", "")
    monkeypatch.setattr(uploader.settings, "tonies_character_name", "")
    path = tmp_path / "song.mp3"
    path.write_bytes(b"synthetic")
    assert uploader.upload_to_tonies(path) == receipt
    assert calls[0][:2] == (path, URL)
    assert calls[0][2].startswith("song [oc:")


def test_target_name_must_be_unique_and_explicit_url_has_priority(monkeypatch, tmp_path):
    calls = []
    api = SimpleNamespace(
        list_targets=lambda: [{"name": "Blue", "edit_url": URL}] * 2,
        upload=lambda *args: calls.append(args) or {"chapter_id": "x"},
    )
    monkeypatch.setattr(uploader, "_get_api_client", lambda *a, **kw: api)
    monkeypatch.setattr(uploader.settings, "tonies_creative_upload_url", "")
    path = tmp_path / "song.mp3"
    path.write_bytes(b"synthetic")
    with pytest.raises(RuntimeError, match="unique"):
        uploader.upload_to_tonies(path, target_character_name="Blue")
    assert not calls
    assert uploader.upload_to_tonies(path, target_url=URL)["chapter_id"] == "x"


def test_rename_keeps_app_token_and_expected_id(monkeypatch):
    calls = []
    api = SimpleNamespace(rename=lambda *args: calls.append(args) or {"chapters": []})
    monkeypatch.setattr(uploader, "_get_api_client", lambda: api)
    assert uploader.rename_tonies_chapter(URL, 2, "New name", chapter_id="stable") == {"chapters": []}
    assert calls == [(URL, 2, "New name", "stable")]
    assert uploader.renamed_title("Old [oc:ab1234]", "New") == "New [oc:ab1234]"
