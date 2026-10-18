"""A ready-made image: Compose pulls it, falls back to building, and releases publish it."""
from pathlib import Path

import pytest
import yaml

from test_container_security import compose  # noqa: F401  (shared rendered-Compose fixture)

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "release-image.yml"
IMAGE = "ghcr.io/c1rca/tonies-yt"


def test_compose_uses_the_published_image_and_can_still_build(compose):  # noqa: F811
    service = compose["services"]["tonies-yt"]
    assert service["image"] == f"{IMAGE}:latest"
    assert service["build"]["context"] == str(ROOT)


def test_the_image_tag_can_be_pinned():
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert f"image: {IMAGE}:${{TONIES_IMAGE_TAG:-latest}}" in text
    assert "TONIES_IMAGE_TAG=" in (ROOT / ".env.example").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def workflow():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_images_are_published_only_for_version_tags(workflow):
    triggers = workflow[True]  # YAML 1.1 reads the "on" key as True
    assert triggers["push"] == {"tags": ["v*"]}
    assert set(triggers) <= {"push", "workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read", "packages": "write"}
    steps = workflow["jobs"]["image"]["steps"]
    build = next(step for step in steps if step.get("name") == "Build and push")
    assert build["with"]["push"] == "${{ github.event_name == 'push' }}"


def test_images_cover_pcs_and_raspberry_pis(workflow):
    steps = workflow["jobs"]["image"]["steps"]
    build = next(step for step in steps if step.get("name") == "Build and push")
    assert build["with"]["platforms"] == "linux/amd64,linux/arm64"
    meta = next(step for step in steps if step.get("uses", "").startswith("docker/metadata-action@"))
    assert meta["with"]["images"] == IMAGE
    for pattern in ("{{version}}", "{{major}}.{{minor}}", "{{major}}"):
        assert f"type=semver,pattern={pattern}" in meta["with"]["tags"]


def test_release_image_is_smoke_tested_before_it_is_pushed(workflow):
    names = [step.get("name", "") for step in workflow["jobs"]["image"]["steps"]]
    assert names.index("Smoke test") < names.index("Build and push")
