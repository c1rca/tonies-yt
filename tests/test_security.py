"""Security boundaries exercised with synthetic credentials and isolated storage."""
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import stat
from threading import Barrier
from unittest.mock import Mock

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import pytest

from app import auth, credentials, main, media_files, media_process
from app.routes import files, setup as setup_routes, tonies

pytestmark = pytest.mark.real_auth
USER, PASSWORD = "synthetic", "synthetic app password"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(main.settings, "data_dir", tmp_path)
    monkeypatch.setattr(main.settings, "tonies_email", "")
    monkeypatch.setattr(main.settings, "tonies_password", "")
    monkeypatch.setattr(main.settings, "allowed_hosts", ("localhost", "127.0.0.1", "::1", "testserver"), raising=False)
    monkeypatch.setattr(main.settings, "public_url", "", raising=False)
    monkeypatch.setattr(setup_routes, "reset_upload_session", lambda: None)
    credentials.lock_runtime()
    yield
    credentials.lock_runtime()


def setup(password=PASSWORD):
    return credentials.initialize_vault(USER, password, "synthetic@example.invalid", "tonies password")


def signed_client(base_url="http://testserver"):
    setup()
    c = TestClient(main.app, base_url=base_url, follow_redirects=False)
    assert c.post("/api/setup/login", json={"username": USER, "app_password": PASSWORD}).json()["ok"]
    return c


@pytest.mark.parametrize("host", ["evil.example", "localhost.evil.example", "user@localhost", "localhost:bad"])
def test_untrusted_or_invalid_host_refused_before_setup(host):
    r = TestClient(main.app).post("/api/setup/init", headers={"host": host}, json={
        "username": USER, "app_password": PASSWORD, "tonies_email": "a@b.invalid", "tonies_password": "x"})
    assert r.status_code == 400
    assert not credentials.vault_configured()


@pytest.mark.parametrize("origin,extra", [
    ("https://testserver", {}), ("null", {}),
    ("http://evil.example", {"x-forwarded-host": "evil.example"}),
    ("http://testserver/path", {}), ("http://user@testserver", {}),
])
def test_exact_origin_boundary_ignores_forwarded_host(origin, extra):
    c = signed_client()
    r = c.post("/api/setup/lock", headers={"origin": origin, **extra})
    assert r.status_code == 403
    assert credentials.vault_unlocked()


def test_actual_https_not_spoofed_forwarding_controls_cookie():
    setup()
    payload = {"username": USER, "app_password": PASSWORD}
    plain = TestClient(main.app).post("/api/setup/login", json=payload, headers={"x-forwarded-proto": "https"})
    assert "secure" not in plain.headers["set-cookie"].lower()
    tls = TestClient(main.app, base_url="https://localhost").post("/api/setup/login", json=payload)
    assert "secure" in tls.headers["set-cookie"].lower()


def test_explicit_public_origin_supports_https_reverse_proxy(monkeypatch):
    monkeypatch.setattr(main.settings, "allowed_hosts", ("localhost", "tonies.example.invalid"))
    monkeypatch.setattr(main.settings, "public_url", "https://tonies.example.invalid")
    setup()
    c = TestClient(main.app, base_url="https://tonies.example.invalid")
    r = c.post("/api/setup/login", json={"username": USER, "app_password": PASSWORD},
               headers={"origin": "https://tonies.example.invalid"})
    assert r.json()["ok"] and "secure" in r.headers["set-cookie"].lower()
    assert c.post("/api/setup/lock", headers={"origin": "http://tonies.example.invalid"}).status_code == 403


@pytest.mark.parametrize("path", ["/static/account.html", "/static/index.html", "/static/logs.html"])
def test_static_app_pages_are_not_public(path):
    assert TestClient(main.app, follow_redirects=False).get(path).status_code == 302


@pytest.mark.parametrize("route,body", [
    ("login", {"username": "x", "app_password": {"secret": "synthetic-private-value"}}),
    ("init", {"username": {"secret": "synthetic-private-value"}, "app_password": "x"}),
])
def test_account_validation_does_not_echo_secrets(route, body):
    r = TestClient(main.app).post("/api/setup/" + route, json=body)
    assert r.status_code == 422
    assert "synthetic-private-value" not in r.text


