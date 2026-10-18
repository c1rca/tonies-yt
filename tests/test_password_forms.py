"""Shipped inline JavaScript -> real HTTP routes; synthetic passwords only.

Node executes the actual registered callbacks, not a Python form reimplementation.
The DOM/transport adapter captures requests; FastAPI handles those exact payloads.
"""
import base64
import hashlib
import json
from pathlib import Path
import re
import socket
import subprocess

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import pytest

from app import credentials, main
from app.routes import setup

pytestmark = pytest.mark.real_auth
USER, PASSWORD = "synthetic", "synthetic app password"
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(main.settings, "data_dir", tmp_path)
    monkeypatch.setattr(main.settings, "tonies_email", "")
    monkeypatch.setattr(main.settings, "tonies_password", "")
    monkeypatch.setattr(setup, "reset_upload_session", lambda: None)
    monkeypatch.setattr(setup, "_cmd_version", lambda cmd: "offline-synthetic")
    def forbidden(*args, **kwargs):
        pytest.fail("Provider/network connections and workers are forbidden")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(main, "start_worker", forbidden)
    credentials.lock_runtime()
    yield
    credentials.lock_runtime()


def client():
    # Deliberately no context manager: never start the background-worker lifespan.
    return TestClient(main.app, follow_redirects=False)


def initialize(password=PASSWORD):
    c = client()
    response = c.post("/api/setup/init", json={"username": USER, "app_password": password,
                      "tonies_email": "synthetic@example.invalid", "tonies_password": "cloud password"})
    assert response.status_code == 200 and response.json()["ok"]
    return c


