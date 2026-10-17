"""Effective Docker defaults; live build/smoke remains a release gate."""
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def compose():
    if not shutil.which("docker"):
        pytest.skip("Docker CLI required to render Compose configuration")
    result = subprocess.run(
        ["docker", "compose", "--env-file", ".env.example", "-p", "tonies-config-test", "config", "--no-env-resolution", "--format", "json"],
        cwd=ROOT, capture_output=True, text=True, check=True,
        env={"PATH": __import__("os").environ["PATH"]}, timeout=20,
    )
    return json.loads(result.stdout)


def test_default_container_is_nonroot_and_immutable(compose):
    service = compose["services"]["tonies-yt"]
    assert service["user"] == "10001:10001"
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert not service.get("privileged", False)
    assert service.get("network_mode") != "host"
    assert service["init"] is True


def test_default_network_and_state_are_project_scoped(compose):
    service = compose["services"]["tonies-yt"]
    assert "container_name" not in service
    assert service["ports"][0]["host_ip"] == "127.0.0.1"
    assert str(service["ports"][0]["published"]) == "8090"
    assert service["ports"][0]["target"] == 8080
    assert len(service["volumes"]) == 1
    volume = service["volumes"][0]
    assert volume["type"] == "volume"
    assert volume["target"] == "/app/data"
    assert volume["source"] in compose["volumes"]
    assert not compose["volumes"][volume["source"]].get("external", False)


def test_default_resource_and_scratch_limits(compose):
    service = compose["services"]["tonies-yt"]
    assert 0 < service["pids_limit"] <= 256
    assert 0 < int(service["mem_limit"]) <= 2 * 1024**3
    assert 0 < float(service["cpus"]) <= 2
    assert service["tmpfs"] == ["/tmp:rw,noexec,nosuid,nodev,size=268435456,mode=1777"]
    assert service["environment"]["OMP_NUM_THREADS"] == "1"
    assert service["environment"]["HOME"] == "/tmp/home"


def test_image_owns_only_data_and_never_runs_remote_install_script():
    text = (ROOT / "Dockerfile").read_text()
    assert re.search(r"(?m)^USER 10001:10001$", text)
    assert "PYTHONDONTWRITEBYTECODE=1" in text
    assert "PYTHONNOUSERSITE=1" in text
    assert "sha256sum -c" in text
    assert "https://deno.land/install.sh" not in text
    assert "/root/.deno" not in text
    assert "chown -R" not in text
    assert not re.search(r"curl[^\n]*\|\s*(?:sh|bash)", text)


def test_image_removes_build_only_python_installer():
    text = (ROOT / "Dockerfile").read_text()
    assert "python -m pip uninstall --yes pip" in text


def test_context_excludes_backups_and_local_artifacts():
    patterns = (ROOT / ".dockerignore").read_text().splitlines()
    for pattern in ("data*", "backups", "*.tar", "*.tar.gz", ".env.*", "node_modules", ".venv"):
        assert pattern in patterns
