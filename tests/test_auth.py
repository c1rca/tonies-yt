"""The real session guard: what is reachable before and after signing in."""
import pytest
from fastapi.testclient import TestClient

from app import auth, credentials, main
from app.routes import setup, tonies

pytestmark = pytest.mark.real_auth

USER, PASSWORD = "parent", "correct horse battery"
PUBLIC = {"/api/health", "/api/setup/status", "/api/setup/login", "/api/setup/init"}


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setattr(credentials.settings, "data_dir", tmp_path)
    monkeypatch.setattr(credentials.settings, "tonies_email", "")
    monkeypatch.setattr(credentials.settings, "tonies_password", "")
    monkeypatch.setattr(setup, "reset_upload_session", lambda: None)
    monkeypatch.setattr(setup, "_cmd_version", lambda cmd: "test")
    credentials.initialize_vault(USER, PASSWORD, "kid@example.invalid", "tonies-secret")
    credentials.lock_runtime()
    yield
    credentials.lock_runtime()


def client():
    return TestClient(main.app, base_url="http://tonies.local:8090", follow_redirects=False)


def sign_in(c):
    r = c.post("/api/setup/login", json={"username": USER, "app_password": PASSWORD})
    assert r.status_code == 200 and r.json()["ok"], r.text
    return r


def protected_api_routes():
    for route in main.app.routes:
        path = getattr(route, "path", "")
        if path.startswith("/api/") and path not in PUBLIC:
            for method in sorted(route.methods - {"HEAD"}):
                yield method, path.replace("{filename}", "x.mp3").replace("{job_id}", "x").replace("{action_id}", "x")


@pytest.mark.parametrize("method,path", sorted(set(protected_api_routes())))
def test_every_other_api_route_needs_a_session(vault, method, path):
    r = client().request(method, path, json={})
    assert r.status_code == 401
    assert r.json() == {"detail": "Sign in required"}


def test_guard_covers_every_api_route():
    assert len(set(protected_api_routes())) >= 25


@pytest.mark.parametrize("page", ["/", "/logs", "/settings", "/docs", "/openapi.json"])
def test_pages_redirect_to_login_without_a_session(vault, page):
    r = client().get(page)
    assert r.status_code == 302 and r.headers["location"] == "/login"


def test_pages_redirect_to_setup_before_first_run(tmp_path, monkeypatch):
    monkeypatch.setattr(credentials.settings, "data_dir", tmp_path)
    assert client().get("/").headers["location"] == "/setup"
    assert client().get("/login").headers["location"] == "/setup"


def test_status_before_sign_in_reveals_nothing_but_next_step(vault):
    assert client().get("/api/setup/status").json() == {
        "configured": True, "unlocked": False, "signed_in": False, "source": "vault"}


def test_sign_in_sets_a_strict_httponly_cookie_and_opens_the_app(vault):
    c = client()
    cookie = sign_in(c).headers["set-cookie"].lower()
    assert auth.COOKIE_NAME in cookie and "httponly" in cookie and "samesite=strict" in cookie
    assert "secure" not in cookie  # plain-http LAN; set when served over https
    assert c.get("/api/jobs").status_code == 200
    assert c.get("/").status_code == 200
    status = c.get("/api/setup/status").json()
    assert status["signed_in"] and status["username"] == USER and status["app_version"] == main.__version__


def test_untrusted_proxy_header_does_not_set_secure_cookie(vault):
    r = client().post("/api/setup/login", json={"username": USER, "app_password": PASSWORD},
                      headers={"x-forwarded-proto": "https"})
    assert "secure" not in r.headers["set-cookie"].lower()


def test_a_second_browser_is_not_signed_in_by_the_first(vault):
    sign_in(client())
    assert client().get("/api/jobs").status_code == 401


def test_forged_or_stale_cookie_is_refused(vault):
    c = client()
    c.cookies.set(auth.COOKIE_NAME, "forged")
    assert c.get("/api/jobs").status_code == 401


def test_lock_signs_every_browser_out(vault):
    first, second = client(), client()
    sign_in(first)
    sign_in(second)
    r = first.post("/api/setup/lock")
    assert r.json()["ok"] and auth.COOKIE_NAME in r.headers["set-cookie"]
    assert first.get("/api/jobs").status_code == 401
    assert second.get("/api/jobs").status_code == 401
    assert not credentials.vault_unlocked()


def test_local_media_full_and_range_requests_require_session_and_relock(vault):
    library = main.settings.data_dir / 'downloads'
    library.mkdir(exist_ok=True)
    (library / 'sample.mp3').write_bytes(b'0123456789')
    url = '/api/files/sample.mp3?inline=true'
    signed_out, signed_in = client(), client()
    for headers in ({}, {'Range': 'bytes=2-4'}):
        response = signed_out.get(url, headers=headers)
        assert response.status_code == 401 and b'0123456789' not in response.content
    sign_in(signed_in)
    response = signed_in.get(url)
    assert response.status_code == 200 and response.content == b'0123456789'
    assert response.headers['cache-control'] == 'no-store'
    response = signed_in.get(url, headers={'Range': 'bytes=2-4'})
    assert response.status_code == 206 and response.content == b'234'
    assert response.headers['cache-control'] == 'no-store'
    assert signed_in.post('/api/setup/lock').json()['ok']
    for headers in ({}, {'Range': 'bytes=2-4'}):
        response = signed_in.get(url, headers=headers)
        assert response.status_code == 401 and b'0123456789' not in response.content