def test_account_malformed_json_does_not_echo_body():
    r = TestClient(main.app).post("/api/setup/login", content='{"app_password":"synthetic-private-value", broken',
                                 headers={"content-type": "application/json"})
    assert r.status_code == 422 and "synthetic-private-value" not in r.text


def test_account_input_and_total_body_are_bounded(monkeypatch):
    verifier = Mock(return_value={"ok": False, "error": "Invalid username or password"})
    monkeypatch.setattr(setup_routes, "login_unlock", verifier)
    c = TestClient(main.app)
    r = c.post("/api/setup/login", json={"username": "x" * 257, "app_password": "synthetic-private-value"})
    assert r.status_code == 422
    assert "synthetic-private-value" not in r.text
    r = c.post("/api/setup/login", content=b"x" * 17000)
    assert r.status_code == 413
    verifier.assert_not_called()


def test_setup_errors_are_not_reflected(monkeypatch):
    monkeypatch.setattr(setup_routes, "initialize_vault", Mock(side_effect=OSError("synthetic-private-value")))
    r = TestClient(main.app).post("/api/setup/init", json={"username": USER, "app_password": PASSWORD})
    assert not r.json()["ok"] and "synthetic-private-value" not in r.text


@pytest.mark.parametrize("password", ["short", " " * 20, "x" * 1025])
def test_new_password_policy_refuses_without_creating_vault(password):
    with pytest.raises(ValueError, match="password"):
        setup(password)
    assert not credentials.vault_configured()


def test_new_password_whitespace_is_preserved_on_setup_and_change():
    password = "  synthetic preserved password  "
    setup(password)
    credentials.lock_runtime()
    assert not credentials.login_unlock(USER, password.strip())["ok"]
    assert credentials.login_unlock(USER, password)["ok"]
    before = (main.settings.data_dir / "secrets" / "vault-credentials.enc").read_bytes()
    assert not credentials.change_app_password(USER, password, "short")["ok"]
    replacement = "  replacement preserved password  "
    assert credentials.change_app_password(USER, password, replacement)["ok"]
    credentials.lock_runtime()
    assert not credentials.login_unlock(USER, replacement.strip())["ok"]
    assert credentials.login_unlock(USER, replacement)["ok"]
    assert (main.settings.data_dir / "secrets" / "vault-credentials.enc").read_bytes() == before


@pytest.mark.parametrize("legacy_password", ["old", "x" * 2048], ids=["short", "long"])
def test_legacy_password_vault_still_unlocks_and_can_rotate(legacy_password):
    # Historical v1 format: no password-whitespace marker and a short password.
    root = main.settings.data_dir / "secrets"
    root.mkdir()
    salt = base64.urlsafe_b64encode(b"0123456789abcdef").decode()
    key = Fernet.generate_key()
    derived = hashlib.pbkdf2_hmac("sha256", legacy_password.encode(), b"0123456789abcdef", 390000, dklen=32)
    meta = {"username": USER, "verifier_salt": salt,
            "verifier_hash": base64.urlsafe_b64encode(derived).decode(), "wrap_salt": salt,
            "wrapped_vault_key": Fernet(base64.urlsafe_b64encode(derived)).encrypt(key).decode(),
            "kdf": "pbkdf2-sha256", "kdf_iterations": 390000, "version": 1}
    (root / "vault-meta.json").write_text(json.dumps(meta))
    encrypted = Fernet(key).encrypt(json.dumps({"tonies_email": "old@example.invalid", "tonies_password": "old-token"}).encode())
    (root / "vault-credentials.enc").write_bytes(encrypted)
    assert credentials.login_unlock(USER, " " + legacy_password + " ")["ok"]
    assert TestClient(main.app).post("/api/setup/login", json={"username": USER, "app_password": legacy_password}).json()["ok"]
    assert credentials.change_app_password(USER, legacy_password, PASSWORD)["ok"]
    assert (root / "vault-credentials.enc").read_bytes() == encrypted
    credentials.lock_runtime()
    assert credentials.login_unlock(USER, PASSWORD)["ok"]
    assert credentials.get_credentials()["password"] == "old-token"


@pytest.mark.parametrize("remaining", ["vault-meta.json", "vault-credentials.enc"])
def test_partial_or_corrupt_vault_cannot_be_overwritten(remaining):
    root = main.settings.data_dir / "secrets"
    root.mkdir()
    p = root / remaining
    p.write_bytes(b"corrupted private existing data")
    assert credentials.vault_configured()
    with pytest.raises(ValueError):
        setup()
    assert p.read_bytes() == b"corrupted private existing data"


