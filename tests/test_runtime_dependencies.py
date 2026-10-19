"""Offline packaging contracts; these do not replace an isolated image build."""

import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_build_context_excludes_runtime_secrets_and_private_data():
    path = ROOT / '.dockerignore'
    assert path.is_file(), 'Docker context must exclude private runtime data'
    patterns = path.read_text().splitlines()
    assert all(item in patterns for item in ['.git', '.env', 'data', '.venv', '__pycache__'])



def _requirements():
    return {
        re.split(r"[<>=!~\[]", line, maxsplit=1)[0].lower(): line
        for raw in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if (line := raw.split("#", 1)[0].strip())
    }


def _docker_instructions():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    text = re.sub(r"\\\r?\n", " ", text)
    return [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def test_requirements_use_existing_http_client_without_browser_dependency():
    requirements = _requirements()
    assert "playwright" not in requirements
    assert requirements["httpx"] == "httpx==0.27.2"
    assert requirements["yt-dlp"] == "yt-dlp[default]>=2025.1.15"


def test_web_parsers_pin_audited_security_fixes():
    requirements = _requirements()
    assert requirements["fastapi"] == "fastapi==0.135.0"
    assert requirements["starlette"] == "starlette==1.3.1"
    assert requirements["python-multipart"] == "python-multipart==0.0.32"


def test_youtube_challenge_solver_is_installed_without_runtime_downloads():
    import importlib.util
    assert importlib.util.find_spec("yt_dlp_ejs") is not None, "Install yt-dlp default extras so disabled remote components do not remove the challenge solver"


def test_vault_crypto_is_an_explicit_pinned_dependency():
    assert re.fullmatch(r"cryptography==\d+\.\d+\.\d+", _requirements().get("cryptography", ""))


def test_container_uses_lean_python_without_browser_installation():
    instructions = _docker_instructions()
    assert [line for line in instructions if line.upper().startswith("FROM ")] == ["FROM python:3.12-slim"]
    assert not re.search(r"playwright|chromium|firefox|webkit", "\n".join(instructions), re.IGNORECASE)


@pytest.mark.parametrize("package", ["ffmpeg", "curl", "unzip", "ca-certificates"])
def test_container_keeps_media_and_https_system_tools(package):
    installs = [shlex.split(line) for line in _docker_instructions() if "apt-get install" in line]
    assert any(package in tokens for tokens in installs)
    assert any("--no-install-recommends" in tokens for tokens in installs)


def test_container_keeps_verified_deno_and_current_yt_dlp_workflow():
    text = "\n".join(_docker_instructions())
    assert "https://github.com/denoland/deno/releases/download/v2.9.7/" in text
    assert "sha256sum -c -" in text
    assert "/usr/local/bin" in text
    assert "pip install --no-cache-dir -r requirements.txt" in text
    assert "pip install --no-cache-dir --upgrade 'yt-dlp[default]'" in text


def test_container_keeps_app_entrypoint_and_compose_data_port_contract():
    instructions = _docker_instructions()
    command = next(line[4:] for line in instructions if line.startswith("CMD "))
    assert json.loads(command) == ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-proxy-headers"]
    assert "WORKDIR /app" in instructions
    assert "COPY app ./app" in instructions
    assert "COPY web ./web" in instructions
    assert "ENV APP_PORT=8080" in instructions
    # New installs use a project-scoped named volume, never an implicit host bind.
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert '${TONIES_BIND_ADDRESS:-127.0.0.1}:${TONIES_PORT:-8090}:8080' in compose
    assert "- tonies-data:/app/data" in compose
    assert "path: .env" in compose
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "DATA_DIR=/app/data" in example
    assert "TONIES_STORAGE_STATE_FILE=" not in example
    assert "_SELECTOR=" not in example


def test_vault_and_http_dependencies_work_without_importing_playwright(tmp_path):
    env = dict(os.environ, DATA_DIR=str(tmp_path), LOG_FILE=str(tmp_path / "logs" / "test.log"), TONIES_EMAIL="", TONIES_PASSWORD="")
    probe = r'''
import importlib.abc
import sys

class NoBrowser(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "playwright" or fullname.startswith("playwright."):
            raise AssertionError("Browser runtime must not be required")

sys.meta_path.insert(0, NoBrowser())
from app import credentials
import httpx
cipher = credentials.Fernet(credentials.Fernet.generate_key())
assert cipher.decrypt(cipher.encrypt(b"synthetic-vault-contract")) == b"synthetic-vault-contract"
with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True}))) as client:
    assert client.get("https://example.invalid/offline").json() == {"ok": True}
assert not any(name == "playwright" or name.startswith("playwright.") for name in sys.modules)
'''
    result = subprocess.run([sys.executable, "-c", probe], cwd=ROOT, env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_release_version_is_consistent():
    from app import __version__
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)
    assert f'org.opencontainers.image.version="{__version__}"' in (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert f"## [{__version__}]" in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def test_runtime_requirements_exclude_test_and_unused_packages():
    requirements = _requirements()
    assert not {"pytest", "keyring", "python-dotenv"} & requirements.keys()
    assert "pytest" in (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