def test_password_change_signs_other_browsers_out(vault):
    mine, other = client(), client()
    sign_in(mine)
    sign_in(other)
    r = mine.post("/api/setup/change-password",
                  json={"username": USER, "current_password": PASSWORD, "new_password": "new synthetic passphrase"})
    assert r.json()["ok"]
    assert mine.get("/api/jobs").status_code == 200
    assert other.get("/api/jobs").status_code == 401


def test_setup_cannot_overwrite_an_existing_vault(vault):
    r = client().post("/api/setup/init", json={
        "username": "intruder", "app_password": "x", "tonies_email": "a@b.c", "tonies_password": "y"})
    assert r.json() == {"ok": False, "error": "The app is already set up; sign in instead"}
    sign_in(client())


def test_first_run_setup_signs_the_creator_in(tmp_path, monkeypatch):
    monkeypatch.setattr(credentials.settings, "data_dir", tmp_path)
    monkeypatch.setattr(setup, "reset_upload_session", lambda: None)
    c = client()
    r = c.post("/api/setup/init", json={
        "username": USER, "app_password": PASSWORD, "tonies_email": "a@b.c", "tonies_password": "y"})
    assert r.json()["ok"] and c.get("/api/jobs").status_code == 200
    credentials.lock_runtime()


def test_env_tonies_account_still_needs_an_app_login(tmp_path, monkeypatch):
    monkeypatch.setattr(credentials.settings, "data_dir", tmp_path)
    monkeypatch.setattr(credentials.settings, "tonies_email", "env@example.invalid")
    monkeypatch.setattr(credentials.settings, "tonies_password", "env-secret")
    monkeypatch.setattr(setup, "reset_upload_session", lambda: None)
    assert client().get("/api/jobs").status_code == 401
    c = client()
    assert c.post("/api/setup/init", json={"username": USER, "app_password": PASSWORD}).json()["ok"]
    credentials.lock_runtime()
    sign_in(c)
    assert credentials.get_credentials() == {
        "email": "env@example.invalid", "password": "env-secret", "source": "env"}
    credentials.lock_runtime()


def test_repeated_wrong_passwords_are_throttled(vault):
    c = client()
    for _ in range(auth.LOGIN_MAX_FAILURES):
        assert c.post("/api/setup/login", json={"username": USER, "app_password": "nope"}).status_code == 200
    r = c.post("/api/setup/login", json={"username": USER, "app_password": PASSWORD})
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    assert not credentials.vault_unlocked()


@pytest.mark.parametrize("headers", [
    {"origin": "http://evil.example"},
    {"origin": "null"},
    {"sec-fetch-site": "cross-site"},
])
def test_cross_site_writes_are_refused_even_when_signed_in(vault, headers):
    c = client()
    sign_in(c)
    r = c.post("/api/setup/lock", headers=headers)
    assert r.status_code == 403
    assert credentials.vault_unlocked()


def test_same_origin_allowed_but_spoofed_proxy_host_refused(vault):
    c = client()
    sign_in(c)
    assert c.post("/api/search-cache/clear", headers={"origin": "http://tonies.local:8090"}).status_code == 200
    assert c.post("/api/search-cache/clear", headers={
        "origin": "https://tonies.example.com", "x-forwarded-host": "tonies.example.com"}).status_code == 403


def test_login_itself_is_refused_cross_site(vault):
    r = client().post("/api/setup/login", json={"username": USER, "app_password": PASSWORD},
                      headers={"origin": "http://evil.example"})
    assert r.status_code == 403
    assert not credentials.vault_unlocked()


def test_undo_is_authenticated_and_cross_site_safe_with_real_queue(vault, cloud_api, monkeypatch):
    from app.actions import ActionQueue
    from tonies_fake import URL
    cloud, api = cloud_api
    queue = ActionQueue(lambda: api, batch_window=0)
    monkeypatch.setattr(queue, "_ensure_worker", lambda target: None)
    monkeypatch.setattr(tonies, "action_queue", queue)
    c = client()
    try:
        sign_in(c)
        action = queue.submit(URL, [{"type": "delete", "chapter_id": "one"}])[0]
        url = f"/api/tonies-actions/{action.id}/undo"
        denied = client().post(url)
        assert denied.status_code == 401 and denied.json() == {"detail": "Sign in required"}
        assert action.public()["undo_available"] and not cloud.calls
        assert c.post(url, headers={"origin": "https://attacker.invalid"}).status_code == 403
        assert action.public()["undo_available"] and not cloud.calls
        response = c.post(url)
        assert response.status_code == 200 and response.json()["actions"][0]["status"] == "canceled"
        assert not cloud.calls
    finally:
        queue.reset()
        queue._transfers.shutdown(wait=True)
