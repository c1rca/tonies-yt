"""Real verifier/HTTP race regressions, with only scheduling seams paused."""
from concurrent.futures import ThreadPoolExecutor
import socket
from threading import Event, Lock

from fastapi.testclient import TestClient
import pytest

from app import auth, credentials, main

pytestmark = pytest.mark.real_auth
USER, PASSWORD = "synthetic", "synthetic app password"
REPLACEMENT = "replacement app password"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(main.settings, "data_dir", tmp_path)
    monkeypatch.setattr(main.settings, "tonies_email", "")
    monkeypatch.setattr(main.settings, "tonies_password", "")
    monkeypatch.setattr(main, "reset_upload_session", lambda: None)
    def forbidden(*args, **kwargs):
        pytest.fail("Provider/network connections and workers are forbidden")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(main, "start_worker", forbidden)
    credentials.lock_runtime()
    yield
    credentials.lock_runtime()


def client():
    return TestClient(main.app, follow_redirects=False)


def login(c, password=PASSWORD):
    return c.post("/api/setup/login", json={"username": USER, "app_password": password})


def initialize():
    owner = client()
    assert owner.post("/api/setup/init", json={"username": USER, "app_password": PASSWORD,
                      "tonies_email": "synthetic@example.invalid", "tonies_password": "cloud password"}).json()["ok"]
    library = main.settings.data_dir / "downloads"
    library.mkdir()
    (library / "synthetic.mp3").write_bytes(b"synthetic private audio")
    assert owner.get("/api/files/synthetic.mp3").content == b"synthetic private audio"
    return owner


def revoke(owner, operation):
    body = {} if operation == "lock" else (
        {"username": USER, "current_password": PASSWORD, "new_password": REPLACEMENT}
        if operation == "change-password" else
        {"username": USER, "app_password": PASSWORD, "tonies_password": "new cloud password"})
    return owner.post("/api/setup/" + operation, json=body)


def assert_media_denied(c):
    for headers in ({}, {"Range": "bytes=0-3"}):
        response = c.get("/api/files/synthetic.mp3", headers=headers)
        assert response.status_code == 401
        assert b"synthetic private audio" not in response.content


@pytest.mark.parametrize("operation", ["lock", "change-password", "update-tonies-credentials"])
def test_verified_login_cannot_issue_after_completed_revocation(monkeypatch, operation):
    owner = initialize()
    existing = client()
    assert login(existing).json()["ok"]
    verified, release, gate = Event(), Event(), Lock()
    calls = [0]
    def reset():
        with gate:
            calls[0] += 1
            first = calls[0] == 1
        if first:
            verified.set()
            assert release.wait(5)
    monkeypatch.setattr(main, "reset_upload_session", reset)
    newcomer = client()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(login, newcomer)
        try:
            assert verified.wait(5), "actual password verification never reached reset seam"
            assert revoke(owner, operation).json()["ok"]
            assert_media_denied(existing)
            if operation == "lock":
                assert not credentials.vault_unlocked()
            else:
                # Rotation intentionally retains only the initiating session.
                assert owner.get("/api/files/synthetic.mp3").status_code == 200
            if operation == "change-password":
                assert not credentials.login_unlock(USER, PASSWORD)["ok"]
        finally:
            release.set()
        response = pending.result(timeout=5)
    assert_media_denied(newcomer)
    assert not response.json()["ok"]
    assert "set-cookie" not in response.headers
    fresh = client()
    assert login(fresh, REPLACEMENT if operation == "change-password" else PASSWORD).json()["ok"]
    assert fresh.get("/api/files/synthetic.mp3").status_code == 200


def test_sequential_lock_allows_normal_fresh_login():
    owner = initialize()
    assert revoke(owner, "lock").json()["ok"]
    assert not credentials.vault_unlocked()
    assert_media_denied(owner)
    assert login(owner).json()["ok"]
    assert credentials.vault_unlocked()
    assert owner.get("/api/files/synthetic.mp3").status_code == 200


def test_lock_during_verifier_never_finishes_with_late_runtime_unlock(monkeypatch):
    owner = initialize()
    verifier_entered, release_verifier, lock_started, lock_completed = Event(), Event(), Event(), Event()
    actual_verify = main.login_unlock
    def verify(*args):
        # This is inside the route's verification phase but before the real verifier
        # acquires its own runtime lock. Lock must serialize or cancel the attempt.
        verifier_entered.set()
        assert release_verifier.wait(5)
        return actual_verify(*args)
    monkeypatch.setattr(main, "login_unlock", verify)
    def lock():
        lock_started.set()
        response = revoke(owner, "lock")
        lock_completed.set()
        return response
    newcomer = client()
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(login, newcomer)
        try:
            assert verifier_entered.wait(5)
            locking = pool.submit(lock)
            assert lock_started.wait(5)
            # Both permitted designs: Lock waits for verification, or Lock finishes
            # and an epoch cancels the verifier. Let the latter finish if possible.
            lock_completed.wait(0.25)
        finally:
            release_verifier.set()
        assert locking.result(timeout=5).json()["ok"]
        pending.result(timeout=5)
    assert not credentials.vault_unlocked(), "pre-lock verification unlocked runtime after Lock"
    assert credentials.get_credentials()["source"] == "none"
    assert_media_denied(newcomer)


def test_revocation_at_session_insertion_is_atomic(monkeypatch):
    owner = initialize()
    issuance_entered, release = Event(), Event()
    create = auth.create_session
    def paused_create(*args, **kwargs):
        issuance_entered.set()
        assert release.wait(5)
        return create(*args, **kwargs)
    monkeypatch.setattr(auth, "create_session", paused_create)
    newcomer = client()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(login, newcomer)
        try:
            assert issuance_entered.wait(5)
            assert revoke(owner, "lock").json()["ok"]
        finally:
            release.set()
        response = pending.result(timeout=5)
    assert_media_denied(newcomer)
    assert not response.json()["ok"]
    assert "set-cookie" not in response.headers


def test_real_uploader_reset_closes_client_outside_auth_and_runtime_locks(monkeypatch):
    """The real reset's client/queue locks must not invert account/runtime locks."""
    from threading import Thread
    from app import uploader
    initialize()
    checked, errors = Event(), []
    probes = []
    def check_account_read():
        try:
            with auth.account_transition():
                credentials.get_credentials()
                auth.session_generation()
        except Exception as error:
            errors.append(error)
        finally:
            checked.set()
    class ClosingClient:
        def close(self):
            # Bound the waiter, so an accidental lock inversion fails rather than
            # hanging the runner. Production client/queue reset is exercised.
            thread = Thread(target=check_account_read, daemon=True)
            probes.append(thread)
            thread.start()
            assert checked.wait(2), "uploader close ran under an account/runtime/session lock"
    monkeypatch.setattr(uploader, "_api_client", ClosingClient())
    monkeypatch.setattr(main, "reset_upload_session", uploader.reset_upload_session)
    try:
        assert login(client()).json()["ok"]
        assert checked.is_set() and not errors
    finally:
        for thread in probes:
            thread.join(timeout=3)
            assert not thread.is_alive()