def test_vault_directory_and_files_are_private():
    setup()
    root = main.settings.data_dir / "secrets"
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    for name in ("vault-meta.json", "vault-credentials.enc"):
        assert stat.S_IMODE((root / name).stat().st_mode) == 0o600


def test_atomic_write_failure_keeps_existing_ciphertext(monkeypatch):
    setup()
    p = main.settings.data_dir / "secrets" / "vault-credentials.enc"
    old = p.read_bytes()
    monkeypatch.setattr(os, "replace", Mock(side_effect=OSError("disk full")))
    result = credentials.update_tonies_credentials(USER, PASSWORD, "new@example.invalid", "new-secret")
    assert not result["ok"]
    assert p.read_bytes() == old
    credentials.lock_runtime()
    assert credentials.login_unlock(USER, PASSWORD)["ok"]
    assert credentials.get_credentials()["password"] == "tonies password"


def test_secret_write_permission_failure_is_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(os, "chmod", Mock(side_effect=PermissionError("private")))
    with pytest.raises(PermissionError):
        credentials._write_text(tmp_path / "private", "private-value")
    assert not (tmp_path / "private").exists()


def test_parallel_first_setup_has_exactly_one_winner():
    barrier = Barrier(2)
    def initialize(index):
        barrier.wait()
        try:
            credentials.initialize_vault(f"user{index}", PASSWORD, "a@example.invalid", f"secret{index}")
            return index
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(initialize, [0, 1]))
    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    credentials.lock_runtime()
    assert credentials.login_unlock(f"user{winners[0]}", PASSWORD)["ok"]
    assert credentials.get_credentials()["password"] == f"secret{winners[0]}"