def form(page, action, values):
    html = (ROOT / "web" / page).read_text()
    script = re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", html, re.S)[-1]
    harness = r'''
const vm = require('node:vm');
const fs = require('node:fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const elements = new Map(), posts = [];
function element(id) {
  if (!elements.has(id)) elements.set(id, {value:'', textContent:'', style:{},
    addEventListener(type, callback) {this[type] = callback;}});
  return elements.get(id);
}
global.document = {getElementById:element};
global.localStorage = {getItem(){return null;}, setItem(){}};
global.window = {location:{search:'',replace(){},href:''}};
global.setTimeout = () => 0;
global.fetch = async (url, options) => {
  if (options?.body) posts.push({url,body:JSON.parse(options.body)});
  return {json:async()=>options ? {ok:false,error:'offline capture'} :
    {configured:true,unlocked:true,username:'synthetic',source:'vault',versions:{}}};
};
(async () => {
  vm.runInThisContext(input.script);
  await new Promise(resolve=>setImmediate(resolve));
  // A human types after initialization/status prefills, before submitting.
  for (const [id, value] of Object.entries(input.values)) element(id).value = value;
  const el=element(input.action);
  if (el.onclick) await el.onclick();
  else await el.submit({preventDefault(){}});
  process.stdout.write(JSON.stringify({posts, status:element('status').textContent}));
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    result = subprocess.run(["node", "-e", harness],
                            input=json.dumps({"script": script, "action": action, "values": values}),
                            text=True, capture_output=True, check=True, timeout=10)
    return json.loads(result.stdout)


def submit(c, page, action, values):
    captured = form(page, action, values)
    assert len(captured["posts"]) == 1, captured
    post = captured["posts"][0]
    return c.post(post["url"], json=post["body"])


@pytest.mark.parametrize("password", [PASSWORD, "  synthetic edge password  "])
def test_login_uses_exact_password_accepted_by_real_setup(password):
    owner = initialize(password)
    assert owner.post("/api/setup/lock").json()["ok"]
    c = client()
    response = submit(c, "login.html", "login-form", {"username": "  synthetic  ", "app-password": password})
    assert response.status_code == 200 and response.json()["ok"]
    assert c.get("/api/jobs").status_code == 200
    if password != password.strip():
        assert not client().post("/api/setup/login", json={"username": USER, "app_password": password.strip()}).json()["ok"]


def test_setup_preserves_app_and_cloud_passwords_and_normalizes_identifiers():
    password, cloud = "  exact setup password  ", "  exact cloud password  "
    c = client()
    response = submit(c, "setup.html", "create", {"username": "  synthetic  ", "app-password": password,
                      "tonies-email": "  synthetic@example.invalid  ", "tonies-password": cloud})
    assert response.json()["ok"]
    assert credentials.get_credentials() == {"email": "synthetic@example.invalid", "password": cloud, "source": "vault"}
    assert credentials.setup_status()["username"] == USER
    assert c.post("/api/setup/lock").json()["ok"]
    assert not credentials.login_unlock(USER, password.strip())["ok"]
    assert submit(client(), "login.html", "login-form", {"username": USER, "app-password": password}).json()["ok"]


def test_settings_preserves_exact_current_and_new_password():
    current, replacement = "  exact current password  ", "  exact replacement password  "
    owner = initialize(current)
    assert submit(owner, "account.html", "save", {"current": current, "next": replacement,
                                                "next-confirm": replacement}).json()["ok"]
    assert owner.get("/api/jobs").status_code == 200
    assert owner.post("/api/setup/lock").json()["ok"]
    assert not credentials.login_unlock(USER, replacement.strip())["ok"]
    assert submit(client(), "login.html", "login-form", {"username": USER, "app-password": replacement}).json()["ok"]


@pytest.mark.parametrize("replacement,confirmation", [(" replacement password ", "replacement password"),
                                                        ("replacement password", "replacement password ")])
def test_confirmation_mismatch_does_not_post_or_change_password(replacement, confirmation):
    initialize()
    before = (main.settings.data_dir / "secrets" / "vault-meta.json").read_bytes()
    captured = form("account.html", "save", {"current": PASSWORD, "next": replacement, "next-confirm": confirmation})
    assert captured["posts"] == []
    assert "do not match" in captured["status"]
    assert (main.settings.data_dir / "secrets" / "vault-meta.json").read_bytes() == before
    assert credentials.login_unlock(USER, PASSWORD)["ok"]


def test_settings_preserves_exact_app_and_cloud_passwords():
    password, cloud = "  exact current password  ", "  replacement cloud password  "
    owner = initialize(password)
    response = submit(owner, "account.html", "save-tonies", {"cred-app-password": password,
                      "tonies-email": "  replacement@example.invalid  ", "tonies-password": cloud})
    assert response.json()["ok"]
    assert owner.get("/api/jobs").status_code == 200
    assert owner.post("/api/setup/lock").json()["ok"]
    assert submit(client(), "login.html", "login-form", {"username": USER, "app-password": password}).json()["ok"]
    assert credentials.get_credentials() == {"email": "replacement@example.invalid", "password": cloud, "source": "vault"}


@pytest.mark.parametrize("password", ["short", " " * 20, "x" * 1025], ids=["short", "whitespace", "long"])
def test_setup_form_does_not_weaken_new_password_policy(password):
    captured = form("setup.html", "create", {"username": USER, "app-password": password,
                    "tonies-email": "synthetic@example.invalid", "tonies-password": "cloud password"})
    if captured["posts"]:
        post = captured["posts"][0]
        assert not client().post(post["url"], json=post["body"]).json().get("ok")
    else:
        assert "required" in captured["status"]
    assert not credentials.vault_configured()


@pytest.mark.parametrize("password", ["short", " " * 20, "x" * 1025], ids=["short", "whitespace", "long"])
def test_settings_form_does_not_weaken_new_password_policy(password):
    owner = initialize()
    before = (main.settings.data_dir / "secrets" / "vault-meta.json").read_bytes()
    captured = form("account.html", "save", {"current": PASSWORD, "next": password, "next-confirm": password})
    if captured["posts"]:
        post = captured["posts"][0]
        assert not owner.post(post["url"], json=post["body"]).json().get("ok")
    else:
        assert "required" in captured["status"]
    assert (main.settings.data_dir / "secrets" / "vault-meta.json").read_bytes() == before
    assert credentials.login_unlock(USER, PASSWORD)["ok"]


@pytest.mark.parametrize("legacy", ["old", "x" * 2048], ids=["short", "long"])
def test_legacy_normalization_is_server_only_and_forms_can_rotate(legacy):
    root = main.settings.data_dir / "secrets"
    root.mkdir()
    salt_bytes = b"0123456789abcdef"
    salt = base64.urlsafe_b64encode(salt_bytes).decode()
    derived = hashlib.pbkdf2_hmac("sha256", legacy.encode(), salt_bytes, 390000, dklen=32)
    key = Fernet.generate_key()
    meta = {"username": USER, "verifier_salt": salt, "verifier_hash": base64.urlsafe_b64encode(derived).decode(),
            "wrap_salt": salt, "wrapped_vault_key": Fernet(base64.urlsafe_b64encode(derived)).encrypt(key).decode(),
            "kdf": "pbkdf2-sha256", "kdf_iterations": 390000, "version": 1}
    (root / "vault-meta.json").write_text(json.dumps(meta))
    encrypted = Fernet(key).encrypt(json.dumps({"tonies_email": "old@example.invalid", "tonies_password": "cloud password"}).encode())
    (root / "vault-credentials.enc").write_bytes(encrypted)
    padded = " " + legacy + " "
    captured = form("login.html", "login-form", {"username": USER, "app-password": padded})
    assert captured["posts"][0]["body"]["app_password"] == padded
    owner = client()
    assert submit(owner, "login.html", "login-form", {"username": USER, "app-password": padded}).json()["ok"]
    replacement = "  exact migrated password  "
    assert submit(owner, "account.html", "save", {"current": padded, "next": replacement, "next-confirm": replacement}).json()["ok"]
    assert (root / "vault-credentials.enc").read_bytes() == encrypted
    assert owner.post("/api/setup/lock").json()["ok"]
    assert not credentials.login_unlock(USER, replacement.strip())["ok"]
    assert submit(client(), "login.html", "login-form", {"username": USER, "app-password": replacement}).json()["ok"]