def test_expired_sessions_are_pruned_and_login_tracking_is_bounded(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(auth.time, "monotonic", lambda: now[0])
    old = auth.create_session()
    now[0] += auth.SESSION_TTL_SEC + 1
    auth.create_session()
    # Old entries are pruned even when their individual token is never used again.
    assert len(auth._sessions) == 1
    assert not auth.session_valid(old)
    for index in range(2500):
        auth.record_login_failure(f"synthetic-{index}")
    assert len(auth._failures) <= 1024


def test_account_status_and_update_never_probe_or_install_runtime(monkeypatch):
    c = signed_client()
    calls = []
    def run(cmd, **kwargs):
        calls.append(cmd)
        return b"synthetic installed version"
    monkeypatch.setattr(setup_routes, "run_bounded", run)
    r = c.get("/api/setup/status")
    assert r.status_code == 200
    assert all("-U" not in cmd and "pip" not in cmd for cmd in calls)
    calls.clear()
    r = c.post("/api/setup/update-yt-dlp")
    assert not r.json()["ok"] and "rebuild" in r.json()["error"].lower()
    assert not calls


def test_sensitive_responses_have_no_store_and_frame_protection():
    c = signed_client()
    r = c.get("/api/jobs")
    assert r.headers.get("cache-control") == "no-store"
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("x-frame-options") == "DENY"


def test_library_listing_and_upload_existing_refuse_symlinks(monkeypatch, tmp_path):
    c = signed_client()
    root = tmp_path / "downloads"
    root.mkdir()
    outside = tmp_path / "private.mp3"
    outside.write_bytes(b"private-value")
    (root / "link.mp3").symlink_to(outside)
    probe = Mock()
    monkeypatch.setattr(media_files, "probe_seconds", probe)
    assert c.get("/api/files").json() == []
    probe.assert_not_called()
    create = Mock()
    monkeypatch.setattr(files, "create_upload_only_job", create)
    assert c.post("/api/upload-existing", json={"filename": "link.mp3"}).status_code == 404
    create.assert_not_called()


def test_upload_size_limit_applies_before_multipart_buffering(monkeypatch):
    c = signed_client()
    monkeypatch.setattr(main.settings, "max_upload_bytes", 16, raising=False)
    conversion = Mock()
    monkeypatch.setattr(media_process, "run_conversion", conversion)
    # Exceed both file cap and permitted multipart overhead.
    r = c.post("/api/files/upload", files={"file": ("a.wav", b"x" * (1024 * 1024 + 100), "audio/wav")})
    assert r.status_code == 413
    conversion.assert_not_called()


def test_upload_stream_limit_cleanup_and_unique_names(monkeypatch, tmp_path):
    c = signed_client()
    monkeypatch.setattr(main.settings, "max_upload_bytes", 16, raising=False)
    run = Mock()
    monkeypatch.setattr(media_process, "run_conversion", run)
    r = c.post("/api/files/upload", files={"file": ("a.wav", b"x" * 17, "audio/wav")})
    assert r.status_code == 413
    run.assert_not_called()
    assert not list((tmp_path / "uploads").glob("*"))


def test_upload_conversion_forbids_network_and_playlist_demuxers(monkeypatch, tmp_path):
    c = signed_client()
    commands = []
    def run(cmd, **_kw):
        commands.append(cmd)
        # Simulate converter output only; actual ffmpeg positive control is separate.
        from pathlib import Path
        Path(cmd[-1]).write_bytes(b"synthetic converted audio")
    monkeypatch.setattr(media_process, "run_conversion", run)
    names = []
    for _ in range(2):
        r = c.post("/api/files/upload", files={"file": ("same.wav", b"synthetic", "audio/wav")})
        assert r.status_code == 200
        names.append(r.json()["filename"])
    assert len(set(names)) == 2
    for cmd in commands:
        assert "-protocol_whitelist" in cmd
        assert cmd[cmd.index("-protocol_whitelist") + 1] == "file,pipe"
        assert "-format_whitelist" in cmd
        demuxers = cmd[cmd.index("-format_whitelist") + 1].split(",")
        assert "mp3" in demuxers and "wav" in demuxers
        assert not {"hls", "concat", "dash", "sdp"}.intersection(demuxers)
    assert not list((tmp_path / "uploads").glob("*"))


@pytest.mark.parametrize("route,operation,payload", [
    ("/api/creative-tonies", "list_creative_tonies", None),
    ("/api/tonies-content", "get_tonies_content", {"target_url": "synthetic-private-value"}),
    ("/api/tonies-content/delete", "delete_tonies_chapter", {"target_url": "synthetic-private-value", "index": 0, "chapter_title": "synthetic-private-value"}),
    ("/api/tonies-content/delete-all", "delete_all_tonies_content", {"target_url": "synthetic-private-value"}),
])
def test_provider_errors_and_request_values_are_not_reflected_or_logged(monkeypatch, caplog, route, operation, payload):
    c = signed_client()
    monkeypatch.setattr(tonies, operation, Mock(side_effect=RuntimeError("synthetic-private-value")))
    r = c.get(route) if payload is None else c.post(route, json=payload)
    assert "synthetic-private-value" not in r.text
    assert "synthetic-private-value" not in caplog.text


def test_tonies_credentials_rotation_revokes_other_sessions_and_resets_client(monkeypatch):
    first = signed_client()
    second = TestClient(main.app)
    assert second.post("/api/setup/login", json={"username": USER, "app_password": PASSWORD}).json()["ok"]
    reset = Mock()
    monkeypatch.setattr(setup_routes, "reset_upload_session", reset)
    assert first.post("/api/setup/update-tonies-credentials", json={"username": USER, "app_password": PASSWORD,
                      "tonies_password": "  preserved Tonies password  "}).json()["ok"]
    assert first.get("/api/jobs").status_code == 200
    assert second.get("/api/jobs").status_code == 401
    assert credentials.get_credentials()["password"] == "  preserved Tonies password  "
    reset.assert_called_once()


def test_failed_account_changes_do_not_revoke_or_replace_credentials():
    c = signed_client()
    before = credentials.get_credentials().copy()
    for route, payload in [
        ("change-password", {"current_password": "incorrect", "new_password": "a replacement password"}),
        ("change-password", {"current_password": PASSWORD, "new_password": "short"}),
        ("update-tonies-credentials", {"app_password": "incorrect", "tonies_password": "bad-change"}),
    ]:
        assert not c.post("/api/setup/" + route, json={"username": USER, **payload}).json()["ok"]
        assert c.get("/api/jobs").status_code == 200
        assert credentials.get_credentials() == before


def test_real_audio_upload_converts_and_rejects_playlist_without_network(tmp_path):
    import io
    import wave
    c = signed_client()
    audio = io.BytesIO()
    with wave.open(audio, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\x00\x00" * 800)
    r = c.post("/api/files/upload", files={"file": ("synthetic.wav", audio.getvalue(), "audio/wav")})
    assert r.status_code == 200
    mp3 = tmp_path / "downloads" / r.json()["filename"]
    assert mp3.is_file() and mp3.stat().st_size > 0
    listed = c.get("/api/files").json()
    assert len(listed) == 1 and listed[0]["name"] == mp3.name
    assert listed[0]["duration_sec"] > 0
    assert str(tmp_path) not in json.dumps(listed)
    # Local concat manifests are excluded too, even though file protocol is allowed.
    manifest = f"ffconcat version 1.0\nfile '{mp3}'\n".encode()
    rejected = c.post("/api/files/upload", files={"file": ("manifest.wav", manifest, "audio/wav")})
    assert rejected.status_code == 400
    assert str(tmp_path) not in rejected.text
    assert len(list((tmp_path / "downloads").glob("*.mp3"))) == 1


def test_overlapping_public_password_checks_are_bounded(monkeypatch):
    from threading import Event
    entered, release = Event(), Event()
    def verify(*args):
        if entered.is_set():
            return {"ok": False, "error": "Invalid username or password"}
        entered.set()
        assert release.wait(5)
        return {"ok": False, "error": "Invalid username or password"}
    monkeypatch.setattr(setup_routes, "login_unlock", verify)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(lambda: TestClient(main.app).post("/api/setup/login", json={"username": USER, "app_password": PASSWORD}))
        assert entered.wait(5)
        try:
            duplicate = TestClient(main.app).post("/api/setup/login", json={"username": USER, "app_password": PASSWORD})
        finally:
            release.set()
        assert first.result().status_code == 200
    assert duplicate.status_code == 429


def test_chunked_auth_request_is_capped_before_json_parser(monkeypatch):
    import asyncio
    verifier = Mock()
    monkeypatch.setattr(setup_routes, "login_unlock", verifier)
    messages = iter([{"type": "http.request", "body": b"x" * 10000, "more_body": True},
                     {"type": "http.request", "body": b"x" * 7000, "more_body": False}])
    sent = []
    async def receive():
        return next(messages)
    async def send(message):
        sent.append(message)
    scope = {"type": "http", "http_version": "1.1", "method": "POST", "scheme": "http",
             "path": "/api/setup/login", "raw_path": b"/api/setup/login", "query_string": b"",
             "headers": [(b"host", b"localhost"), (b"content-type", b"application/json")],
             "server": ("localhost", 80), "client": ("127.0.0.1", 1234)}
    asyncio.run(main.app(scope, receive, send))
    assert next(message for message in sent if message["type"] == "http.response.start")["status"] == 413
    verifier.assert_not_called()


@pytest.mark.parametrize("value", ["*", "http://localhost", "localhost:8080", "", "bad host"])
def test_invalid_host_configuration_is_refused(monkeypatch, value):
    from app.config import _allowed_hosts
    monkeypatch.setenv("APP_ALLOWED_HOSTS", value)
    with pytest.raises(ValueError):
        _allowed_hosts()


def test_default_host_configuration_is_loopback_only(monkeypatch):
    from app.config import _allowed_hosts
    monkeypatch.delenv("APP_ALLOWED_HOSTS", raising=False)
    assert set(_allowed_hosts()) == {"localhost", "127.0.0.1", "::1"}


@pytest.mark.parametrize("value", ["http://localhost/path", "https://user:secret@localhost", "ftp://localhost", "https://localhost?query=secret"])
def test_invalid_public_origin_configuration_is_refused(monkeypatch, value):
    from app.config import _public_url
    monkeypatch.setenv("APP_PUBLIC_URL", value)
    with pytest.raises(ValueError):
        _public_url()


def test_upload_concurrency_is_capped_before_body_processing(monkeypatch):
    import asyncio
    from threading import Event, Lock
    c = signed_client()
    token = c.cookies.get(auth.COOKIE_NAME)
    assert token is not None
    ready, release, guard = Event(), Event(), Lock()
    count = [0]
    async def upload(file):
        with guard:
            count[0] += 1
            current = count[0]
            if current == 2:
                ready.set()
        if current <= 2:
            assert await asyncio.to_thread(release.wait, 5)
        return {"ok": True, "filename": "synthetic.mp3"}
    monkeypatch.setattr(files, "store_upload", upload)
    def post():
        client = TestClient(main.app)
        client.cookies.set(auth.COOKIE_NAME, token)
        return client.post("/api/files/upload", files={"file": ("a.wav", b"synthetic", "audio/wav")})
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(post), pool.submit(post)
        assert ready.wait(5)
        try:
            refused = post()
        finally:
            release.set()
        assert first.result().status_code == second.result().status_code == 200
    assert refused.status_code == 429


def test_body_receive_has_a_deadline_before_parsing(monkeypatch):
    import asyncio
    from app import request_security
    monkeypatch.setattr(request_security, "BODY_READ_TIMEOUT_SEC", 0.01, raising=False)
    messages = []
    async def receive():
        await asyncio.sleep(0.1)
        return {"type": "http.request", "body": b"{}", "more_body": False}
    async def send(message):
        messages.append(message)
    scope = {"type": "http", "http_version": "1.1", "method": "POST", "scheme": "http",
             "path": "/api/setup/login", "raw_path": b"/api/setup/login", "query_string": b"",
             "headers": [(b"host", b"localhost"), (b"content-type", b"application/json")],
             "server": ("localhost", 80), "client": ("127.0.0.1", 1234)}
    asyncio.run(main.app(scope, receive, send))
    assert next(message for message in messages if message["type"] == "http.response.start")["status"] == 408


@pytest.mark.parametrize("origin", ["http://testserver?", "http://testserver#"])
def test_empty_query_or_fragment_is_not_an_origin(origin):
    c = signed_client()
    assert c.post("/api/setup/lock", headers={"origin": origin}).status_code == 403


def test_default_ports_normalize_but_same_origin_remains_allowed():
    c = signed_client()
    assert c.post("/api/search-cache/clear", headers={"origin": "http://testserver:80"}).status_code == 200


def test_expired_cookie_cannot_reopen_the_api(monkeypatch):
    c = signed_client()
    token = c.cookies.get(auth.COOKIE_NAME)
    assert token is not None
    expired_time = auth._sessions[token] + 1
    monkeypatch.setattr(auth.time, "monotonic", lambda: expired_time)
    assert c.get("/api/jobs").status_code == 401


def test_corrupt_ciphertext_refuses_password_rotation_without_metadata_change():
    setup()
    root = main.settings.data_dir / "secrets"
    metadata = (root / "vault-meta.json").read_bytes()
    (root / "vault-credentials.enc").write_bytes(b"corrupted encrypted credentials")
    result = credentials.change_app_password(USER, PASSWORD, "another synthetic password")
    assert not result["ok"]
    assert (root / "vault-meta.json").read_bytes() == metadata


def test_password_rotation_write_failure_preserves_old_login(monkeypatch):
    setup()
    metadata = (main.settings.data_dir / "secrets" / "vault-meta.json").read_bytes()
    monkeypatch.setattr(os, "replace", Mock(side_effect=OSError("synthetic-private-value")))
    result = credentials.change_app_password(USER, PASSWORD, "another synthetic password")
    assert not result["ok"] and "synthetic-private-value" not in json.dumps(result)
    assert (main.settings.data_dir / "secrets" / "vault-meta.json").read_bytes() == metadata
    credentials.lock_runtime()
    assert credentials.login_unlock(USER, PASSWORD)["ok"]


def test_account_response_and_vault_files_never_contain_plaintext_secrets(monkeypatch):
    c = signed_client()
    monkeypatch.setattr(setup_routes, "_cmd_version", lambda cmd: "synthetic-version")
    body = c.get("/api/setup/status").text
    assert PASSWORD not in body and "tonies password" not in body
    assert "verifier_hash" not in body and "wrapped_vault_key" not in body
    for path in (main.settings.data_dir / "secrets").iterdir():
        data = path.read_bytes()
        assert PASSWORD.encode() not in data and b"tonies password" not in data


def test_duplicate_host_or_origin_is_rejected():
    c = signed_client()
    assert c.get("/api/jobs", headers=[("host", "testserver"), ("host", "evil.example")]).status_code == 400
    assert c.post("/api/setup/lock", headers=[("origin", "http://testserver"), ("origin", "http://evil.example")]).status_code == 403
